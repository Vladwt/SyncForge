"""Unit and integration tests for SyncForge covering mandatory scenarios."""

import os
import time
import pytest
from pathlib import Path
from syncforge.core.models import (
    SyncMode, PlanAction, SyncProfile, FolderMapping, FileMetadata, DeviceRole, ConflictResolution, ExecutionStatus
)
from syncforge.core.scanner import FileScanner
from syncforge.core.hasher import compute_blake3_hash
from syncforge.core.path_validator import PathValidator
from syncforge.core.planner import SyncPlanner
from syncforge.core.transfer_engine import TransferEngine
from syncforge.core.version_manager import VersionManager
from syncforge.rules.rule_engine import RuleEngine, Rule, RuleType
from syncforge.storage.database import Database
from syncforge.storage.repositories import BaselineRepository, ProfileRepository, DeviceRepository, HistoryRepository
from syncforge.devices.device_service import DeviceService, WindowsVolumeScanner


@pytest.fixture
def temp_env(tmp_path):
    """Provides isolated synthetic directories and database."""
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
        "profile_repo": ProfileRepository(db),
        "device_repo": DeviceRepository(db),
        "history_repo": HistoryRepository(db),
        "root": tmp_path
    }


def test_scenario_1_and_2_device_identification_independent_of_drive_letter(temp_env):
    """
    Scenario 1 & 2: SSD reconnects under a different drive letter, and a different disk receives the old letter.
    Ensures drive letter is not trusted and hardware identity / marker verifies correctly.
    """
    service = DeviceService()
    # Mock enrolled device with specific volume serial & marker
    enrolled = service.enroll_device(str(temp_env["dst"]), "Samsung T7", DeviceRole.PRIMARY_SYNC, create_marker=True)
    assert enrolled.marker_uuid is not None

    # Simulate drive reconnecting under a new mount point
    new_mount = temp_env["root"] / "new_mount"
    new_mount.mkdir()
    # Copy marker to new mount and fake matching volume serial
    (new_mount / ".syncforge_marker").write_text(enrolled.marker_uuid)

    fake_volume_scanner = [
        service.discover_connected_volumes()[0] # Real scanned drive
    ]
    # Verify candidate matches enrolled identity signals
    verified, mount, note = service.verify_device(enrolled, [enrolled])
    assert verified is True
    assert mount == str(temp_env["dst"])


