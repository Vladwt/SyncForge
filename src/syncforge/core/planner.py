"""Deterministic synchronization planner for Modes A, B, and C."""

import uuid
from pathlib import Path
from typing import Dict, List, Optional
import structlog
from syncforge.core.models import (
    SyncMode, PlanAction, PlannedOperation, SyncProfile, FolderMapping, FileMetadata
)
from syncforge.core.scanner import FileScanner
from syncforge.core.hasher import compute_blake3_hash
from syncforge.core.path_validator import PathValidator
from syncforge.rules.rule_engine import RuleEngine
from syncforge.storage.repositories import BaselineRepository

logger = structlog.get_logger(__name__)


class SyncPlanner:
    """Generates execution plans with conflict detection and deletion preservation."""

    def __init__(self, baseline_repo: Optional[BaselineRepository] = None, scanner: Optional[FileScanner] = None):
        self.baseline_repo = baseline_repo
        self.scanner = scanner or FileScanner()

    def generate_plan(
        self,
        profile: SyncProfile,
        active_dest_root: str,
        rule_engine: Optional[RuleEngine] = None
    ) -> List[PlannedOperation]:
        """
        Scans source and destination directories for each mapping in the profile
        and generates a deterministic list of PlannedOperations.
        """
        operations: List[PlannedOperation] = []
        rules = rule_engine or RuleEngine()

        for mapping in profile.mappings:
            if not mapping.enabled:
                continue

            src_root = Path(mapping.source_root).resolve()
            dst_root = (Path(active_dest_root) / mapping.dest_subpath).resolve()

            # Boundary validation
            valid, errs = PathValidator.validate_mappings([str(src_root)], [str(dst_root)])
            if not valid:
                for err in errs:
                    operations.append(PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.ERROR,
                        rel_path="",
                        reason=err,
                        error_message=err
                    ))
                continue

            # Scan files on both sides
            src_files = self.scanner.scan_directory(str(src_root))
            dst_files = self.scanner.scan_directory(str(dst_root)) if dst_root.exists() else {}

            # Load persistent baseline for Mode B
            baseline = {}
            if profile.mode == SyncMode.BIDIRECTIONAL and self.baseline_repo:
                baseline = self.baseline_repo.get_baseline(profile.id, mapping.id)

            mapping_ops = self._plan_mapping(
                profile=profile,
                mapping=mapping,
                src_root=src_root,
                dst_root=dst_root,
                src_files=src_files,
                dst_files=dst_files,
                baseline=baseline,
                rules=rules
            )
            operations.extend(mapping_ops)

        return operations

    def _plan_mapping(
        self,
        profile: SyncProfile,
        mapping: FolderMapping,
        src_root: Path,
        dst_root: Path,
        src_files: Dict[str, FileMetadata],
        dst_files: Dict[str, FileMetadata],
        baseline: Dict[str, Dict[str, dict]],
        rules: RuleEngine
    ) -> List[PlannedOperation]:
        operations: List[PlannedOperation] = []
        all_rel_paths = set(src_files.keys()) | set(dst_files.keys())

        # Collect destination targets to check for collision
        dest_target_map = {}

        for rel_path in sorted(all_rel_paths):
            src_meta = src_files.get(rel_path)
            dst_meta = dst_files.get(rel_path)

            # Check filtering rules for source files
            if src_meta:
                included, reason = rules.should_include(rel_path, src_meta.size_bytes)
                if not included:
                    # Excluded by rule
                    continue
                routed_rel = rules.determine_destination_path(rel_path)
            else:
                routed_rel = rel_path

            # Detect destination collisions from rules
            if routed_rel.lower() in dest_target_map:
                prev_path = dest_target_map[routed_rel.lower()]
                operations.append(PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.ERROR,
                    rel_path=rel_path,
                    reason=f"Routing collision: '{rel_path}' and '{prev_path}' both map to '{routed_rel}'",
                    error_message="Destination collision detected"
                ))
                continue
            dest_target_map[routed_rel.lower()] = rel_path

            src_abs = str(src_root / rel_path) if src_meta else None
            dst_abs = str(dst_root / routed_rel) if dst_meta or src_meta else None

            # Mode A: Safe One-Way Update
            if profile.mode == SyncMode.ONE_WAY_SAFE:
                op = self._plan_mode_a(
                    profile, mapping, rel_path, routed_rel, src_abs, dst_abs, src_meta, dst_meta
                )
                if op:
                    operations.append(op)

            # Mode B: Safe Bidirectional Sync
            elif profile.mode == SyncMode.BIDIRECTIONAL:
                base_entry = baseline.get(rel_path, {})
                op = self._plan_mode_b(
                    profile, mapping, rel_path, routed_rel, src_abs, dst_abs, src_meta, dst_meta, base_entry
                )
                if op:
                    operations.append(op)

            # Mode C: Versioned Cold Backup
            elif profile.mode == SyncMode.VERSIONED_BACKUP:
                op = self._plan_mode_c(
                    profile, mapping, rel_path, routed_rel, src_abs, dst_abs, src_meta, dst_meta
                )
                if op:
                    operations.append(op)

        return operations

    def _plan_mode_a(
        self, profile: SyncProfile, mapping: FolderMapping, rel_path: str, routed_rel: str,
        src_abs: Optional[str], dst_abs: Optional[str],
        src_meta: Optional[FileMetadata], dst_meta: Optional[FileMetadata]
    ) -> Optional[PlannedOperation]:
        """Mode A — Safe One-Way Update."""
        if src_meta and not dst_meta:
            # New file on source -> Copy to destination
            return PlannedOperation(
                id=str(uuid.uuid4()),
                profile_id=profile.id,
                mapping_id=mapping.id,
                action=PlanAction.COPY,
                rel_path=routed_rel,
                source_abs=src_abs,
                dest_abs=dst_abs,
                source_meta=src_meta,
                dest_meta=None,
                size_bytes=src_meta.size_bytes,
                reason="New source file"
            )

        elif src_meta and dst_meta:
            # Exists on both sides. Check if modified.
            if self._are_files_identical(src_meta, dst_meta, profile.verify_hashes):
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.SKIP,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Identical content"
                )
            else:
                # Modified on source -> Update destination
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.UPDATE,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Source file modified"
                )

        elif not src_meta and dst_meta:
            # File exists only on destination -> PRESERVE (Never propagate deletions!)
            return PlannedOperation(
                id=str(uuid.uuid4()),
                profile_id=profile.id,
                mapping_id=mapping.id,
                action=PlanAction.PRESERVE,
                rel_path=routed_rel,
                source_abs=None,
                dest_abs=dst_abs,
                source_meta=None,
                dest_meta=dst_meta,
                size_bytes=dst_meta.size_bytes,
                reason="Preserved on destination (deletion protection active)"
            )

        return None

    def _plan_mode_b(
        self, profile: SyncProfile, mapping: FolderMapping, rel_path: str, routed_rel: str,
        src_abs: Optional[str], dst_abs: Optional[str],
        src_meta: Optional[FileMetadata], dst_meta: Optional[FileMetadata],
        base_entry: Dict[str, dict]
    ) -> Optional[PlannedOperation]:
        """Mode B — Safe Bidirectional Synchronization using persistent baselines."""
        src_base = base_entry.get("source")
        dst_base = base_entry.get("dest")

        # Initial synchronization (No baseline exists)
        if not src_base and not dst_base:
            if src_meta and not dst_meta:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.COPY,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=None,
                    size_bytes=src_meta.size_bytes,
                    reason="Initial sync: new on PC"
                )
            elif not src_meta and dst_meta:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.COPY,
                    rel_path=routed_rel,
                    source_abs=dst_abs, # Copy SSD to PC
                    dest_abs=src_abs,
                    source_meta=dst_meta,
                    dest_meta=None,
                    size_bytes=dst_meta.size_bytes,
                    reason="Initial sync: new on SSD"
                )
            elif src_meta and dst_meta:
                if self._are_files_identical(src_meta, dst_meta, profile.verify_hashes):
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.SKIP,
                        rel_path=routed_rel,
                        source_abs=src_abs,
                        dest_abs=dst_abs,
                        source_meta=src_meta,
                        dest_meta=dst_meta,
                        size_bytes=src_meta.size_bytes,
                        reason="Initial sync: files identical on both sides"
                    )
                else:
                    # SAFETY REQUIREMENT: Differing same-path files during initial sync
                    # must be registered as CONFLICT, never blindly overwritten!
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.CONFLICT,
                        rel_path=routed_rel,
                        source_abs=src_abs,
                        dest_abs=dst_abs,
                        source_meta=src_meta,
                        dest_meta=dst_meta,
                        size_bytes=max(src_meta.size_bytes, dst_meta.size_bytes),
                        reason="Initial sync conflict: differing files exist on both sides without baseline"
                    )

        # Baseline exists: evaluate state transitions
        src_changed = self._file_differs_from_baseline(src_meta, src_base)
        dst_changed = self._file_differs_from_baseline(dst_meta, dst_base)

        if src_meta and dst_meta:
            if not src_changed and not dst_changed:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.SKIP,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Unchanged since last sync"
                )
            elif src_changed and not dst_changed:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.UPDATE,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Modified only on PC -> update SSD"
                )
            elif not src_changed and dst_changed:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.UPDATE,
                    rel_path=routed_rel,
                    source_abs=dst_abs,
                    dest_abs=src_abs,
                    source_meta=dst_meta,
                    dest_meta=src_meta,
                    size_bytes=dst_meta.size_bytes,
                    reason="Modified only on SSD -> update PC"
                )
            else:
                # Both sides modified independently -> CONFLICT
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.CONFLICT,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=max(src_meta.size_bytes, dst_meta.size_bytes),
                    reason="Conflict: Modified independently on both PC and SSD"
                )

        elif src_meta and not dst_meta:
            # Deleted on SSD, or brand new on PC?
            if dst_base is not None:
                # Was previously synced to SSD, now absent -> Deleted on SSD!
                # SAFETY REQUIREMENT: Preserve file on PC, do not propagate deletion or endlessly recreate.
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.PRESERVE,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=None,
                    size_bytes=src_meta.size_bytes,
                    reason="Deleted on SSD, preserved on PC (deletion protection)"
                )
            else:
                # Brand new file on PC
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.COPY,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=None,
                    size_bytes=src_meta.size_bytes,
                    reason="New file on PC -> copy to SSD"
                )

        elif not src_meta and dst_meta:
            # Deleted on PC, or brand new on SSD?
            if src_base is not None:
                # Was previously synced to PC, now absent -> Deleted on PC!
                # SAFETY REQUIREMENT: Preserve file on SSD, do not propagate deletion.
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.PRESERVE,
                    rel_path=routed_rel,
                    source_abs=None,
                    dest_abs=dst_abs,
                    source_meta=None,
                    dest_meta=dst_meta,
                    size_bytes=dst_meta.size_bytes,
                    reason="Deleted on PC, preserved on SSD (deletion protection)"
                )
            else:
                # Brand new file on SSD
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.COPY,
                    rel_path=routed_rel,
                    source_abs=dst_abs,
                    dest_abs=src_abs,
                    source_meta=dst_meta,
                    dest_meta=None,
                    size_bytes=dst_meta.size_bytes,
                    reason="New file on SSD -> copy to PC"
                )

        return None

    def _plan_mode_c(
        self, profile: SyncProfile, mapping: FolderMapping, rel_path: str, routed_rel: str,
        src_abs: Optional[str], dst_abs: Optional[str],
        src_meta: Optional[FileMetadata], dst_meta: Optional[FileMetadata]
    ) -> Optional[PlannedOperation]:
        """Mode C — Versioned Cold Backup."""
        if src_meta and not dst_meta:
            return PlannedOperation(
                id=str(uuid.uuid4()),
                profile_id=profile.id,
                mapping_id=mapping.id,
                action=PlanAction.COPY,
                rel_path=routed_rel,
                source_abs=src_abs,
                dest_abs=dst_abs,
                source_meta=src_meta,
                dest_meta=None,
                size_bytes=src_meta.size_bytes,
                reason="Incremental backup: new source file"
            )

        elif src_meta and dst_meta:
            if self._are_files_identical(src_meta, dst_meta, profile.verify_hashes):
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.SKIP,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Backup up-to-date (identical content)"
                )
            else:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.UPDATE,
                    rel_path=routed_rel,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes,
                    reason="Incremental backup: version existing file and archive new version"
                )

        elif not src_meta and dst_meta:
            # File exists only on cold backup HDD -> NEVER delete archived files!
            return PlannedOperation(
                id=str(uuid.uuid4()),
                profile_id=profile.id,
                mapping_id=mapping.id,
                action=PlanAction.PRESERVE,
                rel_path=routed_rel,
                source_abs=None,
                dest_abs=dst_abs,
                source_meta=None,
                dest_meta=dst_meta,
                size_bytes=dst_meta.size_bytes,
                reason="Cold backup archive preserved (never deleted)"
            )

        return None

    def _are_files_identical(self, m1: FileMetadata, m2: FileMetadata, verify_hashes: bool) -> bool:
        """Compares two files using size, modification time (with 2-sec tolerance for FAT/exFAT), and hash."""
        if m1.size_bytes != m2.size_bytes:
            return False

        # If verify_hashes is explicitly requested or mtimes differ, check hashes
        # exFAT has 2-second timestamp resolution, NTFS has 100ns
        mtime_diff_sec = abs(m1.mtime_ns - m2.mtime_ns) / 1_000_000_000.0

        if verify_hashes or mtime_diff_sec > 2.0:
            if not m1.hash_blake3 and m1.abs_path and Path(m1.abs_path).exists():
                m1.hash_blake3 = compute_blake3_hash(Path(m1.abs_path))
            if not m2.hash_blake3 and m2.abs_path and Path(m2.abs_path).exists():
                m2.hash_blake3 = compute_blake3_hash(Path(m2.abs_path))

            if m1.hash_blake3 and m2.hash_blake3:
                return m1.hash_blake3 == m2.hash_blake3

        return mtime_diff_sec <= 2.0

    def _file_differs_from_baseline(self, meta: Optional[FileMetadata], base_dict: Optional[dict]) -> bool:
        if not meta or not base_dict:
            return True
        if meta.size_bytes != base_dict.get("size_bytes"):
            return True
        mtime_diff_sec = abs(meta.mtime_ns - base_dict.get("mtime_ns", 0)) / 1_000_000_000.0
        if mtime_diff_sec > 2.0:
            return True
        return False
