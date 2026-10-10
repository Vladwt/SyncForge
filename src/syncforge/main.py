"""SyncForge application entry points."""

import sys
from PySide6.QtWidgets import QApplication
from syncforge.services.logger_config import configure_logging
from syncforge.gui.main_window import MainWindow


def main():
    """Starts the native PySide6 desktop GUI."""
    configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("SyncForge")
    app.setOrganizationName("SyncForge")
    app.setQuitOnLastWindowClosed(False)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
