"""Preview and Execution Page for SyncForge."""

import os
from pathlib import Path
from typing import List, Optional, Callable
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QComboBox,
    QLineEdit, QProgressBar, QMessageBox
)
from syncforge.core.models import PlannedOperation, PlanAction, SyncProfile
from syncforge.i18n.translations import tr, format_bytes
from syncforge.storage.repositories import ProfileRepository


class PreviewPage(QWidget):
    """Synchronization preview table with manual action overrides, filtering, and execution controls."""

    def __init__(
        self,
        profile_repo: ProfileRepository,
        on_execute: Callable[[], None],
        on_pause_toggle: Callable[[], None],
        on_cancel: Callable[[], None],
        on_rescan: Callable[[], None],
        on_profile_selected: Callable[[int], None],
        parent=None
    ):
        super().__init__(parent)
        self.profile_repo = profile_repo
        self.on_execute = on_execute
        self.on_pause_toggle = on_pause_toggle
        self.on_cancel = on_cancel
        self.on_rescan = on_rescan
        self.on_profile_selected = on_profile_selected

        self._displayed_preview_ops: List[PlannedOperation] = []
        self._current_plan: List[PlannedOperation] = []
        self._is_paused = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("preview"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        top_ctrl = QHBoxLayout()
        self.exec_btn = QPushButton(tr("execute_plan"))
        self.exec_btn.setProperty("class", "PrimaryBtn")
        self.exec_btn.clicked.connect(self.on_execute)
        top_ctrl.addWidget(self.exec_btn)

        # Pause and Stop buttons
        self.pause_btn = QPushButton(f"⏸️ {tr('pause_execution')}")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.on_pause_toggle)
        top_ctrl.addWidget(self.pause_btn)

        self.stop_btn = QPushButton(f"⏹️ {tr('cancel_execution')}")
        self.stop_btn.setProperty("class", "DangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_cancel)
        top_ctrl.addWidget(self.stop_btn)

        # Profile switcher directly on Preview page
        prof_lbl = QLabel(tr("history_profile") + ":")
        top_ctrl.addWidget(prof_lbl)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.currentIndexChanged.connect(self.on_profile_selected)
        top_ctrl.addWidget(self.profile_combo)

        # Re-scan button
        self.rescan_btn = QPushButton("🔄 Оновити аналіз")
        self.rescan_btn.clicked.connect(self.on_rescan)
        top_ctrl.addWidget(self.rescan_btn)

        self.action_filter_combo = QComboBox()
        self.action_filter_combo.addItem("⚡ " + tr("filter_pending"), "pending_only")
        self.action_filter_combo.addItem("📋 " + tr("filter_all"), "all")
        self.action_filter_combo.addItem(tr("action_copy"), PlanAction.COPY)
        self.action_filter_combo.addItem(tr("action_update"), PlanAction.UPDATE)
        self.action_filter_combo.addItem(tr("action_preserve"), PlanAction.PRESERVE)
        self.action_filter_combo.addItem(tr("action_conflict"), PlanAction.CONFLICT)
        self.action_filter_combo.addItem(tr("action_skip"), PlanAction.SKIP)
        self.action_filter_combo.currentIndexChanged.connect(self.filter_preview_table)
        top_ctrl.addWidget(self.action_filter_combo)

        self.search_type_combo = QComboBox()
        self.search_type_combo.addItem("🔍 Всюди", "all")
        self.search_type_combo.addItem("📄 Ім'я файлу", "name")
        self.search_type_combo.addItem("📁 Шлях", "path")
        self.search_type_combo.addItem("🏷️ Розширення", "ext")
        self.search_type_combo.currentIndexChanged.connect(self.filter_preview_table)
        top_ctrl.addWidget(self.search_type_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder"))
        self.search_edit.textChanged.connect(self.filter_preview_table)
        top_ctrl.addWidget(self.search_edit)

        top_ctrl.addStretch()
        layout.addLayout(top_ctrl)

        self.summary_lbl = QLabel(tr("no_plan_notice"))
        self.summary_lbl.setStyleSheet("color: #949ba4; font-weight: 500;")
        layout.addWidget(self.summary_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            tr("history_profile"), tr("source_file"), tr("dest_file"), tr("action"), tr("size"), tr("reason")
        ])

        p_hdr = self.table.horizontalHeader()
        p_hdr.setSectionResizeMode(QHeaderView.Interactive)
        p_hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)  # Profile name
        self.table.setColumnWidth(1, 280)  # Source file
        self.table.setColumnWidth(2, 280)  # Dest file
        self.table.setColumnWidth(3, 210)  # Action dropdown
        self.table.setColumnWidth(4, 85)   # Size
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        # Progress row with dedicated fixed-width percentage label (prevents jitter)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0%")
        progress_row.addWidget(self.progress_bar, stretch=1)

        self.progress_pct_lbl = QLabel("0%")
        self.progress_pct_lbl.setFixedWidth(52)
        self.progress_pct_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.progress_pct_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #5865f2;")
        progress_row.addWidget(self.progress_pct_lbl)

        layout.addLayout(progress_row)

    def set_syncing_state(self, is_syncing: bool, is_paused: bool = False):
        """Controls execution/pause/stop button states during active synchronization."""
        self._is_paused = is_paused
        self.exec_btn.setEnabled(not is_syncing)
        self.rescan_btn.setEnabled(not is_syncing)
        self.profile_combo.setEnabled(not is_syncing)

        self.pause_btn.setEnabled(is_syncing)
        self.stop_btn.setEnabled(is_syncing)

        if is_paused:
            self.pause_btn.setText(f"▶️ {tr('resume_execution')}")
        else:
            self.pause_btn.setText(f"⏸️ {tr('pause_execution')}")

    def update_profile_combo(self, active_profile: Optional[SyncProfile], is_all: bool):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("🌐 " + tr("all_profiles"), "all")

        profs = self.profile_repo.list_profiles()
        current_idx = 0 if is_all else -1
        for i, p in enumerate(profs):
            self.profile_combo.addItem(p.name, p)
            if not is_all and active_profile and p.id == active_profile.id:
                current_idx = i + 1

        if 0 <= current_idx < self.profile_combo.count():
            self.profile_combo.setCurrentIndex(current_idx)
        elif profs:
            self.profile_combo.setCurrentIndex(1)
        self.profile_combo.blockSignals(False)

    def set_plan(
        self,
        plan: List[PlannedOperation],
        active_profile: Optional[SyncProfile],
        is_all: bool,
        dest_free_space: str = "N/A"
    ):
        self._current_plan = plan
        self.update_profile_combo(active_profile, is_all)

        error_ops = [op for op in plan if op.action == PlanAction.ERROR]
        pending_ops = [op for op in plan if op.action not in (PlanAction.SKIP, PlanAction.ERROR)]
        total_bytes = sum(op.size_bytes for op in pending_ops if op.action in (PlanAction.COPY, PlanAction.UPDATE, PlanAction.RESTORE))

        if is_all:
            prof_title = f" [🌐 {tr('all_profiles')}]"
            free_space = "—"
        else:
            free_space = dest_free_space
            prof_title = f" [{active_profile.name}]" if active_profile else ""

        if error_ops:
            self.summary_lbl.setText(
                f"Профіль:{prof_title} | ⚠️ УВАГА: Виявлено {len(error_ops)} помилок конфігурації шляхів (папка призначення всередині джерела)!"
            )
            self.summary_lbl.setStyleSheet("color: #ed4245; font-weight: bold;")
        elif len(plan) == 0:
            has_raw_files = False
            if active_profile:
                for m in active_profile.mappings:
                    try:
                        p = Path(m.source_root)
                        if p.exists() and any(p.iterdir()):
                            has_raw_files = True
                            break
                    except Exception:
                        pass
            if has_raw_files:
                self.summary_lbl.setText(
                    f"Профіль:{prof_title} | ⚠️ Усі файли в джерелі проігноровано правилами фільтрації (перевірте вкладку 'Правила') | Вільно на цілі: {free_space}"
                )
                self.summary_lbl.setStyleSheet("color: #fee75c; font-weight: bold;")
            else:
                self.summary_lbl.setText(
                    f"Профіль:{prof_title} | Папка-джерело порожня | Вільно на цілі: {free_space}"
                )
                self.summary_lbl.setStyleSheet("color: #949ba4; font-weight: 500;")
        else:
            skip_count = len(plan) - len(pending_ops)
            if pending_ops:
                self.summary_lbl.setText(
                    f"Профіль:{prof_title} | До виконання: {len(pending_ops)} дій ({format_bytes(total_bytes)}) | Актуальних (без змін): {skip_count} | Вільно на цілі: {free_space}"
                )
                self.summary_lbl.setStyleSheet("color: #f2f3f5; font-weight: 500;")
            else:
                self.summary_lbl.setText(
                    f"Профіль:{prof_title} | Всі файли актуальні (без змін: {skip_count}) | Вільно на цілі: {free_space}"
                )
                self.summary_lbl.setStyleSheet("color: #57f287; font-weight: 500;")
        self.filter_preview_table()

    def filter_preview_table(self):
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            action_filter = self.action_filter_combo.currentData()
            search_query = self.search_edit.text().strip().lower()
            search_type = self.search_type_combo.currentData()

            prof_names = {p.id: p.name for p in self.profile_repo.list_profiles()}

            filtered_ops = []
            for op in self._current_plan:
                if action_filter == "pending_only":
                    if op.action in (PlanAction.SKIP, PlanAction.IGNORE):
                        continue
                elif action_filter == "all":
                    pass
                elif action_filter is not None:
                    if op.action != action_filter:
                        continue

                p_name = prof_names.get(op.profile_id, "")
                if search_query:
                    rel_p = op.rel_path.lower()
                    file_name = Path(rel_p).name.lower()
                    file_ext = Path(rel_p).suffix.lower()

                    if search_type == "name":
                        matched = search_query in file_name
                    elif search_type == "path":
                        matched = search_query in rel_p
                    elif search_type == "ext":
                        query_ext = search_query if search_query.startswith(".") else f".{search_query}"
                        matched = search_query in file_ext or file_ext == query_ext
                    else:  # "all"
                        matched = search_query in rel_p or search_query in p_name.lower()

                    if not matched:
                        continue
                filtered_ops.append((op, p_name))

            self.table.setRowCount(len(filtered_ops))
            self._displayed_preview_ops = [op for op, _ in filtered_ops]

            for row, (op, p_name) in enumerate(filtered_ops):
                prof_item = QTableWidgetItem(p_name or "—")
                prof_item.setToolTip(p_name)
                self.table.setItem(row, 0, prof_item)

                if op.action == PlanAction.ERROR and not op.source_abs:
                    err_src = QTableWidgetItem("⚠️ Помилка налаштування шляхів")
                    err_src.setForeground(QColor("#ed4245"))
                    self.table.setItem(row, 1, err_src)
                else:
                    self.table.setItem(row, 1, QTableWidgetItem(op.source_abs or "—"))

                self.table.setItem(row, 2, QTableWidgetItem(op.dest_abs or "—"))

                if row < 100 or len(filtered_ops) <= 150:
                    action_combo = self._create_row_action_combo(op)
                    self.table.setCellWidget(row, 3, action_combo)
                else:
                    action_str = op.action.value.upper()
                    act_item = QTableWidgetItem(f"{action_str}  ▾")
                    if op.action in (PlanAction.ERROR, PlanAction.CONFLICT, PlanAction.DELETE_DEST):
                        act_item.setForeground(QColor("#ed4245"))
                    elif op.action in (PlanAction.COPY, PlanAction.UPDATE, PlanAction.RESTORE):
                        act_item.setForeground(QColor("#5865f2"))
                    elif op.action in (PlanAction.PRESERVE, PlanAction.ARCHIVE):
                        act_item.setForeground(QColor("#fee75c"))
                    else:
                        act_item.setForeground(QColor("#949ba4"))
                    self.table.setItem(row, 3, act_item)

                self.table.setItem(row, 4, QTableWidgetItem(format_bytes(op.size_bytes)))

                reason_item = QTableWidgetItem(op.reason)
                if op.action == PlanAction.ERROR:
                    reason_item.setForeground(QColor("#ed4245"))
                reason_item.setToolTip(op.reason)
                self.table.setItem(row, 5, reason_item)

        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)

    def _create_row_action_combo(self, op: PlannedOperation) -> QComboBox:
        action_combo = QComboBox()
        is_dest_only = (not op.source_abs and bool(op.dest_abs))

        if op.action == PlanAction.ERROR:
            action_combo.addItem("⚠️ Помилка (ERROR)", PlanAction.ERROR)
            action_combo.addItem(tr("action_skip"), PlanAction.SKIP)
            action_combo.addItem(tr("action_ignore_choice"), PlanAction.IGNORE)
        elif is_dest_only:
            action_combo.addItem(tr("action_keep_dest"), PlanAction.PRESERVE)
            action_combo.addItem(tr("action_restore_pc"), PlanAction.RESTORE)
            action_combo.addItem(tr("action_archive_dest"), PlanAction.ARCHIVE)
            action_combo.addItem(tr("action_delete_dest_choice"), PlanAction.DELETE_DEST)
            action_combo.addItem(tr("action_ignore_choice"), PlanAction.IGNORE)
        else:
            action_combo.addItem(tr("action_copy"), PlanAction.COPY)
            action_combo.addItem(tr("action_update"), PlanAction.UPDATE)
            action_combo.addItem(tr("action_skip"), PlanAction.SKIP)
            action_combo.addItem(tr("action_preserve"), PlanAction.PRESERVE)
            action_combo.addItem(tr("action_conflict"), PlanAction.CONFLICT)

        idx = action_combo.findData(op.action)
        if idx >= 0:
            action_combo.setCurrentIndex(idx)

        if op.action in (PlanAction.ERROR, PlanAction.CONFLICT, PlanAction.DELETE_DEST):
            action_combo.setStyleSheet("color: #ed4245; font-weight: bold;")
        elif op.action in (PlanAction.COPY, PlanAction.UPDATE, PlanAction.RESTORE):
            action_combo.setStyleSheet("color: #5865f2; font-weight: bold;")
        elif op.action in (PlanAction.PRESERVE, PlanAction.ARCHIVE):
            action_combo.setStyleSheet("color: #fee75c; font-weight: bold;")
        elif op.action in (PlanAction.SKIP, PlanAction.IGNORE):
            action_combo.setStyleSheet("color: #949ba4;")

        action_combo.currentIndexChanged.connect(
            lambda c_idx, o=op, cb=action_combo: self._on_user_action_changed(o, cb.itemData(c_idx), cb)
        )
        return action_combo

    def _on_cell_clicked(self, row: int, col: int):
        if col == 3 and not self.table.cellWidget(row, 3):
            if row < len(self._displayed_preview_ops):
                op = self._displayed_preview_ops[row]
                combo = self._create_row_action_combo(op)
                self.table.setCellWidget(row, 3, combo)

    def _on_user_action_changed(self, op: PlannedOperation, new_action: PlanAction, combo: QComboBox):
        if not new_action:
            return

        if new_action == PlanAction.DELETE_DEST:
            reply = QMessageBox.warning(
                self,
                tr("confirm_delete_dest_title"),
                tr("confirm_delete_dest_msg").format(path=op.rel_path),
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                idx = combo.findData(PlanAction.PRESERVE)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
                op.action = PlanAction.PRESERVE
                op.reason = "Збережено на SSD за рішенням користувача"
                self._refresh_row_reason(op)
                return
            op.action = PlanAction.DELETE_DEST
            op.reason = "Підтверджено користувачем: видалити з SSD"
        elif new_action == PlanAction.PRESERVE:
            op.action = PlanAction.PRESERVE
            op.reason = "Залишити тільки на SSD (запам'ятати рішення)"
        elif new_action == PlanAction.RESTORE:
            op.action = PlanAction.RESTORE
            op.reason = "Відновлення з SSD на ПК"
        elif new_action == PlanAction.ARCHIVE:
            op.action = PlanAction.ARCHIVE
            op.reason = "Переміщення в архів .syncforge_archive на SSD"
        elif new_action == PlanAction.IGNORE:
            op.action = PlanAction.IGNORE
            op.reason = "Ігнорувати (додати в правила виключення)"
        elif new_action == PlanAction.SKIP:
            op.action = PlanAction.SKIP
            op.reason = "Пропущено вручну користувачем"
        elif new_action == PlanAction.COPY:
            op.action = PlanAction.COPY
            op.reason = "Примусове копіювання (вручну)"
        elif new_action == PlanAction.UPDATE:
            op.action = PlanAction.UPDATE
            op.reason = "Примусове оновлення (вручну)"

        self._refresh_row_reason(op)

    def _refresh_row_reason(self, op: PlannedOperation):
        for r in range(self.table.rowCount()):
            dest_item = self.table.item(r, 2)
            src_item = self.table.item(r, 1)
            if (dest_item and op.rel_path in dest_item.text()) or (src_item and op.rel_path in src_item.text()):
                self.table.setItem(r, 5, QTableWidgetItem(op.reason))
                break
