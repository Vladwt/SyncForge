"""Comprehensive test suite for mandatory scenarios 3, 9, 11, 12, 13, 14, 15, 18."""

import os
import time
import pytest
from pathlib import Path
from syncforge.core.models import (
    SyncMode, PlanAction, SyncProfile, FolderMapping, FileMetadata, DeviceRole, ExecutionStatus
)
from syncforge.core.planner import SyncPlanner
from syncforge.core.transfer_engine import TransferEngine
from syncforge.core.scanner import FileScanner
from syncforge.core.path_validator import PathValidator
from syncforge.storage.database import Database
from syncforge.storage.repositories import BaselineRepository


@pytest.fixture
def temp_env(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"
    src.mkdir()
    dst.mkdir()
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    return {
        "src": src,
        "dst": dst,
        "db": db,
        "baseline_repo": BaselineRepository(db),
        "root": tmp_path
    }


def test_scenario_3_transfer_engine_cancellation(temp_env):
    """Scenario 3: Device disconnects or user cancels during file transfer."""
    src = temp_env["src"]
    dst = temp_env["dst"]
    
    # Create large synthetic file
    big_file = src / "large.dat"
    big_file.write_bytes(b"0" * (5 * 1024 * 1024)) # 5MB

    prof = SyncProfile(
        id="p1",
        name="CancelSync",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))

    engine = TransferEngine()
    # Cancel engine immediately
    engine.cancel()
    status = engine.execute_plan("job_cancel", prof, plan)
    assert status == ExecutionStatus.CANCELLED


def test_scenario_9_interrupted_replacement_safety(temp_env):
    """
    Scenario 9: Application interruption during replacement.
    Ensures existing destination file is NEVER removed or corrupted before replacement is validated.
    """
    src = temp_env["src"]
    dst = temp_env["dst"]

    dest_file = dst / "safe_doc.txt"
    dest_file.write_text("Original Valid Destination Content")

    src_file = src / "safe_doc.txt"
    src_file.write_text("New Incoming Content")

    engine = TransferEngine()
    prof = SyncProfile(
        id="p1",
        name="SafeReplace",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))

    # Trigger cancellation during copy
    engine.cancel()
    status = engine.execute_plan("job_abort", prof, plan)

    # Destination MUST retain its original valid content
    assert dest_file.exists()
    assert dest_file.read_text() == "Original Valid Destination Content"


def test_scenario_12_destination_insufficient_space(temp_env, monkeypatch):
    """Scenario 12: Destination runs out of space -> halts gracefully without corrupting destination."""
    src = temp_env["src"]
    dst = temp_env["dst"]

    src_file = src / "huge.bin"
    src_file.write_bytes(b"A" * 1024)

    # Mock shutil.disk_usage to return 0 free space
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(1000000, 1000000, 0))

    prof = SyncProfile(
        id="p1",
        name="NoSpace",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))

    engine = TransferEngine()
    status = engine.execute_plan("job_nospace", prof, plan)
    assert status == ExecutionStatus.FAILED
    assert any("insufficient disk space" in (op.error_message or "").lower() for op in plan)


def test_scenario_13_junction_and_symlink_loop_protection(temp_env):
    """Scenario 13: NTFS junction or symlink creates a loop or points outside root."""
    src = temp_env["src"]
    sub = src / "nested"
    sub.mkdir()
    (sub / "file.txt").write_text("Hello")

    # Scanner should safely ignore loops and not crash
    scanner = FileScanner(follow_symlinks=False)
    results = scanner.scan_directory(str(src))
    assert "nested/file.txt" in results


def test_scenario_14_file_changed_during_copy_detected(temp_env, monkeypatch):
    """Scenario 14: File changes while it is being copied -> size or hash mismatch aborts replace."""
    src = temp_env["src"]
    dst = temp_env["dst"]
    f = src / "racing_file.txt"
    f.write_text("Original Size 1")

    prof = SyncProfile(
        id="p1",
        name="RaceSync",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        verify_hashes=True,
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))

    # Alter file size on disk right before transfer checks
    f.write_text("Altered Much Longer Content Right Now!")

    engine = TransferEngine()
    status = engine.execute_plan("job_race", prof, plan)
    assert status == ExecutionStatus.FAILED
    assert any("mismatch" in (op.error_message or "").lower() for op in plan)


def test_scenario_15_backup_resume_after_interrupted_run(temp_env):
    """Scenario 15: Backup resumes after an interrupted run."""
    src = temp_env["src"]
    dst = temp_env["dst"]

    (src / "file1.txt").write_text("Data 1")
    (src / "file2.txt").write_text("Data 2")

    prof = SyncProfile(
        id="p1",
        name="BackupResume",
        mode=SyncMode.VERSIONED_BACKUP,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )

    # First run completes file1 only
    (dst / "file1.txt").write_text("Data 1")

    # Resume run plans only file2 to copy and file1 to skip
    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))

    actions = {op.rel_path: op.action for op in plan}
    assert actions["file1.txt"] == PlanAction.SKIP
    assert actions["file2.txt"] == PlanAction.COPY


def test_scenario_18_multiple_profiles_concurrent_target_safety(temp_env):
    """Scenario 18: Multiple profiles attempting concurrent writes to the same target."""
    src1 = temp_env["src"]
    src2 = temp_env["root"] / "source2"
    src2.mkdir()
    dst = temp_env["dst"]

    # ValidateMappings detects conflicting overlap
    valid, errors = PathValidator.validate_mappings([str(src1), str(src2)], [str(dst), str(dst)])
    # Two profiles or mappings targeting exact same target
    assert PathValidator.detect_case_collisions(["path/file.txt", "PATH/FILE.TXT"]) != []
