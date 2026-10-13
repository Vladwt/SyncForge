"""Automated headless verification for SyncForge GUI v2 features."""

import os
from pathlib import Path
from PySide6.QtWidgets import QComboBox, QCheckBox, QPushButton

from syncforge.storage.database import Database
from syncforge.storage.repositories import ProfileRepository, RuleRepository, SettingsRepository, PlanRepository
from syncforge.core.models import SyncProfile, FolderMapping, SyncMode
from syncforge.rules.rule_engine import Rule, RuleType
from syncforge.gui.main_window import MainWindow


def test_gui_v2_features(tmp_path, qtbot):
    db_path = tmp_path / "test_gui_v2.db"
    db = Database(db_path)

    src_dir = tmp_path / "source"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()
    dst_dir.mkdir()

    # Create two files with different timestamps
    f1 = src_dir / "older.txt"
    f1.write_text("Old content")
    f2 = src_dir / "newer.txt"
    f2.write_text("New content")

    os.utime(f1, (1000000000, 1000000000))
    os.utime(f2, (1700000000, 1700000000))

    # Create test profile
    prof_repo = ProfileRepository(db)
    prof = SyncProfile(
        id="prof_1",
        name="Test Sync Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        dest_root=str(dst_dir),
        mappings=[FolderMapping("m1", "prof_1", str(src_dir), "")]
    )
    prof_repo.save_profile(prof)

    # Create test rule
    rule_repo = RuleRepository(db)
    rule = Rule(
        id="rule_1",
        rule_type=RuleType.EXCLUDE_GLOB,
        pattern="*.custom_test_pattern",
        target_subfolder="",
        priority=50,
        enabled=True
    )
    rule_repo.save_rule(rule)

    # Initialize MainWindow
    window = MainWindow(db=db)
    qtbot.addWidget(window)

    try:
        # 1. Check Profile list & buttons
        window._switch_page("profiles")
        assert window.profiles_table.rowCount() == 1
        act_cell = window.profiles_table.cellWidget(0, 4)
        assert act_cell is not None
        buttons = act_cell.findChildren(QPushButton)
        assert len(buttons) == 3
        btn_labels = [b.text() for b in buttons]
        assert "Аналіз" in btn_labels
        assert "Редагувати" in btn_labels
        assert "Видалити" in btn_labels

        # 2. Check Rules dropdown & checkbox
        window._switch_page("rules")
        assert window.rules_table.rowCount() >= 1
        found_row = None
        for r_idx in range(window.rules_table.rowCount()):
            item = window.rules_table.item(r_idx, 0)
            if item and item.text() == "*.custom_test_pattern":
                found_row = r_idx
                break
        assert found_row is not None
        rule_combo = window.rules_table.cellWidget(found_row, 1)
        assert isinstance(rule_combo, QComboBox)
        assert rule_combo.currentData() == RuleType.EXCLUDE_GLOB

        # Change rule type via dropdown
        rule_combo.setCurrentIndex(rule_combo.findData(RuleType.INCLUDE_GLOB))
        rules = [r for r in rule_repo.list_rules() if r.id == "rule_1"]
        assert len(rules) == 1
        assert rules[0].rule_type == RuleType.INCLUDE_GLOB

        # Toggle rule enabled via checkbox
        rule_cb = window.rules_table.cellWidget(found_row, 4)
        assert isinstance(rule_cb, QCheckBox)
        assert rule_cb.isChecked() is True
        rule_cb.setChecked(False)
        rules = [r for r in rule_repo.list_rules() if r.id == "rule_1"]
        assert rules[0].enabled is False

        # 3. Check Preview auto-planning and sorting (newest on top)
        window._switch_page("preview")
        assert window.active_profile.id == "prof_1"
        assert len(window.current_plan) >= 2
        # Verify profile name is at column 0 and newest file source path is at column 1
        assert window.preview_table.item(0, 0).text() == "Test Sync Profile"
        first_row_src = window.preview_table.item(0, 1).text()
        assert "newer.txt" in first_row_src

        # Verify action dropdown exists in row at column 3
        row_action_combo = window.preview_table.cellWidget(0, 3)
        assert isinstance(row_action_combo, QComboBox)

        # 4. Check persistence of plan and column states
        plan_repo = PlanRepository(db)
        cached_plan = plan_repo.get_plan("prof_1")
        assert len(cached_plan) == len(window.current_plan)

        # Trigger save state
        window._save_table_states()
        settings_repo = SettingsRepository(db)
        assert len(settings_repo.get("col_state_preview", "")) > 0
        assert len(settings_repo.get("col_state_profiles", "")) > 0

        # 5. Check FAQ Page
        window._switch_page("faq")
        assert window.stack.currentIndex() == 8

        # 6. Test 'Оновити аналіз' button discovers new files
        window._switch_page("preview")
        initial_plan_count = len(window.current_plan)

        # Add a new file to source dir
        brand_new_file = src_dir / "brand_new_test_file.txt"
        brand_new_file.write_text("Fresh data created during test", encoding="utf-8")

        # Click rescan button
        window._rescan_preview_plan()
        assert len(window.current_plan) == initial_plan_count + 1
        
        # Verify new file is in preview table and planned as COPY
        found_new = any("brand_new_test_file.txt" in op.rel_path for op in window.current_plan)
        assert found_new is True
    finally:
        window.watcher.stop()


def test_preview_scan_worker_thread_and_progress(tmp_path, qtbot):
    """Verifies that PreviewScanWorker runs asynchronously in QThread and emits progress signals without freezing."""
    from syncforge.gui.main_window import PreviewScanWorker
    from syncforge.core.models import SyncProfile, FolderMapping, SyncMode

    db = Database(tmp_path / "test_async_worker.db")
    win = MainWindow(db=db)
    qtbot.addWidget(win)

    src_dir = tmp_path / "async_src"
    dst_dir = tmp_path / "async_dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    for i in range(10):
        (src_dir / f"test_item_{i}.txt").write_text(f"content {i}", encoding="utf-8")

    mapping = FolderMapping(id="m_async", profile_id="prof_async", source_root=str(src_dir), dest_subpath="")
    prof = SyncProfile(
        id="prof_async",
        name="Async Profile",
        mode=SyncMode.ONE_WAY_SAFE,
        mappings=[mapping],
        dest_root=str(dst_dir)
    )
    win.profile_repo.save_profile(prof)

    # 1. Test worker runs in background QThread and emits progress and finished
    worker = PreviewScanWorker(
        orchestrator=win.orchestrator,
        rule_repo=win.rule_repo,
        profiles=[prof],
        is_all=False
    )

    progress_events = []
    worker.progress.connect(lambda pct, msg: progress_events.append((pct, msg)))

    with qtbot.waitSignal(worker.scan_finished, timeout=5000) as blocker:
        worker.start()

    assert len(progress_events) >= 1
    assert any(pct > 0 for pct, _ in progress_events)
    assert progress_events[-1][0] == 100
    plan, finished_prof = blocker.args
    assert len(plan) == 10
    assert finished_prof.id == prof.id

    # 2. Test progress callbacks directly update progress bar and status
    win._on_scan_progress(55, "Сканування файлів...")
    assert win.progress_bar.value() == 55
    assert "55%" in win.progress_bar.format()
    assert "Сканування файлів..." in win.summary_lbl.text()

    win._on_preview_scan_finished(plan, prof, notify=False)
    assert win.progress_bar.value() == 100
    assert len(win.current_plan) == 10
    assert win.preview_table.rowCount() == 10

    win.watcher.stop()
    win.close()

