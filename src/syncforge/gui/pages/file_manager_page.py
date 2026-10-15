"""Dual-pane file manager page (Total Commander style) for SyncForge."""

import os
import shutil
import stat
from datetime import datetime
from typing import List, Optional, Set, Tuple

from PySide6.QtCore import Qt, Signal, QDir, QThread, QFileInfo
from PySide6.QtGui import QColor, QBrush, QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFrame, QMessageBox, QInputDialog, QProgressDialog, QMenu, QApplication
)

from syncforge.i18n.translations import tr
from syncforge.storage.repositories import ProfileRepository
from syncforge.gui.duplicates_dialog import DuplicateFinderDialog


def format_size(bytes_val: int) -> str:
    """Formats bytes value into human-readable string."""
    if bytes_val < 0:
        return ""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    for unit in ["KiB", "MiB", "GiB", "TiB"]:
        bytes_val /= 1024.0
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
    return f"{bytes_val:.1f} PiB"


class FileCopyWorker(QThread):
    """Background worker for copying files/folders between panels without freezing GUI."""

    progress_signal = Signal(int, int, str)  # current, total, filename
    finished_signal = Signal(bool, str, int)  # success, error_message, count

    def __init__(self, items_to_copy: List[str], dest_dir: str, overwrite: bool = False):
        super().__init__()
        self.items_to_copy = items_to_copy
        self.dest_dir = dest_dir
        self.overwrite = overwrite
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            total_items = len(self.items_to_copy)
            copied_count = 0

            for idx, src_path in enumerate(self.items_to_copy):
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Операцію скасовано користувачем.", copied_count)
                    return

                base_name = os.path.basename(src_path.rstrip("/\\"))
                target_path = os.path.join(self.dest_dir, base_name)
                self.progress_signal.emit(idx + 1, total_items, base_name)

                if os.path.isdir(src_path):
                    if os.path.exists(target_path):
                        # Merge or overwrite contents
                        for root, dirs, files in os.walk(src_path):
                            if self._is_cancelled:
                                break
                            rel = os.path.relpath(root, src_path)
                            dest_sub = os.path.join(target_path, rel)
                            os.makedirs(dest_sub, exist_ok=True)
                            for f in files:
                                s_file = os.path.join(root, f)
                                d_file = os.path.join(dest_sub, f)
                                shutil.copy2(s_file, d_file)
                    else:
                        shutil.copytree(src_path, target_path)
                else:
                    shutil.copy2(src_path, target_path)

                copied_count += 1

            self.finished_signal.emit(True, "", copied_count)
        except Exception as e:
            self.finished_signal.emit(False, str(e), copied_count)


