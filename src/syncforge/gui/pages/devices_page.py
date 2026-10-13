"""Devices Page component for scanning, inspecting, and enrolling drives."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QMessageBox
)
from syncforge.core.models import DeviceRole
from syncforge.devices.device_service import DeviceService
from syncforge.i18n.translations import tr, format_bytes
from syncforge.storage.repositories import DeviceRepository


class DevicesPage(QWidget):
    """Storage devices discovery, inspection, and enrollment."""

    def __init__(
        self,
        device_service: DeviceService,
        device_repo: DeviceRepository,
        parent=None
    ):
        super().__init__(parent)
        self.device_service = device_service
        self.device_repo = device_repo
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("devices"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        ref_btn = QPushButton(tr("refresh_devices"))
        ref_btn.clicked.connect(self.refresh)
        enroll_btn = QPushButton(tr("enroll_device"))
        enroll_btn.setProperty("class", "PrimaryBtn")
        enroll_btn.clicked.connect(self._enroll_selected_device)
        btn_row.addWidget(ref_btn)
        btn_row.addWidget(enroll_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            tr("device_name"), tr("mount_point"), tr("volume_serial"), tr("volume_guid"),
            tr("filesystem"), tr("free_total_space"), tr("status")
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def refresh(self):
        """Scans connected volumes and compares with enrolled device records."""
        self.table.setRowCount(0)
        try:
            volumes = self.device_service.discover_connected_volumes()
        except Exception:
            volumes = []
        enrolled = self.device_repo.list_all()

        for vol in volumes:
            row = self.table.rowCount()
            self.table.insertRow(row)

            is_enrolled = False
            for e in enrolled:
                ver, _, _ = self.device_service.verify_device(e, [vol])
                if ver:
                    is_enrolled = True
                    break

            size_str = f"{format_bytes(vol.free_bytes)} / {format_bytes(vol.total_bytes)}"
            self.table.setItem(row, 0, QTableWidgetItem(vol.friendly_name))
            self.table.setItem(row, 1, QTableWidgetItem(vol.current_mount or ""))
            self.table.setItem(row, 2, QTableWidgetItem(vol.volume_serial or "N/A"))
            self.table.setItem(row, 3, QTableWidgetItem(vol.volume_guid or "N/A"))
            self.table.setItem(row, 4, QTableWidgetItem(vol.filesystem or "N/A"))
            self.table.setItem(row, 5, QTableWidgetItem(size_str))

            status_item = QTableWidgetItem(tr("verified") if is_enrolled else tr("unverified"))
            status_item.setForeground(QColor("#57f287") if is_enrolled else QColor("#fee75c"))
            self.table.setItem(row, 6, status_item)

        self.table.resizeColumnsToContents()

    def _enroll_selected_device(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Попередження", tr("select_drive_first"))
            return

        mount = self.table.item(curr_row, 1).text()
        name = self.table.item(curr_row, 0).text()

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
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Не вдалося зареєструвати пристрій: {e}")
