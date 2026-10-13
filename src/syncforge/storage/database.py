"""SQLite database management, schema creation, migrations, and connections."""

import os
import sqlite3
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)

SCHEMA_VERSION = 2

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    friendly_name TEXT NOT NULL,
    role TEXT NOT NULL,
    volume_guid TEXT,
    volume_serial TEXT,
    disk_serial TEXT,
    marker_uuid TEXT,
    filesystem TEXT,
    total_bytes INTEGER DEFAULT 0,
    free_bytes INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    target_device_id TEXT,
    dest_root TEXT,
    preserve_versions INTEGER DEFAULT 1,
    max_versions INTEGER DEFAULT 10,
    version_subfolder TEXT DEFAULT '.syncforge_versions',
    verify_hashes INTEGER DEFAULT 1,
    analysis_mode TEXT DEFAULT 'balanced',
    io_yield_delay_ms REAL DEFAULT 0.0,
    io_speed_limit_mb REAL DEFAULT 0.0,
    io_buffer_size_kb INTEGER DEFAULT 128,
    mtime_tolerance_sec REAL DEFAULT 2.0,
    balanced_sample_size_kb INTEGER DEFAULT 64,
    enabled INTEGER DEFAULT 1,
    auto_sync_on_connect INTEGER DEFAULT 0,
    auto_sync_interval_mins INTEGER DEFAULT 0,
    deletion_policy TEXT DEFAULT 'preserve_all',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(target_device_id) REFERENCES devices(device_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tombstones (
    profile_id TEXT NOT NULL,
    mapping_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    deleted_side TEXT NOT NULL, /* 'source' or 'dest' */
    decision TEXT NOT NULL,     /* 'keep_dest', 'archive', 'deleted_both', 'ignore' */
    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, mapping_id, rel_path)
);

CREATE TABLE IF NOT EXISTS folder_mappings (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    source_root TEXT NOT NULL,
    dest_subpath TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    custom_rules_id TEXT,
    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    mapping_id TEXT,
    rule_type TEXT NOT NULL, /* exclude_glob, include_glob, max_size, min_size, route */
    pattern TEXT NOT NULL,
    target_subfolder TEXT,    /* For route rule */
    priority INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS baselines (
    profile_id TEXT NOT NULL,
    mapping_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    side TEXT NOT NULL, /* 'source' or 'dest' */
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    hash_blake3 TEXT,
    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, mapping_id, rel_path, side)
);

CREATE TABLE IF NOT EXISTS job_history (
    job_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status TEXT NOT NULL,
    files_processed INTEGER DEFAULT 0,
    bytes_transferred INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    conflicts_count INTEGER DEFAULT 0,
    notes TEXT,
    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operation_records (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    source_path TEXT,
    dest_path TEXT,
    size_bytes INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES job_history(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conflict_resolutions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    resolution TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_versions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    version_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    hash_blake3 TEXT NOT NULL,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cached_plans (
    profile_id TEXT NOT NULL,
    op_id TEXT NOT NULL,
    mapping_id TEXT NOT NULL,
    action TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    source_abs TEXT,
    dest_abs TEXT,
    size_bytes INTEGER DEFAULT 0,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, op_id)
);
"""


def get_default_db_path() -> Path:
    """Returns the persistent DB path in standard Windows %APPDATA% or fallback."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base_dir = Path(appdata) / "SyncForge"
    else:
        base_dir = Path.home() / ".syncforge"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "syncforge.db"


class Database:
    """Thread-safe SQLite connection and transaction manager."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create a connection with foreign keys enabled and row factory set."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")  # High concurrency on Windows
        return conn

    def _init_db(self):
        """Initializes tables and runs migrations."""
        with self.get_connection() as conn:
            conn.executescript(CREATE_TABLES_SQL)
            # Run schema migrations for existing databases
            try:
                cols = [c["name"] for c in conn.execute("PRAGMA table_info(profiles)").fetchall()]
                if "deletion_policy" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN deletion_policy TEXT DEFAULT 'preserve_all';")
                if "analysis_mode" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN analysis_mode TEXT DEFAULT 'balanced';")
                if "io_yield_delay_ms" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN io_yield_delay_ms REAL DEFAULT 0.0;")
                if "io_speed_limit_mb" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN io_speed_limit_mb REAL DEFAULT 0.0;")
                if "io_buffer_size_kb" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN io_buffer_size_kb INTEGER DEFAULT 128;")
                if "mtime_tolerance_sec" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN mtime_tolerance_sec REAL DEFAULT 2.0;")
                if "balanced_sample_size_kb" not in cols:
                    conn.execute("ALTER TABLE profiles ADD COLUMN balanced_sample_size_kb INTEGER DEFAULT 64;")
            except Exception as e:
                logger.warning("Migration note", error=str(e))

            cur = conn.execute("SELECT version FROM schema_info WHERE version = ?", (SCHEMA_VERSION,))
            if not cur.fetchone():
                conn.execute("INSERT OR REPLACE INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
        logger.info("Database initialized successfully", path=str(self.db_path), version=SCHEMA_VERSION)
