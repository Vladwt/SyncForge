"""Settings and preferences page for SyncForge."""

from typing import Callable, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QComboBox, QCheckBox, QPushButton, QMessageBox
)
from syncforge.i18n.translations import tr, set_language, CURRENT_LANG
from syncforge.storage.repositories import SettingsRepository


class SettingsPage(QWidget):
    """Application preferences and configuration settings page."""

    def __init__(
        self,
        settings_repo: SettingsRepository,
        on_theme_changed: Optional[Callable[[bool], None]] = None,
        on_history_cleared: Optional[Callable[[], None]] = None,
        parent=None
    ):
        super().__init__(parent)
        self.settings_repo = settings_repo
        self.on_theme_changed = on_theme_changed
        self.on_history_cleared = on_history_cleared
        self.is_dark_theme = self.settings_repo.get("theme", "dark") == "dark"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        header = QLabel(tr("settings_title"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        group = QGroupBox("Параметри інтерфейсу та продуктивності")
        g_layout = QVBoxLayout(group)

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

        buf_row = QHBoxLayout()
        buf_lbl = QLabel(tr("buffer_size"))
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItem("1 MiB (Швидко для SSD)", 1024 * 1024)
        self.buffer_combo.addItem("4 MiB (Оптимально для великих файлів)", 4 * 1024 * 1024)
        self.buffer_combo.addItem("512 KiB (Економія пам'яті)", 512 * 1024)
        buf_row.addWidget(buf_lbl)
        buf_row.addWidget(self.buffer_combo)
        buf_row.addStretch()
        g_layout.addLayout(buf_row)

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
        if self.on_theme_changed:
            self.on_theme_changed(self.is_dark_theme)

        QMessageBox.information(self, "Налаштування", tr("settings_saved"))

    def _clear_db_history(self):
        reply = QMessageBox.question(
            self, tr("clear_database"), tr("confirm_clear_db"), QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.settings_repo.clear_all_history()
            if self.on_history_cleared:
                self.on_history_cleared()
            QMessageBox.information(self, "Успіх", "Історію успішно очищено.")
