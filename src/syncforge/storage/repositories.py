"""Repositories for storing and retrieving persistent state in SyncForge."""

import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from syncforge.core.models import (
    DeviceInfo, DeviceRole, SyncProfile, FolderMapping,
    SyncMode, PlanAction, ExecutionStatus, ConflictResolution,
    PlannedOperation, DeletionPolicy, AnalysisMode
)
from syncforge.rules.rule_engine import Rule, RuleType
from syncforge.storage.database import Database


class DeviceRepository:
    """Manages enrolled Windows storage devices in SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, device: DeviceInfo):
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO devices (
                    device_id, friendly_name, role, volume_guid, volume_serial,
                    disk_serial, marker_uuid, filesystem, total_bytes, free_bytes,
                    last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device.device_id,
                    device.friendly_name,
                    device.role.value if isinstance(device.role, DeviceRole) else device.role,
                    device.volume_guid,
                    device.volume_serial,
                    device.disk_serial,
                    device.marker_uuid,
                    device.filesystem,
                    device.total_bytes,
                    device.free_bytes,
                    datetime.now()
                )
            )
            conn.commit()

    def get(self, device_id: str) -> Optional[DeviceInfo]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            if not row:
                return None
            return self._row_to_device(row)

    def list_all(self) -> List[DeviceInfo]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY friendly_name ASC").fetchall()
            return [self._row_to_device(r) for r in rows]

    def delete(self, device_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
            conn.commit()

    def _row_to_device(self, row) -> DeviceInfo:
        return DeviceInfo(
            device_id=row["device_id"],
            friendly_name=row["friendly_name"],
            role=DeviceRole(row["role"]),
            volume_guid=row["volume_guid"],
            volume_serial=row["volume_serial"],
            disk_serial=row["disk_serial"],
            marker_uuid=row["marker_uuid"],
            filesystem=row["filesystem"],
            total_bytes=row["total_bytes"],
            free_bytes=row["free_bytes"],
        )


class ProfileRepository:
    """Manages sync profiles and their folder mappings."""

    def __init__(self, db: Database):
        self.db = db

    def save_profile(self, profile: SyncProfile):
        with self.db.get_connection() as conn:
            del_policy_val = (
                profile.deletion_policy.value
                if hasattr(profile.deletion_policy, "value")
                else str(profile.deletion_policy)
            )
            analysis_mode_val = (
                profile.analysis_mode.value
                if hasattr(profile.analysis_mode, "value")
                else str(profile.analysis_mode)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO profiles (
                    id, name, mode, target_device_id, dest_root,
                    preserve_versions, max_versions, version_subfolder,
                    verify_hashes, analysis_mode, io_yield_delay_ms,
                    io_speed_limit_mb, io_buffer_size_kb, mtime_tolerance_sec,
                    balanced_sample_size_kb, enabled, auto_sync_on_connect,
                    auto_sync_interval_mins, deletion_policy, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id,
                    profile.name,
                    profile.mode.value if isinstance(profile.mode, SyncMode) else profile.mode,
                    profile.target_device_id,
                    profile.dest_root,
                    1 if profile.preserve_versions else 0,
                    profile.max_versions,
                    profile.version_subfolder,
                    1 if profile.verify_hashes else 0,
                    analysis_mode_val,
                    float(getattr(profile, "io_yield_delay_ms", 0.0)),
                    float(getattr(profile, "io_speed_limit_mb", 0.0)),
                    int(getattr(profile, "io_buffer_size_kb", 128)),
                    float(getattr(profile, "mtime_tolerance_sec", 2.0)),
                    int(getattr(profile, "balanced_sample_size_kb", 64)),
                    1 if profile.enabled else 0,
                    1 if profile.auto_sync_on_connect else 0,
                    profile.auto_sync_interval_mins,
                    del_policy_val,
                    datetime.now()
                )
            )
            # Re-save folder mappings
            conn.execute("DELETE FROM folder_mappings WHERE profile_id = ?", (profile.id,))
            for m in profile.mappings:
                conn.execute(
                    """
                    INSERT INTO folder_mappings (id, profile_id, source_root, dest_subpath, enabled, custom_rules_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (m.id, profile.id, m.source_root, m.dest_subpath, 1 if m.enabled else 0, m.custom_rules_id)
                )
            conn.commit()

    def get_profile(self, profile_id: str) -> Optional[SyncProfile]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if not row:
                return None
            mappings_rows = conn.execute(
                "SELECT * FROM folder_mappings WHERE profile_id = ?", (profile_id,)
            ).fetchall()
            mappings = [
                FolderMapping(
                    id=m["id"],
                    profile_id=m["profile_id"],
                    source_root=m["source_root"],
                    dest_subpath=m["dest_subpath"],
                    enabled=bool(m["enabled"]),
                    custom_rules_id=m["custom_rules_id"]
                ) for m in mappings_rows
            ]

            del_pol_str = "preserve_all"
            if "deletion_policy" in row.keys() and row["deletion_policy"]:
                del_pol_str = row["deletion_policy"]
            try:
                del_policy = DeletionPolicy(del_pol_str)
            except Exception:
                del_policy = DeletionPolicy.PRESERVE_ALL

            analysis_mode_str = "balanced"
            if "analysis_mode" in row.keys() and row["analysis_mode"]:
                analysis_mode_str = row["analysis_mode"]
            try:
                analysis_mode = AnalysisMode(analysis_mode_str)
            except Exception:
                analysis_mode = AnalysisMode.BALANCED

            return SyncProfile(
                id=row["id"],
                name=row["name"],
                mode=SyncMode(row["mode"]),
                target_device_id=row["target_device_id"],
                dest_root=row["dest_root"],
                mappings=mappings,
                preserve_versions=bool(row["preserve_versions"]),
                max_versions=row["max_versions"],
                version_subfolder=row["version_subfolder"],
                verify_hashes=bool(row["verify_hashes"]),
                analysis_mode=analysis_mode,
                io_yield_delay_ms=float(row["io_yield_delay_ms"]) if "io_yield_delay_ms" in row.keys() and row["io_yield_delay_ms"] is not None else 0.0,
                io_speed_limit_mb=float(row["io_speed_limit_mb"]) if "io_speed_limit_mb" in row.keys() and row["io_speed_limit_mb"] is not None else 0.0,
                io_buffer_size_kb=int(row["io_buffer_size_kb"]) if "io_buffer_size_kb" in row.keys() and row["io_buffer_size_kb"] is not None else 128,
                mtime_tolerance_sec=float(row["mtime_tolerance_sec"]) if "mtime_tolerance_sec" in row.keys() and row["mtime_tolerance_sec"] is not None else 2.0,
                balanced_sample_size_kb=int(row["balanced_sample_size_kb"]) if "balanced_sample_size_kb" in row.keys() and row["balanced_sample_size_kb"] is not None else 64,
                enabled=bool(row["enabled"]),
                auto_sync_on_connect=bool(row["auto_sync_on_connect"]),
                auto_sync_interval_mins=row["auto_sync_interval_mins"],
                deletion_policy=del_policy,
            )

    def list_profiles(self) -> List[SyncProfile]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id FROM profiles ORDER BY name ASC").fetchall()
            return [self.get_profile(r["id"]) for r in rows if self.get_profile(r["id"]) is not None]

    def delete_profile(self, profile_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            conn.commit()

    def export_profiles(self, file_path: str, profile_ids: Optional[List[str]] = None) -> int:
        """Exports profiles, mappings, and associated rules to a JSON file."""
        all_profs = self.list_profiles()
        if profile_ids:
            export_list = [p for p in all_profs if p.id in profile_ids]
        else:
            export_list = all_profs

        rule_repo = RuleRepository(self.db)
        exported_data = {
            "version": "1.0",
            "app": "SyncForge",
            "exported_at": datetime.now().isoformat(),
            "profiles": []
        }

        for p in export_list:
            p_rules = rule_repo.list_rules(profile_id=p.id)
            prof_dict = {
                "id": p.id,
                "name": p.name,
                "mode": p.mode.value if hasattr(p.mode, "value") else str(p.mode),
                "target_device_id": p.target_device_id,
                "dest_root": p.dest_root,
                "preserve_versions": p.preserve_versions,
                "max_versions": p.max_versions,
                "version_subfolder": p.version_subfolder,
                "verify_hashes": p.verify_hashes,
                "analysis_mode": p.analysis_mode.value if hasattr(p.analysis_mode, "value") else str(p.analysis_mode),
                "io_yield_delay_ms": p.io_yield_delay_ms,
                "io_speed_limit_mb": p.io_speed_limit_mb,
                "io_buffer_size_kb": p.io_buffer_size_kb,
                "mtime_tolerance_sec": p.mtime_tolerance_sec,
                "balanced_sample_size_kb": p.balanced_sample_size_kb,
                "enabled": p.enabled,
                "auto_sync_on_connect": p.auto_sync_on_connect,
                "auto_sync_interval_mins": p.auto_sync_interval_mins,
                "deletion_policy": p.deletion_policy.value if hasattr(p.deletion_policy, "value") else str(p.deletion_policy),
                "mappings": [
                    {
                        "id": m.id,
                        "source_root": m.source_root,
                        "dest_subpath": m.dest_subpath,
                        "enabled": m.enabled,
                        "custom_rules_id": m.custom_rules_id
                    }
                    for m in p.mappings
                ],
                "rules": [
                    {
                        "id": r.id,
                        "rule_type": r.rule_type.value if hasattr(r.rule_type, "value") else str(r.rule_type),
                        "pattern": r.pattern,
                        "target_subfolder": r.target_subfolder,
                        "priority": r.priority,
                        "enabled": r.enabled
                    }
                    for r in p_rules
                ]
            }
            exported_data["profiles"].append(prof_dict)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(exported_data, f, ensure_ascii=False, indent=2)

        return len(exported_data["profiles"])

    def import_profiles(self, file_path: str) -> int:
        """Imports profiles, mappings, and rules from a JSON file. Overwrites or adds profiles."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profiles_raw = data.get("profiles", [])
        rule_repo = RuleRepository(self.db)
        imported_count = 0

        for p_data in profiles_raw:
            pid = p_data.get("id") or str(uuid.uuid4())
            mappings = [
                FolderMapping(
                    id=m.get("id") or str(uuid.uuid4()),
                    profile_id=pid,
                    source_root=m.get("source_root", ""),
                    dest_subpath=m.get("dest_subpath", ""),
                    enabled=bool(m.get("enabled", True)),
                    custom_rules_id=m.get("custom_rules_id")
                )
                for m in p_data.get("mappings", [])
            ]

            try:
                mode = SyncMode(p_data.get("mode", SyncMode.ONE_WAY_SAFE.value))
            except Exception:
                mode = SyncMode.ONE_WAY_SAFE

            try:
                analysis_mode = AnalysisMode(p_data.get("analysis_mode", AnalysisMode.BALANCED.value))
            except Exception:
                analysis_mode = AnalysisMode.BALANCED

            try:
                deletion_policy = DeletionPolicy(p_data.get("deletion_policy", DeletionPolicy.PRESERVE_ALL.value))
            except Exception:
                deletion_policy = DeletionPolicy.PRESERVE_ALL

            profile = SyncProfile(
                id=pid,
                name=p_data.get("name", "Імпортований профіль"),
                mode=mode,
                target_device_id=p_data.get("target_device_id"),
                dest_root=p_data.get("dest_root"),
                mappings=mappings,
                preserve_versions=bool(p_data.get("preserve_versions", True)),
                max_versions=int(p_data.get("max_versions", 10)),
                version_subfolder=p_data.get("version_subfolder", ".syncforge_versions"),
                verify_hashes=bool(p_data.get("verify_hashes", True)),
                analysis_mode=analysis_mode,
                io_yield_delay_ms=float(p_data.get("io_yield_delay_ms", 0.0)),
                io_speed_limit_mb=float(p_data.get("io_speed_limit_mb", 0.0)),
                io_buffer_size_kb=int(p_data.get("io_buffer_size_kb", 128)),
                mtime_tolerance_sec=float(p_data.get("mtime_tolerance_sec", 2.0)),
                balanced_sample_size_kb=int(p_data.get("balanced_sample_size_kb", 64)),
                enabled=bool(p_data.get("enabled", True)),
                auto_sync_on_connect=bool(p_data.get("auto_sync_on_connect", False)),
                auto_sync_interval_mins=int(p_data.get("auto_sync_interval_mins", 0)),
                deletion_policy=deletion_policy
            )
            self.save_profile(profile)

            # Import profile-specific rules
            for r_data in p_data.get("rules", []):
                try:
                    rt = RuleType(r_data.get("rule_type", RuleType.EXCLUDE_GLOB.value))
                except Exception:
                    rt = RuleType.EXCLUDE_GLOB

                rule = Rule(
                    id=r_data.get("id") or str(uuid.uuid4()),
                    rule_type=rt,
                    pattern=r_data.get("pattern", "*"),
                    target_subfolder=r_data.get("target_subfolder"),
                    priority=int(r_data.get("priority", 0)),
                    enabled=bool(r_data.get("enabled", True))
                )
                rule_repo.save_rule(rule, profile_id=pid)

            imported_count += 1

        return imported_count


class RuleRepository:
    """Manages persistent filtering and routing rules."""

    def __init__(self, db: Database):
        self.db = db
        self._ensure_default_rules()

    def _ensure_default_rules(self):
        """Seed default safety exclusions if table is empty."""
        with self.db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
            if count == 0:
                defaults = [
                    ("default-1", None, None, "exclude_glob", "*.tmp", None, 100, 1),
                    ("default-2", None, None, "exclude_glob", "node_modules/**", None, 90, 1),
                    ("default-3", None, None, "exclude_glob", "target/**", None, 80, 1),
                    ("default-4", None, None, "exclude_glob", ".git/**", None, 110, 1),
                    ("default-5", None, None, "route", "*.pdf", "Documents/PDF", 50, 1),
                ]
                conn.executemany(
                    """
                    INSERT INTO rules (id, profile_id, mapping_id, rule_type, pattern, target_subfolder, priority, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    defaults
                )
                conn.commit()

    def list_rules(self, profile_id: Optional[str] = None) -> List[Rule]:
        with self.db.get_connection() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM rules WHERE profile_id = ? OR profile_id IS NULL ORDER BY priority DESC",
                    (profile_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM rules ORDER BY priority DESC").fetchall()

            rules = []
            for r in rows:
                rt = RuleType.EXCLUDE_GLOB
                for t in RuleType:
                    if t.value == r["rule_type"]:
                        rt = t
                        break
                rules.append(Rule(
                    id=r["id"],
                    rule_type=rt,
                    pattern=r["pattern"],
                    target_subfolder=r["target_subfolder"],
                    priority=r["priority"],
                    enabled=bool(r["enabled"])
                ))
            return rules

    def save_rule(self, rule: Rule, profile_id: Optional[str] = None):
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rules (id, profile_id, rule_type, pattern, target_subfolder, priority, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    profile_id,
                    rule.rule_type.value if hasattr(rule.rule_type, "value") else str(rule.rule_type),
                    rule.pattern,
                    rule.target_subfolder,
                    rule.priority,
                    1 if rule.enabled else 0
                )
            )
            conn.commit()

    def delete_rule(self, rule_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
            conn.commit()


class SettingsRepository:
    """Manages application key-value settings."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: str = "") -> str:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set(self, key: str, value: str):
        with self.db.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()

    def clear_all_history(self):
        """Cleans history and operation audit records."""
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM operation_records")
            conn.execute("DELETE FROM job_history")
            conn.commit()


class BaselineRepository:
    """Stores previous synchronization baselines for safe Mode B bidirectional sync."""

    def __init__(self, db: Database):
        self.db = db

    def save_baseline_entry(self, profile_id: str, mapping_id: str, rel_path: str, side: str,
                            size_bytes: int, mtime_ns: int, hash_blake3: Optional[str]):
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO baselines (
                    profile_id, mapping_id, rel_path, side, size_bytes, mtime_ns, hash_blake3, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (profile_id, mapping_id, rel_path, side, size_bytes, mtime_ns, hash_blake3, datetime.now())
            )
            conn.commit()

    def get_baseline(self, profile_id: str, mapping_id: str) -> Dict[str, Dict[str, dict]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM baselines WHERE profile_id = ? AND mapping_id = ?",
                (profile_id, mapping_id)
            ).fetchall()
            result: Dict[str, Dict[str, dict]] = {}
            for r in rows:
                path = r["rel_path"]
                if path not in result:
                    result[path] = {}
                result[path][r["side"]] = {
                    "size_bytes": r["size_bytes"],
                    "mtime_ns": r["mtime_ns"],
                    "hash_blake3": r["hash_blake3"],
                    "last_synced_at": r["last_synced_at"]
                }
            return result

    def save_baseline_batch(self, entries: List[tuple]):
        """Saves a batch of baseline entries in a single atomic transaction."""
        if not entries:
            return
        with self.db.get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO baselines (
                    profile_id, mapping_id, rel_path, side, size_bytes, mtime_ns, hash_blake3, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                entries
            )
            conn.commit()

    def commit_sync_state_batch(
        self,
        baseline_entries: List[tuple],
        tombstone_removals: Optional[List[tuple]] = None,
        tombstone_records: Optional[List[tuple]] = None
    ):
        """
        Atomically commits baselines and tombstone changes in a SINGLE SQLite transaction,
        guaranteeing crash recovery consistency for Mode B bidirectional synchronization.
        """
        if not baseline_entries and not tombstone_removals and not tombstone_records:
            return

        with self.db.get_connection() as conn:
            if baseline_entries:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO baselines (
                        profile_id, mapping_id, rel_path, side, size_bytes, mtime_ns, hash_blake3, last_synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    baseline_entries
                )
            if tombstone_removals:
                conn.executemany(
                    "DELETE FROM tombstones WHERE profile_id = ? AND mapping_id = ? AND rel_path = ?",
                    tombstone_removals
                )
            if tombstone_records:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO tombstones (profile_id, mapping_id, rel_path, deleted_side, decision, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    tombstone_records
                )
            conn.commit()

    def reconcile_state_after_crash(self, profile_id: str):
        """
        Cleans up inconsistent state if a previous sync crashed or aborted midway:
        - Prunes obsolete tombstones for files where an active baseline exists.
        """
        with self.db.get_connection() as conn:
            conn.execute(
                """
                DELETE FROM tombstones
                WHERE profile_id = ?
                  AND rel_path IN (
                      SELECT b.rel_path FROM baselines b
                      WHERE b.profile_id = tombstones.profile_id
                        AND b.mapping_id = tombstones.mapping_id
                        AND b.last_synced_at >= tombstones.deleted_at
                  )
                """,
                (profile_id,)
            )
            conn.commit()

    def remove_baseline_entry(self, profile_id: str, mapping_id: str, rel_path: str):
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM baselines WHERE profile_id = ? AND mapping_id = ? AND rel_path = ?",
                (profile_id, mapping_id, rel_path)
            )
            conn.commit()


class TombstoneRepository:
    """Manages persistent tombstones recording file deletions and user retention decisions."""

    def __init__(self, db: Database):
        self.db = db

    def record_tombstone(self, profile_id: str, mapping_id: str, rel_path: str, deleted_side: str, decision: str):
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tombstones (profile_id, mapping_id, rel_path, deleted_side, decision, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (profile_id, mapping_id, rel_path, deleted_side, decision, datetime.now())
            )
            conn.commit()

    def get_tombstones(self, profile_id: str, mapping_id: Optional[str] = None) -> Dict[str, dict]:
        with self.db.get_connection() as conn:
            if mapping_id:
                rows = conn.execute(
                    "SELECT * FROM tombstones WHERE profile_id = ? AND mapping_id = ?",
                    (profile_id, mapping_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tombstones WHERE profile_id = ?",
                    (profile_id,)
                ).fetchall()
            return {r["rel_path"]: dict(r) for r in rows}

    def remove_tombstone(self, profile_id: str, mapping_id: str, rel_path: str):
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM tombstones WHERE profile_id = ? AND mapping_id = ? AND rel_path = ?",
                (profile_id, mapping_id, rel_path)
            )
            conn.commit()

    def remove_tombstones_batch(self, entries: List[tuple]):
        """Removes a batch of tombstones in a single atomic transaction."""
        if not entries:
            return
        with self.db.get_connection() as conn:
            conn.executemany(
                "DELETE FROM tombstones WHERE profile_id = ? AND mapping_id = ? AND rel_path = ?",
                entries
            )
            conn.commit()

    def clear_tombstones(self, profile_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM tombstones WHERE profile_id = ?", (profile_id,))
            conn.commit()


class HistoryRepository:
    """Maintains audit history of sync runs and individual file operations."""

    def __init__(self, db: Database):
        self.db = db

    def record_job(self, job_id: str, profile_id: str, mode: str, started_at: datetime,
                   finished_at: Optional[datetime], status: str, files_processed: int,
                   bytes_transferred: int, errors_count: int, conflicts_count: int, notes: str = ""):
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_history (
                    job_id, profile_id, mode, started_at, finished_at, status,
                    files_processed, bytes_transferred, errors_count, conflicts_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, profile_id, mode, started_at, finished_at, status,
                 files_processed, bytes_transferred, errors_count, conflicts_count, notes)
            )
            conn.commit()

    def list_jobs(self, limit: int = 50) -> List[dict]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT j.*, p.name AS profile_name
                FROM job_history j
                LEFT JOIN profiles p ON j.profile_id = p.id
                ORDER BY j.started_at DESC
                LIMIT ?
                """, (limit,)
            ).fetchall()
            return [dict(r) for r in rows]


class PlanRepository:
    """Persists analysis dry-run plans so they survive application restart."""

    def __init__(self, db: Database):
        self.db = db

    def save_plan(self, profile_id: str, operations: List[PlannedOperation]):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM cached_plans WHERE profile_id = ?", (profile_id,))
            rows = [
                (
                    profile_id, op.id, op.mapping_id, op.action.value, op.rel_path,
                    op.source_abs, op.dest_abs, op.size_bytes, op.reason
                )
                for op in operations
            ]
            if rows:
                conn.executemany(
                    """
                    INSERT INTO cached_plans (
                        profile_id, op_id, mapping_id, action, rel_path,
                        source_abs, dest_abs, size_bytes, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows
                )
            conn.commit()

    def get_plan(self, profile_id: str) -> List[PlannedOperation]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cached_plans WHERE profile_id = ? ORDER BY op_id ASC",
                (profile_id,)
            ).fetchall()
            plan = []
            for r in rows:
                plan.append(PlannedOperation(
                    id=r["op_id"],
                    profile_id=r["profile_id"],
                    mapping_id=r["mapping_id"],
                    action=PlanAction(r["action"]),
                    rel_path=r["rel_path"],
                    source_abs=r["source_abs"],
                    dest_abs=r["dest_abs"],
                    size_bytes=r["size_bytes"],
                    reason=r["reason"] or ""
                ))
            return plan

    def clear_plan(self, profile_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM cached_plans WHERE profile_id = ?", (profile_id,))
            conn.commit()
