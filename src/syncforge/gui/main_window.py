import os
import sys
import uuid
from pathlib import Path
from typing import Optional, List
import structlog
from PySide6.QtCore import Qt, QTimer, Signal, QByteArray
from PySide6.QtGui import QColor, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QMessageBox, QFrame,
    QMenu, QSystemTrayIcon
)
from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlanAction, PlannedOperation,
    ExecutionStatus, DeviceRole, ConflictResolution, DeviceInfo
)
from syncforge.core.transfer_engine import TransferProgress
from syncforge.storage.database import Database
from syncforge.storage.repositories import (
    ProfileRepository, DeviceRepository, BaselineRepository, HistoryRepository,
    RuleRepository, SettingsRepository, PlanRepository, TombstoneRepository
)
from syncforge.devices.device_service import DeviceService
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.services.file_watcher import DirectoryWatcher
from syncforge.rules.rule_engine import RuleEngine, Rule, RuleType
from syncforge.i18n.translations import tr, set_language, format_bytes, CURRENT_LANG
from syncforge.core.planner import _sort_plan_key
from syncforge.gui.dialogs import ProfileEditorDialog, RuleEditorDialog, show_scrollable_error
from syncforge.gui.duplicates_dialog import DuplicateFinderDialog
from syncforge.gui.styles import DARK_STYLESHEET, LIGHT_STYLESHEET, create_app_icon
from syncforge.gui.workers import PreviewScanWorker, SyncExecutionWorker
from syncforge.gui.pages import (
    DashboardPage, DevicesPage, ProfilesPage, RulesPage,
    PreviewPage, ConflictsPage, HistoryPage, SettingsPage, FaqPage,
    FileManagerPage
)

logger = structlog.get_logger(__name__)


