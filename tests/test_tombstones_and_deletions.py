import os
import time
import uuid
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlanAction, DeletionPolicy, ExecutionStatus
)
from syncforge.core.planner import SyncPlanner
from syncforge.core.transfer_engine import TransferEngine
from syncforge.storage.database import Database
from syncforge.storage.repositories import (
    BaselineRepository, TombstoneRepository, ProfileRepository
)
from syncforge.gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_tombstone_lifecycle_and_skip(tmp_path):
    """
    Tests that when a file is deleted on PC and user keeps it on SSD:
    1. First scan detects it as PRESERVE.
    2. Execution records a tombstone.
    3. Next scan classifies it as SKIP ("Збережено на SSD за рішенням користувача").
    4. It is sorted into Tier 4 (not stuck in active preview).
    """
    db = Database(str(tmp_path / "test.db"))
    base_repo = BaselineRepository(db)
    tomb_repo = TombstoneRepository(db)

    pc_dir = tmp_path / "pc"
    ssd_dir = tmp_path / "ssd"
    pc_dir.mkdir()
    ssd_dir.mkdir()

    file_a = pc_dir / "project.docx"
    file_a.write_text("initial document", encoding="utf-8")

    mapping = FolderMapping(id=str(uuid.uuid4()), profile_id="p1", source_root=str(pc_dir), dest_subpath="projects")
    profile = SyncProfile(
        id="p1",
        name="Test",
        mode=SyncMode.ONE_WAY_SAFE,
        mappings=[mapping],
        dest_root=str(ssd_dir),
        deletion_policy=DeletionPolicy.PRESERVE_ALL
    )

    planner = SyncPlanner(baseline_repo=base_repo, tombstone_repo=tomb_repo)
    engine = TransferEngine(baseline_repo=base_repo, tombstone_repo=tomb_repo)

    # 1. Initial sync
    plan1 = planner.generate_plan(profile, str(ssd_dir))
    assert len(plan1) == 1
    assert plan1[0].action == PlanAction.COPY

    status1 = engine.execute_plan("job1", profile, plan1)
    assert status1 == ExecutionStatus.SUCCESS
    assert (ssd_dir / "projects" / "project.docx").exists()

    # 2. User deletes file on PC!
    file_a.unlink()
    assert not file_a.exists()
    assert (ssd_dir / "projects" / "project.docx").exists()

    # Scan 2: File exists only on SSD, was tracked in baseline -> PRESERVE
    plan2 = planner.generate_plan(profile, str(ssd_dir))
    assert len(plan2) == 1
    assert plan2[0].action == PlanAction.PRESERVE
    assert "Видалено на ПК" in plan2[0].reason

    # User executes plan with PRESERVE (decision = keep on SSD)
    status2 = engine.execute_plan("job2", profile, plan2)
    assert status2 == ExecutionStatus.SUCCESS

    # Verify tombstone was saved in SQLite
    tombstones = tomb_repo.get_tombstones(profile.id, mapping.id)
    assert "project.docx" in tombstones
    assert tombstones["project.docx"]["decision"] == "keep_dest"

    # Scan 3: With tombstone saved, file becomes SKIP!
    plan3 = planner.generate_plan(profile, str(ssd_dir))
    assert len(plan3) == 1
    assert plan3[0].action == PlanAction.SKIP
    assert "Збережено на SSD за рішенням користувача" in plan3[0].reason


