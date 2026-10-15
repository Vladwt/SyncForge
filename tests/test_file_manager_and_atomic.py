"""Tests for atomic baseline/tombstone persistence, crash reconciliation, and file manager worker."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from syncforge.storage.database import Database
from syncforge.storage.repositories import BaselineRepository, TombstoneRepository
from syncforge.gui.pages.file_manager_page import format_size, FileCopyWorker


def test_format_size():
    assert format_size(500) == "500 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(1024 * 1024 * 5) == "5.0 MiB"
    assert format_size(1024 * 1024 * 1024 * 2) == "2.0 GiB"


def test_atomic_commit_sync_state_batch(tmp_path: Path):
    """Verifies that baseline additions and tombstone removals are committed atomically."""
    db_file = tmp_path / "syncforge_atomic_test.db"
    db = Database(str(db_file))
    base_repo = BaselineRepository(db)
    tomb_repo = TombstoneRepository(db)

    profile_id = "test-prof-atomic"
    mapping_id = "m1"
    tomb_repo.record_tombstone(profile_id, mapping_id, "folder1/file1.txt", "source", "delete")

    tombstones_before = tomb_repo.get_tombstones(profile_id, mapping_id)
    assert len(tombstones_before) == 1

    # Atomic batch commit: add new baseline entry and remove obsolete tombstone
    now_dt = datetime.now()
    base_entries = [
        (profile_id, mapping_id, "folder1/file1.txt", "source", 200, 123456789, "hash-new", now_dt),
        (profile_id, mapping_id, "folder1/file1.txt", "dest", 200, 123456789, "hash-new", now_dt),
    ]
    tombstone_removals = [(profile_id, mapping_id, "folder1/file1.txt")]

    base_repo.commit_sync_state_batch(
        baseline_entries=base_entries,
        tombstone_removals=tombstone_removals,
        tombstone_records=[]
    )

    # Check baseline updated
    baseline = base_repo.get_baseline(profile_id, mapping_id)
    assert "folder1/file1.txt" in baseline
    assert baseline["folder1/file1.txt"]["source"]["hash_blake3"] == "hash-new"

    # Check tombstone removed in same transaction
    tombstones_after = tomb_repo.get_tombstones(profile_id, mapping_id)
    assert len(tombstones_after) == 0


def test_reconcile_state_after_crash(tmp_path: Path):
    """Verifies that reconcile_state_after_crash purges obsolete tombstones if baselines are newer."""
    db_file = tmp_path / "syncforge_crash_rec.db"
    db = Database(str(db_file))
    base_repo = BaselineRepository(db)
    tomb_repo = TombstoneRepository(db)

    profile_id = "test-crash-reconcile"
    mapping_id = "m1"
    # Create an old tombstone from an interrupted/crash scenario
    tomb_repo.record_tombstone(profile_id, mapping_id, "data/file_a.txt", "source", "delete")

    # Simulate baseline present and updated at or after tombstone deletion
    base_repo.save_baseline_entry(
        profile_id=profile_id,
        mapping_id=mapping_id,
        rel_path="data/file_a.txt",
        side="source",
        size_bytes=100,
        mtime_ns=123456789,
        hash_blake3="hash-live"
    )

    # Before reconciliation: tombstone exists
    assert len(tomb_repo.get_tombstones(profile_id, mapping_id)) == 1

    # Reconcile after crash
    base_repo.reconcile_state_after_crash(profile_id)

    # Tombstone is purged because baseline exists and is current
    assert len(tomb_repo.get_tombstones(profile_id, mapping_id)) == 0


def test_file_copy_worker(tmp_path: Path):
    """Verifies background FileCopyWorker copies files and directories correctly."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    f1 = src_dir / "file1.txt"
    f1.write_text("Hello SyncForge Copy Worker!", encoding="utf-8")

    sub_dir = src_dir / "sub_folder"
    sub_dir.mkdir()
    f2 = sub_dir / "nested.txt"
    f2.write_text("Nested content", encoding="utf-8")

    items = [str(f1), str(sub_dir)]
    worker = FileCopyWorker(items, str(dst_dir))

    # Run synchronously for unit test
    worker.run()

    dest_f1 = dst_dir / "file1.txt"
    dest_nested = dst_dir / "sub_folder" / "nested.txt"

    assert dest_f1.exists()
    assert dest_f1.read_text(encoding="utf-8") == "Hello SyncForge Copy Worker!"
    assert dest_nested.exists()
    assert dest_nested.read_text(encoding="utf-8") == "Nested content"
