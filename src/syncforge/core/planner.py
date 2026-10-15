"""Deterministic synchronization planner for Modes A, B, and C."""

import uuid
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable
import structlog
from syncforge.core.models import (
    SyncMode, PlanAction, PlannedOperation, SyncProfile, FolderMapping, FileMetadata, DeletionPolicy, AnalysisMode
)
from syncforge.core.scanner import FileScanner
from syncforge.core.hasher import compute_blake3_hash, compute_sample_hash
from syncforge.core.comparator import FileComparator
from syncforge.core.path_validator import PathValidator
from syncforge.rules.rule_engine import RuleEngine
from syncforge.storage.repositories import BaselineRepository, TombstoneRepository

logger = structlog.get_logger(__name__)


def _action_tier(action: PlanAction) -> int:
    """
    Tier 1: New files missing on destination (COPY)
    Tier 2: Modified files requiring update (UPDATE)
    Tier 3: Conflicts, deletions, archives, and decisions (CONFLICT, RESTORE, ARCHIVE, DELETE_DEST, PRESERVE, ERROR, IGNORE)
    Tier 4: Up-to-date / skipped files not requiring operations (SKIP)
    """
    if action == PlanAction.COPY:
        return 1
    elif action == PlanAction.UPDATE:
        return 2
    elif action in (
        PlanAction.CONFLICT, PlanAction.RESTORE, PlanAction.ARCHIVE,
        PlanAction.DELETE_DEST, PlanAction.PRESERVE, PlanAction.ERROR, PlanAction.IGNORE
    ):
        return 3
    elif action == PlanAction.SKIP:
        return 4
    return 5


def _sort_plan_key(o: PlannedOperation):
    tier = _action_tier(o.action)
    mtime = max(
        o.source_meta.mtime_ns if o.source_meta else 0,
        o.dest_meta.mtime_ns if o.dest_meta else 0
    )
    return (tier, -mtime, o.rel_path.lower())