class FilePanelWidget(QWidget):
    """Single file panel in the dual-pane view."""

    path_changed = Signal(str)
    selection_changed = Signal()
    request_duplicate_finder = Signal(str)

    def __init__(self, initial_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.current_path: str = ""
        self._is_loading = False
        self._init_ui()
        
        # Default start directory
        start_dir = initial_path or os.path.expanduser("~")
        if not os.path.exists(start_dir):
            drives = QDir.drives()
            start_dir = drives[0].absolutePath() if drives else "C:\\"
        self.navigate_to(start_dir)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Drive and navigation bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.drive_combo = QComboBox()
        self.drive_combo.setFixedWidth(80)
        self._populate_drives()
        self.drive_combo.currentIndexChanged.connect(self._on_drive_selected)
        top_bar.addWidget(self.drive_combo)

        self.up_btn = QPushButton("⬆ ..")
        self.up_btn.setToolTip("Перейти на рівень вище (..)")
        self.up_btn.setFixedWidth(50)
        self.up_btn.clicked.connect(self.navigate_up)
        top_bar.addWidget(self.up_btn)

        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._on_path_entered)
        top_bar.addWidget(self.path_edit)

        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("Оновити каталог")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(self.refresh_btn)

        layout.addLayout(top_bar)

        # File list table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ім'я", "Розмір", "Змінено", "Тип"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        # Status footer
        self.status_lbl = QLabel("0 елементів")
        self.status_lbl.setStyleSheet("color: #949ba4; font-size: 11px; padding: 2px;")
        layout.addWidget(self.status_lbl)

    def _populate_drives(self):
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        drives = QDir.drives()
        for d in drives:
            p = os.path.normpath(d.absolutePath())
            self.drive_combo.addItem(p, p)
        self.drive_combo.blockSignals(False)

    def _on_drive_selected(self, index: int):
        drive_path = self.drive_combo.itemData(index)
        if drive_path and os.path.exists(drive_path):
            self.navigate_to(drive_path)

    def _on_path_entered(self):
        target = self.path_edit.text().strip()
        if os.path.isdir(target):
            self.navigate_to(target)
        else:
            QMessageBox.warning(self, "Помилка", f"Каталог не існує:\n{target}")
            self.path_edit.setText(self.current_path)

    def navigate_up(self):
        parent_dir = os.path.dirname(self.current_path.rstrip("/\\"))
        if parent_dir and os.path.isdir(parent_dir):
            self.navigate_to(parent_dir)

    def navigate_to(self, path: str):
        norm = os.path.normpath(os.path.abspath(path))
        if not os.path.isdir(norm):
            return

        self.current_path = norm
        self.path_edit.setText(norm)

        # Update drive combo without firing signal
        for idx in range(self.drive_combo.count()):
            d_val = self.drive_combo.itemData(idx)
            if norm.lower().startswith(d_val.lower()):
                self.drive_combo.blockSignals(True)
                self.drive_combo.setCurrentIndex(idx)
                self.drive_combo.blockSignals(False)
                break

        self.refresh()
        self.path_changed.emit(self.current_path)

    def refresh(self):
        if not self.current_path or not os.path.isdir(self.current_path):
            return

        self._is_loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        # Add parent entry [..] if not filesystem root
        parent_dir = os.path.dirname(self.current_path.rstrip("/\\"))
        is_root = (parent_dir == self.current_path or not parent_dir or self.current_path.endswith(":\\"))

        try:
            entries = os.scandir(self.current_path)
            dirs = []
            files = []
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e)
                    else:
                        files.append(e)
                except (PermissionError, OSError):
                    pass

            dirs.sort(key=lambda x: x.name.lower())
            files.sort(key=lambda x: x.name.lower())
        except (PermissionError, OSError) as e:
            QMessageBox.warning(self, "Помилка доступу", f"Неможливо відкрити каталог:\n{e}")
            self._is_loading = False
            return

        total_rows = (0 if is_root else 1) + len(dirs) + len(files)
        self.table.setRowCount(total_rows)

        row = 0
        if not is_root:
            up_item = QTableWidgetItem("📁 [..]")
            up_item.setData(Qt.UserRole, parent_dir)
            up_item.setData(Qt.UserRole + 1, "dir")
            self.table.setItem(row, 0, up_item)
            self.table.setItem(row, 1, QTableWidgetItem("<ПАПКА>"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem("Папка"))
            row += 1

        for d in dirs:
            try:
                st = d.stat(follow_symlinks=False)
                mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                mtime_str = ""

            name_item = QTableWidgetItem(f"📁 {d.name}")
            name_item.setData(Qt.UserRole, d.path)
            name_item.setData(Qt.UserRole + 1, "dir")

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem("<ПАПКА>"))
            self.table.setItem(row, 2, QTableWidgetItem(mtime_str))
            self.table.setItem(row, 3, QTableWidgetItem("Папка"))
            row += 1

        total_bytes = 0
        for f in files:
            try:
                st = f.stat(follow_symlinks=False)
                size_bytes = st.st_size
                total_bytes += size_bytes
                size_str = format_size(size_bytes)
                mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                size_str = ""
                mtime_str = ""

            ext = os.path.splitext(f.name)[1].lower().lstrip(".")
            name_item = QTableWidgetItem(f"📄 {f.name}")
            name_item.setData(Qt.UserRole, f.path)
            name_item.setData(Qt.UserRole + 1, "file")

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(size_str))
            self.table.setItem(row, 2, QTableWidgetItem(mtime_str))
            self.table.setItem(row, 3, QTableWidgetItem(ext.upper() if ext else "Файл"))
            row += 1

        self._is_loading = False
        self._update_status()

    def _on_selection_changed(self):
        if not self._is_loading:
            self._update_status()
            self.selection_changed.emit()

    def _update_status(self):
        selected_rows = set(item.row() for item in self.table.selectedItems())
        selected_items = self.get_selected_paths()
        
        dir_count = 0
        file_count = 0
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                kind = item.data(Qt.UserRole + 1)
                if kind == "dir":
                    dir_count += 1
                elif kind == "file":
                    file_count += 1

        status_text = f"Папок: {dir_count}, Файлів: {file_count}"
        if selected_items:
            status_text += f" | Виділено: {len(selected_items)}"
        self.status_lbl.setText(status_text)

    def _on_cell_double_clicked(self, row: int, col: int):
        item = self.table.item(row, 0)
        if not item:
            return
        path = item.data(Qt.UserRole)
        kind = item.data(Qt.UserRole + 1)

        if kind == "dir":
            if path and os.path.isdir(path):
                self.navigate_to(path)
        elif kind == "file":
            if path and os.path.isfile(path):
                try:
                    os.startfile(path)
                except Exception as e:
                    QMessageBox.warning(self, "Помилка відкриття", f"Не вдалося відкрити файл:\n{e}")

    def get_selected_paths(self) -> List[str]:
        """Returns list of selected real filesystem paths (ignoring `..`)."""
        selected_rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        paths = []
        for r in selected_rows:
            item = self.table.item(r, 0)
            if item:
                path = item.data(Qt.UserRole)
                text = item.text()
                if path and os.path.exists(path) and "[..]" not in text:
                    paths.append(path)
        return paths

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        selected = self.get_selected_paths()

        if selected:
            open_act = menu.addAction("Відкрити")
            open_act.triggered.connect(lambda: self._on_cell_double_clicked(self.table.currentRow(), 0))
            
            copy_path_act = menu.addAction("Копіювати повний шлях")
            copy_path_act.triggered.connect(lambda: QApplication.clipboard().setText("\n".join(selected)))

        open_explorer_act = menu.addAction("Відкрити каталог у Провіднику Windows")
        open_explorer_act.triggered.connect(lambda: os.startfile(self.current_path))

        menu.addSeparator()

        dup_act = menu.addAction("🔎 Пошук дублікатів у цій папці...")
        dup_act.triggered.connect(lambda: self.request_duplicate_finder.emit(self.current_path))

        menu.exec(self.table.viewport().mapToGlobal(pos))


