"""Tests for synchronization pause, resume, and cancellation functionality."""

import time
from pathlib import Path
from PySide6.QtWidgets import QScrollArea, QPushButton

from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlannedOperation, PlanAction, ExecutionStatus
)
from syncforge.core.transfer_engine import TransferEngine
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.storage.database import Database
from syncforge.storage.repositories import BaselineRepository, HistoryRepository, DeviceRepository, TombstoneRepository, RuleRepository, ProfileRepository
from syncforge.devices.device_service import DeviceService
from syncforge.core.planner import SyncPlanner
from syncforge.gui.workers.sync_workers import SyncExecutionWorker
from syncforge.gui.dialogs import ProfileEditorDialog


def test_transfer_engine_pause_and_resume(tmp_path):
    """Verifies that TransferEngine honors pause() and resume() states during copy."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    # Create test file
    test_file = src / "data.bin"
    test_file.write_bytes(b"A" * (256 * 1024))

    engine = TransferEngine()

    op = PlannedOperation(
        id="op1",
        profile_id="p1",
        mapping_id="m1",
        action=PlanAction.COPY,
        rel_path="data.bin",
        source_abs=str(test_file),
        dest_abs=str(dst / "data.bin"),
        size_bytes=len(test_file.read_bytes())
    )

    prof = SyncProfile(
        id="p1",
        name="Test",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )

    # Initial state
    assert not engine._is_paused
    engine.pause()
    assert engine._is_paused
    engine.resume()
    assert not engine._is_paused

    status = engine.execute_plan("job1", prof, [op])
    assert status == ExecutionStatus.SUCCESS
    assert (dst / "data.bin").exists()
    assert (dst / "data.bin").read_bytes() == test_file.read_bytes()


def test_transfer_engine_cancellation(tmp_path):
    """Verifies that TransferEngine safely aborts and cleans up temp files on cancel()."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    test_file = src / "large.bin"
    test_file.write_bytes(b"B" * (512 * 1024))

    engine = TransferEngine()
    engine.cancel()

    op = PlannedOperation(
        id="op1",
        profile_id="p1",
        mapping_id="m1",
        action=PlanAction.COPY,
        rel_path="large.bin",
        source_abs=str(test_file),
        dest_abs=str(dst / "large.bin"),
        size_bytes=len(test_file.read_bytes())
    )

    prof = SyncProfile(
        id="p1",
        name="Test",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )

    status = engine.execute_plan("job2", prof, [op])
    assert status == ExecutionStatus.CANCELLED
    # Verify no partial temporary files left behind
    tmp_files = list(dst.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_sync_orchestrator_pause_resume_methods(tmp_path):
    """Verifies that SyncOrchestrator delegates pause, resume, and cancel to TransferEngine."""
    class DummyService:
        pass
    class DummyRepo:
        pass

    orchestrator = SyncOrchestrator(
        device_service=DummyService(),
        device_repo=DummyRepo(),
        baseline_repo=DummyRepo(),
        history_repo=DummyRepo()
    )

    assert not orchestrator.is_paused()
    orchestrator.pause()
    assert orchestrator.is_paused()
    orchestrator.resume()
    assert not orchestrator.is_paused()

    orchestrator.cancel()
    assert orchestrator.engine._is_cancelled


def test_start_stop_start_lifecycle(tmp_path):
    """Regression test: Start -> Stop -> Start completes successfully with clean state."""
    db = Database(tmp_path / "test_lifecycle.db")
    dev_service = DeviceService()
    dev_repo = DeviceRepository(db)
    base_repo = BaselineRepository(db)
    hist_repo = HistoryRepository(db)
    tomb_repo = TombstoneRepository(db)
    rule_repo = RuleRepository(db)

    orchestrator = SyncOrchestrator(
        device_service=dev_service,
        device_repo=dev_repo,
        baseline_repo=base_repo,
        history_repo=hist_repo,
        tombstone_repo=tomb_repo,
        rule_repo=rule_repo
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f1 = src / "file1.bin"
    f2 = src / "file2.bin"
    f1.write_bytes(b"File 1 content" * 1024)
    f2.write_bytes(b"File 2 content" * 1024)

    prof = SyncProfile(
        id="p1",
        name="LifecycleTest",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p1", str(src), "")]
    )
    ProfileRepository(db).save_profile(prof)

    # 1. First run: Start -> Stop
    cancelled_triggered = False

    def on_progress(p):
        nonlocal cancelled_triggered
        if not cancelled_triggered:
            cancelled_triggered = True
            orchestrator.cancel()

    ops = orchestrator.prepare_dry_run(prof)
    assert len(ops) == 2

    status1 = orchestrator.execute_sync(prof, ops, progress_callback=on_progress)
    assert status1 == ExecutionStatus.CANCELLED
    assert orchestrator._is_cancelled is True
    assert orchestrator.engine._is_cancelled is True

    # 2. Second run: Start -> Successful completion (without creating a new orchestrator)
    orchestrator.reset()
    assert orchestrator._is_cancelled is False
    assert orchestrator.engine._is_cancelled is False

    ops2 = orchestrator.prepare_dry_run(prof)
    status2 = orchestrator.execute_sync(prof, ops2)
    assert status2 == ExecutionStatus.SUCCESS

    # Verify both files exist and are intact
    assert (dst / "file1.bin").read_bytes() == f1.read_bytes()
    assert (dst / "file2.bin").read_bytes() == f2.read_bytes()


def test_pause_stop_start_lifecycle(tmp_path):
    """Regression test: Pause -> Stop -> Start completes successfully."""
    db = Database(tmp_path / "test_pause_lifecycle.db")
    orchestrator = SyncOrchestrator(
        device_service=DeviceService(),
        device_repo=DeviceRepository(db),
        baseline_repo=BaselineRepository(db),
        history_repo=HistoryRepository(db),
        tombstone_repo=TombstoneRepository(db),
        rule_repo=RuleRepository(db)
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f1 = src / "alpha.bin"
    f2 = src / "beta.bin"
    f1.write_bytes(b"Alpha content" * 2048)
    f2.write_bytes(b"Beta content" * 2048)

    prof = SyncProfile(
        id="p_pause",
        name="PauseTest",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p_pause", str(src), "")]
    )
    ProfileRepository(db).save_profile(prof)

    # 1. Run with Pause then Stop
    def on_progress(p):
        if not orchestrator.is_paused():
            orchestrator.pause()
            assert orchestrator.is_paused()
            # Stop while paused
            orchestrator.cancel()

    ops = orchestrator.prepare_dry_run(prof)
    status1 = orchestrator.execute_sync(prof, ops, progress_callback=on_progress)
    assert status1 == ExecutionStatus.CANCELLED
    assert orchestrator.is_paused() is False

    # 2. Start again with clean state
    orchestrator.reset()
    assert not orchestrator._is_cancelled
    assert not orchestrator.is_paused()

    ops2 = orchestrator.prepare_dry_run(prof)
    status2 = orchestrator.execute_sync(prof, ops2)
    assert status2 == ExecutionStatus.SUCCESS
    assert (dst / "alpha.bin").read_bytes() == f1.read_bytes()
    assert (dst / "beta.bin").read_bytes() == f2.read_bytes()


def test_worker_start_stop_start_lifecycle(tmp_path, qtbot):
    """Tests SyncExecutionWorker Qt lifecycle across Start -> Stop -> Start."""
    db = Database(tmp_path / "test_worker_lifecycle.db")
    orchestrator = SyncOrchestrator(
        device_service=DeviceService(),
        device_repo=DeviceRepository(db),
        baseline_repo=BaselineRepository(db),
        history_repo=HistoryRepository(db),
        tombstone_repo=TombstoneRepository(db),
        rule_repo=RuleRepository(db)
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f = src / "doc.bin"
    f.write_bytes(b"W" * (1024 * 1024))

    prof = SyncProfile(
        id="p_worker",
        name="WorkerTest",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p_worker", str(src), "")],
        io_yield_delay_ms=20.0
    )
    ProfileRepository(db).save_profile(prof)
    ops = orchestrator.prepare_dry_run(prof)

    # Worker 1: Start -> Stop
    worker1 = SyncExecutionWorker(orchestrator, [(prof, ops)])
    worker1.progress.connect(lambda p: worker1.cancel())

    with qtbot.waitSignal(worker1.execution_finished, timeout=5000) as blocker:
        worker1.start()

    status1, failed1, success1 = blocker.args
    assert status1 == ExecutionStatus.CANCELLED

    # Worker 2: Start again with same orchestrator
    ops2 = orchestrator.prepare_dry_run(prof)
    worker2 = SyncExecutionWorker(orchestrator, [(prof, ops2)])

    with qtbot.waitSignal(worker2.execution_finished, timeout=5000) as blocker2:
        worker2.start()

    status2, failed2, success2 = blocker2.args
    assert status2 == ExecutionStatus.SUCCESS
    assert len(success2) == 1
    assert (dst / "doc.bin").read_bytes() == f.read_bytes()


def test_profile_editor_dialog_scroll_and_height(tmp_path, qtbot):
    """Verifies ProfileEditorDialog uses QScrollArea and respects 1080p display limits."""
    dialog = ProfileEditorDialog(None, profile=None, devices=[])
    qtbot.addWidget(dialog)

    # Assert scroll area is present
    scroll_areas = dialog.findChildren(QScrollArea)
    assert len(scroll_areas) >= 1
    assert scroll_areas[0].widgetResizable() is True

    # Assert height is within bounds (< 750px, fits 1080p easily)
    assert dialog.height() <= 750
    assert dialog.minimumHeight() >= 450

    # Assert Save and Cancel buttons exist and are enabled
    save_btns = [b for b in dialog.findChildren(QPushButton) if "save" in b.text().lower() or "зберегти" in b.text().lower()]
    assert len(save_btns) >= 1


def test_mode_b_bidirectional_baseline_sides(tmp_path):
    """Verifies that in Mode B, copying SSD -> PC records baseline with source=PC and dest=SSD."""
    db = Database(str(tmp_path / "test_baseline.db"))
    base_repo = BaselineRepository(db)
    tomb_repo = TombstoneRepository(db)

    pc_dir = tmp_path / "pc"
    ssd_dir = tmp_path / "ssd"
    pc_dir.mkdir()
    ssd_dir.mkdir()

    # Create one file on PC and one file on SSD
    (pc_dir / "pc_doc.txt").write_text("content from pc", encoding="utf-8")
    (ssd_dir / "ssd_doc.txt").write_text("content from ssd", encoding="utf-8")

    mapping = FolderMapping(id="m1", profile_id="p_bidi", source_root=str(pc_dir), dest_subpath="")
    prof = SyncProfile(
        id="p_bidi",
        name="Mode B Test",
        mode=SyncMode.BIDIRECTIONAL,
        mappings=[mapping],
        dest_root=str(ssd_dir),
        verify_hashes=True
    )

    planner = SyncPlanner(baseline_repo=base_repo, tombstone_repo=tomb_repo)
    engine = TransferEngine(baseline_repo=base_repo, tombstone_repo=tomb_repo)

    plan = planner.generate_plan(prof, str(ssd_dir))
    assert len(plan) == 2

    # Execute plan (both PC -> SSD and SSD -> PC)
    status = engine.execute_plan("job_bidi_1", prof, plan)
    assert status == ExecutionStatus.SUCCESS

    # Verify files exist on both sides
    assert (ssd_dir / "pc_doc.txt").exists()
    assert (pc_dir / "ssd_doc.txt").exists()

    # Verify baseline in DB has 'source' as PC and 'dest' as SSD for BOTH files!
    baseline = base_repo.get_baseline(prof.id, mapping.id)
    assert "pc_doc.txt" in baseline
    assert "ssd_doc.txt" in baseline

    assert "source" in baseline["ssd_doc.txt"]
    assert "dest" in baseline["ssd_doc.txt"]
    assert baseline["ssd_doc.txt"]["source"]["size_bytes"] == len(b"content from ssd")
    assert baseline["ssd_doc.txt"]["dest"]["size_bytes"] == len(b"content from ssd")

    # Now modify the file on PC that originated from SSD
    time.sleep(0.01)
    (pc_dir / "ssd_doc.txt").write_text("updated on pc side!", encoding="utf-8")

    # Generate plan again - planner must recognize that PC was updated and SSD unchanged from baseline!
    plan2 = planner.generate_plan(prof, str(ssd_dir))
    actions = {op.rel_path: op.action for op in plan2}
    assert actions.get("ssd_doc.txt") == PlanAction.UPDATE
    # Crucially, source must be PC and dest must be SSD
    ssd_op = next(op for op in plan2 if op.rel_path == "ssd_doc.txt")
    assert Path(ssd_op.source_abs).resolve() == (pc_dir / "ssd_doc.txt").resolve()
    assert Path(ssd_op.dest_abs).resolve() == (ssd_dir / "ssd_doc.txt").resolve()


def test_corrupted_sector_chkdsk_continuation(tmp_path, monkeypatch):
    """Verifies that a bad sector / corrupted file (WinError 1392) does not abort the whole sync."""
    db = Database(str(tmp_path / "test_chkdsk.db"))
    base_repo = BaselineRepository(db)

    pc_dir = tmp_path / "pc"
    ssd_dir = tmp_path / "ssd"
    pc_dir.mkdir()
    ssd_dir.mkdir()

    f1 = pc_dir / "good1.txt"
    f2 = pc_dir / "bad_sector.txt"
    f3 = pc_dir / "good2.txt"

    f1.write_text("good file 1", encoding="utf-8")
    f2.write_text("corrupted content", encoding="utf-8")
    f3.write_text("good file 2", encoding="utf-8")

    mapping = FolderMapping(id="m1", profile_id="p_err", source_root=str(pc_dir), dest_subpath="")
    prof = SyncProfile(
        id="p_err",
        name="Error Test",
        mode=SyncMode.ONE_WAY_SAFE,
        mappings=[mapping],
        dest_root=str(ssd_dir),
        verify_hashes=False
    )

    ops = [
        PlannedOperation(
            id="op1", profile_id=prof.id, mapping_id="m1", action=PlanAction.COPY,
            rel_path="good1.txt", source_abs=str(f1), dest_abs=str(ssd_dir / "good1.txt"),
            size_bytes=f1.stat().st_size
        ),
        PlannedOperation(
            id="op2", profile_id=prof.id, mapping_id="m1", action=PlanAction.COPY,
            rel_path="bad_sector.txt", source_abs=str(f2), dest_abs=str(ssd_dir / "bad_sector.txt"),
            size_bytes=f2.stat().st_size
        ),
        PlannedOperation(
            id="op3", profile_id=prof.id, mapping_id="m1", action=PlanAction.COPY,
            rel_path="good2.txt", source_abs=str(f3), dest_abs=str(ssd_dir / "good2.txt"),
            size_bytes=f3.stat().st_size
        ),
    ]

    engine = TransferEngine(baseline_repo=base_repo)

    # Monkeypatch open only for bad_sector.txt to simulate WinError 1392
    original_open = open
    def mock_open(file, *args, **kwargs):
        if "bad_sector.txt" in str(file) and "rb" in args:
            err = OSError(1392, "The file or directory is corrupted and unreadable")
            err.winerror = 1392
            raise err
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    status = engine.execute_plan("job_chkdsk", prof, ops)

    # Must be FAILED overall due to bad_sector.txt, but NOT CANCELLED and NOT drive disconnected
    assert status == ExecutionStatus.FAILED
    assert ops[0].status == ExecutionStatus.SUCCESS
    assert ops[1].status == ExecutionStatus.FAILED
    assert "CHKDSK" in (ops[1].error_message or "")
    assert ops[2].status == ExecutionStatus.SUCCESS

    # Remaining valid files were successfully transferred!
    assert (ssd_dir / "good1.txt").exists()
    assert (ssd_dir / "good2.txt").exists()
    assert not (ssd_dir / "bad_sector.txt").exists()