class MainWindow(QMainWindow):
    """Main desktop application window shell."""

    fs_change_signal = Signal(str)

    def __init__(self, db: Optional[Database] = None):
        super().__init__()
        self.db = db or Database()
        self.device_repo = DeviceRepository(self.db)
        self.profile_repo = ProfileRepository(self.db)
        self.baseline_repo = BaselineRepository(self.db)
        self.history_repo = HistoryRepository(self.db)
        self.rule_repo = RuleRepository(self.db)
        self.settings_repo = SettingsRepository(self.db)
        self.plan_repo = PlanRepository(self.db)
        self.tombstone_repo = TombstoneRepository(self.db)
        self.device_service = DeviceService()
        self.orchestrator = SyncOrchestrator(
            device_service=self.device_service,
            device_repo=self.device_repo,
            baseline_repo=self.baseline_repo,
            history_repo=self.history_repo,
            tombstone_repo=self.tombstone_repo,
            rule_repo=self.rule_repo
        )

        self.current_plan: List[PlannedOperation] = []
        self.active_profile: Optional[SyncProfile] = None
        self.is_all_profiles_selected: bool = False
        self._dirty_profiles: set = set()
        self.preview_worker: Optional[PreviewScanWorker] = None
        self.sync_worker: Optional[SyncExecutionWorker] = None

        # Load saved settings
        saved_lang = self.settings_repo.get("language", "uk")
        set_language(saved_lang)
        self.is_dark_theme = self.settings_repo.get("theme", "dark") == "dark"

        self.setWindowTitle(tr("app_title"))
        self.resize(1220, 800)
        self.setMinimumSize(950, 600)
        self.setStyleSheet(DARK_STYLESHEET if self.is_dark_theme else LIGHT_STYLESHEET)

        self.app_icon = create_app_icon()
        self.setWindowIcon(self.app_icon)

        # File watcher for automatic updates
        self.watcher = DirectoryWatcher(debounce_seconds=1.5)
        self.watcher.start()
        self.watcher.subscribe(self._on_fs_changed_worker)
        self.fs_change_signal.connect(self._on_fs_changed_ui)

        self._init_ui()
        self._restore_table_states()
        self._init_system_tray()
        self._setup_profile_watchers()
        self._restore_last_session()

    # --- Backward compatibility aliases for table properties and widgets ---
    @property
    def preview_table(self):
        return self.preview_page.table

    @property
    def preview_rescan_btn(self):
        return self.preview_page.rescan_btn

    @property
    def preview_exec_btn(self):
        return self.preview_page.exec_btn

    @property
    def preview_profile_combo(self):
        return self.preview_page.profile_combo

    @property
    def progress_bar(self):
        return self.preview_page.progress_bar

    @property
    def progress_pct_lbl(self):
        return getattr(self.preview_page, "progress_pct_lbl", None)

    @property
    def summary_lbl(self):
        return self.preview_page.summary_lbl

    @property
    def action_filter_combo(self):
        return self.preview_page.action_filter_combo

    @property
    def search_edit(self):
        return self.preview_page.search_edit

    @property
    def dev_table(self):
        return self.devices_page.table

    @property
    def profiles_table(self):
        return self.profiles_page.table

    @property
    def rules_table(self):
        return self.rules_page.table

    @property
    def conflicts_table(self):
        return self.conflicts_page.table

    @property
    def history_table(self):
        return self.history_page.table

    def _setup_profile_watchers(self):
        """Watches all source and destination directories of configured profiles."""
        profs = self.profile_repo.list_profiles()
        for p in profs:
            for m in p.mappings:
                if m.source_root:
                    self.watcher.watch_path(m.source_root)
            if p.dest_root:
                self.watcher.watch_path(p.dest_root)

    def _on_fs_changed_worker(self, changed_path: str):
        self.fs_change_signal.emit(changed_path)

    def _on_fs_changed_ui(self, changed_path: str):
        """Auto-refreshes preview if active profile contains the changed path, or updates dashboard."""
        # CRITICAL PERFORMANCE & STABILITY OPTIMIZATION:
        # Never launch automatic scans while synchronization execution is actively running!
        # Background writes to the destination or temporary files trigger Watchdog events, which
        # would otherwise cause recurring disk scanning storms, lock contentions, and drive disconnects.
        if self.sync_worker and self.sync_worker.isRunning():
            return

        self.dashboard_page.refresh()
        norm_changed = changed_path.lower().replace("\\", "/")

        if self.is_all_profiles_selected:
            profs = self.profile_repo.list_profiles()
            for p in profs:
                relevant = any(
                    norm_changed.startswith(m.source_root.lower().replace("\\", "/"))
                    for m in p.mappings if m.source_root
                ) or (bool(p.dest_root) and norm_changed.startswith(p.dest_root.lower().replace("\\", "/")))
                if relevant:
                    self._dirty_profiles.add(p.id)
                    self._run_all_profiles_preview(notify=False)
                    return
            return

        if not self.active_profile:
            profs = self.profile_repo.list_profiles()
            if profs:
                self.active_profile = profs[0]

        if self.active_profile:
            is_relevant = any(
                norm_changed.startswith(m.source_root.lower().replace("\\", "/"))
                for m in self.active_profile.mappings
                if m.source_root
            ) or (bool(self.active_profile.dest_root) and norm_changed.startswith(self.active_profile.dest_root.lower().replace("\\", "/")))
            if is_relevant:
                self._dirty_profiles.add(self.active_profile.id)
                self._run_profile_preview(self.active_profile, notify=False, force=True)

    def _save_table_states(self):
        """Persists table column widths and order across application restarts."""
        tables = {
            "dev": getattr(self, "dev_table", None),
            "profiles": getattr(self, "profiles_table", None),
            "rules": getattr(self, "rules_table", None),
            "preview": getattr(self, "preview_table", None),
            "conflicts": getattr(self, "conflicts_table", None),
            "history": getattr(self, "history_table", None),
        }
        for name, tbl in tables.items():
            if tbl:
                hex_val = tbl.horizontalHeader().saveState().toHex().data().decode("ascii")
                self.settings_repo.set(f"col_state_{name}", hex_val)

    def _restore_table_states(self):
        """Restores persisted table column widths from previous session."""
        tables = {
            "dev": getattr(self, "dev_table", None),
            "profiles": getattr(self, "profiles_table", None),
            "rules": getattr(self, "rules_table", None),
            "preview": getattr(self, "preview_table", None),
            "conflicts": getattr(self, "conflicts_table", None),
            "history": getattr(self, "history_table", None),
        }
        for name, tbl in tables.items():
            if tbl:
                hex_val = self.settings_repo.get(f"col_state_{name}", "")
                if hex_val:
                    try:
                        tbl.horizontalHeader().restoreState(QByteArray.fromHex(hex_val.encode("ascii")))
                    except Exception:
                        pass

    def _restore_last_session(self):
        """Restores last active profile and cached dry-run analysis plan."""
        last_pid = self.settings_repo.get("last_active_profile_id", "")
        if last_pid == "all":
            self.is_all_profiles_selected = True
            self._run_all_profiles_preview(notify=False)
            return

        if last_pid:
            self.active_profile = self.profile_repo.get_profile(last_pid)
        if not self.active_profile:
            profs = self.profile_repo.list_profiles()
            if profs:
                self.active_profile = profs[0]

        if self.active_profile:
            cached = self.plan_repo.get_plan(self.active_profile.id)
            if cached:
                self.current_plan = cached
                self._populate_preview_table(self.current_plan)
            else:
                self._run_profile_preview(self.active_profile, notify=False)

    def _init_system_tray(self):
        """Initializes persistent system tray icon and menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("SyncForge — File Synchronization & Backup")

        tray_menu = QMenu()
        show_action = QAction("Показати SyncForge", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()
        quit_action = QAction("Вийти з програми", self)
        quit_action.triggered.connect(self._quit_application)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick or reason == QSystemTrayIcon.Trigger:
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()

    def _quit_application(self):
        self._save_table_states()
        if self.active_profile and self.current_plan:
            self.plan_repo.save_plan(self.active_profile.id, self.current_plan)
        self.watcher.stop()
        QApplication.quit()

    def closeEvent(self, event):
        """Intercepts window close to minimize to system tray if enabled, saving layout and state."""
        self._save_table_states()
        if self.active_profile:
            self.settings_repo.set("last_active_profile_id", self.active_profile.id)
            if self.current_plan:
                self.plan_repo.save_plan(self.active_profile.id, self.current_plan)

        minimize_to_tray = self.settings_repo.get("minimize_tray", "1") == "1"
        if minimize_to_tray and hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "SyncForge",
                "Програма продовжує працювати у фоновому режимі.",
                QSystemTrayIcon.Information,
                2000
            )
            event.ignore()
        else:
            if hasattr(self, "preview_worker") and self.preview_worker and self.preview_worker.isRunning():
                self.preview_worker.wait(1000)
            self.watcher.stop()
            event.accept()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Left Navigation Sidebar
        nav_panel = QFrame()
        nav_panel.setObjectName("NavPanel")
        nav_panel.setFixedWidth(230)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(14, 22, 14, 22)
        nav_layout.setSpacing(8)

        title_lbl = QLabel("SyncForge 1.0")
        title_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #5865f2; margin-bottom: 12px;")
        nav_layout.addWidget(title_lbl)

        self.nav_buttons = {}
        pages = [
            ("dashboard", tr("dashboard")),
            ("devices", tr("devices")),
            ("profiles", tr("profiles")),
            ("rules", tr("rules")),
            ("preview", tr("preview")),
            ("conflicts", tr("conflicts")),
            ("history", tr("history")),
            ("file_manager", tr("file_manager")),
            ("settings", tr("settings")),
            ("faq", tr("faq")),
        ]

        for page_id, page_title in pages:
            btn = QPushButton(page_title)
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, pid=page_id: self._switch_page(pid))
            nav_layout.addWidget(btn)
            self.nav_buttons[page_id] = btn

        nav_layout.addStretch()

        self.status_lbl = QLabel(tr("status_ready"))
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setMaximumHeight(48)
        self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 4px; font-size: 12px;")
        nav_layout.addWidget(self.status_lbl)

        main_layout.addWidget(nav_panel)

        # Right Stacked Pages
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        # 1. Dashboard
        self.dashboard_page = DashboardPage(
            profile_repo=self.profile_repo,
            device_service=self.device_service,
            on_create_profile=self._create_new_profile_dialog,
            on_switch_page=self._switch_page,
            get_current_plan_conflicts=lambda: sum(1 for op in self.current_plan if op.action == PlanAction.CONFLICT),
            on_open_duplicates=self._open_duplicate_finder_dialog,
            parent=self
        )
        self.stack.addWidget(self.dashboard_page)

        # 2. Devices
        self.devices_page = DevicesPage(
            device_service=self.device_service,
            device_repo=self.device_repo,
            parent=self
        )
        self.stack.addWidget(self.devices_page)

        # 3. Profiles
        self.profiles_page = ProfilesPage(
            profile_repo=self.profile_repo,
            device_repo=self.device_repo,
            device_service=self.device_service,
            on_run_preview=lambda prof: self._run_profile_preview(prof, force=True),
            on_profiles_changed=self._on_profiles_changed,
            parent=self
        )
        self.stack.addWidget(self.profiles_page)

        # 4. Rules
        self.rules_page = RulesPage(
            rule_repo=self.rule_repo,
            parent=self
        )
        self.stack.addWidget(self.rules_page)

        # 5. Preview
        self.preview_page = PreviewPage(
            profile_repo=self.profile_repo,
            on_execute=self._execute_active_plan,
            on_pause_toggle=self._toggle_sync_pause,
            on_cancel=self._cancel_active_sync,
            on_rescan=self._rescan_preview_plan,
            on_profile_selected=self._on_preview_profile_selected,
            parent=self
        )
        self.stack.addWidget(self.preview_page)

        # 6. Conflicts
        self.conflicts_page = ConflictsPage(
            get_plan_callback=lambda: self.current_plan,
            parent=self
        )
        self.stack.addWidget(self.conflicts_page)

        # 7. History
        self.history_page = HistoryPage(
            history_repo=self.history_repo,
            parent=self
        )
        self.stack.addWidget(self.history_page)

        # 8. File Manager (Total Commander style)
        self.file_manager_page = FileManagerPage(
            profile_repo=self.profile_repo,
            parent=self
        )
        self.stack.addWidget(self.file_manager_page)

        # 9. Settings
        self.settings_page = SettingsPage(
            settings_repo=self.settings_repo,
            on_theme_changed=self._on_theme_changed,
            on_history_cleared=lambda: self.history_page.refresh(),
            parent=self
        )
        self.stack.addWidget(self.settings_page)

        # 10. FAQ
        self.faq_page = FaqPage(parent=self)
        self.stack.addWidget(self.faq_page)

        self._switch_page("dashboard")

    def _open_duplicate_finder_dialog(self, initial_path: Optional[str] = None):
        target = initial_path
        if not target and self.active_profile and self.active_profile.mappings:
            target = self.active_profile.mappings[0].source_root
        dlg = DuplicateFinderDialog(initial_dir=target, parent=self)
        dlg.exec()

    def _switch_page(self, page_id: str):
        for pid, btn in self.nav_buttons.items():
            btn.setChecked(pid == page_id)

        index_map = {
            "dashboard": 0, "devices": 1, "profiles": 2, "rules": 3,
            "preview": 4, "conflicts": 5, "history": 6, "file_manager": 7,
            "settings": 8, "faq": 9
        }
        self.stack.setCurrentIndex(index_map.get(page_id, 0))

        if page_id == "dashboard":
            self.dashboard_page.refresh()
        elif page_id == "devices":
            self.devices_page.refresh()
        elif page_id == "profiles":
            self.profiles_page.refresh()
        elif page_id == "rules":
            self.rules_page.refresh()
        elif page_id == "preview":
            if hasattr(self, "preview_worker") and self.preview_worker and self.preview_worker.isRunning():
                pass
            elif self.is_all_profiles_selected:
                if not self.current_plan:
                    self._run_all_profiles_preview(notify=False)
            else:
                if not self.active_profile:
                    profs = self.profile_repo.list_profiles()
                    if profs:
                        self.active_profile = profs[0]
                if self.active_profile and not self.current_plan:
                    self._run_profile_preview(self.active_profile, notify=False)
        elif page_id == "conflicts":
            self.conflicts_page.refresh()
        elif page_id == "history":
            self.history_page.refresh()
        elif page_id == "file_manager":
            self.file_manager_page.refresh_both()

    def _on_theme_changed(self, is_dark: bool):
        self.is_dark_theme = is_dark
        self.setStyleSheet(DARK_STYLESHEET if is_dark else LIGHT_STYLESHEET)

    def _on_profiles_changed(self):
        self._setup_profile_watchers()
        self._update_preview_profile_combo()
        self.dashboard_page.refresh()

    def _create_new_profile_dialog(self):
        self.profiles_page._create_new_profile_dialog()

    def _refresh_dashboard(self):
        self.dashboard_page.refresh()

    def _refresh_profiles_list(self):
        self.profiles_page.refresh()

    def _refresh_rules_table(self):
        self.rules_page.refresh()

    def _refresh_devices_table(self):
        self.devices_page.refresh()

    def _refresh_history_table(self):
        self.history_page.refresh()

    def _refresh_conflicts_table(self):
        self.conflicts_page.refresh()

    # --- Scanning & Plan Generation ---

    def _set_scanning_ui_state(self, is_scanning: bool):
        self.preview_page.set_syncing_state(is_scanning)

    def _on_scan_progress(self, percent: int, message: str):
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"{percent}% - {message}")
        if hasattr(self, "progress_pct_lbl") and self.progress_pct_lbl:
            self.progress_pct_lbl.setText(f"{percent}%")
        if hasattr(self, "summary_lbl") and self.summary_lbl:
            self.summary_lbl.setText(f"Сканування: {message}")

    def _on_preview_scan_finished(self, plan: List[PlannedOperation], profile: Optional[SyncProfile], notify: bool = False, is_all: bool = False):
        self.current_plan = plan
        if not is_all and profile:
            self.active_profile = profile
            self.settings_repo.set("last_active_profile_id", profile.id)
            self._dirty_profiles.discard(profile.id)
        elif is_all:
            self._dirty_profiles.clear()

        self._populate_preview_table(self.current_plan)

        pending_count = sum(1 for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE, PlanAction.ERROR))
        total_count = len(self.current_plan)
        if pending_count > 0:
            self.status_lbl.setText(f"Аналіз оновлено: {pending_count} дій до виконання (із {total_count} перевірених)")
        else:
            self.status_lbl.setText(f"Аналіз оновлено: всі {total_count} файлів актуальні")
        self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")

        self._set_scanning_ui_state(False)
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("100% - Готово")

        if notify:
            self._switch_page("preview")

    def _on_preview_scan_error(self, err_msg: str, notify: bool = False):
        self._set_scanning_ui_state(False)
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0%")
        self.status_lbl.setText("Помилка аналізу")
        self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")
        if notify:
            QMessageBox.critical(self, "Помилка формування плану", err_msg)

    def _run_profile_preview(self, profile: SyncProfile, notify: bool = True, sync: bool = False, force: bool = False):
        if not profile:
            return

        if notify:
            self._switch_page("preview")

        # Fast cache hit check
        if not force and not sync and not os.environ.get("PYTEST_CURRENT_TEST") and profile.id not in self._dirty_profiles:
            cached = self.plan_repo.get_plan(profile.id) if self.plan_repo else []
            if cached:
                self.current_plan = cached
                self.active_profile = profile
                self.settings_repo.set("last_active_profile_id", profile.id)
                self._populate_preview_table(self.current_plan)
                pending_count = sum(1 for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE, PlanAction.ERROR))
                total_count = len(self.current_plan)
                mode_str = getattr(profile.analysis_mode, "value", str(profile.analysis_mode))
                if pending_count > 0:
                    self.status_lbl.setText(f"Кешований план ({mode_str}, без змін): {pending_count} дій (із {total_count} файлів)")
                else:
                    self.status_lbl.setText(f"Кешований план ({mode_str}, без змін): всі {total_count} файлів актуальні")
                self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
                if hasattr(self, "progress_bar"):
                    self.progress_bar.setValue(100)
                    self.progress_bar.setFormat(f"100% - Кеш ({mode_str})")
                return

        self.status_lbl.setText("Сканування...")
        self.status_lbl.setStyleSheet("color: #faa61a; font-weight: bold; padding: 6px;")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0% - Ініціалізація...")

        if sync or os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                active_rules = self.rule_repo.list_rules(profile.id)
                engine = RuleEngine(active_rules)
                self.current_plan = self.orchestrator.prepare_dry_run(
                    profile,
                    rule_engine=engine,
                    progress_callback=self._on_scan_progress
                )
                self.active_profile = profile
                self.plan_repo.save_plan(profile.id, self.current_plan)
                self.settings_repo.set("last_active_profile_id", profile.id)
                self._dirty_profiles.discard(profile.id)
                self._populate_preview_table(self.current_plan)

                pending_count = sum(1 for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE, PlanAction.ERROR))
                total_count = len(self.current_plan)
                if pending_count > 0:
                    self.status_lbl.setText(f"Аналіз оновлено: {pending_count} дій до виконання (із {total_count} перевірених)")
                else:
                    self.status_lbl.setText(f"Аналіз оновлено: всі {total_count} файлів актуальні")
                self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
                if hasattr(self, "progress_bar"):
                    self.progress_bar.setValue(100)
                    self.progress_bar.setFormat("100% - Готово")
            except Exception as e:
                self.status_lbl.setText("Помилка аналізу")
                self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")
                if notify:
                    QMessageBox.critical(self, "Помилка формування плану", str(e))
            return

        if self.preview_worker and self.preview_worker.isRunning():
            return

        self._set_scanning_ui_state(True)
        self.preview_worker = PreviewScanWorker(
            orchestrator=self.orchestrator,
            rule_repo=self.rule_repo,
            profiles=[profile],
            plan_repo=self.plan_repo,
            is_all=False
        )
        self.preview_worker.progress.connect(self._on_scan_progress)
        self.preview_worker.scan_finished.connect(
            lambda plan, prof: self._on_preview_scan_finished(plan, prof, notify=False, is_all=False)
        )
        self.preview_worker.scan_error.connect(
            lambda err: self._on_preview_scan_error(err, notify=notify)
        )
        self.preview_worker.start()

    def _run_all_profiles_preview(self, notify: bool = True, sync: bool = False):
        """Prepares dry-run synchronization plans across ALL configured profiles."""
        profs = self.profile_repo.list_profiles()
        if not profs:
            self.current_plan = []
            self._populate_preview_table([])
            return

        if notify:
            self._switch_page("preview")

        self.status_lbl.setText("Сканування...")
        self.status_lbl.setStyleSheet("color: #faa61a; font-weight: bold; padding: 6px;")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0% - Ініціалізація...")

        if sync or os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                all_ops: List[PlannedOperation] = []
                for p in profs:
                    active_rules = self.rule_repo.list_rules(p.id)
                    engine = RuleEngine(active_rules)
                    p_ops = self.orchestrator.prepare_dry_run(
                        p,
                        rule_engine=engine,
                        progress_callback=self._on_scan_progress
                    )
                    all_ops.extend(p_ops)
                    self.plan_repo.save_plan(p.id, p_ops)

                all_ops.sort(key=_sort_plan_key)
                self.current_plan = all_ops
                self._populate_preview_table(self.current_plan)

                pending_count = sum(1 for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE, PlanAction.ERROR))
                total_count = len(self.current_plan)
                if pending_count > 0:
                    self.status_lbl.setText(f"Аналіз оновлено: {pending_count} дій до виконання (із {total_count} перевірених)")
                else:
                    self.status_lbl.setText(f"Аналіз оновлено: всі {total_count} файлів актуальні")
                self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
                if hasattr(self, "progress_bar"):
                    self.progress_bar.setValue(100)
                    self.progress_bar.setFormat("100% - Готово")
            except Exception as e:
                self.status_lbl.setText("Помилка аналізу")
                self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")
                if notify:
                    QMessageBox.critical(self, "Помилка формування зведеного плану", str(e))
            return

        if self.preview_worker and self.preview_worker.isRunning():
            return

        self._set_scanning_ui_state(True)
        self.preview_worker = PreviewScanWorker(
            orchestrator=self.orchestrator,
            rule_repo=self.rule_repo,
            profiles=profs,
            plan_repo=self.plan_repo,
            is_all=True
        )
        self.preview_worker.progress.connect(self._on_scan_progress)
        self.preview_worker.scan_finished.connect(
            lambda plan, prof: self._on_preview_scan_finished(plan, prof, notify=False, is_all=True)
        )
        self.preview_worker.scan_error.connect(
            lambda err: self._on_preview_scan_error(err, notify=notify)
        )
        self.preview_worker.start()

    def _update_preview_profile_combo(self):
        self.preview_page.update_profile_combo(self.active_profile, self.is_all_profiles_selected)

    def _on_preview_profile_selected(self, index: int):
        data = self.preview_profile_combo.itemData(index)
        if data == "all":
            self.is_all_profiles_selected = True
            self.settings_repo.set("last_active_profile_id", "all")
            self._run_all_profiles_preview(notify=False)
        elif isinstance(data, SyncProfile):
            self.is_all_profiles_selected = False
            self.active_profile = data
            self.settings_repo.set("last_active_profile_id", data.id)
            self._run_profile_preview(self.active_profile, notify=False)

    def _rescan_preview_plan(self):
        if self.is_all_profiles_selected:
            self._run_all_profiles_preview(notify=True)
            return

        profs = self.profile_repo.list_profiles()
        if not profs:
            QMessageBox.information(self, "Повідомлення", "Немає налаштованих профілів.")
            return

        if self.active_profile:
            fresh = self.profile_repo.get_profile(self.active_profile.id)
            self.active_profile = fresh if fresh else profs[0]
        else:
            self.active_profile = profs[0]

        if self.active_profile:
            self._run_profile_preview(self.active_profile, notify=True, force=True)

    def _populate_preview_table(self, plan: List[PlannedOperation]):
        free_space = "N/A"
        if not self.is_all_profiles_selected and self.active_profile:
            dest_path = ""
            if self.active_profile.dest_root:
                dest_path = self.active_profile.dest_root
            elif self.active_profile.target_device_id:
                try:
                    dest_path = self.orchestrator._resolve_and_verify_dest_root(self.active_profile)
                except Exception:
                    dest_path = ""
            if dest_path:
                try:
                    import shutil
                    free_b = shutil.disk_usage(dest_path).free
                    free_space = format_bytes(free_b)
                except Exception:
                    pass

        self.preview_page.set_plan(
            plan=plan,
            active_profile=self.active_profile,
            is_all=self.is_all_profiles_selected,
            dest_free_space=free_space
        )

    # --- Freshness Validation & Sync Execution ---

    def _validate_plan_freshness(self, profile: SyncProfile, plan: List[PlannedOperation]) -> tuple[bool, str]:
        dest_root = ""
        if profile.target_device_id:
            try:
                dest_root = self.orchestrator._resolve_and_verify_dest_root(profile)
            except Exception as e:
                return False, f"Цільовий накопичувач відключено або шлях недоступний: {e}"
        elif profile.dest_root:
            dest_root = profile.dest_root

        if not dest_root or not Path(dest_root).exists():
            return False, "Цільовий накопичувач відключено або шлях недоступний"

        for m in profile.mappings:
            if m.enabled and not Path(m.source_root).exists():
                return False, f"Папка-джерело на ПК не знайдена: {m.source_root}"

        for op in plan:
            if op.action in (PlanAction.SKIP, PlanAction.IGNORE, PlanAction.ERROR, PlanAction.PRESERVE):
                continue

            if op.action in (PlanAction.COPY, PlanAction.UPDATE) and op.source_abs:
                src_p = Path(op.source_abs)
                if op.source_meta is not None:
                    try:
                        if not src_p.exists():
                            return False, f"Файл видалено на ПК: {op.rel_path}"
                        st = src_p.stat()
                        if st.st_size != op.source_meta.size_bytes or abs(st.st_mtime_ns - op.source_meta.mtime_ns) > 2_000_000_000:
                            return False, f"Файл змінено на ПК: {op.rel_path}"
                    except Exception:
                        pass

            elif op.action == PlanAction.RESTORE and op.dest_abs:
                dst_p = Path(op.dest_abs)
                try:
                    if not dst_p.exists():
                        return False, f"Файл для відновлення зник на накопичувачі: {op.rel_path}"
                except Exception:
                    pass

        return True, ""

    def _execute_active_plan(self):
        if self.is_all_profiles_selected:
            profs = self.profile_repo.list_profiles()
            if not profs:
                QMessageBox.warning(self, "Повідомлення", "Спочатку створіть профіль синхронізації.")
                self._switch_page("profiles")
                return

            if not self.current_plan:
                self._run_all_profiles_preview(notify=False, sync=False)

            actionable_ops = [op for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE)]
            if not actionable_ops:
                QMessageBox.information(self, "Повідомлення", "Всі файли актуальні, немає дій для синхронізації.")
                return

            error_ops = [op for op in self.current_plan if op.action == PlanAction.ERROR]
            if error_ops:
                res = QMessageBox.question(
                    self,
                    "Помилки в плані",
                    f"План містить {len(error_ops)} помилок конфігурації/колізій шляхів.\n\n"
                    "Пропустити ці файли та продовжити виконання синхронізації для решти файлів?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if res == QMessageBox.Yes:
                    for e_op in error_ops:
                        e_op.action = PlanAction.SKIP
                        e_op.reason = "Пропущено автоматично через помилку/колізію шляху"
                    actionable_ops = [op for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE)]
                    if not actionable_ops:
                        QMessageBox.information(self, "Повідомлення", "Після пропуску помилкових файлів немає дій для синхронізації.")
                        return
                else:
                    return

            has_conflicts = any(op.action == PlanAction.CONFLICT and not op.conflict_resolution for op in actionable_ops)
            if has_conflicts:
                QMessageBox.critical(self, "Невирішені конфлікти", tr("unresolved_conflicts_err"))
                self._switch_page("conflicts")
                return

            prof_map = {p.id: p for p in profs}
            grouped_ops: dict[str, List[PlannedOperation]] = {}
            for op in actionable_ops:
                grouped_ops.setdefault(op.profile_id, []).append(op)

            for pid, p_ops in grouped_ops.items():
                p = prof_map.get(pid)
                if p:
                    is_fresh, stale_reason = self._validate_plan_freshness(p, p_ops)
                    if not is_fresh:
                        QMessageBox.warning(
                            self,
                            tr("plan_stale_title"),
                            f"План для профілю '{p.name}' застарів.\n\nПричина: {stale_reason}"
                        )
                        self._run_all_profiles_preview(notify=False, sync=False)
                        return

            delete_ops = [op for op in actionable_ops if op.action == PlanAction.DELETE_DEST]
            warn_del = ""
            if delete_ops:
                warn_del = f"\n\nУВАГА: Зведений план містить ВИДАЛЕННЯ {len(delete_ops)} файлів на цільових пристроях!"

            confirm_msg = tr("confirm_execute").format(count=len(actionable_ops), name=tr("all_profiles"))
            if len(self.current_plan) > len(actionable_ops):
                confirm_msg += f"\n(Всього перевірено файлів: {len(self.current_plan)}, актуальних без змін: {len(self.current_plan) - len(actionable_ops)})"
            confirm_msg += warn_del

            reply = QMessageBox.question(
                self, "Підтвердження", confirm_msg,
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

            worker_items = []
            for pid, p_ops in grouped_ops.items():
                p = prof_map.get(pid)
                if p:
                    worker_items.append((p, p_ops))

            self._start_sync_worker(worker_items, is_all=True)
            return

        # Single profile plan execution
        if not self.active_profile:
            profs = self.profile_repo.list_profiles()
            if profs:
                self.active_profile = profs[0]
            else:
                QMessageBox.warning(self, "Повідомлення", "Спочатку створіть профіль синхронізації.")
                self._switch_page("profiles")
                return

        if not self.current_plan:
            self._run_profile_preview(self.active_profile, notify=False, sync=False)

        actionable_ops = [op for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE)]
        if not actionable_ops:
            QMessageBox.information(self, "Повідомлення", "Всі файли актуальні, немає дій для синхронізації.")
            return

        is_fresh, stale_reason = self._validate_plan_freshness(self.active_profile, actionable_ops)
        if not is_fresh:
            QMessageBox.warning(
                self,
                tr("plan_stale_title"),
                tr("plan_stale_msg") + f"\n\nПричина: {stale_reason}"
            )
            self._run_profile_preview(self.active_profile, notify=False, sync=False)
            return

        error_ops = [op for op in self.current_plan if op.action == PlanAction.ERROR]
        if error_ops:
            res = QMessageBox.question(
                self,
                "Помилки в плані",
                f"План містить {len(error_ops)} помилок конфігурації/колізій шляхів.\n\n"
                "Пропустити ці файли та продовжити виконання синхронізації для решти файлів?",
                QMessageBox.Yes | QMessageBox.No
            )
            if res == QMessageBox.Yes:
                for e_op in error_ops:
                    e_op.action = PlanAction.SKIP
                    e_op.reason = "Пропущено автоматично через помилку/колізію шляху"
                actionable_ops = [op for op in self.current_plan if op.action not in (PlanAction.SKIP, PlanAction.IGNORE)]
                if not actionable_ops:
                    QMessageBox.information(self, "Повідомлення", "Після пропуску помилкових файлів немає дій для синхронізації.")
                    return
            else:
                return

        has_conflicts = any(op.action == PlanAction.CONFLICT and not op.conflict_resolution for op in actionable_ops)
        if has_conflicts:
            QMessageBox.critical(self, "Невирішені конфлікти", tr("unresolved_conflicts_err"))
            self._switch_page("conflicts")
            return

        delete_ops = [op for op in actionable_ops if op.action == PlanAction.DELETE_DEST]
        warn_del = ""
        if delete_ops:
            warn_del = f"\n\nУВАГА: План містить ВИДАЛЕННЯ {len(delete_ops)} файлів на цільовому пристрої!"

        confirm_msg = tr("confirm_execute").format(count=len(actionable_ops), name=self.active_profile.name)
        if len(self.current_plan) > len(actionable_ops):
            confirm_msg += f"\n(Всього перевірено файлів: {len(self.current_plan)}, актуальних без змін: {len(self.current_plan) - len(actionable_ops)})"
        confirm_msg += warn_del

        reply = QMessageBox.question(
            self, "Підтвердження", confirm_msg,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._start_sync_worker([(self.active_profile, actionable_ops)], is_all=False)

    def _start_sync_worker(self, items: List[tuple[SyncProfile, List[PlannedOperation]]], is_all: bool):
        if self.sync_worker and self.sync_worker.isRunning():
            self.sync_worker.wait(1000)
            if self.sync_worker.isRunning():
                QMessageBox.warning(self, "Попередження", "Синхронізація вже виконується або завершує попередню дію.")
                return

        self.preview_page.set_syncing_state(True, is_paused=False)
        self.status_lbl.setText(tr("status_syncing"))
        self.status_lbl.setStyleSheet("color: #fee75c; font-weight: bold; padding: 6px;")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0% - Початок синхронізації...")

        self.sync_worker = SyncExecutionWorker(self.orchestrator, items)
        self.sync_worker.progress.connect(self._on_sync_progress)
        self.sync_worker.execution_finished.connect(
            lambda status, failed, success, a=is_all: self._on_sync_finished(status, failed, success, a)
        )
        self.sync_worker.execution_error.connect(self._on_sync_error)
        self.sync_worker.start()

    def _toggle_sync_pause(self):
        """Pauses or resumes ongoing synchronization."""
        if not self.sync_worker or not self.sync_worker.isRunning():
            return

        if self.sync_worker.is_paused():
            self.sync_worker.resume()
            self.preview_page.set_syncing_state(is_syncing=True, is_paused=False)
            self.status_lbl.setText(tr("status_syncing"))
            self.status_lbl.setStyleSheet("color: #fee75c; font-weight: bold; padding: 6px;")
        else:
            self.sync_worker.pause()
            self.preview_page.set_syncing_state(is_syncing=True, is_paused=True)
            self.status_lbl.setText(tr("status_paused"))
            self.status_lbl.setStyleSheet("color: #faa61a; font-weight: bold; padding: 6px;")

    def _cancel_active_sync(self):
        """Immediately interrupts ongoing synchronization."""
        if not self.sync_worker or not self.sync_worker.isRunning():
            return

        reply = QMessageBox.question(
            self,
            "Підтвердження зупинки",
            "Ви дійсно бажаєте зупинити синхронізацію?\nПоточний файл буде безпечно очищено з тимчасового буфера.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.sync_worker.cancel()
            self.status_lbl.setText("Зупинка синхронізації...")
            self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")

    def _on_sync_progress(self, p: TransferProgress):
        if hasattr(self, "progress_bar") and p.total_operations > 0:
            val = int((p.completed_operations / p.total_operations) * 100)
            cur_file = Path(p.current_file).name if p.current_file else ""
            speed_str = f" ({p.current_speed_bps / (1024*1024):.1f} МБ/с)" if p.current_speed_bps > 0 else ""
            self.progress_bar.setValue(val)
            self.progress_bar.setFormat(f"{val}% - {p.completed_operations}/{p.total_operations} {cur_file}{speed_str}")
            if hasattr(self, "progress_pct_lbl") and self.progress_pct_lbl:
                self.progress_pct_lbl.setText(f"{val}%")

        if hasattr(self, "status_lbl") and self.status_lbl:
            cur_f = Path(p.current_file).name if p.current_file else ""
            if self.sync_worker and self.sync_worker.is_paused():
                self.status_lbl.setText(f"Призупинено: {p.completed_operations}/{p.total_operations} — {cur_f}")
            else:
                self.status_lbl.setText(f"Синхронізація: {p.completed_operations}/{p.total_operations} — {cur_f}")

    def _on_sync_finished(self, status: ExecutionStatus, failed_ops: list, successful_ops: list, is_all: bool):
        self.preview_page.set_syncing_state(False, is_paused=False)
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("100% - Готово")
        if hasattr(self, "progress_pct_lbl") and self.progress_pct_lbl:
            self.progress_pct_lbl.setText("100%")

        if status == ExecutionStatus.CANCELLED:
            self.status_lbl.setText(tr("status_cancelled"))
            self.status_lbl.setStyleSheet("color: #faa61a; font-weight: bold; padding: 6px;")
            QMessageBox.information(
                self,
                "Синхронізацію скасовано",
                f"Синхронізацію було зупинено користувачем.\nУспішно скопійовано: {len(successful_ops)} файлів."
            )
        elif failed_ops:
            self.status_lbl.setText(f"Завершено: {len(successful_ops)} успішно, {len(failed_ops)} помилок")
            self.status_lbl.setStyleSheet("color: #faa61a; font-weight: bold; padding: 6px;")

            error_lines = []
            for op in failed_ops:
                err = op.error_message or "Невідома помилка"
                error_lines.append(f"• {op.rel_path}\n  Помилка: {err}")
            details_text = "\n\n".join(error_lines)

            summary_text = (
                f"Синхронізація продовжувалася для всіх доступних файлів!\n\n"
                f"• Успішно скопійовано: {len(successful_ops)}\n"
                f"• Пропущено через помилки (файл зайнятий / пошкоджений / CHKDSK): {len(failed_ops)}"
            )

            show_scrollable_error(
                self,
                title="Синхронізацію завершено з окремими помилками",
                summary=summary_text,
                details=details_text,
                is_warning=bool(successful_ops)
            )
        else:
            self.status_lbl.setText(tr("status_ready"))
            self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
            QMessageBox.information(
                self,
                "Готово",
                f"Синхронізацію успішно завершено!\nОброблено файлів: {len(successful_ops)}"
            )

        if not failed_ops and status != ExecutionStatus.CANCELLED:
            if is_all:
                self._run_all_profiles_preview(notify=False)
            elif self.active_profile:
                self._run_profile_preview(self.active_profile, notify=False)

    def _on_sync_error(self, err_msg: str):
        self.preview_page.set_syncing_state(False, is_paused=False)
        self.status_lbl.setText(tr("status_error"))
        self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0%")
        show_scrollable_error(self, title="Помилка синхронізації", summary=err_msg, details=err_msg, is_warning=False)
