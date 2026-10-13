"""Conflicts Resolution Page for SyncForge."""

from typing import List, Callable, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QMessageBox
)
from syncforge.core.models import PlannedOperation, PlanAction, ConflictResolution
from syncforge.i18n.translations import tr, format_bytes


class ConflictsPage(QWidget):
    """Page for reviewing and resolving file conflicts."""

    def __init__(self, get_plan_callback: Callable[[], List[PlannedOperation]], parent=None):
        super().__init__(parent)
        self.get_plan_callback = get_plan_callback
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("conflicts_title"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        info = QLabel(tr("conflict_instruction"))
        info.setStyleSheet("color: #949ba4;")
        layout.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("source_file"), "Розмір / Час", tr("resolution")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 450)
        self.table.setColumnWidth(1, 150)
        layout.addWidget(self.table)

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

    def refresh(self):
        """Refreshes conflict entries from the current plan."""
        self.table.setRowCount(0)
        plan = self.get_plan_callback()
        confls = [op for op in plan if op.action == PlanAction.CONFLICT]
        for op in confls:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(op.rel_path))
            self.table.setItem(row, 1, QTableWidgetItem(f"{format_bytes(op.size_bytes)}"))
            res_str = op.conflict_resolution.value if op.conflict_resolution else "Очікує вибору"
            self.table.setItem(row, 2, QTableWidgetItem(res_str))

    def _resolve_conflict(self, res: ConflictResolution):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Попередження", "Виберіть рядок конфлікту з таблиці.")
            return

        plan = self.get_plan_callback()
        confls = [op for op in plan if op.action == PlanAction.CONFLICT]
        if curr_row < len(confls):
            confls[curr_row].conflict_resolution = res
            self.refresh()