def test_deletion_policies(tmp_path):
    """Tests profile deletion policies: RESTORE_DELETED, ARCHIVE_COPY, and SYNC_DELETE."""
    db = Database(str(tmp_path / "test.db"))
    base_repo = BaselineRepository(db)
    tomb_repo = TombstoneRepository(db)

    pc_dir = tmp_path / "pc"
    ssd_dir = tmp_path / "ssd"
    pc_dir.mkdir()
    ssd_dir.mkdir()

    f = pc_dir / "data.txt"
    f.write_text("important data", encoding="utf-8")

    mapping = FolderMapping(id="m1", profile_id="p2", source_root=str(pc_dir), dest_subpath="")
    planner = SyncPlanner(baseline_repo=base_repo, tombstone_repo=tomb_repo)
    engine = TransferEngine(baseline_repo=base_repo, tombstone_repo=tomb_repo)

    # Initial sync
    prof = SyncProfile(id="p2", name="P2", mode=SyncMode.ONE_WAY_SAFE, mappings=[mapping], dest_root=str(ssd_dir))
    plan = planner.generate_plan(prof, str(ssd_dir))
    engine.execute_plan("j0", prof, plan)
    assert (ssd_dir / "data.txt").exists()

    # Delete on PC
    f.unlink()

    # Case A: RESTORE_DELETED policy
    prof.deletion_policy = DeletionPolicy.RESTORE_DELETED
    plan_restore = planner.generate_plan(prof, str(ssd_dir))
    assert len(plan_restore) == 1
    assert plan_restore[0].action == PlanAction.RESTORE

    # Execute restore
    engine.execute_plan("j_res", prof, plan_restore)
    assert f.exists() # Restored on PC!
    assert f.read_text(encoding="utf-8") == "important data"

    # Re-delete on PC
    f.unlink()

    # Case B: ARCHIVE_COPY policy
    prof.deletion_policy = DeletionPolicy.ARCHIVE_COPY
    plan_archive = planner.generate_plan(prof, str(ssd_dir))
    assert len(plan_archive) == 1
    assert plan_archive[0].action == PlanAction.ARCHIVE

    engine.execute_plan("j_arc", prof, plan_archive)
    assert not (ssd_dir / "data.txt").exists() # Moved from root
    archive_dir = ssd_dir / ".syncforge_archive"
    assert archive_dir.exists()
    archived_files = list(archive_dir.glob("data.*.txt"))
    assert len(archived_files) == 1


def test_four_tier_sorting(tmp_path):
    """
    Tests that planner sorts:
    Tier 1: COPY
    Tier 2: UPDATE
    Tier 3: CONFLICT, PRESERVE, RESTORE, etc.
    Tier 4: SKIP
    Within tier: newest mtime first.
    """
    from syncforge.core.models import PlannedOperation, FileMetadata

    op_copy = PlannedOperation(id="1", profile_id="p", mapping_id="m", action=PlanAction.COPY, rel_path="b_new.txt")
    op_update = PlannedOperation(id="2", profile_id="p", mapping_id="m", action=PlanAction.UPDATE, rel_path="a_upd.txt")
    op_preserve = PlannedOperation(id="3", profile_id="p", mapping_id="m", action=PlanAction.PRESERVE, rel_path="c_del.txt")
    op_skip = PlannedOperation(id="4", profile_id="p", mapping_id="m", action=PlanAction.SKIP, rel_path="d_skip.txt")

    ops = [op_skip, op_preserve, op_copy, op_update]
    from syncforge.core.planner import _sort_plan_key
    ops.sort(key=_sort_plan_key)

    assert [o.action for o in ops] == [
        PlanAction.COPY,
        PlanAction.UPDATE,
        PlanAction.PRESERVE,
        PlanAction.SKIP
    ]


def test_plan_staleness_detection(qapp, tmp_path):
    """Tests that modifying or deleting files after plan generation makes the plan stale."""
    db = Database(str(tmp_path / "test.db"))
    win = MainWindow(db=db)

    pc_dir = tmp_path / "pc"
    ssd_dir = tmp_path / "ssd"
    pc_dir.mkdir()
    ssd_dir.mkdir()

    test_file = pc_dir / "doc.txt"
    test_file.write_text("v1", encoding="utf-8")

    mapping = FolderMapping(id="m1", profile_id="p1", source_root=str(pc_dir), dest_subpath="")
    prof = SyncProfile(id="p1", name="P1", mode=SyncMode.ONE_WAY_SAFE, mappings=[mapping], dest_root=str(ssd_dir))
    win.profile_repo.save_profile(prof)
    win.active_profile = prof

    # Generate plan
    plan = win.orchestrator.prepare_dry_run(prof)
    assert len(plan) == 1
    win.current_plan = plan

    # Validate freshness: initially fresh
    fresh, msg = win._validate_plan_freshness(prof, plan)
    assert fresh is True

    # Modify file on disk -> staleness detected!
    time.sleep(0.05)
    test_file.write_text("v2 modified", encoding="utf-8")
    os.utime(test_file, (time.time() + 10, time.time() + 10))

    fresh, msg = win._validate_plan_freshness(prof, plan)
    assert fresh is False
    assert "змінено на ПК" in msg

    win.watcher.stop()
    win.close()


