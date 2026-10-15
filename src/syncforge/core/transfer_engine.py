"""Reliable file transfer engine with chunking, temporary files, and verification."""

import os
import shutil
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List
import uuid
import blake3
import structlog
from syncforge.core.models import (
    PlannedOperation, PlanAction, ExecutionStatus, ConflictResolution, SyncProfile, SyncMode
)
from syncforge.core.hasher import compute_blake3_hash
from syncforge.core.path_validator import PathValidator
from syncforge.core.version_manager import VersionManager
from syncforge.storage.repositories import BaselineRepository, HistoryRepository, TombstoneRepository, RuleRepository
from syncforge.rules.rule_engine import Rule, RuleType

logger = structlog.get_logger(__name__)

COPY_BUFFER_SIZE = 1024 * 1024  # 1 MiB chunk


@dataclass
class TransferProgress:
    total_operations: int = 0
    completed_operations: int = 0
    total_bytes: int = 0
    transferred_bytes: int = 0
    current_file: str = ""
    current_speed_bps: float = 0.0
    status_text: str = ""


class TransferEngine:
    """
    Executes planned operations safely:
    - Free space check
    - Stream copy to .tmp destination
    - Hash verification
    - Optional versioning of old file
    - Atomic rename / replace
    - Never delete good destination before replacement is validated
    - Update baseline and tombstones on success
    """

    def __init__(
        self,
        baseline_repo: Optional[BaselineRepository] = None,
        history_repo: Optional[HistoryRepository] = None,
        tombstone_repo: Optional[TombstoneRepository] = None,
        rule_repo: Optional[RuleRepository] = None
    ):
        self.baseline_repo = baseline_repo
        self.history_repo = history_repo
        self.tombstone_repo = tombstone_repo
        self.rule_repo = rule_repo
        self._is_cancelled = False
        self._is_paused = False
        self.last_abort_reason: Optional[str] = None

    def reset(self):
        """Resets cancellation and pause flags for a clean execution."""
        self._is_cancelled = False
        self._is_paused = False
        self.last_abort_reason = None
        self._consecutive_errors = 0

    def cancel(self):
        self._is_cancelled = True
        self._is_paused = False

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def execute_plan(
        self,
        job_id: str,
        profile: SyncProfile,
        operations: List[PlannedOperation],
        progress_callback: Optional[Callable[[TransferProgress], None]] = None
    ) -> ExecutionStatus:
        self._is_paused = False
        self.last_abort_reason = None
        self._consecutive_errors = 0

        total_bytes = sum(op.size_bytes for op in operations if op.action in (PlanAction.COPY, PlanAction.UPDATE))
        progress = TransferProgress(
            total_operations=len(operations),
            completed_operations=0,
            total_bytes=total_bytes,
            transferred_bytes=0,
            status_text="Starting transfer..."
        )

        if self._is_cancelled:
            progress.status_text = "Синхронізацію скасовано користувачем."
            if progress_callback:
                progress_callback(progress)
            return ExecutionStatus.CANCELLED

        version_mgr = None
        if profile.dest_root and profile.preserve_versions:
            version_mgr = VersionManager(Path(profile.dest_root), profile.version_subfolder)

        errors_count = 0
        conflicts_count = 0
        bytes_transferred = 0

        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        # Buffer size and throttling config
        buf_size = max(4096, getattr(profile, "io_buffer_size_kb", 128) * 1024)
        yield_delay_s = max(0.0, getattr(profile, "io_yield_delay_ms", 0.0) / 1000.0)
        speed_limit_mb = max(0.0, getattr(profile, "io_speed_limit_mb", 0.0))
        speed_limit_bps = speed_limit_mb * 1024 * 1024 if speed_limit_mb > 0 else 0.0

        # SQLite batching buffers
        pending_baselines: List[tuple] = []
        pending_tombstones: List[tuple] = []
        last_flush_time = time.time()

        def flush_batches(force: bool = False):
            nonlocal last_flush_time
            now_f = time.time()
            if force or len(pending_baselines) >= 50 or (now_f - last_flush_time >= 2.0 and pending_baselines):
                if self.baseline_repo and pending_baselines:
                    self.baseline_repo.save_baseline_batch(pending_baselines)
                    pending_baselines.clear()
                if self.tombstone_repo and pending_tombstones:
                    self.tombstone_repo.remove_tombstones_batch(pending_tombstones)
                    pending_tombstones.clear()
                last_flush_time = now_f

        def on_chunk_transferred(n_bytes: int):
            nonlocal bytes_transferred, last_time, last_bytes
            bytes_transferred += n_bytes
            progress.transferred_bytes = bytes_transferred
            now_c = time.time()
            if now_c - last_time >= 0.25:
                dt = now_c - last_time
                progress.current_speed_bps = (bytes_transferred - last_bytes) / dt if dt > 0 else 0.0
                last_time = now_c
                last_bytes = bytes_transferred
                if progress_callback:
                    progress_callback(progress)

        try:
            for op in operations:
                if self._is_cancelled:
                    progress.status_text = "Синхронізацію скасовано користувачем."
                    if progress_callback:
                        progress_callback(progress)
                    return ExecutionStatus.CANCELLED

                while self._is_paused:
                    time.sleep(0.2)
                    if self._is_cancelled:
                        return ExecutionStatus.CANCELLED

                progress.current_file = op.rel_path
                now = time.time()
                if now - last_time >= 0.5:
                    dt = now - last_time
                    progress.current_speed_bps = (bytes_transferred - last_bytes) / dt if dt > 0 else 0.0
                    last_time = now
                    last_bytes = bytes_transferred

                if progress_callback:
                    progress_callback(progress)

                # Process operation
                try:
                    if op.action == PlanAction.SKIP:
                        op.status = ExecutionStatus.SKIPPED

                    elif op.action == PlanAction.PRESERVE:
                        # User confirmed keeping file on SSD or PC
                        if not op.source_abs and op.dest_abs:
                            if self.tombstone_repo:
                                self.tombstone_repo.record_tombstone(
                                    profile.id, op.mapping_id, op.rel_path, "source", "keep_dest"
                                )
                            if self.baseline_repo:
                                self.baseline_repo.remove_baseline_entry(profile.id, op.mapping_id, op.rel_path)
                        elif op.source_abs and not op.dest_abs:
                            if self.tombstone_repo:
                                self.tombstone_repo.record_tombstone(
                                    profile.id, op.mapping_id, op.rel_path, "dest", "keep_src"
                                )
                            if self.baseline_repo:
                                self.baseline_repo.remove_baseline_entry(profile.id, op.mapping_id, op.rel_path)
                        op.status = ExecutionStatus.SUCCESS

                    elif op.action == PlanAction.RESTORE:
                        self._execute_restore(
                            profile, op, version_mgr,
                            buf_size=buf_size,
                            yield_delay_s=yield_delay_s,
                            speed_limit_bps=speed_limit_bps,
                            on_chunk=on_chunk_transferred,
                            pending_baselines=pending_baselines,
                            pending_tombstones=pending_tombstones
                        )
                        progress.transferred_bytes = bytes_transferred

                    elif op.action == PlanAction.ARCHIVE:
                        self._execute_archive(profile, op)

                    elif op.action == PlanAction.DELETE_DEST:
                        self._execute_delete_dest(profile, op)

                    elif op.action == PlanAction.IGNORE:
                        self._execute_ignore(profile, op)

                    elif op.action == PlanAction.ERROR:
                        op.status = ExecutionStatus.FAILED
                        errors_count += 1

                    elif op.action == PlanAction.CONFLICT:
                        if op.conflict_resolution is not None:
                            self._resolve_and_execute_conflict(
                                profile, op, version_mgr,
                                buf_size=buf_size,
                                yield_delay_s=yield_delay_s,
                                speed_limit_bps=speed_limit_bps,
                                on_chunk=on_chunk_transferred,
                                pending_baselines=pending_baselines,
                                pending_tombstones=pending_tombstones
                            )
                        else:
                            op.status = ExecutionStatus.CONFLICT
                            conflicts_count += 1

                    elif op.action in (PlanAction.COPY, PlanAction.UPDATE):
                        self._execute_copy_or_update(
                            profile, op, version_mgr,
                            buf_size=buf_size,
                            yield_delay_s=yield_delay_s,
                            speed_limit_bps=speed_limit_bps,
                            on_chunk=on_chunk_transferred,
                            pending_baselines=pending_baselines,
                            pending_tombstones=pending_tombstones
                        )
                        progress.transferred_bytes = bytes_transferred

                    flush_batches(force=False)

                except InterruptedError as e:
                    logger.info("Operation interrupted by cancellation", op=op.rel_path)
                    op.status = ExecutionStatus.CANCELLED
                    op.error_message = str(e)
                    progress.status_text = "Синхронізацію скасовано користувачем."
                    if progress_callback:
                        progress_callback(progress)
                    return ExecutionStatus.CANCELLED

                except Exception as e:
                    if self._is_cancelled:
                        logger.info("Operation interrupted by cancellation", op=op.rel_path)
                        op.status = ExecutionStatus.CANCELLED
                        op.error_message = str(e)
                        progress.status_text = "Синхронізацію скасовано користувачем."
                        if progress_callback:
                            progress_callback(progress)
                        return ExecutionStatus.CANCELLED

                    logger.error("Error executing operation", op=op.rel_path, error=str(e))
                    op.status = ExecutionStatus.FAILED
                    winerror = getattr(e, "winerror", None)
                    if winerror in (1392, 433, 23):
                        op.error_message = f"Файл/сектор пошкоджено (потрібен CHKDSK): {e}"
                    else:
                        op.error_message = str(e)
                    errors_count += 1

                    # Detect genuine hardware / device disconnection:
                    # Check whether the drive root / anchor is physically missing from the OS.
                    is_device_disconnected = False
                    try:
                        dst_root_check = Path(op.dest_abs).anchor if op.dest_abs else (Path(profile.dest_root).anchor if profile.dest_root else "")
                        src_root_check = Path(op.source_abs).anchor if op.source_abs else ""
                        if dst_root_check and not Path(dst_root_check).exists():
                            is_device_disconnected = True
                        elif src_root_check and not Path(src_root_check).exists():
                            is_device_disconnected = True
                    except Exception:
                        pass

                    if is_device_disconnected:
                        reason_msg = f"Цільовий накопичувач відключено ({e})"
                        logger.critical("Target drive disconnected! Aborting remaining operations.", error=str(e))
                        progress.status_text = f"Помилка вводу/виводу: накопичувач відключено ({e})"
                        self.last_abort_reason = reason_msg
                        progress.completed_operations += 1
                        if progress_callback:
                            progress_callback(progress)
                        return ExecutionStatus.FAILED

                    # For individual file errors (WinError 1392 sector corruption, PermissionError, sharing violations, etc.):
                    # Continue transferring the remaining files so the rest of the batch succeeds!
                    logger.warning("Skipping failed file and continuing sync for remaining files", op=op.rel_path, error=str(e))

                # Inter-file yield delay if configured
                if yield_delay_s > 0:
                    time.sleep(yield_delay_s)

                progress.completed_operations += 1
                if progress_callback:
                    progress_callback(progress)

        finally:
            flush_batches(force=True)

        status = ExecutionStatus.SUCCESS if errors_count == 0 and conflicts_count == 0 else ExecutionStatus.FAILED
        return status

    def _execute_copy_or_update(
        self,
        profile: SyncProfile,
        op: PlannedOperation,
        version_mgr: Optional[VersionManager],
        buf_size: int = COPY_BUFFER_SIZE,
        yield_delay_s: float = 0.0,
        speed_limit_bps: float = 0.0,
        on_chunk: Optional[Callable[[int], None]] = None,
        pending_baselines: Optional[List[tuple]] = None,
        pending_tombstones: Optional[List[tuple]] = None,
    ):
        if not op.source_abs or not op.dest_abs:
            raise ValueError(f"Missing source or destination path for {op.rel_path}")

        src_path = Path(op.source_abs)
        dst_path = Path(op.dest_abs)

        if not src_path.exists():
            raise FileNotFoundError(f"Source file missing: {src_path}")

        # Check destination free space
        try:
            free_space = shutil.disk_usage(str(dst_path.parent if dst_path.parent.exists() else dst_path.root)).free
            if free_space < op.size_bytes + (10 * 1024 * 1024):  # Margin of 10 MiB
                raise IOError(f"Insufficient disk space on destination. Free: {free_space}, Required: {op.size_bytes}")
        except IOError:
            raise
        except Exception:
            pass

        dst_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy to temporary file first: <dest_file>.<uuid>.tmp
        tmp_dst = dst_path.parent / f"{dst_path.name}.{time.time_ns()}.tmp"

        try:
            src_hasher = blake3.blake3()
            # Stream copying with single-pass source BLAKE3 hashing & throttling
            with open(src_path, "rb") as fsrc, open(tmp_dst, "wb") as fdst:
                chunk_start = time.perf_counter()
                while True:
                    if self._is_cancelled:
                        tmp_dst.unlink(missing_ok=True)
                        raise InterruptedError("Cancelled during transfer")
                    while self._is_paused:
                        time.sleep(0.1)
                        if self._is_cancelled:
                            tmp_dst.unlink(missing_ok=True)
                            raise InterruptedError("Cancelled during transfer")
                    chunk = fsrc.read(buf_size)
                    if not chunk:
                        break
                    src_hasher.update(chunk)
                    fdst.write(chunk)

                    if on_chunk:
                        on_chunk(len(chunk))

                    # Speed limit throttle
                    if speed_limit_bps > 0:
                        elapsed = time.perf_counter() - chunk_start
                        expected_time = len(chunk) / speed_limit_bps
                        if expected_time > elapsed:
                            time.sleep(expected_time - elapsed)
                        chunk_start = time.perf_counter()

                    # Yield delay per chunk if specified
                    if yield_delay_s > 0:
                        time.sleep(yield_delay_s)

                # Ensure written bytes are flushed to disk
                fdst.flush()
                try:
                    os.fsync(fdst.fileno())
                except OSError:
                    pass

            src_hash = src_hasher.hexdigest()

            # Preserve metadata (timestamps, attributes)
            try:
                shutil.copystat(str(src_path), str(tmp_dst))
            except Exception as stat_err:
                logger.warning("copystat non-fatal error", path=op.rel_path, error=str(stat_err))

            # Verification before replacement: check file sizes
            tmp_stat = tmp_dst.stat()
            src_stat = src_path.stat()
            if tmp_stat.st_size != src_stat.st_size or tmp_stat.st_size != op.size_bytes:
                raise IOError(f"File size mismatch or modified during transfer! Original planned: {op.size_bytes}, Copied: {tmp_stat.st_size}")

            # Verify BLAKE3 hashes if enabled
            if profile.verify_hashes:
                # Source hash is already computed from the streaming copy loop without double reading!
                # Destination verification uses profile rate limits and buffer size
                tmp_hash = compute_blake3_hash(
                    tmp_dst,
                    chunk_size=buf_size,
                    yield_delay_ms=profile.io_yield_delay_ms,
                    speed_limit_mb=profile.io_speed_limit_mb
                )
                if src_hash != tmp_hash:
                    raise IOError(f"BLAKE3 hash mismatch! File corrupted during transfer.")

            # Safe atomic replacement: archive old version right before replace
            if dst_path.exists() and version_mgr and profile.preserve_versions:
                version_mgr.archive_version(dst_path, op.rel_path, profile.max_versions)

            os.replace(str(tmp_dst), str(dst_path))

            op.status = ExecutionStatus.SUCCESS

            # Correct Baseline side assignment:
            # Check if op.source_abs belongs to mapping.source_root (PC side)
            mapping = next((m for m in profile.mappings if m.id == op.mapping_id), None)
            is_source_pc = True
            if mapping and mapping.source_root and op.source_abs:
                try:
                    is_source_pc = Path(op.source_abs).resolve().is_relative_to(Path(mapping.source_root).resolve())
                except Exception:
                    is_source_pc = op.source_abs.replace('/', '\\').lower().startswith(
                        mapping.source_root.replace('/', '\\').lower()
                    )

            dst_st = dst_path.stat()
            if is_source_pc:
                pc_stat = src_stat
                ssd_stat = dst_st
            else:
                pc_stat = dst_st
                ssd_stat = src_stat

            now_dt = datetime.now()

            # Record baseline entries
            if pending_baselines is not None:
                pending_baselines.append((
                    profile.id, op.mapping_id, op.rel_path, "source",
                    pc_stat.st_size, int(pc_stat.st_mtime_ns), src_hash, now_dt
                ))
                pending_baselines.append((
                    profile.id, op.mapping_id, op.rel_path, "dest",
                    ssd_stat.st_size, int(ssd_stat.st_mtime_ns), src_hash, now_dt
                ))
            elif self.baseline_repo:
                self.baseline_repo.save_baseline_entry(
                    profile_id=profile.id,
                    mapping_id=op.mapping_id,
                    rel_path=op.rel_path,
                    side="source",
                    size_bytes=pc_stat.st_size,
                    mtime_ns=int(pc_stat.st_mtime_ns),
                    hash_blake3=src_hash
                )
                self.baseline_repo.save_baseline_entry(
                    profile_id=profile.id,
                    mapping_id=op.mapping_id,
                    rel_path=op.rel_path,
                    side="dest",
                    size_bytes=ssd_stat.st_size,
                    mtime_ns=int(ssd_stat.st_mtime_ns),
                    hash_blake3=src_hash
                )

            # Clear persistent tombstone on successful copy/update
            if pending_tombstones is not None:
                pending_tombstones.append((profile.id, op.mapping_id, op.rel_path))
            elif self.tombstone_repo:
                self.tombstone_repo.remove_tombstone(profile.id, op.mapping_id, op.rel_path)

        except Exception as e:
            if tmp_dst.exists():
                try:
                    tmp_dst.unlink()
                except Exception:
                    pass
            raise e

    def _execute_restore(
        self,
        profile: SyncProfile,
        op: PlannedOperation,
        version_mgr: Optional[VersionManager],
        buf_size: int = COPY_BUFFER_SIZE,
        yield_delay_s: float = 0.0,
        speed_limit_bps: float = 0.0,
        on_chunk: Optional[Callable[[int], None]] = None,
        pending_baselines: Optional[List[tuple]] = None,
        pending_tombstones: Optional[List[tuple]] = None,
    ):
        """Restores a file from destination SSD back to PC source."""
        if not op.dest_abs or not Path(op.dest_abs).exists():
            raise FileNotFoundError(f"Файл для відновлення на цільовому пристрої відсутній: {op.dest_abs}")

        mapping = next((m for m in profile.mappings if m.id == op.mapping_id), None)
        if mapping:
            src_path = Path(mapping.source_root) / op.rel_path
        elif op.source_abs:
            src_path = Path(op.source_abs)
        else:
            raise ValueError(f"Неможливо визначити шлях на ПК для відновлення: {op.rel_path}")

        restore_op = PlannedOperation(
            id=str(uuid.uuid4()),
            profile_id=profile.id,
            mapping_id=op.mapping_id,
            action=PlanAction.COPY,
            rel_path=op.rel_path,
            source_abs=op.dest_abs,  # Read from SSD
            dest_abs=str(src_path),  # Write to PC
            size_bytes=op.size_bytes
        )
        self._execute_copy_or_update(
            profile, restore_op, version_mgr,
            buf_size=buf_size,
            yield_delay_s=yield_delay_s,
            speed_limit_bps=speed_limit_bps,
            on_chunk=on_chunk,
            pending_baselines=pending_baselines,
            pending_tombstones=pending_tombstones
        )
        if pending_tombstones is not None:
            pending_tombstones.append((profile.id, op.mapping_id, op.rel_path))
        elif self.tombstone_repo:
            self.tombstone_repo.remove_tombstone(profile.id, op.mapping_id, op.rel_path)
        op.status = ExecutionStatus.SUCCESS

    def _execute_archive(self, profile: SyncProfile, op: PlannedOperation):
        """Moves a file on destination SSD to .syncforge_archive subfolder and records tombstone."""
        if not op.dest_abs or not Path(op.dest_abs).exists():
            raise FileNotFoundError(f"Файл для архівування відсутній: {op.dest_abs}")

        src_file = Path(op.dest_abs)
        dest_root = Path(profile.dest_root) if profile.dest_root else src_file.parent
        archive_dir = dest_root / ".syncforge_archive"

        rel_p = Path(op.rel_path)
        ts = int(time.time())
        archive_target = archive_dir / rel_p.parent / f"{rel_p.stem}.{ts}{rel_p.suffix}"
        archive_target.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(src_file), str(archive_target))

        if self.tombstone_repo:
            self.tombstone_repo.record_tombstone(
                profile.id, op.mapping_id, op.rel_path, "source", "archive"
            )
        if self.baseline_repo:
            self.baseline_repo.remove_baseline_entry(profile.id, op.mapping_id, op.rel_path)
        op.status = ExecutionStatus.SUCCESS

    def _execute_delete_dest(self, profile: SyncProfile, op: PlannedOperation):
        """Deletes a file on destination SSD after user confirmation and records tombstone."""
        if op.dest_abs and Path(op.dest_abs).exists():
            Path(op.dest_abs).unlink(missing_ok=True)

        if self.tombstone_repo:
            self.tombstone_repo.record_tombstone(
                profile.id, op.mapping_id, op.rel_path, "both", "deleted_both"
            )
        if self.baseline_repo:
            self.baseline_repo.remove_baseline_entry(profile.id, op.mapping_id, op.rel_path)
        op.status = ExecutionStatus.SUCCESS

    def _execute_ignore(self, profile: SyncProfile, op: PlannedOperation):
        """Creates an exclusion rule and records tombstone to ignore this file."""
        if self.rule_repo:
            pattern = op.rel_path.replace("\\", "/")
            rule = Rule(
                id=str(uuid.uuid4()),
                rule_type=RuleType.EXCLUDE_GLOB,
                pattern=pattern,
                priority=100,
                enabled=True
            )
            self.rule_repo.save_rule(rule, profile_id=profile.id)

        if self.tombstone_repo:
            self.tombstone_repo.record_tombstone(
                profile.id, op.mapping_id, op.rel_path, "source", "ignore"
            )
        op.status = ExecutionStatus.SKIPPED

    def _resolve_and_execute_conflict(
        self,
        profile: SyncProfile,
        op: PlannedOperation,
        version_mgr: Optional[VersionManager],
        buf_size: int = COPY_BUFFER_SIZE,
        yield_delay_s: float = 0.0,
        speed_limit_bps: float = 0.0,
        on_chunk: Optional[Callable[[int], None]] = None,
        pending_baselines: Optional[List[tuple]] = None,
        pending_tombstones: Optional[List[tuple]] = None,
    ):
        """Executes a conflict resolution decision."""
        if op.conflict_resolution == ConflictResolution.SKIP:
            op.status = ExecutionStatus.SKIPPED
            return

        elif op.conflict_resolution == ConflictResolution.KEEP_SOURCE:
            # Overwrite dest with source
            self._execute_copy_or_update(
                profile, op, version_mgr,
                buf_size=buf_size,
                yield_delay_s=yield_delay_s,
                speed_limit_bps=speed_limit_bps,
                on_chunk=on_chunk,
                pending_baselines=pending_baselines,
                pending_tombstones=pending_tombstones
            )

        elif op.conflict_resolution == ConflictResolution.KEEP_DEST:
            # Overwrite source with dest: invert source and dest
            inverted_op = PlannedOperation(
                id=op.id,
                profile_id=op.profile_id,
                mapping_id=op.mapping_id,
                action=PlanAction.UPDATE,
                rel_path=op.rel_path,
                source_abs=op.dest_abs,
                dest_abs=op.source_abs,
                size_bytes=op.size_bytes
            )
            self._execute_copy_or_update(
                profile, inverted_op, version_mgr,
                buf_size=buf_size,
                yield_delay_s=yield_delay_s,
                speed_limit_bps=speed_limit_bps,
                on_chunk=on_chunk,
                pending_baselines=pending_baselines,
                pending_tombstones=pending_tombstones
            )
            op.status = ExecutionStatus.SUCCESS

        elif op.conflict_resolution == ConflictResolution.KEEP_BOTH:
            # Create disambiguated filename on dest, e.g. "doc (SSD-conflict).txt"
            dst_p = Path(op.dest_abs)
            ext = dst_p.suffix
            stem = dst_p.stem
            disambiguated_name = f"{stem}_(conflict_{int(time.time())}){ext}"
            new_dst = dst_p.parent / disambiguated_name

            custom_op = PlannedOperation(
                id=op.id,
                profile_id=op.profile_id,
                mapping_id=op.mapping_id,
                action=PlanAction.COPY,
                rel_path=f"{dst_p.parent.name}/{disambiguated_name}",
                source_abs=op.source_abs,
                dest_abs=str(new_dst),
                size_bytes=op.size_bytes
            )
            self._execute_copy_or_update(
                profile, custom_op, version_mgr,
                buf_size=buf_size,
                yield_delay_s=yield_delay_s,
                speed_limit_bps=speed_limit_bps,
                on_chunk=on_chunk,
                pending_baselines=pending_baselines,
                pending_tombstones=pending_tombstones
            )
            op.status = ExecutionStatus.SUCCESS
