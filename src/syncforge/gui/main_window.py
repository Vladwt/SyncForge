"""Custom styled PySide6 GUI for SyncForge."""

import sys
from pathlib import Path
from typing import Optional, List
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QIcon, QFont, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QLineEdit, QComboBox, QMessageBox, QProgressBar,
    QFrame, QSplitter, QCheckBox, QSpinBox
)
from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlanAction, PlannedOperation,
    ExecutionStatus, DeviceRole, ConflictResolution
)
from syncforge.storage.database import Database
from syncforge.storage.repositories import (
    ProfileRepository, DeviceRepository, BaselineRepository, HistoryRepository
)
from syncforge.devices.device_service import DeviceService
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.rules.rule_engine import RuleEngine, Rule, RuleType
from syncforge.i18n.translations import tr, set_language

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #18191c;
    color: #e3e5e8;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#NavPanel {
    background-color: #121315;
    border-right: 1px solid #2b2d31;
}
QPushButton#NavBtn {
    background-color: transparent;
    color: #949ba4;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-weight: 500;
}
QPushButton#NavBtn:hover {
    background-color: #2b2d31;
    color: #f2f3f5;
}
QPushButton#NavBtn:checked {
    background-color: #35373c;
    color: #5865f2;
    font-weight: bold;
}
QPushButton.PrimaryBtn {
    background-color: #5865f2;
    color: white;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
    border: none;
}
QPushButton.PrimaryBtn:hover {
    background-color: #4752c4;
}
QPushButton.DangerBtn {
    background-color: #da373c;
    color: white;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
    border: none;
}
QTableWidget {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    gridline-color: #2b2d31;
    selection-background-color: #35373c;
}
QHeaderView::section {
    background-color: #2b2d31;
    color: #e3e5e8;
    padding: 6px;
    border: none;
    font-weight: bold;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #1e1f22;
    border: 1px solid #35373c;
    border-radius: 4px;
    padding: 6px;
    color: #f2f3f5;
}
QProgressBar {
    background-color: #1e1f22;
    border-radius: 4px;
    text-align: center;
    color: white;
}
QProgressBar::chunk {
    background-color: #5865f2;
    border-radius: 4px;
}
QFrame.Card {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    border-radius: 8px;
    padding: 12px;
}
"""


class MainWindow(QMainWindow):
    """Main application window with dashboard, devices, profiles, preview and execution."""

    def __init__(self, db: Optional[Database] = None):
        super().__init__()
        self.db = db or Database()
        self.device_repo = DeviceRepository(self.db)
        self.profile_repo = ProfileRepository(self.db)
        self.baseline_repo = BaselineRepository(self.db)
        self.history_repo = HistoryRepository(self.db)
        self.device_service = DeviceService()
        self.orchestrator = SyncOrchestrator(
            device_service=self.device_service,
            device_repo=self.device_repo,
            baseline_repo=self.baseline_repo,
            history_repo=self.history_repo
        )

        self.current_plan: List[PlannedOperation] = []

        self.setWindowTitle(tr("app_title"))
        self.resize(1150, 750)
        self.setStyleSheet(DARK_STYLESHEET)

        self._init_ui()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Left Navigation Sidebar
        nav_panel = QFrame()
        nav_panel.setObjectName("NavPanel")
        nav_panel.setFixedWidth(220)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(12, 20, 12, 20)
        nav_layout.setSpacing(8)

        title_lbl = QLabel("SyncForge 1.0")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #5865f2; margin-bottom: 12px;")
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
            ("settings", tr("settings")),
        ]

        for page_id, page_title in pages:
            btn = QPushButton(page_title)
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, pid=page_id: self._switch_page(pid))
            nav_layout.addWidget(btn)
            self.nav_buttons[page_id] = btn

        nav_layout.addStretch()

        # System Status in bottom of nav
        status_lbl = QLabel("● Ready")
        status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
        nav_layout.addWidget(status_lbl)

        main_layout.addWidget(nav_panel)

        # Right Stacked Pages
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        self._build_dashboard_page()
        self._build_devices_page()
        self._build_profiles_page()
        self._build_rules_page()
        self._build_preview_page()
        self._build_conflicts_page()
        self._build_history_page()
        self._build_settings_page()

        self._switch_page("dashboard")

    def _switch_page(self, page_id: str):
        for pid, btn in self.nav_buttons.items():
            btn.setChecked(pid == page_id)

        index_map = {
            "dashboard": 0, "devices": 1, "profiles": 2, "rules": 3,
            "preview": 4, "conflicts": 5, "history": 6, "settings": 7
        }
        self.stack.setCurrentIndex(index_map.get(page_id, 0))

        if page_id == "dashboard":
            self._refresh_dashboard()
        elif page_id == "devices":
            self._refresh_devices_table()
        elif page_id == "profiles":
            self._refresh_profiles_list()
        elif page_id == "history":
            self._refresh_history_table()

    # --- Page 1: Dashboard ---
    def _build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        header = QLabel(tr("dashboard"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        # Stat cards layout
        cards_layout = QHBoxLayout()
        self.card_profiles = self._create_card(tr("total_profiles"), "0")
        self.card_devices = self._create_card(tr("connected_devices"), "0")
        self.card_conflicts = self._create_card(tr("pending_conflicts"), "0")
        cards_layout.addWidget(self.card_profiles)
        cards_layout.addWidget(self.card_devices)
        cards_layout.addWidget(self.card_conflicts)
        layout.addLayout(cards_layout)

        # Quick action section
        action_box = QFrame()
        action_box.setProperty("class", "Card")
        ab_layout = QVBoxLayout(action_box)
        ab_title = QLabel(tr("quick_actions"))
        ab_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        ab_layout.addWidget(ab_title)

        ab_btns = QHBoxLayout()
        run_btn = QPushButton(tr("preview_plan"))
        run_btn.setProperty("class", "PrimaryBtn")
        run_btn.clicked.connect(lambda: self._switch_page("preview"))
        ab_btns.addWidget(run_btn)
        ab_layout.addLayout(ab_btns)
        layout.addWidget(action_box)

        layout.addStretch()
        self.stack.addWidget(page)

    def _create_card(self, title: str, val: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        l = QVBoxLayout(card)
        t = QLabel(title)
        t.setStyleSheet("color: #949ba4; font-size: 12px;")
        v = QLabel(val)
        v.setStyleSheet("font-size: 24px; font-weight: bold; color: #5865f2;")
        l.addWidget(t)
        l.addWidget(v)
        card.value_lbl = v
        return card

    def _refresh_dashboard(self):
        profs = self.profile_repo.list_profiles()
        devs = self.device_service.discover_connected_volumes()
        self.card_profiles.value_lbl.setText(str(len(profs)))
        self.card_devices.value_lbl.setText(str(len(devs)))
        confls = sum(1 for op in self.current_plan if op.action == PlanAction.CONFLICT)
        self.card_conflicts.value_lbl.setText(str(confls))

    # --- Page 2: Devices ---
    def _build_devices_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("devices"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        ref_btn = QPushButton(tr("refresh_devices"))
        ref_btn.clicked.connect(self._refresh_devices_table)
        enroll_btn = QPushButton(tr("enroll_device"))
        enroll_btn.setProperty("class", "PrimaryBtn")
        enroll_btn.clicked.connect(self._enroll_selected_device)
        btn_row.addWidget(ref_btn)
        btn_row.addWidget(enroll_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.dev_table = QTableWidget(0, 7)
        self.dev_table.setHorizontalHeaderLabels([
            tr("device_name"), tr("mount_point"), tr("volume_serial"), tr("volume_guid"),
            "Filesystem", "Free / Total", tr("status")
        ])
        self.dev_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.dev_table)

        self.stack.addWidget(page)

    def _refresh_devices_table(self):
        self.dev_table.setRowCount(0)
        volumes = self.device_service.discover_connected_volumes()
        enrolled = self.device_repo.list_all()
        enrolled_map = {e.device_id: e for e in enrolled}

        for vol in volumes:
            row = self.dev_table.rowCount()
            self.dev_table.insertRow(row)

            # Check verification
            is_enrolled = False
            for e in enrolled:
                ver, _, _ = self.device_service.verify_device(e, [vol])
                if ver:
                    is_enrolled = True
                    break

            size_str = f"{vol.free_bytes // (1024**3)} GB / {vol.total_bytes // (1024**3)} GB"
            self.dev_table.setItem(row, 0, QTableWidgetItem(vol.friendly_name))
            self.dev_table.setItem(row, 1, QTableWidgetItem(vol.current_mount or ""))
            self.dev_table.setItem(row, 2, QTableWidgetItem(vol.volume_serial or "N/A"))
            self.dev_table.setItem(row, 3, QTableWidgetItem(vol.volume_guid or "N/A"))
            self.dev_table.setItem(row, 4, QTableWidgetItem(vol.filesystem or "N/A"))
            self.dev_table.setItem(row, 5, QTableWidgetItem(size_str))
            status_item = QTableWidgetItem(tr("verified") if is_enrolled else tr("unverified"))
            status_item.setForeground(QColor("#57f287") if is_enrolled else QColor("#fee75c"))
            self.dev_table.setItem(row, 6, status_item)

    def _enroll_selected_device(self):
        curr_row = self.dev_table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a drive from the table first.")
            return

        mount = self.dev_table.item(curr_row, 1).text()
        name = self.dev_table.item(curr_row, 0).text()

        # Confirmation dialog for marker creation safety requirement
        reply = QMessageBox.question(
            self, "Device Enrollment",
            f"Enroll drive {mount} ('{name}') into SyncForge and write persistent identification marker?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                enrolled = self.device_service.enroll_device(mount, name, DeviceRole.PRIMARY_SYNC, create_marker=True)
                self.device_repo.save(enrolled)
                QMessageBox.information(self, "Success", f"Device {name} successfully enrolled!")
                self._refresh_devices_table()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to enroll device: {e}")

    # --- Page 3: Profiles ---
    def _build_profiles_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("profiles"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        # Profile controls
        top_bar = QHBoxLayout()
        create_btn = QPushButton(tr("create_profile"))
        create_btn.setProperty("class", "PrimaryBtn")
        create_btn.clicked.connect(self._create_new_profile_dialog)
        top_bar.addWidget(create_btn)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.profiles_table = QTableWidget(0, 5)
        self.profiles_table.setHorizontalHeaderLabels([
            tr("profile_name"), tr("sync_mode"), tr("dest_folder"), "Folders", "Actions"
        ])
        self.profiles_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.profiles_table)

        self.stack.addWidget(page)

    def _refresh_profiles_list(self):
        self.profiles_table.setRowCount(0)
        profs = self.profile_repo.list_profiles()
        for p in profs:
            row = self.profiles_table.rowCount()
            self.profiles_table.insertRow(row)
            self.profiles_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.profiles_table.setItem(row, 1, QTableWidgetItem(p.mode.value))
            self.profiles_table.setItem(row, 2, QTableWidgetItem(p.dest_root or "Enrolled Device"))
            self.profiles_table.setItem(row, 3, QTableWidgetItem(str(len(p.mappings))))
            
            # Action button
            run_btn = QPushButton("Dry-Run / Preview")
            run_btn.clicked.connect(lambda checked, prof=p: self._run_profile_preview(prof))
            self.profiles_table.setCellWidget(row, 4, run_btn)

    def _create_new_profile_dialog(self):
        # Quick modal or inline creation
        name, ok = QFileDialog.getExistingDirectory(self, "Select Source Directory for Profile"), True
        if name:
            import uuid
            p_id = str(uuid.uuid4())
            mapping = FolderMapping(id=str(uuid.uuid4()), profile_id=p_id, source_root=name, dest_subpath="Backup")
            prof = SyncProfile(
                id=p_id,
                name=f"Sync: {Path(name).name}",
                mode=SyncMode.ONE_WAY_SAFE,
                dest_root=None,
                mappings=[mapping]
            )
            # Pick destination directory
            dest_dir = QFileDialog.getExistingDirectory(self, "Select Destination Directory")
            if dest_dir:
                prof.dest_root = dest_dir
                self.profile_repo.save_profile(prof)
                self._refresh_profiles_list()

    def _run_profile_preview(self, profile: SyncProfile):
        try:
            self.current_plan = self.orchestrator.prepare_dry_run(profile)
            self.active_profile = profile
            self._populate_preview_table(self.current_plan)
            self._switch_page("preview")
        except Exception as e:
            QMessageBox.critical(self, "Error Generating Preview", str(e))

    # --- Page 4: Rules ---
    def _build_rules_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("rules"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        info = QLabel("Configured Exclusion and Routing Rules:")
        layout.addWidget(info)

        self.rules_table = QTableWidget(4, 3)
        self.rules_table.setHorizontalHeaderLabels(["Pattern", "Rule Type", "Target Subfolder"])
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Default active safety rules
        self.rules_table.setItem(0, 0, QTableWidgetItem("*.tmp"))
        self.rules_table.setItem(0, 1, QTableWidgetItem("Exclude"))
        self.rules_table.setItem(0, 2, QTableWidgetItem("—"))
        self.rules_table.setItem(1, 0, QTableWidgetItem("node_modules/*"))
        self.rules_table.setItem(1, 1, QTableWidgetItem("Exclude"))
        self.rules_table.setItem(1, 2, QTableWidgetItem("—"))
        self.rules_table.setItem(2, 0, QTableWidgetItem("target/*"))
        self.rules_table.setItem(2, 1, QTableWidgetItem("Exclude"))
        self.rules_table.setItem(2, 2, QTableWidgetItem("—"))
        self.rules_table.setItem(3, 0, QTableWidgetItem("*.pdf"))
        self.rules_table.setItem(3, 1, QTableWidgetItem("Route Folder"))
        self.rules_table.setItem(3, 2, QTableWidgetItem("Documents/PDF"))

        layout.addWidget(self.rules_table)
        self.stack.addWidget(page)

    # --- Page 5: Preview ---
    def _build_preview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("preview"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        top_ctrl = QHBoxLayout()
        exec_btn = QPushButton(tr("execute_plan"))
        exec_btn.setProperty("class", "PrimaryBtn")
        exec_btn.clicked.connect(self._execute_active_plan)
        top_ctrl.addWidget(exec_btn)
        top_ctrl.addStretch()
        layout.addLayout(top_ctrl)

        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels([
            tr("source_file"), tr("dest_file"), tr("action"), tr("size"), tr("reason")
        ])
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.preview_table)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.stack.addWidget(page)

    def _populate_preview_table(self, plan: List[PlannedOperation]):
        self.preview_table.setRowCount(0)
        for op in plan:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            self.preview_table.setItem(row, 0, QTableWidgetItem(op.source_abs or "—"))
            self.preview_table.setItem(row, 1, QTableWidgetItem(op.dest_abs or "—"))
            
            act_item = QTableWidgetItem(op.action.value.upper())
            if op.action == PlanAction.CONFLICT:
                act_item.setForeground(QColor("#ed4245"))
            elif op.action in (PlanAction.COPY, PlanAction.UPDATE):
                act_item.setForeground(QColor("#5865f2"))
            elif op.action == PlanAction.PRESERVE:
                act_item.setForeground(QColor("#fee75c"))
            self.preview_table.setItem(row, 2, act_item)

            self.preview_table.setItem(row, 3, QTableWidgetItem(f"{op.size_bytes} B"))
            self.preview_table.setItem(row, 4, QTableWidgetItem(op.reason))

    def _execute_active_plan(self):
        if not self.current_plan or not hasattr(self, "active_profile"):
            QMessageBox.warning(self, "Notice", "No plan available to execute. Generate a preview first.")
            return

        has_conflicts = any(op.action == PlanAction.CONFLICT and not op.conflict_resolution for op in self.current_plan)
        if has_conflicts:
            QMessageBox.critical(self, "Unresolved Conflicts", "Please resolve all conflicts in the Conflicts tab before proceeding.")
            self._switch_page("conflicts")
            return

        reply = QMessageBox.question(
            self, "Execute Synchronization",
            f"Are you sure you want to execute {len(self.current_plan)} planned operations?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            def on_progress(p):
                if p.total_operations > 0:
                    val = int((p.completed_operations / p.total_operations) * 100)
                    self.progress_bar.setValue(val)

            try:
                status = self.orchestrator.execute_sync(self.active_profile, self.current_plan, on_progress)
                QMessageBox.information(self, "Finished", f"Sync executed with status: {status.value}")
                self.progress_bar.setValue(100)
            except Exception as e:
                QMessageBox.critical(self, "Sync Failed", str(e))

    # --- Page 6: Conflicts ---
    def _build_conflicts_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("conflicts"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        self.conflicts_table = QTableWidget(0, 3)
        self.conflicts_table.setHorizontalHeaderLabels([tr("source_file"), "File Size / Date", "Resolution"])
        self.conflicts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.conflicts_table)

        btn_box = QHBoxLayout()
        keep_src_btn = QPushButton(tr("keep_source"))
        keep_src_btn.clicked.connect(lambda: self._resolve_conflict(ConflictResolution.KEEP_SOURCE))
        keep_dst_btn = QPushButton(tr("keep_dest"))
        keep_dst_btn.clicked.connect(lambda: self._resolve_conflict(ConflictResolution.KEEP_DEST))
        keep_both_btn = QPushButton(tr("keep_both"))
        keep_both_btn.clicked.connect(lambda: self._resolve_conflict(ConflictResolution.KEEP_BOTH))
        skip_btn = QPushButton(tr("skip"))
        skip_btn.clicked.connect(lambda: self._resolve_conflict(ConflictResolution.SKIP))

        btn_box.addWidget(keep_src_btn)
        btn_box.addWidget(keep_dst_btn)
        btn_box.addWidget(keep_both_btn)
        btn_box.addWidget(skip_btn)
        layout.addLayout(btn_box)

        self.stack.addWidget(page)

    def _resolve_conflict(self, res: ConflictResolution):
        curr_row = self.conflicts_table.currentRow()
        if curr_row < 0:
            return
        # Set resolution on selected conflict op
        confls = [op for op in self.current_plan if op.action == PlanAction.CONFLICT]
        if curr_row < len(confls):
            confls[curr_row].conflict_resolution = res
            self.conflicts_table.setItem(curr_row, 2, QTableWidgetItem(res.value))
            QMessageBox.information(self, "Resolution", f"Resolution '{res.value}' applied.")

    # --- Page 7: History ---
    def _build_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("history"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels([
            tr("job_time"), "Profile", tr("job_status"), tr("files_count"), tr("bytes_count")
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.history_table)

        self.stack.addWidget(page)

    def _refresh_history_table(self):
        self.history_table.setRowCount(0)
        jobs = self.history_repo.list_jobs(50)
        for j in jobs:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(str(j.get("started_at"))))
            self.history_table.setItem(row, 1, QTableWidgetItem(j.get("profile_id", "")))
            self.history_table.setItem(row, 2, QTableWidgetItem(j.get("status", "")))
            self.history_table.setItem(row, 3, QTableWidgetItem(str(j.get("files_processed", 0))))
            self.history_table.setItem(row, 4, QTableWidgetItem(f"{j.get('bytes_transferred', 0)} B"))

    # --- Page 8: Settings ---
    def _build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(tr("settings"))
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        lang_lbl = QLabel(tr("language"))
        layout.addWidget(lang_lbl)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Українська (Ukrainian)", "uk")
        self.lang_combo.addItem("English", "en")
        layout.addWidget(self.lang_combo)

        save_btn = QPushButton(tr("save_settings"))
        save_btn.setProperty("class", "PrimaryBtn")
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        layout.addStretch()
        self.stack.addWidget(page)

    def _save_settings(self):
        lang = self.lang_combo.currentData()
        set_language(lang)
        QMessageBox.information(self, "Settings", "Settings saved successfully! Restart application to reapply all strings.")