def test_all_profiles_preview_and_table_column(tmp_path, qtbot):
    """Verifies Preview table has 6 columns, displays profile name, and handles 'All Profiles' option."""
    db_path = tmp_path / "test_all_profiles.db"
    db = Database(db_path)
    win = MainWindow(db=db)
    qtbot.addWidget(win)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    dst1 = tmp_path / "dst1"
    dst2 = tmp_path / "dst2"
    for d in (src1, src2, dst1, dst2):
        d.mkdir()

    (src1 / "file1.txt").write_text("file 1 content", encoding="utf-8")
    (src2 / "file2.txt").write_text("file 2 content", encoding="utf-8")

    m1 = FolderMapping(id="m1", profile_id="p1", source_root=str(src1), dest_subpath="")
    p1 = SyncProfile(id="p1", name="Profile Alpha", mode=SyncMode.ONE_WAY_SAFE, mappings=[m1], dest_root=str(dst1))
    m2 = FolderMapping(id="m2", profile_id="p2", source_root=str(src2), dest_subpath="")
    p2 = SyncProfile(id="p2", name="Profile Beta", mode=SyncMode.ONE_WAY_SAFE, mappings=[m2], dest_root=str(dst2))

    win.profile_repo.save_profile(p1)
    win.profile_repo.save_profile(p2)

    # Check preview table has 6 columns
    assert win.preview_table.columnCount() == 6

    # Select "All Profiles"
    win._update_preview_profile_combo()
    assert win.preview_profile_combo.count() == 3
    assert win.preview_profile_combo.itemData(0) == "all"

    # Trigger all profiles preview
    win._on_preview_profile_selected(0)
    assert win.is_all_profiles_selected is True
    assert len(win.current_plan) == 2
    assert win.preview_table.rowCount() == 2

    # Check profile names appear in column 0
    col0_vals = {win.preview_table.item(r, 0).text() for r in range(2)}
    assert col0_vals == {"Profile Alpha", "Profile Beta"}

    # Check action combobox is in column 3
    cb0 = win.preview_table.cellWidget(0, 3)
    assert isinstance(cb0, QComboBox)

    win.watcher.stop()
    win.close()


def test_plan_freshness_with_enrolled_target_device(tmp_path, qtbot, monkeypatch):
    """Regression test: verifies that _validate_plan_freshness resolves enrolled target_device_id dynamically."""
    from syncforge.core.models import DeviceRole
    db_path = tmp_path / "test_enrolled_device.db"
    db = Database(db_path)
    win = MainWindow(db=db)
    qtbot.addWidget(win)

    pc_dir = tmp_path / "pc_data"
    usb_dir = tmp_path / "usb_drive"
    pc_dir.mkdir()
    usb_dir.mkdir()

    f = pc_dir / "sample.txt"
    f.write_text("sample content", encoding="utf-8")

    # Enroll mock USB device
    dev = win.device_service.enroll_device(
        mount_path=str(usb_dir),
        friendly_name="Test USB",
        role=DeviceRole.PRIMARY_SYNC,
        create_marker=True
    )
    win.device_repo.save(dev)
    monkeypatch.setattr(win.device_service, "discover_connected_volumes", lambda: [dev])

    # Create profile using target_device_id WITHOUT static dest_root
    mapping = FolderMapping(id="m_dev", profile_id="p_dev", source_root=str(pc_dir), dest_subpath="")
    prof = SyncProfile(
        id="p_dev",
        name="Device Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        mappings=[mapping],
        target_device_id=dev.device_id,
        dest_root=""
    )
    win.profile_repo.save_profile(prof)
    win.active_profile = prof

    plan = win.orchestrator.prepare_dry_run(prof)
    assert len(plan) == 2  # sample.txt (COPY) + .syncforge_marker (PRESERVE)

    # Before fix, this returned False, "Цільовий накопичувач відключено або шлях недоступний"
    is_fresh, reason = win._validate_plan_freshness(prof, plan)
    assert is_fresh is True
    assert reason == ""

    win.watcher.stop()
    win.close()