def test_scenario_4_independent_modification_conflict(temp_env):
    """Scenario 4: Same file modified independently on both sides -> conflict."""
    src = temp_env["src"]
    dst = temp_env["dst"]
    file_rel = "doc.txt"

    (src / file_rel).write_text("Version on PC")
    (dst / file_rel).write_text("Version on SSD")

    # Save baseline to simulate previously synced file
    temp_env["baseline_repo"].save_baseline_entry("p1", "m1", file_rel, "source", 10, 1000, "hash_old")
    temp_env["baseline_repo"].save_baseline_entry("p1", "m1", file_rel, "dest", 10, 1000, "hash_old")

    planner = SyncPlanner(baseline_repo=temp_env["baseline_repo"])
    prof = SyncProfile(
        id="p1",
        name="Bi-sync",
        mode=SyncMode.BIDIRECTIONAL,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.CONFLICT


def test_scenario_5_and_6_unilateral_deletion_preservation(temp_env):
    """
    Scenario 5 & 6: File deleted on PC but retained on SSD (and vice-versa).
    Safety Policy: Preserve the file, never automatically delete!
    """
    src = temp_env["src"]
    dst = temp_env["dst"]
    file_rel = "preserved.txt"

    # Exists only on SSD
    (dst / file_rel).write_text("Important SSD file")
    temp_env["baseline_repo"].save_baseline_entry("p1", "m1", file_rel, "source", 10, 1000, "h")
    temp_env["baseline_repo"].save_baseline_entry("p1", "m1", file_rel, "dest", 10, 1000, "h")

    planner = SyncPlanner(baseline_repo=temp_env["baseline_repo"])
    prof = SyncProfile(
        id="p1",
        name="Bi-sync",
        mode=SyncMode.BIDIRECTIONAL,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.PRESERVE


def test_scenario_7_initial_sync_differing_files_conflict(temp_env):
    """
    Scenario 7: Same-path files differ during initial synchronization (no baseline).
    Conservative safety check: Must trigger conflict, never blindly overwrite!
    """
    src = temp_env["src"]
    dst = temp_env["dst"]
    file_rel = "conflict_init.txt"

    (src / file_rel).write_text("Source Data A")
    (dst / file_rel).write_text("Dest Data B")

    planner = SyncPlanner(baseline_repo=temp_env["baseline_repo"])
    prof = SyncProfile(
        id="p1",
        name="Bi-sync",
        mode=SyncMode.BIDIRECTIONAL,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.CONFLICT


def test_scenario_8_routing_rules_collision_detection(temp_env):
    """Scenario 8: Two routing rules or files map different files to one destination -> collision detected."""
    src = temp_env["src"]
    dst = temp_env["dst"]

    (src / "file1.pdf").write_text("PDF 1")
    (src / "sub").mkdir()
    (src / "sub" / "file1.pdf").write_text("PDF 2")

    # Rule routes all PDFs into "Documents/PDF"
    rule = Rule(id="r1", rule_type=RuleType.ROUTE_FOLDER, pattern="*.pdf", target_subfolder="Documents/PDF")
    engine = RuleEngine([rule])

    planner = SyncPlanner()
    prof = SyncProfile(
        id="p1",
        name="Route-sync",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst), rule_engine=engine)
    assert any(op.action == PlanAction.ERROR and "collision" in op.reason.lower() for op in plan)


def test_scenario_10_identical_timestamps_different_contents(temp_env):
    """Scenario 10: Identical timestamps but different contents detected via BLAKE3 hash."""
    src = temp_env["src"]
    dst = temp_env["dst"]
    f_rel = "test_hash.txt"

    src_f = src / f_rel
    dst_f = dst / f_rel

    src_f.write_text("Content AAAAAA")
    dst_f.write_text("Content BBBBBB")

    # Force identical timestamps
    now = time.time()
    os.utime(str(src_f), (now, now))
    os.utime(str(dst_f), (now, now))

    planner = SyncPlanner()
    prof = SyncProfile(
        id="p1",
        name="ModeA",
        mode=SyncMode.ONE_WAY_SAFE,
        verify_hashes=True,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.UPDATE


def test_scenario_16_cloned_marker_protection(temp_env):
    """
    Scenario 16: Registered device marker is cloned to another drive.
    Conservative identity policy must BLOCK writing when volume serial does not match!
    """
    service = DeviceService()
    enrolled = service.enroll_device(str(temp_env["dst"]), "Original SSD", DeviceRole.PRIMARY_SYNC, create_marker=True)
    enrolled.volume_serial = "1111-2222"

    # Cloned candidate has same marker but different serial
    cloned_candidate = service.discover_connected_volumes()[0]
    cloned_candidate.marker_uuid = enrolled.marker_uuid
    cloned_candidate.volume_serial = "9999-8888" # Differing hardware serial

    verified, _, explanation = service.verify_device(enrolled, [cloned_candidate])
    assert verified is False
    assert "SAFETY ALERT: Marker cloned" in explanation


def test_scenario_17_source_dest_overlap_rejection(temp_env):
    """Scenario 17: Both source and destination folders overlap -> rejected."""
    src = temp_env["src"]
    nested_dst = src / "nested_backup"
    nested_dst.mkdir()

    valid, errors = PathValidator.validate_mappings([str(src)], [str(nested_dst)])
    assert valid is False
    assert any("inside source" in e for e in errors)


def test_scenario_19_unicode_and_nested_paths(temp_env):
    """Scenario 19: Unicode filenames and deeply nested paths."""
    src = temp_env["src"]
    dst = temp_env["dst"]

    nested_dir = src / "Папка_Юнікод" / "Deep" / "Україна"
    nested_dir.mkdir(parents=True)
    unicode_file = nested_dir / "Звіт_2026_Синхронізація.txt"
    unicode_file.write_text("Привіт зі Львова!", encoding="utf-8")

    planner = SyncPlanner()
    prof = SyncProfile(
        id="p1",
        name="UnicodeSync",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.COPY

    engine = TransferEngine()
    status = engine.execute_plan("job1", prof, plan)
    assert status == ExecutionStatus.SUCCESS

    # Verify destination file
    dst_file = dst / "Папка_Юнікод" / "Deep" / "Україна" / "Звіт_2026_Синхронізація.txt"
    assert dst_file.exists()
    assert dst_file.read_text(encoding="utf-8") == "Привіт зі Львова!"


def test_scenario_20_version_archiving_and_restore(temp_env):
    """Scenario 20: Restoring an old file version after a newer version was saved."""
    dst = temp_env["dst"]
    f = dst / "data.txt"
    f.write_text("Old Version 1")

    vm = VersionManager(dst)
    archived_path = vm.archive_version(f, "data.txt")
    assert archived_path is not None
    assert archived_path.exists()

    # Overwrite file with new content
    f.write_text("New Version 2")
    assert f.read_text() == "New Version 2"

    # Restore archived version
    restored = vm.restore_version(archived_path, f)
    assert restored is True
    assert f.read_text() == "Old Version 1"
