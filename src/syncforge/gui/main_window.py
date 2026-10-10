"""Production-grade PySide6 GUI for SyncForge."""

import sys
import uuid
from pathlib import Path
from typing import Optional, List
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QIcon, QFont, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QLineEdit, QComboBox, QMessageBox, QProgressBar,
    QFrame, QSplitter, QCheckBox, QSpinBox, QGroupBox, QMenu
)
from syncforge.core.models import (
    SyncProfile, FolderMapping, SyncMode, PlanAction, PlannedOperation,
    ExecutionStatus, DeviceRole, ConflictResolution, DeviceInfo
)
from syncforge.storage.database import Database
from syncforge.storage.repositories import (
    ProfileRepository, DeviceRepository, BaselineRepository, HistoryRepository,
    RuleRepository, SettingsRepository
)
from syncforge.devices.device_service import DeviceService
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.rules.rule_engine import RuleEngine, Rule, RuleType
from syncforge.i18n.translations import tr, set_language, format_bytes, CURRENT_LANG
from syncforge.gui.dialogs import ProfileEditorDialog, RuleEditorDialog

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
QPushButton {
    background-color: #2b2d31;
    color: #f2f3f5;
    border: 1px solid #35373c;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #35373c;
    border-color: #4e5058;
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
QPushButton.DangerBtn:hover {
    background-color: #a1282c;
}
QTableWidget {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    gridline-color: #2b2d31;
    selection-background-color: #35373c;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #2b2d31;
    color: #e3e5e8;
    padding: 7px;
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
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #5865f2;
}
QComboBox QAbstractItemView {
    background-color: #1e1f22;
    border: 1px solid #35373c;
    selection-background-color: #5865f2;
    color: #f2f3f5;
}
QProgressBar {
    background-color: #1e1f22;
    border-radius: 4px;
    text-align: center;
    color: white;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #5865f2;
    border-radius: 4px;
}
QFrame.Card {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    border-radius: 8px;
    padding: 14px;
}
QGroupBox {
    border: 1px solid #2b2d31;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #949ba4;
}
"""

LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #f2f3f5;
    color: #2e3338;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#NavPanel {
    background-color: #e3e5e8;
    border-right: 1px solid #d1d4d7;
}
QPushButton#NavBtn {
    background-color: transparent;
    color: #4e5058;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-weight: 500;
}
QPushButton#NavBtn:hover {
    background-color: #d1d4d7;
    color: #060607;
}
QPushButton#NavBtn:checked {
    background-color: #ffffff;
    color: #5865f2;
    font-weight: bold;
}
QPushButton {
    background-color: #ffffff;
    color: #2e3338;
    border: 1px solid #c7ccd1;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #e3e5e8;
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
QTableWidget {
    background-color: #ffffff;
    border: 1px solid #d1d4d7;
    gridline-color: #e3e5e8;
    selection-background-color: #e3e5e8;
    selection-color: #060607;
}
QHeaderView::section {
    background-color: #e3e5e8;
    color: #2e3338;
    padding: 7px;
    border: none;
    font-weight: bold;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #ffffff;
    border: 1px solid #c7ccd1;
    border-radius: 4px;
    padding: 6px;
    color: #2e3338;
}
QFrame.Card {
    background-color: #ffffff;
    border: 1px solid #d1d4d7;
    border-radius: 8px;
    padding: 14px;
}
"""


class MainWindow(QMainWindow):
    """Main desktop application window."""

    def __init__(self, db: Optional[Database] = None):
        super().__init__()
        self.db = db or Database()
        self.device_repo = DeviceRepository(self.db)
        self.profile_repo = ProfileRepository(self.db)
        self.baseline_repo = BaselineRepository(self.db)
        self.history_repo = HistoryRepository(self.db)
        self.rule_repo = RuleRepository(self.db)
        self.settings_repo = SettingsRepository(self.db)
        self.device_service = DeviceService()
        self.orchestrator = SyncOrchestrator(
            device_service=self.device_service,
            device_repo=self.device_repo,
            baseline_repo=self.baseline_repo,
            history_repo=self.history_repo
        )

        self.current_plan: List[PlannedOperation] = []
        self.active_profile: Optional[SyncProfile] = None

        # Load saved settings
        saved_lang = self.settings_repo.get("language", "uk")
        set_language(saved_lang)
        self.is_dark_theme = self.settings_repo.get("theme", "dark") == "dark"

        self.setWindowTitle(tr("app_title"))
        self.resize(1180, 780)
        self.setStyleSheet(DARK_STYLESHEET if self.is_dark_theme else LIGHT_STYLESHEET)

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

        self.status_lbl = QLabel(tr("status_ready"))
        self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
        nav_layout.addWidget(self.status_lbl)

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
        elif page_id == "rules":
            self._refresh_rules_table()
        elif page_id == "conflicts":
            self._refresh_conflicts_table()
        elif page_id == "history":
            self._refresh_history_table()

    # --- Page 1: Dashboard ---
    def _build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(22)

        header = QLabel(tr("dashboard"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        cards_layout = QHBoxLayout()
        self.card_profiles = self._create_card(tr("total_profiles"), "0")
        self.card_devices = self._create_card(tr("connected_devices"), "0")
        self.card_conflicts = self._create_card(tr("pending_conflicts"), "0")
        cards_layout.addWidget(self.card_profiles)
        cards_layout.addWidget(self.card_devices)
        cards_layout.addWidget(self.card_conflicts)
        layout.addLayout(cards_layout)

        # Quick Actions Card
        action_box = QFrame()
        action_box.setProperty("class", "Card")
        ab_layout = QVBoxLayout(action_box)
        ab_title = QLabel(tr("quick_actions"))
        ab_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        ab_layout.addWidget(ab_title)

        ab_btns = QHBoxLayout()
        quick_prof_btn = QPushButton("+ " + tr("create_profile"))
        quick_prof_btn.setProperty("class", "PrimaryBtn")
        quick_prof_btn.clicked.connect(self._create_new_profile_dialog)
        
        quick_preview_btn = QPushButton(tr("preview_plan"))
        quick_preview_btn.clicked.connect(lambda: self._switch_page("profiles"))
        
        ab_btns.addWidget(quick_prof_btn)
        ab_btns.addWidget(quick_preview_btn)
        ab_btns.addStretch()
        ab_layout.addLayout(ab_btns)
        layout.addWidget(action_box)

        layout.addStretch()
        self.stack.addWidget(page)

    def _create_card(self, title: str, val: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        l = QVBoxLayout(card)
        t = QLabel(title)
        t.setStyleSheet("color: #949ba4; font-size: 13px;")
        v = QLabel(val)
        v.setStyleSheet("font-size: 26px; font-weight: bold; color: #5865f2;")
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
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("devices"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
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
            tr("filesystem"), tr("free_total_space"), tr("status")
        ])
        self.dev_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.dev_table)

        self.stack.addWidget(page)

    def _refresh_devices_table(self):
        self.dev_table.setRowCount(0)
        volumes = self.device_service.discover_connected_volumes()
        enrolled = self.device_repo.list_all()

        for vol in volumes:
            row = self.dev_table.rowCount()
            self.dev_table.insertRow(row)

            is_enrolled = False
            for e in enrolled:
                ver, _, _ = self.device_service.verify_device(e, [vol])
                if ver:
                    is_enrolled = True
                    break

            size_str = f"{format_bytes(vol.free_bytes)} / {format_bytes(vol.total_bytes)}"
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
            QMessageBox.warning(self, "Попередження", tr("select_drive_first"))
            return

        mount = self.dev_table.item(curr_row, 1).text()
        name = self.dev_table.item(curr_row, 0).text()

        reply = QMessageBox.question(
            self, tr("enroll_confirm_title"),
            tr("enroll_confirm_msg").format(mount=mount, name=name),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                enrolled = self.device_service.enroll_device(mount, name, DeviceRole.PRIMARY_SYNC, create_marker=True)
                self.device_repo.save(enrolled)
                QMessageBox.information(self, "Успіх", tr("enroll_success").format(name=name))
                self._refresh_devices_table()
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Не вдалося зареєструвати пристрій: {e}")

    # --- Page 3: Profiles ---
    def _build_profiles_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("profiles"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        top_bar = QHBoxLayout()
        create_btn = QPushButton("+ " + tr("create_profile"))
        create_btn.setProperty("class", "PrimaryBtn")
        create_btn.clicked.connect(self._create_new_profile_dialog)
        top_bar.addWidget(create_btn)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.profiles_table = QTableWidget(0, 5)
        self.profiles_table.setHorizontalHeaderLabels([
            tr("profile_name"), tr("sync_mode"), tr("dest_folder"), tr("folders_count"), tr("actions")
        ])
        self.profiles_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.profiles_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.profiles_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.profiles_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.profiles_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.profiles_table)

        self.stack.addWidget(page)

    def _get_friendly_mode_title(self, mode: SyncMode) -> str:
        if mode == SyncMode.ONE_WAY_SAFE:
            return tr("mode_one_way")
        elif mode == SyncMode.BIDIRECTIONAL:
            return tr("mode_bidirectional")
        elif mode == SyncMode.VERSIONED_BACKUP:
            return tr("mode_backup")
        return mode.value

    def _refresh_profiles_list(self):
        self.profiles_table.setRowCount(0)
        profs = self.profile_repo.list_profiles()
        devices = self.device_repo.list_all()
        dev_map = {d.device_id: d.friendly_name for d in devices}

        for p in profs:
            row = self.profiles_table.rowCount()
            self.profiles_table.insertRow(row)
            self.profiles_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.profiles_table.setItem(row, 1, QTableWidgetItem(self._get_friendly_mode_title(p.mode)))
            
            dest_display = p.dest_root or (dev_map.get(p.target_device_id, "Зареєстрований SSD"))
            self.profiles_table.setItem(row, 2, QTableWidgetItem(dest_display))
            self.profiles_table.setItem(row, 3, QTableWidgetItem(str(len(p.mappings))))

            # Actions widget with buttons
            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(4, 2, 4, 2)
            act_layout.setSpacing(6)

            run_btn = QPushButton(tr("dry_run_btn"))
            run_btn.setProperty("class", "PrimaryBtn")
            run_btn.clicked.connect(lambda checked, prof=p: self._run_profile_preview(prof))

            edit_btn = QPushButton(tr("edit_profile"))
            edit_btn.clicked.connect(lambda checked, prof=p: self._edit_profile_dialog(prof))

            del_btn = QPushButton(tr("delete_profile"))
            del_btn.setProperty("class", "DangerBtn")
            del_btn.clicked.connect(lambda checked, prof=p: self._delete_profile(prof))

            act_layout.addWidget(run_btn)
            act_layout.addWidget(edit_btn)
            act_layout.addWidget(del_btn)
            self.profiles_table.setCellWidget(row, 4, act_widget)

    def _create_new_profile_dialog(self):
        devs = self.device_service.discover_connected_volumes()
        dialog = ProfileEditorDialog(self, profile=None, devices=devs)
        if dialog.exec():
            new_prof = dialog.result_profile
            if new_prof:
                self.profile_repo.save_profile(new_prof)
                self._refresh_profiles_list()

    def _edit_profile_dialog(self, profile: SyncProfile):
        devs = self.device_service.discover_connected_volumes()
        dialog = ProfileEditorDialog(self, profile=profile, devices=devs)
        if dialog.exec():
            updated_prof = dialog.result_profile
            if updated_prof:
                self.profile_repo.save_profile(updated_prof)
                self._refresh_profiles_list()

    def _delete_profile(self, profile: SyncProfile):
        reply = QMessageBox.question(
            self, tr("delete_profile"),
            tr("confirm_delete_profile").format(name=profile.name),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.profile_repo.delete_profile(profile.id)
            self._refresh_profiles_list()

    def _run_profile_preview(self, profile: SyncProfile):
        try:
            active_rules = self.rule_repo.list_rules(profile.id)
            engine = RuleEngine(active_rules)
            self.current_plan = self.orchestrator.prepare_dry_run(profile, rule_engine=engine)
            self.active_profile = profile
            self._populate_preview_table(self.current_plan)
            self._switch_page("preview")
        except Exception as e:
            QMessageBox.critical(self, "Помилка формування плану", str(e))

    # --- Page 4: Rules ---
    def _build_rules_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("rules_title"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        add_rule_btn = QPushButton("+ " + tr("add_rule"))
        add_rule_btn.setProperty("class", "PrimaryBtn")
        add_rule_btn.clicked.connect(self._add_rule_dialog)

        edit_rule_btn = QPushButton(tr("edit_rule"))
        edit_rule_btn.clicked.connect(self._edit_selected_rule)

        del_rule_btn = QPushButton(tr("delete_rule"))
        del_rule_btn.setProperty("class", "DangerBtn")
        del_rule_btn.clicked.connect(self._delete_selected_rule)

        up_btn = QPushButton(tr("move_up"))
        up_btn.clicked.connect(lambda: self._adjust_rule_priority(10))

        down_btn = QPushButton(tr("move_down"))
        down_btn.clicked.connect(lambda: self._adjust_rule_priority(-10))

        btn_row.addWidget(add_rule_btn)
        btn_row.addWidget(edit_rule_btn)
        btn_row.addWidget(del_rule_btn)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rules_table = QTableWidget(0, 5)
        self.rules_table.setHorizontalHeaderLabels([
            tr("rule_pattern"), tr("rule_type"), tr("rule_target"), tr("rule_priority"), tr("rule_enabled")
        ])
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.rules_table)

        self.stack.addWidget(page)

    def _refresh_rules_table(self):
        self.rules_table.setRowCount(0)
        rules = self.rule_repo.list_rules()
        for r in rules:
            row = self.rules_table.rowCount()
            self.rules_table.insertRow(row)
            self.rules_table.setItem(row, 0, QTableWidgetItem(r.pattern))
            
            # Localized rule type label
            type_lbl = r.rule_type.value
            if r.rule_type == RuleType.EXCLUDE_GLOB:
                type_lbl = tr("rule_type_exclude_glob")
            elif r.rule_type == RuleType.INCLUDE_GLOB:
                type_lbl = tr("rule_type_include_glob")
            elif r.rule_type == RuleType.MAX_SIZE:
                type_lbl = tr("rule_type_max_size")
            elif r.rule_type == RuleType.MIN_SIZE:
                type_lbl = tr("rule_type_min_size")
            elif r.rule_type in (RuleType.ROUTE, RuleType.ROUTE_FOLDER):
                type_lbl = tr("rule_type_route")

            self.rules_table.setItem(row, 1, QTableWidgetItem(type_lbl))
            self.rules_table.setItem(row, 2, QTableWidgetItem(r.target_subfolder or "—"))
            self.rules_table.setItem(row, 3, QTableWidgetItem(str(r.priority)))
            self.rules_table.setItem(row, 4, QTableWidgetItem("Так" if r.enabled else "Ні"))
            self.rules_table.item(row, 0).setData(Qt.UserRole, r)

    def _add_rule_dialog(self):
        d = RuleEditorDialog(self)
        if d.exec() and d.result_rule:
            self.rule_repo.save_rule(d.result_rule)
            self._refresh_rules_table()

    def _edit_selected_rule(self):
        curr_row = self.rules_table.currentRow()
        if curr_row < 0:
            return
        item = self.rules_table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        d = RuleEditorDialog(self, rule=rule)
        if d.exec() and d.result_rule:
            self.rule_repo.save_rule(d.result_rule)
            self._refresh_rules_table()

    def _delete_selected_rule(self):
        curr_row = self.rules_table.currentRow()
        if curr_row < 0:
            return
        item = self.rules_table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        reply = QMessageBox.question(self, tr("delete_rule"), tr("confirm_delete_rule"), QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.rule_repo.delete_rule(rule.id)
            self._refresh_rules_table()

    def _adjust_rule_priority(self, delta: int):
        curr_row = self.rules_table.currentRow()
        if curr_row < 0:
            return
        item = self.rules_table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        rule.priority = max(0, rule.priority + delta)
        self.rule_repo.save_rule(rule)
        self._refresh_rules_table()

    # --- Page 5: Preview ---
    def _build_preview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("preview"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        top_ctrl = QHBoxLayout()
        exec_btn = QPushButton(tr("execute_plan"))
        exec_btn.setProperty("class", "PrimaryBtn")
        exec_btn.clicked.connect(self._execute_active_plan)
        top_ctrl.addWidget(exec_btn)

        # Action Filter Dropdown
        self.action_filter_combo = QComboBox()
        self.action_filter_combo.addItem(tr("filter_all"), None)
        self.action_filter_combo.addItem(tr("action_copy"), PlanAction.COPY)
        self.action_filter_combo.addItem(tr("action_update"), PlanAction.UPDATE)
        self.action_filter_combo.addItem(tr("action_preserve"), PlanAction.PRESERVE)
        self.action_filter_combo.addItem(tr("action_conflict"), PlanAction.CONFLICT)
        self.action_filter_combo.currentIndexChanged.connect(self._filter_preview_table)
        top_ctrl.addWidget(self.action_filter_combo)

        # Search box
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder"))
        self.search_edit.textChanged.connect(self._filter_preview_table)
        top_ctrl.addWidget(self.search_edit)

        top_ctrl.addStretch()
        layout.addLayout(top_ctrl)

        # Table & Summary
        self.summary_lbl = QLabel(tr("no_plan_notice"))
        self.summary_lbl.setStyleSheet("color: #949ba4; font-weight: 500;")
        layout.addWidget(self.summary_lbl)

        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels([
            tr("source_file"), tr("dest_file"), tr("action"), tr("size"), tr("reason")
        ])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.preview_table)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.stack.addWidget(page)

    def _populate_preview_table(self, plan: List[PlannedOperation]):
        total_bytes = sum(op.size_bytes for op in plan if op.action in (PlanAction.COPY, PlanAction.UPDATE))
        free_space = "N/A"
        if self.active_profile and self.active_profile.dest_root:
            try:
                import shutil
                free_b = shutil.disk_usage(self.active_profile.dest_root).free
                free_space = format_bytes(free_b)
            except Exception:
                pass

        self.summary_lbl.setText(
            tr("summary_plan").format(count=len(plan), size=format_bytes(total_bytes), free=free_space)
        )
        self._filter_preview_table()

    def _filter_preview_table(self):
        self.preview_table.setRowCount(0)
        action_filter = self.action_filter_combo.currentData()
        search_query = self.search_edit.text().strip().lower()

        for op in self.current_plan:
            if action_filter and op.action != action_filter:
                continue
            if search_query and (search_query not in op.rel_path.lower()):
                continue

            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            self.preview_table.setItem(row, 0, QTableWidgetItem(op.source_abs or "—"))
            self.preview_table.setItem(row, 1, QTableWidgetItem(op.dest_abs or "—"))

            act_str = op.action.value.upper()
            color = "#f2f3f5"
            if op.action == PlanAction.CONFLICT:
                act_str = tr("action_conflict")
                color = "#ed4245"
            elif op.action == PlanAction.COPY:
                act_str = tr("action_copy")
                color = "#5865f2"
            elif op.action == PlanAction.UPDATE:
                act_str = tr("action_update")
                color = "#5865f2"
            elif op.action == PlanAction.PRESERVE:
                act_str = tr("action_preserve")
                color = "#fee75c"
            elif op.action == PlanAction.SKIP:
                act_str = tr("action_skip")
                color = "#949ba4"

            act_item = QTableWidgetItem(act_str)
            act_item.setForeground(QColor(color))
            self.preview_table.setItem(row, 2, act_item)
            self.preview_table.setItem(row, 3, QTableWidgetItem(format_bytes(op.size_bytes)))
            self.preview_table.setItem(row, 4, QTableWidgetItem(op.reason))

    def _execute_active_plan(self):
        if not self.current_plan or not self.active_profile:
            QMessageBox.warning(self, "Повідомлення", tr("no_plan_notice"))
            return

        has_conflicts = any(op.action == PlanAction.CONFLICT and not op.conflict_resolution for op in self.current_plan)
        if has_conflicts:
            QMessageBox.critical(self, "Невирішені конфлікти", tr("unresolved_conflicts_err"))
            self._switch_page("conflicts")
            return

        reply = QMessageBox.question(
            self, "Підтвердження",
            tr("confirm_execute").format(count=len(self.current_plan), name=self.active_profile.name),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.status_lbl.setText(tr("status_syncing"))
            self.status_lbl.setStyleSheet("color: #fee75c; font-weight: bold; padding: 6px;")

            def on_progress(p):
                if p.total_operations > 0:
                    val = int((p.completed_operations / p.total_operations) * 100)
                    self.progress_bar.setValue(val)

            try:
                status = self.orchestrator.execute_sync(self.active_profile, self.current_plan, on_progress)
                self.progress_bar.setValue(100)
                self.status_lbl.setText(tr("status_ready"))
                self.status_lbl.setStyleSheet("color: #57f287; font-weight: bold; padding: 6px;")
                QMessageBox.information(self, "Готово", tr("sync_completed").format(status=status.value))
            except Exception as e:
                self.status_lbl.setText(tr("status_error"))
                self.status_lbl.setStyleSheet("color: #ed4245; font-weight: bold; padding: 6px;")
                QMessageBox.critical(self, "Помилка синхронізації", str(e))

    # --- Page 6: Conflicts ---
    def _build_conflicts_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("conflicts_title"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        info = QLabel(tr("conflict_instruction"))
        info.setStyleSheet("color: #949ba4;")
        layout.addWidget(info)

        self.conflicts_table = QTableWidget(0, 3)
        self.conflicts_table.setHorizontalHeaderLabels([tr("source_file"), "Розмір / Час", tr("resolution")])
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
        btn_box.addStretch()
        layout.addLayout(btn_box)

        self.stack.addWidget(page)

    def _refresh_conflicts_table(self):
        self.conflicts_table.setRowCount(0)
        confls = [op for op in self.current_plan if op.action == PlanAction.CONFLICT]
        for op in confls:
            row = self.conflicts_table.rowCount()
            self.conflicts_table.insertRow(row)
            self.conflicts_table.setItem(row, 0, QTableWidgetItem(op.rel_path))
            self.conflicts_table.setItem(row, 1, QTableWidgetItem(f"{format_bytes(op.size_bytes)}"))
            res_str = op.conflict_resolution.value if op.conflict_resolution else "Очікує вибору"
            self.conflicts_table.setItem(row, 2, QTableWidgetItem(res_str))

    def _resolve_conflict(self, res: ConflictResolution):
        curr_row = self.conflicts_table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Попередження", "Виберіть рядок конфлікту з таблиці.")
            return

        confls = [op for op in self.current_plan if op.action == PlanAction.CONFLICT]
        if curr_row < len(confls):
            confls[curr_row].conflict_resolution = res
            self._refresh_conflicts_table()

    # --- Page 7: History ---
    def _build_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("history"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels([
            tr("job_time"), tr("history_profile"), tr("job_status"), tr("files_count"), tr("bytes_count")
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
            
            st_item = QTableWidgetItem(j.get("status", "").upper())
            st_item.setForeground(QColor("#57f287") if j.get("status") == "success" else QColor("#ed4245"))
            self.history_table.setItem(row, 2, st_item)
            self.history_table.setItem(row, 3, QTableWidgetItem(str(j.get("files_processed", 0))))
            self.history_table.setItem(row, 4, QTableWidgetItem(format_bytes(j.get("bytes_transferred", 0))))

    # --- Page 8: Settings ---
    def _build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        header = QLabel(tr("settings_title"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        group = QGroupBox("Параметри інтерфейсу та продуктивності")
        g_layout = QVBoxLayout(group)

        # Language Selection Dropdown
        lang_row = QHBoxLayout()
        lang_lbl = QLabel(tr("language"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Українська (Ukrainian)", "uk")
        self.lang_combo.addItem("English", "en")
        idx = self.lang_combo.findData(CURRENT_LANG)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        lang_row.addWidget(lang_lbl)
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch()
        g_layout.addLayout(lang_row)

        # Theme Selection Dropdown
        theme_row = QHBoxLayout()
        theme_lbl = QLabel(tr("theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("theme_dark"), "dark")
        self.theme_combo.addItem(tr("theme_light"), "light")
        self.theme_combo.setCurrentIndex(0 if self.is_dark_theme else 1)
        theme_row.addWidget(theme_lbl)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()
        g_layout.addLayout(theme_row)

        # Buffer size dropdown
        buf_row = QHBoxLayout()
        buf_lbl = QLabel(tr("buffer_size"))
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItem("1 MiB (Швидко для SSD)", 1024*1024)
        self.buffer_combo.addItem("4 MiB (Оптимально для великих файлів)", 4*1024*1024)
        self.buffer_combo.addItem("512 KiB (Економія пам'яті)", 512*1024)
        buf_row.addWidget(buf_lbl)
        buf_row.addWidget(self.buffer_combo)
        buf_row.addStretch()
        g_layout.addLayout(buf_row)

        # Checkboxes
        self.notif_cb = QCheckBox(tr("enable_notifications"))
        self.notif_cb.setChecked(self.settings_repo.get("notifications", "1") == "1")
        g_layout.addWidget(self.notif_cb)

        self.autostart_cb = QCheckBox(tr("autostart"))
        self.autostart_cb.setChecked(self.settings_repo.get("autostart", "0") == "1")
        g_layout.addWidget(self.autostart_cb)

        self.tray_cb = QCheckBox(tr("minimize_tray"))
        self.tray_cb.setChecked(self.settings_repo.get("minimize_tray", "1") == "1")
        g_layout.addWidget(self.tray_cb)

        layout.addWidget(group)

        # Buttons
        ctrl_row = QHBoxLayout()
        save_btn = QPushButton(tr("save_settings"))
        save_btn.setProperty("class", "PrimaryBtn")
        save_btn.clicked.connect(self._save_settings)

        clear_db_btn = QPushButton(tr("clear_database"))
        clear_db_btn.setProperty("class", "DangerBtn")
        clear_db_btn.clicked.connect(self._clear_db_history)

        ctrl_row.addWidget(save_btn)
        ctrl_row.addWidget(clear_db_btn)
        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        layout.addStretch()
        self.stack.addWidget(page)

    def _save_settings(self):
        new_lang = self.lang_combo.currentData()
        new_theme = self.theme_combo.currentData()

        self.settings_repo.set("language", new_lang)
        self.settings_repo.set("theme", new_theme)
        self.settings_repo.set("notifications", "1" if self.notif_cb.isChecked() else "0")
        self.settings_repo.set("autostart", "1" if self.autostart_cb.isChecked() else "0")
        self.settings_repo.set("minimize_tray", "1" if self.tray_cb.isChecked() else "0")

        set_language(new_lang)
        self.is_dark_theme = (new_theme == "dark")
        self.setStyleSheet(DARK_STYLESHEET if self.is_dark_theme else LIGHT_STYLESHEET)

        QMessageBox.information(self, "Налаштування", tr("settings_saved"))

    def _clear_db_history(self):
        reply = QMessageBox.question(
            self, tr("clear_database"), tr("confirm_clear_db"), QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.settings_repo.clear_all_history()
            self._refresh_history_table()
            QMessageBox.information(self, "Успіх", "Історію успішно очищено.")
