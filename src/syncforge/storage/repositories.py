"""Repositories for storing and retrieving persistent state in SyncForge."""

import json
from datetime import datetime
from typing import List, Optional, Dict
from syncforge.core.models import (
    DeviceInfo, DeviceRole, SyncProfile, FolderMapping,
    SyncMode, PlanAction, ExecutionStatus, ConflictResolution
)
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
                    datetime.utcnow()
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
            conn.execute(
                """
                INSERT OR REPLACE INTO profiles (
                    id, name, mode, target_device_id, dest_root,
                    preserve_versions, max_versions, version_subfolder,
                    verify_hashes, enabled, auto_sync_on_connect,
                    auto_sync_interval_mins, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    1 if profile.enabled else 0,
                    1 if profile.auto_sync_on_connect else 0,
                    profile.auto_sync_interval_mins,
                    datetime.utcnow()
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
                enabled=bool(row["enabled"]),
                auto_sync_on_connect=bool(row["auto_sync_on_connect"]),
                auto_sync_interval_mins=row["auto_sync_interval_mins"],
            )

    def list_profiles(self) -> List[SyncProfile]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id FROM profiles ORDER BY name ASC").fetchall()
            return [self.get_profile(r["id"]) for r in rows if self.get_profile(r["id"]) is not None]

    def delete_profile(self, profile_id: str):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
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
                (profile_id, mapping_id, rel_path, side, size_bytes, mtime_ns, hash_blake3, datetime.utcnow())
            )
            conn.commit()

    def get_baseline(self, profile_id: str, mapping_id: str) -> Dict[str, Dict[str, dict]]:
        """Returns {rel_path: {'source': {...}, 'dest': {...}}}"""
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

    def remove_baseline_entry(self, profile_id: str, mapping_id: str, rel_path: str):
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM baselines WHERE profile_id = ? AND mapping_id = ? AND rel_path = ?",
                (profile_id, mapping_id, rel_path)
            )
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
                "SELECT * FROM job_history ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
