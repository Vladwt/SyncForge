"""SyncForge application entry points."""

import sys
from PySide6.QtWidgets import QApplication
from syncforge.gui.main_window import MainWindow


def main():
    """Starts the native PySide6 desktop GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName("SyncForge")
    app.setOrganizationName("SyncForge")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
