"""Rules Management Page for SyncForge."""

from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QComboBox,
    QCheckBox, QMessageBox
)
from syncforge.i18n.translations import tr
from syncforge.rules.rule_engine import Rule, RuleType
from syncforge.storage.repositories import RuleRepository
from syncforge.gui.dialogs import RuleEditorDialog


class RulesPage(QWidget):
    """File filtering and routing rules management table."""

    def __init__(self, rule_repo: RuleRepository, parent=None):
        super().__init__(parent)
        self.rule_repo = rule_repo
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
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

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            tr("rule_pattern"), tr("rule_type"), tr("rule_target"), tr("rule_priority"), tr("rule_enabled")
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 100)
        layout.addWidget(self.table)

    def refresh(self):
        """Reloads all rules into table."""
        self.table.setRowCount(0)
        rules = self.rule_repo.list_rules()
        for r in rules:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(r.pattern))

            type_combo = QComboBox()
            type_combo.addItem(tr("rule_type_exclude_glob"), RuleType.EXCLUDE_GLOB)
            type_combo.addItem(tr("rule_type_include_glob"), RuleType.INCLUDE_GLOB)
            type_combo.addItem(tr("rule_type_max_size"), RuleType.MAX_SIZE)
            type_combo.addItem(tr("rule_type_min_size"), RuleType.MIN_SIZE)
            type_combo.addItem(tr("rule_type_route"), RuleType.ROUTE)

            t_idx = type_combo.findData(r.rule_type)
            if t_idx >= 0:
                type_combo.setCurrentIndex(t_idx)

            type_combo.currentIndexChanged.connect(
                lambda c_idx, rule=r, cb=type_combo: self._on_rule_type_changed(rule, cb.itemData(c_idx))
            )
            self.table.setCellWidget(row, 1, type_combo)

            self.table.setItem(row, 2, QTableWidgetItem(r.target_subfolder or "—"))
            self.table.setItem(row, 3, QTableWidgetItem(str(r.priority)))

            enable_cb = QCheckBox("Так" if r.enabled else "Ні")
            enable_cb.setChecked(r.enabled)
            enable_cb.toggled.connect(
                lambda checked, rule=r, cb=enable_cb: self._on_rule_enabled_toggled(rule, checked, cb)
            )
            self.table.setCellWidget(row, 4, enable_cb)

            self.table.setItem(row, 0, QTableWidgetItem(r.pattern))
            self.table.item(row, 0).setData(Qt.UserRole, r)

    def _on_rule_type_changed(self, rule: Rule, new_type):
        if not new_type:
            return
        if isinstance(new_type, str):
            for t in RuleType:
                if t.value == new_type or t.name == new_type:
                    new_type = t
                    break
        rule.rule_type = new_type
        self.rule_repo.save_rule(rule)

    def _on_rule_enabled_toggled(self, rule: Rule, enabled: bool, cb: QCheckBox):
        rule.enabled = enabled
        cb.setText("Так" if enabled else "Ні")
        self.rule_repo.save_rule(rule)

    def _add_rule_dialog(self):
        d = RuleEditorDialog(self)
        if d.exec() and d.result_rule:
            self.rule_repo.save_rule(d.result_rule)
            self.refresh()

    def _edit_selected_rule(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            return
        item = self.table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        d = RuleEditorDialog(self, rule=rule)
        if d.exec() and d.result_rule:
            self.rule_repo.save_rule(d.result_rule)
            self.refresh()

    def _delete_selected_rule(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            return
        item = self.table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        reply = QMessageBox.question(self, tr("delete_rule"), tr("confirm_delete_rule"), QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.rule_repo.delete_rule(rule.id)
            self.refresh()

    def _adjust_rule_priority(self, delta: int):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            return
        item = self.table.item(curr_row, 0)
        rule = item.data(Qt.UserRole)
        rule.priority = max(0, rule.priority + delta)
        self.rule_repo.save_rule(rule)
        self.refresh()