class FileManagerPage(QWidget):
    """Total Commander-style dual-pane file management interface."""

    def __init__(self, profile_repo: ProfileRepository, parent=None):
        super().__init__(parent)
        self.profile_repo = profile_repo
        self.active_worker: Optional[FileCopyWorker] = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        # Header bar with Profile quick setup
        header_layout = QHBoxLayout()
        title_lbl = QLabel(tr("file_manager"))
        title_lbl.setStyleSheet("font-size: 22px; font-weight: bold; color: #5865f2;")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        prof_lbl = QLabel("Профіль:")
        prof_lbl.setStyleSheet("font-weight: 500; color: #949ba4;")
        header_layout.addWidget(prof_lbl)

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self._populate_profiles()
        header_layout.addWidget(self.profile_combo)

        load_prof_btn = QPushButton("📁 " + tr("load_profile_paths"))
        load_prof_btn.setToolTip("Завантажити шлях джерела в ліву панель, а призначення — в праву")
        load_prof_btn.clicked.connect(self._apply_profile_paths)
        header_layout.addWidget(load_prof_btn)

        main_layout.addLayout(header_layout)

        # Dual panels with QSplitter
        self.splitter = QSplitter(Qt.Horizontal)
        self.left_panel = FilePanelWidget(parent=self)
        self.right_panel = FilePanelWidget(parent=self)

        self.left_panel.request_duplicate_finder.connect(self._open_duplicate_finder)
        self.right_panel.request_duplicate_finder.connect(self._open_duplicate_finder)

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([500, 500])
        main_layout.addWidget(self.splitter, 1)

        # Bottom Total Commander style actions bar
        actions_frame = QFrame()
        actions_frame.setProperty("class", "Card")
        actions_layout = QHBoxLayout(actions_frame)
        actions_layout.setContentsMargins(10, 8, 10, 8)
        actions_layout.setSpacing(10)

        # Copy L -> R
        self.copy_lr_btn = QPushButton(tr("copy_left_to_right"))
        self.copy_lr_btn.setProperty("class", "PrimaryBtn")
        self.copy_lr_btn.setToolTip("Скопіювати виділені файли з лівої панелі в праву")
        self.copy_lr_btn.clicked.connect(lambda: self._copy_between_panels(src_panel=self.left_panel, dst_panel=self.right_panel))
        actions_layout.addWidget(self.copy_lr_btn)

        # Copy R -> L
        self.copy_rl_btn = QPushButton(tr("copy_right_to_left"))
        self.copy_rl_btn.setProperty("class", "PrimaryBtn")
        self.copy_rl_btn.setToolTip("Скопіювати виділені файли з правої панелі в ліву")
        self.copy_rl_btn.clicked.connect(lambda: self._copy_between_panels(src_panel=self.right_panel, dst_panel=self.left_panel))
        actions_layout.addWidget(self.copy_rl_btn)

        # Compare folders
        self.compare_btn = QPushButton("🔍 " + tr("compare_folders"))
        self.compare_btn.setToolTip("Порівняти вміст лівої та правої панелей за іменами, розміром та часом")
        self.compare_btn.clicked.connect(self._compare_panels)
        actions_layout.addWidget(self.compare_btn)

        # New folder
        self.mkdir_btn = QPushButton("📁 " + tr("new_folder"))
        self.mkdir_btn.clicked.connect(self._create_folder)
        actions_layout.addWidget(self.mkdir_btn)

        # Delete selected
        self.del_btn = QPushButton("🗑️ " + tr("delete_selected"))
        self.del_btn.setProperty("class", "DangerBtn")
        self.del_btn.clicked.connect(self._delete_selected)
        actions_layout.addWidget(self.del_btn)

        # Duplicate finder
        self.dup_btn = QPushButton("🔎 " + tr("duplicate_finder"))
        self.dup_btn.setToolTip("Пошук дублікатів файлів")
        self.dup_btn.clicked.connect(lambda: self._open_duplicate_finder(self.left_panel.current_path))
        actions_layout.addWidget(self.dup_btn)

        actions_layout.addStretch()

        # Refresh both
        self.refresh_all_btn = QPushButton("🔄 " + tr("refresh_both"))
        self.refresh_all_btn.clicked.connect(self.refresh_both)
        actions_layout.addWidget(self.refresh_all_btn)

        main_layout.addWidget(actions_frame)

    def _populate_profiles(self):
        self.profile_combo.clear()
        profs = self.profile_repo.list_profiles()
        for p in profs:
            self.profile_combo.addItem(p.name, p.id)

    def _apply_profile_paths(self):
        prof_id = self.profile_combo.currentData()
        if not prof_id:
            return
        prof = self.profile_repo.get_profile(prof_id)
        if not prof:
            return

        if prof.mappings and prof.mappings[0].source_root and os.path.isdir(prof.mappings[0].source_root):
            self.left_panel.navigate_to(prof.mappings[0].source_root)

        if prof.dest_root and os.path.isdir(prof.dest_root):
            self.right_panel.navigate_to(prof.dest_root)

    def _get_active_panel(self) -> FilePanelWidget:
        """Determines the active panel based on focus or selection."""
        if self.right_panel.table.hasFocus() or (not self.left_panel.table.hasFocus() and bool(self.right_panel.get_selected_paths())):
            return self.right_panel
        return self.left_panel

    def _copy_between_panels(self, src_panel: FilePanelWidget, dst_panel: FilePanelWidget):
        selected = src_panel.get_selected_paths()
        if not selected:
            QMessageBox.information(self, "Копіювання", "Будь ласка, виберіть файли або папки для копіювання.")
            return

        dest_dir = dst_panel.current_path
        if not os.path.isdir(dest_dir):
            QMessageBox.warning(self, "Помилка", f"Цільовий каталог недоступний:\n{dest_dir}")
            return

        # Confirm
        item_names = "\n".join([os.path.basename(p) for p in selected[:5]])
        if len(selected) > 5:
            item_names += f"\n... та ще {len(selected) - 5} елементів"

        res = QMessageBox.question(
            self,
            "Підтвердження копіювання",
            f"Скопіювати {len(selected)} елементів:\n{item_names}\n\nУ цільовий каталог:\n{dest_dir}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if res != QMessageBox.Yes:
            return

        # Setup progress dialog
        progress = QProgressDialog("Копіювання файлів...", "Скасувати", 0, len(selected), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(200)

        self.active_worker = FileCopyWorker(selected, dest_dir)
        self.active_worker.progress_signal.connect(
            lambda cur, tot, name: (progress.setValue(cur), progress.setLabelText(f"Копіювання: {name} ({cur}/{tot})"))
        )
        progress.canceled.connect(self.active_worker.cancel)

        def on_finished(success: bool, err_msg: str, count: int):
            progress.close()
            dst_panel.refresh()
            if success:
                QMessageBox.information(self, "Успіх", f"Успішно скопійовано {count} елементів.")
            else:
                if "скасовано" in err_msg.lower():
                    QMessageBox.warning(self, "Скасовано", f"Копіювання перервано. Скопійовано: {count}.")
                else:
                    QMessageBox.critical(self, "Помилка копіювання", f"Виникла помилка:\n{err_msg}")

        self.active_worker.finished_signal.connect(on_finished)
        self.active_worker.start()

    def _create_folder(self):
        panel = self._get_active_panel()
        folder_name, ok = QInputDialog.getText(self, "Нова папка", "Введіть назву нової папки:")
        if ok and folder_name.strip():
            target_path = os.path.join(panel.current_path, folder_name.strip())
            try:
                os.makedirs(target_path, exist_ok=False)
                panel.refresh()
            except FileExistsError:
                QMessageBox.warning(self, "Помилка", "Папка з такою назвою вже існує.")
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Не вдалося створити папку:\n{e}")

    def _delete_selected(self):
        panel = self._get_active_panel()
        selected = panel.get_selected_paths()
        if not selected:
            QMessageBox.information(self, "Видалення", "Виберіть файли або папки для видалення.")
            return

        item_names = "\n".join([os.path.basename(p) for p in selected[:5]])
        if len(selected) > 5:
            item_names += f"\n... та ще {len(selected) - 5} елементів"

        res = QMessageBox.warning(
            self,
            "Підтвердження видалення",
            f"УВАГА: Ви впевнені, що бажаєте видалити {len(selected)} елементів?\n\n{item_names}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res != QMessageBox.Yes:
            return

        deleted = 0
        errors = []
        for p in selected:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                deleted += 1
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")

        panel.refresh()
        if errors:
            QMessageBox.critical(self, "Помилка видалення", f"Видалено {deleted} елементів.\nПомилки:\n" + "\n".join(errors[:5]))
        else:
            QMessageBox.information(self, "Успіх", f"Успішно видалено {deleted} елементів.")

    def _compare_panels(self):
        """Highlights matching, different and unique files between Left and Right directories."""
        left_path = self.left_panel.current_path
        right_path = self.right_panel.current_path

        if not os.path.isdir(left_path) or not os.path.isdir(right_path):
            QMessageBox.warning(self, "Порівняння", "Обидві панелі повинні мати відкриті дійсні каталоги.")
            return

        # Gather files and folders on both sides
        left_items = {}
        for r in range(self.left_panel.table.rowCount()):
            item = self.left_panel.table.item(r, 0)
            if item and "[..]" not in item.text():
                p = item.data(Qt.UserRole)
                name = os.path.basename(p)
                try:
                    st = os.stat(p)
                    left_items[name] = (r, st.st_size, int(st.st_mtime))
                except Exception:
                    pass

        right_items = {}
        for r in range(self.right_panel.table.rowCount()):
            item = self.right_panel.table.item(r, 0)
            if item and "[..]" not in item.text():
                p = item.data(Qt.UserRole)
                name = os.path.basename(p)
                try:
                    st = os.stat(p)
                    right_items[name] = (r, st.st_size, int(st.st_mtime))
                except Exception:
                    pass

        # Colors for highlighting
        green_color = QColor(27, 85, 45, 120)     # Unique on this side
        yellow_color = QColor(130, 95, 20, 140)   # Different size/mtime

        unique_left = 0
        unique_right = 0
        diff_count = 0
        same_count = 0

        # Check left
        for name, (row_idx, size, mtime) in left_items.items():
            if name not in right_items:
                unique_left += 1
                for c in range(4):
                    self.left_panel.table.item(row_idx, c).setBackground(QBrush(green_color))
            else:
                _, r_size, r_mtime = right_items[name]
                if size != r_size or abs(mtime - r_mtime) > 2:
                    diff_count += 1
                    for c in range(4):
                        self.left_panel.table.item(row_idx, c).setBackground(QBrush(yellow_color))
                else:
                    same_count += 1

        # Check right
        for name, (row_idx, size, mtime) in right_items.items():
            if name not in left_items:
                unique_right += 1
                for c in range(4):
                    self.right_panel.table.item(row_idx, c).setBackground(QBrush(green_color))
            else:
                _, l_size, l_mtime = left_items[name]
                if size != l_size or abs(mtime - l_mtime) > 2:
                    for c in range(4):
                        self.right_panel.table.item(row_idx, c).setBackground(QBrush(yellow_color))

        msg = (
            f"Результати порівняння каталогів:\n\n"
            f"• Тільки зліва (зелений): {unique_left}\n"
            f"• Тільки справа (зелений): {unique_right}\n"
            f"• Відрізняються за розміром/часом (жовтий): {diff_count}\n"
            f"• Однакових: {same_count}"
        )
        QMessageBox.information(self, "Порівняння завершено", msg)

    def _open_duplicate_finder(self, folder_path: Optional[str] = None):
        target = folder_path or self.left_panel.current_path
        dlg = DuplicateFinderDialog(initial_dir=target, parent=self)
        dlg.exec()

    def refresh_both(self):
        self.left_panel.refresh()
        self.right_panel.refresh()
        self._populate_profiles()
