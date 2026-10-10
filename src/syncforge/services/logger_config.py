"""Structured logging configuration for SyncForge."""

import io
import os
import sys
import logging
from pathlib import Path
import structlog


class NullWriter:
    """Null stream writer to safely handle sys.stdout/stderr when None in GUI mode."""
    def write(self, s):
        pass
    def flush(self):
        pass


def get_log_file_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        log_dir = Path(appdata) / "SyncForge" / "logs"
    else:
        log_dir = Path.home() / ".syncforge" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "syncforge.log"


def configure_logging():
    """
    Safely configures structlog for both GUI (windowed) and console/test modes.
    Guarantees sys.stdout and sys.stderr are valid file-like objects to avoid weakref errors.
    Logs to a rotating or appending file in AppData as well as stream if available.
    """
    # Fix NoneType for windowed PyInstaller executables
    if sys.stdout is None:
        sys.stdout = NullWriter()
    if sys.stderr is None:
        sys.stderr = NullWriter()

    log_file = get_log_file_path()

    # Standard library logging setup so structlog routes safely to both file and stream
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers on re-configuration
    if not root_logger.handlers:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)

        if not isinstance(sys.stdout, NullWriter):
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setLevel(logging.INFO)
            root_logger.addHandler(stream_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if os.environ.get("SYNCFORGE_LOG_JSON") else structlog.dev.ConsoleRenderer(colors=False)
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
