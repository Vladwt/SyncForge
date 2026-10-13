"""History Page component displaying past synchronization execution runs."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView
)
from syncforge.i18n.translations import tr, format_bytes
from syncforge.storage.repositories import HistoryRepository


class HistoryPage(QWidget):
    """History log page with formatted columns and clear display."""

    def __init__(self, history_repo: HistoryRepository, parent=None):
        super().__init__(parent)
        self.history_repo = history_repo
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel(tr("history"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            tr("job_time"), tr("history_profile"), tr("job_status"), tr("files_count"), tr("bytes_count"), tr("history_details")
        ])

        h_hdr = self.table.horizontalHeader()
        h_hdr.setSectionResizeMode(QHeaderView.Interactive)
        h_hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)  # Time
        self.table.setColumnWidth(1, 240)  # Profile name
        self.table.setColumnWidth(2, 120)  # Status
        self.table.setColumnWidth(3, 110)  # Files count
        self.table.setColumnWidth(4, 120)  # Bytes count
        layout.addWidget(self.table)

    def refresh(self):
        """Loads latest history jobs from repository."""
        self.table.setRowCount(0)
        jobs = self.history_repo.list_jobs(50)
        for j in jobs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(j.get("started_at"))))

            prof_name = j.get("profile_name") or j.get("profile_id", "")
            self.table.setItem(row, 1, QTableWidgetItem(prof_name))

            status_str = (j.get("status") or "").upper()
            st_item = QTableWidgetItem(status_str)
            st_item.setForeground(QColor("#57f287") if status_str == "SUCCESS" else QColor("#ed4245"))
            self.table.setItem(row, 2, st_item)
            self.table.setItem(row, 3, QTableWidgetItem(str(j.get("files_processed", 0))))
            self.table.setItem(row, 4, QTableWidgetItem(format_bytes(j.get("bytes_transferred", 0))))

            note_text = j.get("notes") or ("Успішно виконано" if status_str == "SUCCESS" else "Помилка виконання")
            note_item = QTableWidgetItem(note_text)
            note_item.setToolTip(note_text)
            self.table.setItem(row, 5, note_item)
