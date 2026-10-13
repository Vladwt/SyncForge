"""Modal dialogs for creating and editing synchronization profiles and rules."""

import uuid
from pathlib import Path
from typing import Optional, List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QGroupBox, QFormLayout
)
from syncforge.core.models import SyncProfile, FolderMapping, SyncMode, DeviceInfo, DeletionPolicy, AnalysisMode
from syncforge.core.path_validator import PathValidator
from syncforge.rules.rule_engine import Rule, RuleType
from syncforge.i18n.translations import tr


class ProfileEditorDialog(QDialog):
    """Full-featured profile creation and editing modal."""

    def __init__(self, parent=None, profile: Optional[SyncProfile] = None, devices: Optional[List[DeviceInfo]] = None):
        super().__init__(parent)
        self.profile = profile
        self.devices = devices or []
        self.result_profile: Optional[SyncProfile] = None

        self.setWindowTitle(tr("profile_editor_title"))
        self.resize(720, 620)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # 1. Basic Profile Info Group
        basic_group = QGroupBox(tr("profile_basic_settings"))
        basic_layout = QFormLayout(basic_group)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Наприклад: Робочі проекти на SSD")
        if self.profile:
            self.name_edit.setText(self.profile.name)
        basic_layout.addRow(tr("profile_name") + ":", self.name_edit)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("mode_one_way"), SyncMode.ONE_WAY_SAFE)
        self.mode_combo.addItem(tr("mode_bidirectional"), SyncMode.BIDIRECTIONAL)
        self.mode_combo.addItem(tr("mode_backup"), SyncMode.VERSIONED_BACKUP)
        if self.profile:
            idx = self.mode_combo.findData(self.profile.mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
        basic_layout.addRow(tr("sync_mode") + ":", self.mode_combo)

        self.deletion_policy_combo = QComboBox()
        self.deletion_policy_combo.addItem(tr("policy_preserve_all"), DeletionPolicy.PRESERVE_ALL)
        self.deletion_policy_combo.addItem(tr("policy_ask_user"), DeletionPolicy.ASK_USER)
        self.deletion_policy_combo.addItem(tr("policy_archive_copy"), DeletionPolicy.ARCHIVE_COPY)
        self.deletion_policy_combo.addItem(tr("policy_restore_deleted"), DeletionPolicy.RESTORE_DELETED)
        self.deletion_policy_combo.addItem(tr("policy_sync_delete"), DeletionPolicy.SYNC_DELETE)

        cur_policy = getattr(self.profile, "deletion_policy", DeletionPolicy.PRESERVE_ALL) if self.profile else DeletionPolicy.PRESERVE_ALL
        if isinstance(cur_policy, str):
            try:
                cur_policy = DeletionPolicy(cur_policy)
            except Exception:
                cur_policy = DeletionPolicy.PRESERVE_ALL
        p_idx = self.deletion_policy_combo.findData(cur_policy)
        if p_idx >= 0:
            self.deletion_policy_combo.setCurrentIndex(p_idx)
        basic_layout.addRow(tr("deletion_policy") + ":", self.deletion_policy_combo)

        layout.addWidget(basic_group)

        # 2. Source Folders (Where to copy from on PC)
        mappings_group = QGroupBox(tr("source_folders"))
        mappings_layout = QVBoxLayout(mappings_group)

        table_ctrls = QHBoxLayout()
        add_map_btn = QPushButton("+ " + tr("add_mapping"))
        add_map_btn.setProperty("class", "PrimaryBtn")
        add_map_btn.clicked.connect(self._add_mapping)
        rem_map_btn = QPushButton("- " + tr("remove_mapping"))
        rem_map_btn.clicked.connect(self._remove_mapping)
        table_ctrls.addWidget(add_map_btn)
        table_ctrls.addWidget(rem_map_btn)
        table_ctrls.addStretch()
        mappings_layout.addLayout(table_ctrls)

        self.mappings_table = QTableWidget(0, 2)
        self.mappings_table.setHorizontalHeaderLabels([tr("source_path"), tr("dest_subpath")])
        self.mappings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.mappings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.mappings_table.setMinimumHeight(130)
        mappings_layout.addWidget(self.mappings_table)

        if self.profile:
            for m in self.profile.mappings:
                self._insert_mapping_row(m.source_root, m.dest_subpath)

        layout.addWidget(mappings_group)

        # 3. Target Destination (Where to save: Enrolled Device or Local Folder)
        dest_group = QGroupBox(tr("dest_section_title"))
        dest_layout = QFormLayout(dest_group)

        self.device_combo = QComboBox()
        self.device_combo.addItem(tr("dest_device_none"), None)
        for d in self.devices:
            mount_txt = f"[{d.current_mount}] " if d.current_mount else ""
            self.device_combo.addItem(f"{mount_txt}{d.friendly_name} ({d.filesystem or 'Диск'})", d.device_id)

        if self.profile and self.profile.target_device_id:
            idx = self.device_combo.findData(self.profile.target_device_id)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        dest_layout.addRow(tr("dest_device") + ":", self.device_combo)

        # Fallback local destination path with Browse button
        dest_box = QHBoxLayout()
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("Шлях до папки, напр. D:\\Backup (якщо накопичувач не обрано)")
        if self.profile and self.profile.dest_root:
            self.dest_edit.setText(self.profile.dest_root)
        self.dest_edit.textEdited.connect(self._on_dest_text_edited)
        browse_dest_btn = QPushButton(tr("browse"))
        browse_dest_btn.clicked.connect(self._browse_dest)
        dest_box.addWidget(self.dest_edit)
        dest_box.addWidget(browse_dest_btn)
        dest_layout.addRow(tr("dest_folder") + ":", dest_box)

        layout.addWidget(dest_group)

        # 4. Advanced Settings & Automation
        adv_group = QGroupBox(tr("adv_settings_title"))
        adv_layout = QVBoxLayout(adv_group)

        self.preserve_versions_cb = QCheckBox(tr("preserve_versions"))
        self.preserve_versions_cb.setChecked(self.profile.preserve_versions if self.profile else True)
        adv_layout.addWidget(self.preserve_versions_cb)

        self.verify_hashes_cb = QCheckBox(tr("verify_hashes_copy"))
        self.verify_hashes_cb.setChecked(self.profile.verify_hashes if self.profile else True)
        adv_layout.addWidget(self.verify_hashes_cb)

        self.auto_connect_cb = QCheckBox(tr("auto_sync_connect"))
        self.auto_connect_cb.setChecked(self.profile.auto_sync_on_connect if self.profile else False)
        adv_layout.addWidget(self.auto_connect_cb)

        spin_row = QHBoxLayout()
        spin_lbl = QLabel(tr("auto_sync_interval"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(0, 1440)
        self.interval_spin.setValue(self.profile.auto_sync_interval_mins if self.profile else 0)
        spin_row.addWidget(spin_lbl)
        spin_row.addWidget(self.interval_spin)
        spin_row.addStretch()
        adv_layout.addLayout(spin_row)

        layout.addWidget(adv_group)

        # 5. Analysis Mode & I/O Throttling (External Drive Protection)
        io_group = QGroupBox(tr("io_settings_title"))
        io_layout = QFormLayout(io_group)
        io_layout.setContentsMargins(16, 16, 16, 16)
        io_layout.setSpacing(10)

        self.analysis_mode_combo = QComboBox()
        self.analysis_mode_combo.addItem(tr("analysis_fast"), AnalysisMode.FAST)
        self.analysis_mode_combo.addItem(tr("analysis_balanced"), AnalysisMode.BALANCED)
        self.analysis_mode_combo.addItem(tr("analysis_full"), AnalysisMode.FULL)
        cur_mode = getattr(self.profile, "analysis_mode", AnalysisMode.BALANCED) if self.profile else AnalysisMode.BALANCED
        idx = self.analysis_mode_combo.findData(cur_mode)
        self.analysis_mode_combo.setCurrentIndex(idx if idx >= 0 else 1)
        io_layout.addRow(tr("analysis_mode_label"), self.analysis_mode_combo)

        self.io_yield_spin = QDoubleSpinBox()
        self.io_yield_spin.setRange(0.0, 500.0)
        self.io_yield_spin.setSingleStep(0.5)
        self.io_yield_spin.setSuffix(" ms")
        self.io_yield_spin.setValue(getattr(self.profile, "io_yield_delay_ms", 0.0) if self.profile else 0.0)
        io_layout.addRow(tr("io_yield_delay"), self.io_yield_spin)

        self.io_speed_spin = QSpinBox()
        self.io_speed_spin.setRange(0, 2000)
        self.io_speed_spin.setSingleStep(10)
        self.io_speed_spin.setSuffix(" MB/s")
        self.io_speed_spin.setValue(int(getattr(self.profile, "io_speed_limit_mb", 0.0)) if self.profile else 0)
        io_layout.addRow(tr("io_speed_limit"), self.io_speed_spin)

        self.io_buffer_combo = QComboBox()
        for b_size in [64, 128, 256, 512, 1024]:
            self.io_buffer_combo.addItem(f"{b_size} KiB", b_size)
        cur_buf = getattr(self.profile, "io_buffer_size_kb", 128) if self.profile else 128
        b_idx = self.io_buffer_combo.findData(cur_buf)
        self.io_buffer_combo.setCurrentIndex(b_idx if b_idx >= 0 else 1)
        io_layout.addRow(tr("io_buffer_size"), self.io_buffer_combo)

        self.mtime_tol_spin = QDoubleSpinBox()
        self.mtime_tol_spin.setRange(0.001, 60.0)
        self.mtime_tol_spin.setSingleStep(0.5)
        self.mtime_tol_spin.setSuffix(" s")
        self.mtime_tol_spin.setValue(getattr(self.profile, "mtime_tolerance_sec", 2.0) if self.profile else 2.0)
        io_layout.addRow(tr("mtime_tolerance"), self.mtime_tol_spin)

        layout.addWidget(io_group)

        # Dialog Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("save_profile"))
        save_btn.setProperty("class", "PrimaryBtn")
        save_btn.clicked.connect(self._validate_and_save)
        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _on_device_changed(self, index: int):
        if self.device_combo.currentData():
            self.dest_edit.clear()

    def _on_dest_text_edited(self, text: str):
        if text.strip() and self.device_combo.currentIndex() != 0:
            self.device_combo.setCurrentIndex(0)

    def _browse_dest(self):
        d = QFileDialog.getExistingDirectory(self, tr("dest_folder"))
        if d:
            self.dest_edit.setText(d)
            self.device_combo.setCurrentIndex(0)

    def _add_mapping(self):
        d = QFileDialog.getExistingDirectory(self, tr("source_path"))
        if d:
            sub = Path(d).name
            self._insert_mapping_row(d, sub)

    def _insert_mapping_row(self, src: str, dst_sub: str):
        row = self.mappings_table.rowCount()
        self.mappings_table.insertRow(row)
        self.mappings_table.setItem(row, 0, QTableWidgetItem(src))
        self.mappings_table.setItem(row, 1, QTableWidgetItem(dst_sub))

    def _remove_mapping(self):
        row = self.mappings_table.currentRow()
        if row >= 0:
            self.mappings_table.removeRow(row)

    def _validate_and_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Помилка", tr("err_profile_name_empty"))
            return

        if self.mappings_table.rowCount() == 0:
            QMessageBox.warning(self, "Помилка", tr("err_no_mappings"))
            return

        target_dev_id = self.device_combo.currentData()
        dest_root = self.dest_edit.text().strip() or None

        if not target_dev_id and not dest_root:
            QMessageBox.warning(self, "Помилка", tr("err_no_dest"))
            return

        mappings = []
        p_id = self.profile.id if self.profile else str(uuid.uuid4())
        for r in range(self.mappings_table.rowCount()):
            src = self.mappings_table.item(r, 0).text().strip()
            dst_sub = self.mappings_table.item(r, 1).text().strip()
            mappings.append(FolderMapping(
                id=str(uuid.uuid4()),
                profile_id=p_id,
                source_root=src,
                dest_subpath=dst_sub
            ))

        # Validate boundary overlap if local destination is used
        if dest_root:
            src_paths = [m.source_root for m in mappings]
            dst_paths = [str((Path(dest_root) / m.dest_subpath).resolve()) for m in mappings]
            valid, errs = PathValidator.validate_mappings(src_paths, dst_paths)
            if not valid:
                err_text = "\n\n".join(errs)
                QMessageBox.critical(
                    self,
                    "Помилка розташування папок",
                    f"Виявлено конфлікт шляхів синхронізації:\n\n{err_text}\n\n"
                    "Папка призначення не повинна знаходитися всередині папки-джерела (і навпаки).\n"
                    "Будь ласка, вкажіть конкретну робочу папку (наприклад, D:/.../PC_Projects), а не спільну батьківську папку."
                )
                return

        del_pol = self.deletion_policy_combo.currentData()
        if isinstance(del_pol, str):
            del_pol = DeletionPolicy(del_pol)
        elif not isinstance(del_pol, DeletionPolicy):
            del_pol = DeletionPolicy.PRESERVE_ALL

        analysis_mode = self.analysis_mode_combo.currentData()
        if not isinstance(analysis_mode, AnalysisMode):
            analysis_mode = AnalysisMode.BALANCED

        self.result_profile = SyncProfile(
            id=p_id,
            name=name,
            mode=self.mode_combo.currentData(),
            target_device_id=target_dev_id,
            dest_root=dest_root,
            mappings=mappings,
            preserve_versions=self.preserve_versions_cb.isChecked(),
            verify_hashes=self.verify_hashes_cb.isChecked(),
            analysis_mode=analysis_mode,
            io_yield_delay_ms=float(self.io_yield_spin.value()),
            io_speed_limit_mb=float(self.io_speed_spin.value()),
            io_buffer_size_kb=int(self.io_buffer_combo.currentData() or 128),
            mtime_tolerance_sec=float(self.mtime_tol_spin.value()),
            auto_sync_on_connect=self.auto_connect_cb.isChecked(),
            auto_sync_interval_mins=self.interval_spin.value(),
            deletion_policy=del_pol
        )
        self.accept()


class RuleEditorDialog(QDialog):
    """Modal for creating or editing filtering/routing rules."""

    def __init__(self, parent=None, rule: Optional[Rule] = None):
        super().__init__(parent)
        self.rule = rule
        self.result_rule: Optional[Rule] = None

        self.setWindowTitle(tr("rule_editor_title"))
        self.resize(480, 280)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()

        # Rule Type Dropdown
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("rule_type_exclude_glob"), RuleType.EXCLUDE_GLOB)
        self.type_combo.addItem(tr("rule_type_include_glob"), RuleType.INCLUDE_GLOB)
        self.type_combo.addItem(tr("rule_type_max_size"), RuleType.MAX_SIZE)
        self.type_combo.addItem(tr("rule_type_min_size"), RuleType.MIN_SIZE)
        self.type_combo.addItem(tr("rule_type_route"), RuleType.ROUTE)

        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("rule_type") + ":", self.type_combo)

        # Pattern
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("*.tmp, node_modules/**, або розмір у байтах")
        form.addRow(tr("rule_pattern") + ":", self.pattern_edit)

        # Target subfolder
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("Наприклад: Documents/PDF (лише для маршрутизації)")
        form.addRow(tr("rule_target") + ":", self.target_edit)

        # Priority
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 1000)
        self.priority_spin.setValue(50)
        form.addRow(tr("rule_priority") + ":", self.priority_spin)

        # Enabled Checkbox
        self.enabled_cb = QCheckBox(tr("rule_enabled"))
        self.enabled_cb.setChecked(True)
        form.addRow("", self.enabled_cb)

        layout.addLayout(form)

        if self.rule:
            idx = self.type_combo.findData(self.rule.rule_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            self.pattern_edit.setText(self.rule.pattern)
            self.target_edit.setText(self.rule.target_subfolder or "")
            self.priority_spin.setValue(self.rule.priority)
            self.enabled_cb.setChecked(self.rule.enabled)

        self._on_type_changed()

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("save_rule"))
        save_btn.setProperty("class", "PrimaryBtn")
        save_btn.clicked.connect(self._save)
        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _on_type_changed(self):
        curr_type = self.type_combo.currentData()
        self.target_edit.setEnabled(curr_type in (RuleType.ROUTE, RuleType.ROUTE_FOLDER))

    def _save(self):
        pat = self.pattern_edit.text().strip()
        if not pat:
            QMessageBox.warning(self, "Помилка", "Шаблон правила не може бути порожнім.")
            return

        r_id = self.rule.id if self.rule else str(uuid.uuid4())
        self.result_rule = Rule(
            id=r_id,
            rule_type=self.type_combo.currentData(),
            pattern=pat,
            target_subfolder=self.target_edit.text().strip() or None,
            priority=self.priority_spin.value(),
            enabled=self.enabled_cb.isChecked()
        )
        self.accept()
