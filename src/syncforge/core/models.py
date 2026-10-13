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


class AnalysisMode(str, Enum):
    """File comparison and analysis strategy during preview/planning."""
    FAST = "fast"          # Metadata-first: size + mtime (0 disk reads for unchanged files)
    BALANCED = "balanced"  # Metadata + SQLite Baseline cache + selective sample check
    FULL = "full"          # Full streaming BLAKE3 checksum comparison of all files


class PlanAction(str, Enum):
    """Deterministic actions generated during planning."""
    COPY = "copy"              # Copy new file to destination
    UPDATE = "update"          # Replace/update existing destination file
    SKIP = "skip"              # Nothing to do (identical content/metadata)
    PRESERVE = "preserve"      # Unilateral deletion or untouched file protected from deletion (Leave on SSD)
    CONFLICT = "conflict"      # Independent changes on both sides requiring resolution
    RESTORE = "restore"        # Restore file from SSD back to PC
    ARCHIVE = "archive"        # Archive copy on SSD and remove active instance
    DELETE_DEST = "delete_dest"# Synchronously delete from SSD after explicit user confirmation
    IGNORE = "ignore"          # Ignore file and create exclusion rule
    ERROR = "error"            # Path boundary violation, loop, missing permissions, etc.


class DeletionPolicy(str, Enum):
    """Profile-specific policy for handling deleted files."""
    PRESERVE_ALL = "preserve_all"          # Зберігати на іншій стороні, нічого не робити
    ASK_USER = "ask_user"                  # Запитувати рішення для кожного видалення
    ARCHIVE_COPY = "archive_copy"          # Автоматично створювати архівну копію
    RESTORE_DELETED = "restore_deleted"    # Відновлювати видалені файли з іншої сторони
    SYNC_DELETE = "sync_delete"            # Пропонувати синхронне видалення з підтвердженням


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
    verify_hashes: bool = True             # Verify BLAKE3 hash after file copy / transfer
    analysis_mode: AnalysisMode = AnalysisMode.BALANCED  # Fast, Balanced, or Full preview analysis
    io_yield_delay_ms: float = 0.0         # Pause in ms between file reads/chunks to prevent SSD overheating
    io_speed_limit_mb: float = 0.0         # Read speed limit in MB/s (0 = unlimited)
    io_buffer_size_kb: int = 128           # Read buffer size in KB (64, 128, 256, 512, 1024)
    mtime_tolerance_sec: float = 2.0       # Timestamp difference tolerance (FAT/exFAT = 2.0s, NTFS = 0.001s)
    balanced_sample_size_kb: int = 64      # Head/tail sample size in KB for balanced mode checks
    enabled: bool = True
    auto_sync_on_connect: bool = False
    auto_sync_interval_mins: int = 0       # 0 = disabled
    deletion_policy: DeletionPolicy = DeletionPolicy.PRESERVE_ALL
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
