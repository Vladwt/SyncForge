"""Profiles Management Page for SyncForge."""

from typing import Callable, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QMessageBox, QFileDialog
)
from syncforge.core.models import SyncProfile, SyncMode
from syncforge.devices.device_service import DeviceService
from syncforge.i18n.translations import tr
from syncforge.storage.repositories import ProfileRepository, DeviceRepository
from syncforge.gui.dialogs import ProfileEditorDialog


class ProfilesPage(QWidget):
    """Synchronization profiles list, CRUD, export, import, and direct scan triggering."""

    def __init__(
        self,
        profile_repo: ProfileRepository,
        device_repo: DeviceRepository,
        device_service: DeviceService,
        on_run_preview: Callable[[SyncProfile], None],
        on_profiles_changed: Optional[Callable[[], None]] = None,
        parent=None
    ):
        super().__init__(parent)
        self.profile_repo = profile_repo
        self.device_repo = device_repo
        self.device_service = device_service
        self.on_run_preview = on_run_preview
        self.on_profiles_changed = on_profiles_changed
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
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

        export_btn = QPushButton("📤 " + "Експорт профілів")
        export_btn.setToolTip("Експортувати налаштування всіх профілів та їхніх правил у JSON файл")
        export_btn.clicked.connect(self._export_profiles_action)
        top_bar.addWidget(export_btn)

        import_btn = QPushButton("📥 " + "Імпорт профілів")
        import_btn.setToolTip("Імпортувати конфігурацію профілів із JSON файлу")
        import_btn.clicked.connect(self._import_profiles_action)
        top_bar.addWidget(import_btn)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            tr("profile_name"), tr("sync_mode"), tr("dest_column"), tr("folders_count"), tr("actions")
        ])

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setStretchLastSection(False)
        self.table.setColumnWidth(0, 240)  # Profile name
        self.table.setColumnWidth(1, 310)  # Mode
        self.table.setColumnWidth(2, 260)  # Dest folder
        self.table.setColumnWidth(3, 75)   # Folders count
        self.table.setColumnWidth(4, 340)  # Actions
        layout.addWidget(self.table)

    def _get_friendly_mode_title(self, mode: SyncMode) -> str:
        if mode == SyncMode.ONE_WAY_SAFE:
            return tr("mode_one_way")
        elif mode == SyncMode.BIDIRECTIONAL:
            return tr("mode_bidirectional")
        elif mode == SyncMode.VERSIONED_BACKUP:
            return tr("mode_backup")
        return mode.value

    def refresh(self):
        """Reloads profiles from database into table."""
        self.table.setRowCount(0)
        profs = self.profile_repo.list_profiles()
        devices = self.device_repo.list_all()
        dev_map = {d.device_id: d.friendly_name for d in devices}

        for p in profs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(p.name))
            self.table.setItem(row, 1, QTableWidgetItem(self._get_friendly_mode_title(p.mode)))

            dest_display = p.dest_root or (dev_map.get(p.target_device_id, "Зареєстрований SSD"))
            self.table.setItem(row, 2, QTableWidgetItem(dest_display))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(p.mappings))))

            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(4, 2, 4, 2)
            act_layout.setSpacing(6)

            run_btn = QPushButton("Аналіз")
            run_btn.setProperty("class", "PrimaryBtn")
            run_btn.setStyleSheet("padding: 4px 10px; font-size: 11px; font-weight: bold;")
            run_btn.setToolTip("Створити попередній план синхронізації")
            run_btn.clicked.connect(lambda checked, prof=p: self.on_run_preview(prof))

            edit_btn = QPushButton("Редагувати")
            edit_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
            edit_btn.setToolTip("Налаштувати папки та параметри")
            edit_btn.clicked.connect(lambda checked, prof=p: self._edit_profile_dialog(prof))

            del_btn = QPushButton("Видалити")
            del_btn.setProperty("class", "DangerBtn")
            del_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
            del_btn.setToolTip("Видалити цей профіль")
            del_btn.clicked.connect(lambda checked, prof=p: self._delete_profile(prof))

            act_layout.addWidget(run_btn)
            act_layout.addWidget(edit_btn)
            act_layout.addWidget(del_btn)
            act_layout.addStretch()
            self.table.setCellWidget(row, 4, act_widget)

    def _create_new_profile_dialog(self):
        try:
            devs = self.device_service.discover_connected_volumes()
        except Exception:
            devs = []
        dialog = ProfileEditorDialog(self, profile=None, devices=devs)
        if dialog.exec():
            new_prof = dialog.result_profile
            if new_prof:
                self.profile_repo.save_profile(new_prof)
                self.refresh()
                if self.on_profiles_changed:
                    self.on_profiles_changed()

    def _edit_profile_dialog(self, profile: SyncProfile):
        try:
            devs = self.device_service.discover_connected_volumes()
        except Exception:
            devs = []
        dialog = ProfileEditorDialog(self, profile=profile, devices=devs)
        if dialog.exec():
            updated_prof = dialog.result_profile
            if updated_prof:
                self.profile_repo.save_profile(updated_prof)
                self.refresh()
                if self.on_profiles_changed:
                    self.on_profiles_changed()

    def _delete_profile(self, profile: SyncProfile):
        reply = QMessageBox.question(
            self, tr("delete_profile"),
            tr("confirm_delete_profile").format(name=profile.name),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.profile_repo.delete_profile(profile.id)
            self.refresh()
            if self.on_profiles_changed:
                self.on_profiles_changed()

    def _export_profiles_action(self):
        profs = self.profile_repo.list_profiles()
        if not profs:
            QMessageBox.warning(self, "Попередження", "Немає профілів для експорту.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Експорт профілів",
            "syncforge_profiles.json",
            "JSON файли (*.json);;Усі файли (*.*)"
        )
        if not file_path:
            return

        try:
            count = self.profile_repo.export_profiles(file_path)
            QMessageBox.information(
                self,
                "Успіх",
                f"Успішно експортовано {count} профілів у файл:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося експортувати профілі: {e}")

    def _import_profiles_action(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Імпорт профілів",
            "",
            "JSON файли (*.json);;Усі файли (*.*)"
        )
        if not file_path:
            return

        try:
            count = self.profile_repo.import_profiles(file_path)
            self.refresh()
            if self.on_profiles_changed:
                self.on_profiles_changed()
            QMessageBox.information(
                self,
                "Успіх",
                f"Успішно імпортовано {count} профілів із файлу:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Помилка імпорту", f"Не вдалося імпортувати профілі:\n{e}")
