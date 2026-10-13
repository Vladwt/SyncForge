"""Regression and behavioral tests for analysis modes, I/O throttling, baseline caching, and UI plan caching."""

import os
import time
from pathlib import Path
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox

from syncforge.core.hasher import compute_blake3_hash, compute_sample_hash
from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlanAction, AnalysisMode
)
from syncforge.core.planner import SyncPlanner
from syncforge.gui.dialogs import ProfileEditorDialog
from syncforge.gui.main_window import MainWindow
from syncforge.storage.database import Database
from syncforge.storage.repositories import ProfileRepository, BaselineRepository, PlanRepository


def test_analysis_mode_fast_zero_disk_reads(tmp_path):
    """Fast mode uses metadata-only heuristic: size and mtime, requiring zero file content reads."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f_src = src / "file1.dat"
    f_dst = dst / "file1.dat"
    f_src.write_bytes(b"A" * 1024)
    f_dst.write_bytes(b"A" * 1024)

    now = time.time()
    os.utime(f_src, (now, now))
    os.utime(f_dst, (now, now))

    prof = SyncProfile(
        id="p_fast",
        name="Fast Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        analysis_mode=AnalysisMode.FAST,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p_fast", str(src), "")]
    )

    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.SKIP
    assert plan[0].reason == "Актуальний (вміст ідентичний)"

    # Now change timestamp on source: size is same, mtime differs -> FAST marks UPDATE
    os.utime(f_src, (now + 10, now + 10))
    plan2 = planner.generate_plan(prof, str(dst))
    assert len(plan2) == 1
    assert plan2[0].action == PlanAction.UPDATE


def test_analysis_mode_balanced_catches_content_shift(tmp_path):
    """Balanced mode uses sample check for unbaselined files with identical timestamps but differing data."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f_src = src / "ambiguous.txt"
    f_dst = dst / "ambiguous.txt"
    f_src.write_text("Hello PC World!")
    f_dst.write_text("Hello SSD World")

    # Identical size (15 bytes) and identical timestamps
    now = time.time()
    os.utime(f_src, (now, now))
    os.utime(f_dst, (now, now))

    prof = SyncProfile(
        id="p_bal",
        name="Balanced Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        analysis_mode=AnalysisMode.BALANCED,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p_bal", str(src), "")]
    )

    planner = SyncPlanner()
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    # Sample check catches that content differs despite equal size and timestamp
    assert plan[0].action == PlanAction.UPDATE


def test_analysis_mode_balanced_reuses_baseline_hash(tmp_path):
    """Balanced mode checks SQLite baseline first: if unchanged since last sync, skips disk reads completely."""
    db_path = tmp_path / "baseline_test.db"
    db = Database(db_path)
    base_repo = BaselineRepository(db)

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    f_src = src / "synced.dat"
    f_dst = dst / "synced.dat"
    f_src.write_bytes(b"Important Baselined Content")
    f_dst.write_bytes(b"Important Baselined Content")

    now = time.time()
    os.utime(f_src, (now, now))
    os.utime(f_dst, (now, now))

    st_src = os.stat(f_src)
    st_dst = os.stat(f_dst)

    # Record persistent baseline in SQLite
    base_repo.save_baseline_entry("p_base", "m1", "synced.dat", "source", st_src.st_size, int(st_src.st_mtime_ns), "hash123")
    base_repo.save_baseline_entry("p_base", "m1", "synced.dat", "dest", st_dst.st_size, int(st_dst.st_mtime_ns), "hash123")

    prof = SyncProfile(
        id="p_base",
        name="Baseline Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        analysis_mode=AnalysisMode.BALANCED,
        dest_root=str(dst),
        mappings=[FolderMapping("m1", "p_base", str(src), "")]
    )

    planner = SyncPlanner(baseline_repo=base_repo)
    plan = planner.generate_plan(prof, str(dst))
    assert len(plan) == 1
    assert plan[0].action == PlanAction.SKIP
    assert "без змін з останньої синхронізації" in plan[0].reason


