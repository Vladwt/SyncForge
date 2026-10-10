"""Reliable file transfer engine with chunking, temporary files, and verification."""

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List
import structlog
from syncforge.core.models import (
    PlannedOperation, PlanAction, ExecutionStatus, ConflictResolution, SyncProfile, SyncMode
)
from syncforge.core.hasher import compute_blake3_hash
from syncforge.core.path_validator import PathValidator
from syncforge.core.version_manager import VersionManager
from syncforge.storage.repositories import BaselineRepository, HistoryRepository

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
    - Update baseline on success
    """

    def __init__(
        self,
        baseline_repo: Optional[BaselineRepository] = None,
        history_repo: Optional[HistoryRepository] = None
    ):
        self.baseline_repo = baseline_repo
        self.history_repo = history_repo
        self._is_cancelled = False
        self._is_paused = False

    def cancel(self):
        self._is_cancelled = True

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

        total_bytes = sum(op.size_bytes for op in operations if op.action in (PlanAction.COPY, PlanAction.UPDATE))
        progress = TransferProgress(
            total_operations=len(operations),
            completed_operations=0,
            total_bytes=total_bytes,
            transferred_bytes=0,
            status_text="Starting transfer..."
        )

        version_mgr = None
        if profile.dest_root and profile.preserve_versions:
            version_mgr = VersionManager(Path(profile.dest_root), profile.version_subfolder)

        errors_count = 0
        conflicts_count = 0
        bytes_transferred = 0

        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        for op in operations:
            if self._is_cancelled:
                progress.status_text = "Operation cancelled by user."
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
                progress.current_speed_bps = (bytes_transferred - last_bytes) / dt if dt > 0 else 0
                last_time = now
                last_bytes = bytes_transferred

            if progress_callback:
                progress_callback(progress)

            # Process operation
            try:
                if op.action == PlanAction.SKIP or op.action == PlanAction.PRESERVE:
                    op.status = ExecutionStatus.SKIPPED
                elif op.action == PlanAction.ERROR:
                    op.status = ExecutionStatus.FAILED
                    errors_count += 1
                elif op.action == PlanAction.CONFLICT:
                    if op.conflict_resolution is not None:
                        self._resolve_and_execute_conflict(profile, op, version_mgr)
                    else:
                        op.status = ExecutionStatus.CONFLICT
                        conflicts_count += 1
                elif op.action in (PlanAction.COPY, PlanAction.UPDATE, PlanAction.RESTORE):
                    self._execute_copy_or_update(profile, op, version_mgr)
                    bytes_transferred += op.size_bytes
                    progress.transferred_bytes = bytes_transferred
            except Exception as e:
                logger.error("Error executing operation", op=op.rel_path, error=str(e))
                op.status = ExecutionStatus.FAILED
                op.error_message = str(e)
                errors_count += 1

            progress.completed_operations += 1
            if progress_callback:
                progress_callback(progress)

        status = ExecutionStatus.SUCCESS if errors_count == 0 and conflicts_count == 0 else ExecutionStatus.FAILED
        return status

    def _execute_copy_or_update(
        self, profile: SyncProfile, op: PlannedOperation, version_mgr: Optional[VersionManager]
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
            # Stream copying
            with open(src_path, "rb") as fsrc, open(tmp_dst, "wb") as fdst:
                while True:
                    if self._is_cancelled:
                        tmp_dst.unlink(missing_ok=True)
                        raise InterruptedError("Cancelled during transfer")
                    chunk = fsrc.read(COPY_BUFFER_SIZE)
                    if not chunk:
                        break
                    fdst.write(chunk)

            # Preserve metadata (timestamps, attributes)
            shutil.copystat(str(src_path), str(tmp_dst))

            # Verification before replacement: check file sizes
            tmp_stat = tmp_dst.stat()
            src_stat = src_path.stat()
            if tmp_stat.st_size != src_stat.st_size or tmp_stat.st_size != op.size_bytes:
                raise IOError(f"File size mismatch or modified during transfer! Original planned: {op.size_bytes}, Copied: {tmp_stat.st_size}")

            # Verify BLAKE3 hashes if enabled
            if profile.verify_hashes:
                src_hash = compute_blake3_hash(src_path)
                tmp_hash = compute_blake3_hash(tmp_dst)
                if src_hash != tmp_hash:
                    raise IOError(f"BLAKE3 hash mismatch! File corrupted during transfer.")
            else:
                src_hash = None

            # Safe atomic replacement: archive old version right before replace
            if dst_path.exists() and version_mgr and profile.preserve_versions:
                version_mgr.archive_version(dst_path, op.rel_path, profile.max_versions)

            os.replace(str(tmp_dst), str(dst_path))

            op.status = ExecutionStatus.SUCCESS

            # Update baseline in Mode B
            if profile.mode == SyncMode.BIDIRECTIONAL and self.baseline_repo:
                dst_st = dst_path.stat()
                self.baseline_repo.save_baseline_entry(
                    profile_id=profile.id,
                    mapping_id=op.mapping_id,
                    rel_path=op.rel_path,
                    side="source",
                    size_bytes=src_stat.st_size,
                    mtime_ns=int(src_stat.st_mtime_ns),
                    hash_blake3=src_hash
                )
                self.baseline_repo.save_baseline_entry(
                    profile_id=profile.id,
                    mapping_id=op.mapping_id,
                    rel_path=op.rel_path,
                    side="dest",
                    size_bytes=dst_st.st_size,
                    mtime_ns=int(dst_st.st_mtime_ns),
                    hash_blake3=src_hash
                )

        except Exception as e:
            if tmp_dst.exists():
                try:
                    tmp_dst.unlink()
                except Exception:
                    pass
            raise e

    def _resolve_and_execute_conflict(
        self, profile: SyncProfile, op: PlannedOperation, version_mgr: Optional[VersionManager]
    ):
        """Executes a conflict resolution decision."""
        if op.conflict_resolution == ConflictResolution.SKIP:
            op.status = ExecutionStatus.SKIPPED
            return

        elif op.conflict_resolution == ConflictResolution.KEEP_SOURCE:
            # Overwrite dest with source
            self._execute_copy_or_update(profile, op, version_mgr)

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
            self._execute_copy_or_update(profile, inverted_op, version_mgr)
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
            self._execute_copy_or_update(profile, custom_op, version_mgr)
            op.status = ExecutionStatus.SUCCESS
