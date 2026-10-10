"""Core data models and enums for SyncForge."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List


class SyncMode(str, Enum):
    """Synchronization modes."""
    ONE_WAY_SAFE = "one_way_safe"      # Mode A: Safe One-Way Update
    BIDIRECTIONAL = "bidirectional"    # Mode B: Safe Bidirectional Sync
    VERSIONED_BACKUP = "versioned_backup"  # Mode C: Versioned Cold Backup


class DeviceRole(str, Enum):
    """Registered device roles."""
    PRIMARY_SYNC = "primary_sync"  # e.g., Fast external SSD
    BACKUP_HDD = "backup_hdd"      # e.g., Cold secondary backup drive
    OTHER = "other"


class PlanAction(str, Enum):
    """Deterministic actions generated during planning."""
    COPY = "copy"              # Copy new file to destination
    UPDATE = "update"          # Replace/update existing destination file
    SKIP = "skip"              # Nothing to do (identical content/metadata)
    PRESERVE = "preserve"      # Unilateral deletion or untouched file protected from deletion
    CONFLICT = "conflict"      # Independent changes on both sides requiring resolution
    RESTORE = "restore"        # Manual or planned restore of an archived version
    ERROR = "error"            # Path boundary violation, loop, missing permissions, etc.


class ConflictResolution(str, Enum):
    """Choices for conflict resolution."""
    KEEP_SOURCE = "keep_source"        # Overwrite destination with source (PC version)
    KEEP_DEST = "keep_dest"            # Overwrite source with destination (SSD/remote version)
    KEEP_BOTH = "keep_both"            # Keep both using disambiguated filename
    SKIP = "skip"                      # Do not touch either side


class ExecutionStatus(str, Enum):
    """Execution status for jobs and operations."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"


@dataclass
class DeviceInfo:
    """Represents a discovered or enrolled Windows storage device."""
    device_id: str                      # Persistent SyncForge UUID
    friendly_name: str
    role: DeviceRole = DeviceRole.PRIMARY_SYNC
    volume_guid: Optional[str] = None   # e.g., \\?\Volume{guid}\
    volume_serial: Optional[str] = None # Windows Volume Serial Number
    disk_serial: Optional[str] = None   # Physical drive serial if accessible
    marker_uuid: Optional[str] = None   # UUID stored in .syncforge_marker on disk
    filesystem: Optional[str] = None    # NTFS, exFAT, FAT32
    total_bytes: int = 0
    free_bytes: int = 0
    current_mount: Optional[str] = None # e.g., "E:\"
    is_connected: bool = False
    verified: bool = False              # Passes strict multi-signal identity check
    verification_notes: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FileMetadata:
    """Filesystem metadata for a scanned file."""
    rel_path: str                      # Normalized forward-slash relative path
    abs_path: str                      # Complete Windows absolute path
    size_bytes: int
    mtime_ns: int                      # Modification time in nanoseconds
    ctime_ns: int                      # Creation time in nanoseconds
    is_symlink: bool = False
    is_junction: bool = False
    is_readonly: bool = False
    hash_blake3: Optional[str] = None  # Lazy or computed BLAKE3 hash
    error: Optional[str] = None


@dataclass
class FolderMapping:
    """A mapping from a source directory to a destination relative/sub directory."""
    id: str
    profile_id: str
    source_root: str                   # e.g. "D:\Work\Documents"
    dest_subpath: str                  # Relative to device root or profile dest, e.g. "Documents\Work"
    enabled: bool = True
    custom_rules_id: Optional[str] = None


@dataclass
class SyncProfile:
    """Complete profile defining a synchronization / backup task."""
    id: str
    name: str
    mode: SyncMode
    target_device_id: Optional[str] = None  # Expected device UUID for verification
    dest_root: Optional[str] = None         # Fallback or base destination path
    mappings: List[FolderMapping] = field(default_factory=list)
    preserve_versions: bool = True
    max_versions: int = 10
    version_subfolder: str = ".syncforge_versions"
    verify_hashes: bool = True             # Always verify BLAKE3 hash on copy
    enabled: bool = True
    auto_sync_on_connect: bool = False
    auto_sync_interval_mins: int = 0       # 0 = disabled
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PlannedOperation:
    """A single deterministic planned action in an execution plan."""
    id: str
    profile_id: str
    mapping_id: str
    action: PlanAction
    rel_path: str
    source_abs: Optional[str] = None
    dest_abs: Optional[str] = None
    source_meta: Optional[FileMetadata] = None
    dest_meta: Optional[FileMetadata] = None
    size_bytes: int = 0
    reason: str = ""
    conflict_resolution: Optional[ConflictResolution] = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    error_message: Optional[str] = None