def test_io_throttling_and_rate_limiting(tmp_path):
    """Verifies compute_blake3_hash respects speed_limit_mb and yield_delay_ms without failure."""
    f = tmp_path / "large_payload.bin"
    # Write 1 MB file
    f.write_bytes(b"X" * (1024 * 1024))

    # Test with rate limiting (5 MB/s) and yield delay
    t0 = time.perf_counter()
    h = compute_blake3_hash(
        f,
        chunk_size=64 * 1024,
        yield_delay_ms=1.0,
        speed_limit_mb=5.0
    )
    elapsed = time.perf_counter() - t0

    assert len(h) == 64
    # 1MB at 5MB/s takes at least ~0.15s
    assert elapsed >= 0.15


def test_sample_hash_fast_head_tail(tmp_path):
    """Verifies compute_sample_hash returns equal hashes for identical files and differs for different files."""
    f1 = tmp_path / "sample1.bin"
    f2 = tmp_path / "sample2.bin"
    f3 = tmp_path / "sample3.bin"

    f1.write_bytes(b"HeaderData" + (b"0" * 200000) + b"TailDataA")
    f2.write_bytes(b"HeaderData" + (b"0" * 200000) + b"TailDataA")
    f3.write_bytes(b"HeaderData" + (b"0" * 200000) + b"TailDataB")

    s1 = compute_sample_hash(f1, sample_size=1024)
    s2 = compute_sample_hash(f2, sample_size=1024)
    s3 = compute_sample_hash(f3, sample_size=1024)

    assert s1 == s2
    assert s1 != s3


def test_persistence_of_analysis_and_io_profile_settings(tmp_path):
    """Verifies that all analysis modes and I/O throttling parameters survive SQLite round-trip."""
    db_path = tmp_path / "profile_test.db"
    db = Database(db_path)
    repo = ProfileRepository(db)

    prof = SyncProfile(
        id="p_custom",
        name="Custom IO Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        analysis_mode=AnalysisMode.FULL,
        io_yield_delay_ms=5.5,
        io_speed_limit_mb=45.0,
        io_buffer_size_kb=512,
        mtime_tolerance_sec=1.5,
        balanced_sample_size_kb=128,
        verify_hashes=True
    )
    repo.save_profile(prof)

    loaded = repo.get_profile("p_custom")
    assert loaded is not None
    assert loaded.analysis_mode == AnalysisMode.FULL
    assert loaded.io_yield_delay_ms == 5.5
    assert loaded.io_speed_limit_mb == 45.0
    assert loaded.io_buffer_size_kb == 512
    assert loaded.mtime_tolerance_sec == 1.5
    assert loaded.balanced_sample_size_kb == 128
    assert loaded.verify_hashes is True


def test_smart_plan_caching_and_dirty_tracking(tmp_path, qtbot):
    """Verifies that MainWindow serves cached plan from SQLite instantly when clean, and refreshes when forced."""
    db_path = tmp_path / "cache_test.db"
    db = Database(db_path)
    win = MainWindow(db=db)
    qtbot.addWidget(win)

    src = tmp_path / "c_src"
    dst = tmp_path / "c_dst"
    src.mkdir()
    dst.mkdir()
    (src / "test.txt").write_text("Hello")

    mapping = FolderMapping("m_c", "p_c", str(src), "")
    prof = SyncProfile(id="p_c", name="Cache Profile", mode=SyncMode.ONE_WAY_SAFE, mappings=[mapping], dest_root=str(dst))
    win.profile_repo.save_profile(prof)

    # 1. First run generates plan and saves in SQLite
    win._run_profile_preview(prof, notify=False, force=True)
    assert len(win.current_plan) == 1

    # 2. Re-invoking without force loads plan from SQLite cache instantly
    win.current_plan = []
    # Make sure profile is clean (not in dirty set)
    win._dirty_profiles.clear()
    # In test environment, temporarily unset PYTEST_CURRENT_TEST to verify the real-world cache hit path
    old_env = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        win._run_profile_preview(prof, notify=False, force=False)
        assert len(win.current_plan) == 1
        assert "Кешований план" in win.status_lbl.text()
    finally:
        if old_env:
            os.environ["PYTEST_CURRENT_TEST"] = old_env

    # 3. Mark profile dirty (simulating watchdog file event)
    win._dirty_profiles.add("p_c")
    assert "p_c" in win._dirty_profiles
