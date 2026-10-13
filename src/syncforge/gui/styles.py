"""QSS Stylesheets and UI theme utilities for SyncForge."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QFont, QColor, QPixmap, QPainter

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #18191c;
    color: #e3e5e8;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#NavPanel {
    background-color: #121315;
    border-right: 1px solid #2b2d31;
}
QPushButton#NavBtn {
    background-color: transparent;
    color: #949ba4;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-weight: 500;
}
QPushButton#NavBtn:hover {
    background-color: #2b2d31;
    color: #f2f3f5;
}
QPushButton#NavBtn:checked {
    background-color: #35373c;
    color: #5865f2;
    font-weight: bold;
}
QPushButton {
    background-color: #2b2d31;
    color: #f2f3f5;
    border: 1px solid #35373c;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #35373c;
    border-color: #4e5058;
}
QPushButton.PrimaryBtn {
    background-color: #5865f2;
    color: white;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
    border: none;
}
QPushButton.PrimaryBtn:hover {
    background-color: #4752c4;
}
QPushButton.DangerBtn {
    background-color: #da373c;
    color: white;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
    border: none;
}
QPushButton.DangerBtn:hover {
    background-color: #a1282c;
}
QTableWidget {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    gridline-color: #2b2d31;
    selection-background-color: #35373c;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #2b2d31;
    color: #e3e5e8;
    padding: 8px;
    border: none;
    font-weight: bold;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #1e1f22;
    border: 1px solid #35373c;
    border-radius: 4px;
    padding: 6px;
    color: #f2f3f5;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #5865f2;
}
QComboBox QAbstractItemView {
    background-color: #1e1f22;
    border: 1px solid #35373c;
    selection-background-color: #5865f2;
    color: #f2f3f5;
}
QProgressBar {
    background-color: #1e1f22;
    border-radius: 4px;
    text-align: center;
    color: white;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #5865f2;
    border-radius: 4px;
}
QFrame.Card {
    background-color: #1e1f22;
    border: 1px solid #2b2d31;
    border-radius: 8px;
    padding: 14px;
}
QGroupBox {
    border: 1px solid #2b2d31;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #949ba4;
}
"""

LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #f2f3f5;
    color: #2e3338;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#NavPanel {
    background-color: #e3e5e8;
    border-right: 1px solid #d1d4d7;
}
QPushButton#NavBtn {
    background-color: transparent;
    color: #4e5058;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-weight: 500;
}
QPushButton#NavBtn:hover {
    background-color: #d1d4d7;
    color: #060607;
}
QPushButton#NavBtn:checked {
    background-color: #ffffff;
    color: #5865f2;
    font-weight: bold;
}
QPushButton {
    background-color: #ffffff;
    color: #2e3338;
    border: 1px solid #c7ccd1;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #e3e5e8;
}
QPushButton.PrimaryBtn {
    background-color: #5865f2;
    color: white;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
    border: none;
}
QPushButton.PrimaryBtn:hover {
    background-color: #4752c4;
}
QTableWidget {
    background-color: #ffffff;
    border: 1px solid #d1d4d7;
    gridline-color: #e3e5e8;
    selection-background-color: #e3e5e8;
    selection-color: #060607;
}
QHeaderView::section {
    background-color: #e3e5e8;
    color: #2e3338;
    padding: 8px;
    border: none;
    font-weight: bold;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #ffffff;
    border: 1px solid #c7ccd1;
    border-radius: 4px;
    padding: 6px;
    color: #2e3338;
}
QFrame.Card {
    background-color: #ffffff;
    border: 1px solid #d1d4d7;
    border-radius: 8px;
    padding: 14px;
}
"""


def create_app_icon() -> QIcon:
    """Generate high-contrast 32x32 icon with letters SF."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#5865f2"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
    painter.setPen(QColor("white"))
    f = QFont("Segoe UI", 12, QFont.Bold)
    painter.setFont(f)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "SF")
    painter.end()
    return QIcon(pixmap)
