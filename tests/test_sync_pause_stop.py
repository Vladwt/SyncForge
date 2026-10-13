"""Tests for synchronization pause, resume, and cancellation functionality."""

import time
from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlannedOperation, PlanAction, ExecutionStatus
)
from syncforge.core.transfer_engine import TransferEngine
from syncforge.services.sync_orchestrator import SyncOrchestrator


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
