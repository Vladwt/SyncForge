"""Duplicate files finder dialog with group inspection and safe deletion."""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QCheckBox, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QProgressBar, QMessageBox, QFileDialog, QMenu
)
from syncforge.core.duplicates import DuplicateFinder, DuplicateGroup
from syncforge.i18n.translations import format_bytes, tr


class DuplicateScanWorker(QThread):
    """Background worker thread for searching duplicate files."""
    progress = Signal(int, str)
    finished_scan = Signal(list)
    error = Signal(str)

    def __init__(self, directories: List[str], min_size_bytes: int, recursive: bool):
        super().__init__()
        self.directories = directories
        self.min_size_bytes = min_size_bytes
        self.recursive = recursive
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            finder = DuplicateFinder(is_cancelled=lambda: self._is_cancelled)
            groups = finder.scan_directories(
                directories=self.directories,
                min_size_bytes=self.min_size_bytes,
                recursive=self.recursive,
                progress_callback=self.progress.emit
            )
            self.finished_scan.emit(groups)
        except Exception as e:
            self.error.emit(str(e))


class DuplicateFinderDialog(QDialog):
    """Interactive dialog to search for duplicate files, review them, and safely delete copies."""

    def __init__(self, parent=None, default_folder: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Пошук дублікатів файлів")
        self.setMinimumSize(850, 560)
        self.resize(960, 640)

        self.default_folder = default_folder or ""
        self.worker: Optional[DuplicateScanWorker] = None
        self.groups: List[DuplicateGroup] = []

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Header description
        desc_lbl = QLabel(
            "Швидкий та безпечний пошук однакових файлів на основі точного розміру, вибіркової перевірки та BLAKE3-хешів."
        )
        desc_lbl.setStyleSheet("color: #949ba4; font-size: 13px;")
        layout.addWidget(desc_lbl)

        # Folder selection row
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Папка для пошуку:"))
        self.folder_edit = QLineEdit(self.default_folder)
        self.folder_edit.setPlaceholderText("Оберіть папку для аналізу (наприклад, D:\\Work або диск SSD)")
        folder_row.addWidget(self.folder_edit, stretch=1)

        browse_btn = QPushButton("Обрати...")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)

        layout.addLayout(folder_row)

        # Filter options row
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Мінімальний розмір:"))
        self.size_filter_combo = QComboBox()
        self.size_filter_combo.addItem("Всі файли (> 0 байт)", 1)
        self.size_filter_combo.addItem("Більше 100 КБ", 100 * 1024)
        self.size_filter_combo.addItem("Більше 1 МБ", 1024 * 1024)
        self.size_filter_combo.addItem("Більше 10 МБ", 10 * 1024 * 1024)
        self.size_filter_combo.addItem("Більше 100 МБ", 100 * 1024 * 1024)
        self.size_filter_combo.setCurrentIndex(1)
        opt_row.addWidget(self.size_filter_combo)

        self.recursive_cb = QCheckBox("Включаючи підпапки (рекурсивно)")
        self.recursive_cb.setChecked(True)
        opt_row.addWidget(self.recursive_cb)

        opt_row.addStretch()

        self.start_btn = QPushButton("🔍 Знайти дублікати")
        self.start_btn.setProperty("class", "PrimaryBtn")
        self.start_btn.clicked.connect(self._start_scan)
        opt_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹️ Зупинити")
        self.stop_btn.setProperty("class", "DangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_scan)
        opt_row.addWidget(self.stop_btn)

        layout.addLayout(opt_row)

        # Progress bar & status
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0%")
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Вкажіть папку та натисніть 'Знайти дублікати'.")
        self.status_lbl.setStyleSheet("color: #949ba4; font-weight: 500;")
        layout.addWidget(self.status_lbl)

        # Tree view for grouped duplicate results
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Файл / Шлях", "Розмір", "Дата зміни", "BLAKE3 Хеш"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        layout.addWidget(self.tree, stretch=1)

        # Bottom action bar for marking & deletion
        bottom_row = QHBoxLayout()

        select_older_btn = QPushButton("Позначити старіші (залишити найновіший)")
        select_older_btn.clicked.connect(self._select_older_duplicates)
        bottom_row.addWidget(select_older_btn)

        select_newer_btn = QPushButton("Позначити новіші (залишити найстаріший)")
        select_newer_btn.clicked.connect(self._select_newer_duplicates)
        bottom_row.addWidget(select_newer_btn)

        uncheck_all_btn = QPushButton("Зняти всі позначки")
        uncheck_all_btn.clicked.connect(self._uncheck_all)
        bottom_row.addWidget(uncheck_all_btn)

        bottom_row.addStretch()

        self.delete_btn = QPushButton("🗑️ Видалити позначені дублікати")
        self.delete_btn.setProperty("class", "DangerBtn")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_marked_files)
        bottom_row.addWidget(self.delete_btn)

        close_btn = QPushButton("Закрити")
        close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(close_btn)

        layout.addLayout(bottom_row)

    def _browse_folder(self):
        curr = self.folder_edit.text().strip() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Оберіть папку для пошуку дублікатів", curr)
        if chosen:
            self.folder_edit.setText(chosen)

    def _start_scan(self):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).exists():
            QMessageBox.warning(self, "Помилка", "Вкажіть коректний шлях до існуючої папки.")
            return

        self.tree.clear()
        self.groups.clear()
        self.delete_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setText("Початок сканування...")

        min_sz = self.size_filter_combo.currentData()
        rec = self.recursive_cb.isChecked()

        self.worker = DuplicateScanWorker([folder], min_size_bytes=min_sz, recursive=rec)
        self.worker.progress.connect(self._on_scan_progress)
        self.worker.finished_scan.connect(self._on_scan_finished)
        self.worker.error.connect(self._on_scan_error)
        self.worker.start()

    def _stop_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status_lbl.setText("Зупинка сканування...")
            self.stop_btn.setEnabled(False)

    def _on_scan_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{percent}% - {message}")
        self.status_lbl.setText(message)

    def _on_scan_finished(self, groups: List[DuplicateGroup]):
        self.groups = groups
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("100% - Готово")

        total_dups = sum(len(g.files) for g in groups)
        total_wasted = sum(g.wasted_bytes for g in groups)

        if not groups:
            self.status_lbl.setText("✅ Дублікатів не знайдено.")
            self.delete_btn.setEnabled(False)
            return

        self.status_lbl.setText(
            f"Знайдено {len(groups)} груп дублікатів ({total_dups} файлів). "
            f"Марно зайнято: {format_bytes(total_wasted)}."
        )
        self._populate_tree(groups)
        self.delete_btn.setEnabled(True)

    def _on_scan_error(self, err_msg: str):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_lbl.setText(f"Помилка: {err_msg}")
        QMessageBox.critical(self, "Помилка", f"Не вдалося завершити сканування:\n{err_msg}")

    def _populate_tree(self, groups: List[DuplicateGroup]):
        self.tree.clear()
        for idx, g in enumerate(groups, 1):
            group_item = QTreeWidgetItem(self.tree)
            group_item.setText(0, f"📁 Група #{idx}: {len(g.files)} однакових файлів (зайве: {format_bytes(g.wasted_bytes)})")
            group_item.setText(1, format_bytes(g.size_bytes))
            group_item.setText(3, g.hash_blake3[:16] + "...")
            group_item.setExpanded(True)
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)

            for f in g.files:
                dt_str = datetime.fromtimestamp(f.mtime).strftime("%Y-%m-%d %H:%M:%S")
                child = QTreeWidgetItem(group_item)
                child.setText(0, f.path)
                child.setText(1, format_bytes(f.size_bytes))
                child.setText(2, dt_str)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, f.path)

    def _select_older_duplicates(self):
        """Marks all files in each group except the one with the newest modification time."""
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            children = [group_item.child(c) for c in range(group_item.childCount())]
            if len(children) < 2:
                continue
            # Sort by datetime ascending (oldest first)
            children.sort(key=lambda item: item.text(2))
            # Mark all except the newest (the last one)
            for ch in children[:-1]:
                ch.setCheckState(0, Qt.Checked)
            children[-1].setCheckState(0, Qt.Unchecked)

    def _select_newer_duplicates(self):
        """Marks all files in each group except the one with the oldest modification time (the original)."""
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            children = [group_item.child(c) for c in range(group_item.childCount())]
            if len(children) < 2:
                continue
            # Sort by datetime ascending (oldest first)
            children.sort(key=lambda item: item.text(2))
            # Keep the oldest (first one), mark the rest
            children[0].setCheckState(0, Qt.Unchecked)
            for ch in children[1:]:
                ch.setCheckState(0, Qt.Checked)

    def _uncheck_all(self):
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for c in range(group_item.childCount()):
                group_item.child(c).setCheckState(0, Qt.Unchecked)

    def _delete_marked_files(self):
        marked_paths: List[str] = []
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for c in range(group_item.childCount()):
                child = group_item.child(c)
                if child.checkState(0) == Qt.Checked:
                    p = child.data(0, Qt.UserRole)
                    if p:
                        marked_paths.append(p)

        if not marked_paths:
            QMessageBox.information(self, "Повідомлення", "Не вибрано жодного файлу для видалення.")
            return

        reply = QMessageBox.question(
            self,
            "Підтвердження видалення",
            f"Ви дійсно бажаєте видалити {len(marked_paths)} позначених файлів-дублікатів?\n\n"
            f"Увага: видалені копії буде безповоротно стерто з диска.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        deleted_count = 0
        error_count = 0
        for path_str in marked_paths:
            try:
                p = Path(path_str)
                if p.exists() and p.is_file():
                    p.unlink()
                    deleted_count += 1
            except Exception:
                error_count += 1

        QMessageBox.information(
            self,
            "Результат видалення",
            f"Успішно видалено дублікатів: {deleted_count}." +
            (f"\nПомилок видалення: {error_count}" if error_count > 0 else "")
        )
        # Re-run scan to update list
        self._start_scan()

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item or not item.data(0, Qt.UserRole):
            return

        file_path = item.data(0, Qt.UserRole)
        menu = QMenu(self)

        open_folder_act = menu.addAction("📂 Відкрити розташування у Провіднику")
        copy_path_act = menu.addAction("📋 Скопіювати шлях")

        action = menu.exec(self.tree.mapToGlobal(pos))
        if action == open_folder_act:
            try:
                subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
            except Exception:
                pass
        elif action == copy_path_act:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(file_path)