class SyncPlanner:
    """Generates execution plans with conflict detection and deletion preservation."""

    def __init__(
        self,
        baseline_repo: Optional[BaselineRepository] = None,
        tombstone_repo: Optional[TombstoneRepository] = None,
        scanner: Optional[FileScanner] = None
    ):
        self.baseline_repo = baseline_repo
        self.tombstone_repo = tombstone_repo
        self.scanner = scanner or FileScanner()

    def generate_plan(
        self,
        profile: SyncProfile,
        active_dest_root: str,
        rule_engine: Optional[RuleEngine] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[PlannedOperation]:
        """
        Scans source and destination directories for each mapping in the profile
        and generates a deterministic list of PlannedOperations.
        """
        operations: List[PlannedOperation] = []
        rules = rule_engine or RuleEngine()

        enabled_mappings = [m for m in profile.mappings if m.enabled]
        if not enabled_mappings:
            if progress_callback:
                progress_callback(100, "Немає активних папок для аналізу")
            return operations

        if self.baseline_repo:
            try:
                self.baseline_repo.reconcile_state_after_crash(profile.id)
            except Exception as e:
                logger.warning("Baseline crash reconciliation non-fatal error", error=str(e))

        num_mappings = len(enabled_mappings)
        slice_weight = 90.0 / num_mappings

        for m_idx, mapping in enumerate(enabled_mappings):
            base_pct = int(m_idx * slice_weight)
            mapping_name = mapping.dest_subpath or Path(mapping.source_root).name

            src_root = Path(mapping.source_root).resolve()
            dst_root = (Path(active_dest_root) / mapping.dest_subpath).resolve()

            if progress_callback:
                progress_callback(base_pct + 2, f"Перевірка меж '{mapping_name}'...")

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
            if progress_callback:
                progress_callback(base_pct + 5, f"Сканування джерела '{Path(src_root).name}'...")

            def on_src_scan(count, path):
                if progress_callback:
                    curr_pct = base_pct + 5 + min(int(0.35 * slice_weight), int(count * 0.05))
                    progress_callback(curr_pct, f"Сканування джерела '{Path(src_root).name}' ({count} файлів)...")

            src_files = self.scanner.scan_directory(str(src_root), progress_callback=on_src_scan)

            if progress_callback:
                progress_callback(base_pct + int(0.45 * slice_weight), f"Сканування призначення '{mapping_name}'...")

            def on_dst_scan(count, path):
                if progress_callback:
                    curr_pct = base_pct + int(0.45 * slice_weight) + min(int(0.30 * slice_weight), int(count * 0.05))
                    progress_callback(curr_pct, f"Сканування призначення '{mapping_name}' ({count} файлів)...")

            dst_files = self.scanner.scan_directory(str(dst_root), progress_callback=on_dst_scan) if dst_root.exists() else {}

            if progress_callback:
                progress_callback(base_pct + int(0.80 * slice_weight), f"Аналіз правил та формування плану '{mapping_name}'...")

            # Load persistent baseline
            baseline = {}
            if self.baseline_repo:
                baseline = self.baseline_repo.get_baseline(profile.id, mapping.id)

            # Load persistent tombstones
            tombstones = {}
            if self.tombstone_repo:
                tombstones = self.tombstone_repo.get_tombstones(profile.id, mapping.id)

            mapping_ops = self._plan_mapping(
                profile=profile,
                mapping=mapping,
                src_root=src_root,
                dst_root=dst_root,
                src_files=src_files,
                dst_files=dst_files,
                baseline=baseline,
                tombstones=tombstones,
                rules=rules,
                progress_callback=progress_callback,
                base_pct=base_pct + int(0.80 * slice_weight),
                slice_weight=slice_weight * 0.15
            )
            operations.extend(mapping_ops)

        if progress_callback:
            progress_callback(95, "Сортування списку дій...")

        operations.sort(key=_sort_plan_key)

        if progress_callback:
            actionable = sum(1 for op in operations if op.action not in (PlanAction.SKIP, PlanAction.IGNORE))
            progress_callback(100, f"Готово! До виконання: {actionable} дій (перевірено файлів: {len(operations)})")

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
        tombstones: Dict[str, dict],
        rules: RuleEngine,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        base_pct: int = 80,
        slice_weight: float = 15.0
    ) -> List[PlannedOperation]:
        operations: List[PlannedOperation] = []
        all_rel_paths = set(src_files.keys()) | set(dst_files.keys())
        total_paths = len(all_rel_paths)
        mapping_name = Path(mapping.source_root).name
        mode_str = getattr(profile.analysis_mode, "value", str(profile.analysis_mode))

        # Collect destination targets to check for collision
        dest_target_map = {}

        for idx, rel_path in enumerate(sorted(all_rel_paths)):
            if progress_callback and idx % 1000 == 0 and total_paths > 1000:
                cur_p = min(94, base_pct + int((idx / total_paths) * slice_weight))
                progress_callback(cur_p, f"Аналіз ({mode_str}) '{mapping_name}' ({idx}/{total_paths})...")

            if getattr(profile, "io_yield_delay_ms", 0.0) > 0 and idx % 200 == 0:
                time.sleep(profile.io_yield_delay_ms / 1000.0)

            src_meta = src_files.get(rel_path)
            base_entry = baseline.get(rel_path, {})
            tombstone_entry = tombstones.get(rel_path)

            # Check filtering rules for source files
            if src_meta:
                included, reason = rules.should_include(rel_path, src_meta.size_bytes)
                if not included:
                    # Excluded by rule
                    continue
                routed_rel = rules.determine_destination_path(rel_path)
            else:
                routed_rel = rel_path

            # Look up destination file metadata under its actual routed destination path
            dst_meta = dst_files.get(routed_rel) or dst_files.get(rel_path)

            src_abs = str(src_root / rel_path)
            dst_abs = str(dst_root / routed_rel)

            if src_meta and routed_rel.lower() in dest_target_map:
                prev_path = dest_target_map[routed_rel.lower()]
                operations.append(PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.ERROR,
                    rel_path=rel_path,
                    source_abs=src_abs,
                    dest_abs=dst_abs,
                    source_meta=src_meta,
                    dest_meta=dst_meta,
                    size_bytes=src_meta.size_bytes if src_meta else 0,
                    reason=f"Routing collision (колізія маршрутизації): '{rel_path}' та '{prev_path}' мають однаковий цільовий шлях '{routed_rel}'",
                    error_message=f"Routing collision: '{rel_path}' and '{prev_path}' both map to '{routed_rel}'"
                ))
                continue

            if src_meta:
                dest_target_map[routed_rel.lower()] = rel_path

            # Mode A: Safe One-Way Update
            if profile.mode == SyncMode.ONE_WAY_SAFE:
                op = self._plan_mode_a(
                    profile, mapping, rel_path, routed_rel, src_abs, dst_abs,
                    src_meta, dst_meta, base_entry, tombstone_entry
                )
                if op:
                    operations.append(op)

            # Mode B: Safe Bidirectional Sync
            elif profile.mode == SyncMode.BIDIRECTIONAL:
                op = self._plan_mode_b(
                    profile, mapping, rel_path, routed_rel, src_abs, dst_abs,
                    src_meta, dst_meta, base_entry, tombstone_entry
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

        # 4-tier sorting: Tier 1 (COPY), Tier 2 (UPDATE), Tier 3 (CONFLICT/deletions), Tier 4 (SKIP)
        operations.sort(key=_sort_plan_key)
        return operations

    def _plan_mode_a(
        self, profile: SyncProfile, mapping: FolderMapping, rel_path: str, routed_rel: str,
        src_abs: Optional[str], dst_abs: Optional[str],
        src_meta: Optional[FileMetadata], dst_meta: Optional[FileMetadata],
        base_entry: Optional[Dict[str, dict]] = None,
        tombstone: Optional[dict] = None
    ) -> Optional[PlannedOperation]:
        """Mode A — Safe One-Way Update with persistent tombstones and deletion policies."""
        base_entry = base_entry or {}
        src_base = base_entry.get("source")

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
                reason="Новий файл на ПК -> копіювати на ціль"
            )

        elif src_meta and dst_meta:
            # 1. Fast baseline check: if both sides match persistent baseline, skip disk reads completely
            dst_base = base_entry.get("dest")
            tol = getattr(profile, "mtime_tolerance_sec", 2.0)
            if src_base and dst_base:
                if not self._file_differs_from_baseline(src_meta, src_base, tol) and not self._file_differs_from_baseline(dst_meta, dst_base, tol):
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
                        reason="Актуальний (без змін з останньої синхронізації)"
                    )

            # 2. Check identical using configured analysis_mode
            if self._are_files_identical(src_meta, dst_meta, profile, base_entry):
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
                    reason="Актуальний (вміст ідентичний)"
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
                    reason="Змінено на ПК -> оновити на цілі"
                )

        elif not src_meta and dst_meta:
            # File exists only on destination.
            # 1. Check persistent tombstone first
            if tombstone:
                dec = tombstone.get("decision", "")
                if dec in ("keep_dest", "preserve"):
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.SKIP,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Збережено на SSD за рішенням користувача"
                    )
                elif dec == "ignore":
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.SKIP,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Ігнорується за рішенням користувача"
                    )

            # 2. Check if file was previously tracked (i.e. deleted on PC)
            was_tracked = src_base is not None
            if not was_tracked:
                # Untracked file: exists only on destination, never tracked on source
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
                    reason="Файл існує лише на цільовому пристрої (раніше не синхронізувався)"
                )

            # 3. Tracked file deleted on PC -> apply profile deletion policy
            policy = getattr(profile, "deletion_policy", DeletionPolicy.PRESERVE_ALL)
            if policy == DeletionPolicy.RESTORE_DELETED:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.RESTORE,
                    rel_path=routed_rel,
                    source_abs=None,
                    dest_abs=dst_abs,
                    source_meta=None,
                    dest_meta=dst_meta,
                    size_bytes=dst_meta.size_bytes,
                    reason="Видалено на ПК: відновлення на ПК із SSD (політика)"
                )
            elif policy == DeletionPolicy.ARCHIVE_COPY:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.ARCHIVE,
                    rel_path=routed_rel,
                    source_abs=None,
                    dest_abs=dst_abs,
                    source_meta=None,
                    dest_meta=dst_meta,
                    size_bytes=dst_meta.size_bytes,
                    reason="Видалено на ПК: архівування на SSD (політика)"
                )
            elif policy == DeletionPolicy.SYNC_DELETE:
                return PlannedOperation(
                    id=str(uuid.uuid4()),
                    profile_id=profile.id,
                    mapping_id=mapping.id,
                    action=PlanAction.DELETE_DEST,
                    rel_path=routed_rel,
                    source_abs=None,
                    dest_abs=dst_abs,
                    source_meta=None,
                    dest_meta=dst_meta,
                    size_bytes=dst_meta.size_bytes,
                    reason="Видалено на ПК: пропонується видалення на SSD (потребує підтвердження)"
                )
            elif policy == DeletionPolicy.ASK_USER:
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
                    reason="Видалено на ПК: оберіть дію (Залишити / Відновити / Архів / Видалити)"
                )
            else:  # PRESERVE_ALL
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
                    reason="Видалено на ПК, збережено на SSD (захист від видалення)"
                )

        return None

    def _plan_mode_b(
        self, profile: SyncProfile, mapping: FolderMapping, rel_path: str, routed_rel: str,
        src_abs: Optional[str], dst_abs: Optional[str],
        src_meta: Optional[FileMetadata], dst_meta: Optional[FileMetadata],
        base_entry: Dict[str, dict],
        tombstone: Optional[dict] = None
    ) -> Optional[PlannedOperation]:
        """Mode B — Safe Bidirectional Synchronization using persistent baselines and tombstones."""
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
                    reason="Початкова синхронізація: новий на ПК"
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
                    reason="Початкова синхронізація: новий на SSD"
                )
            elif src_meta and dst_meta:
                if self._are_files_identical(src_meta, dst_meta, profile, base_entry):
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
                        reason="Початкова синхронізація: файли ідентичні"
                    )
                else:
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
                        reason="Початковий конфлікт: різні файли існують на обох сторонах"
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
                    reason="Актуальний (без змін з останньої синхронізації)"
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
                    reason="Змінено тільки на ПК -> оновити SSD"
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
                    reason="Змінено тільки на SSD -> оновити ПК"
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
                    reason="Конфлікт: файл незалежно змінено на ПК та SSD"
                )

        elif src_meta and not dst_meta:
            # File on PC, absent on SSD.
            if dst_base is not None:
                # Deleted on SSD!
                if tombstone and tombstone.get("decision") in ("keep_src", "preserve"):
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.SKIP,
                        rel_path=routed_rel,
                        source_abs=src_abs,
                        dest_abs=dst_abs,
                        source_meta=src_meta,
                        dest_meta=None,
                        size_bytes=src_meta.size_bytes,
                        reason="Збережено на ПК за рішенням користувача"
                    )

                policy = getattr(profile, "deletion_policy", DeletionPolicy.PRESERVE_ALL)
                if policy == DeletionPolicy.RESTORE_DELETED:
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
                        reason="Видалено на SSD: відновлення на SSD з ПК (політика)"
                    )
                elif policy == DeletionPolicy.ARCHIVE_COPY:
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.ARCHIVE,
                        rel_path=routed_rel,
                        source_abs=src_abs,
                        dest_abs=dst_abs,
                        source_meta=src_meta,
                        dest_meta=None,
                        size_bytes=src_meta.size_bytes,
                        reason="Видалено на SSD: архівування на ПК (політика)"
                    )
                elif policy == DeletionPolicy.SYNC_DELETE:
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.DELETE_DEST,
                        rel_path=routed_rel,
                        source_abs=src_abs,
                        dest_abs=dst_abs,
                        source_meta=src_meta,
                        dest_meta=None,
                        size_bytes=src_meta.size_bytes,
                        reason="Видалено на SSD: пропонується видалення на ПК (потребує підтвердження)"
                    )
                else:
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
                        reason="Видалено на SSD, збережено на ПК (захист видалення)"
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
                    reason="Новий файл на ПК -> копіювати на SSD"
                )

        elif not src_meta and dst_meta:
            # File on SSD, absent on PC.
            if src_base is not None:
                # Deleted on PC!
                if tombstone and tombstone.get("decision") in ("keep_dest", "preserve"):
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.SKIP,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Збережено на SSD за рішенням користувача"
                    )

                policy = getattr(profile, "deletion_policy", DeletionPolicy.PRESERVE_ALL)
                if policy == DeletionPolicy.RESTORE_DELETED:
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.RESTORE,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Видалено на ПК: відновлення на ПК із SSD (політика)"
                    )
                elif policy == DeletionPolicy.ARCHIVE_COPY:
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.ARCHIVE,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Видалено на ПК: архівування на SSD (політика)"
                    )
                elif policy == DeletionPolicy.SYNC_DELETE:
                    return PlannedOperation(
                        id=str(uuid.uuid4()),
                        profile_id=profile.id,
                        mapping_id=mapping.id,
                        action=PlanAction.DELETE_DEST,
                        rel_path=routed_rel,
                        source_abs=None,
                        dest_abs=dst_abs,
                        source_meta=None,
                        dest_meta=dst_meta,
                        size_bytes=dst_meta.size_bytes,
                        reason="Видалено на ПК: пропонується видалення на SSD (потребує підтвердження)"
                    )
                elif policy == DeletionPolicy.ASK_USER:
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
                        reason="Видалено на ПК: оберіть дію (Залишити / Відновити / Архів / Видалити)"
                    )
                else:  # PRESERVE_ALL
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
                        reason="Видалено на ПК, збережено на SSD (захист видалення)"
                    )
            else:
                # Untracked or brand new file on SSD
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
                    reason="Новий файл на SSD -> копіювати на ПК"
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
            if self._are_files_identical(src_meta, dst_meta, profile, {}):
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

    def _are_files_identical(
        self,
        m1: FileMetadata,
        m2: FileMetadata,
        profile_or_verify: object,
        base_entry: Optional[Dict[str, dict]] = None,
    ) -> bool:
        return FileComparator.are_files_identical(m1, m2, profile_or_verify, base_entry)

    def _file_differs_from_baseline(
        self,
        meta: Optional[FileMetadata],
        base_dict: Optional[dict],
        mtime_tol_sec: float = 2.0
    ) -> bool:
        return FileComparator.file_differs_from_baseline(meta, base_dict, mtime_tol_sec)

