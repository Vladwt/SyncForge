"""Real-time filesystem change watcher for automatic synchronization trigger."""

import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Set
import structlog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = structlog.get_logger(__name__)


class DebouncedChangeHandler(FileSystemEventHandler):
    """Debounces filesystem events to prevent high-frequency firing."""

    def __init__(self, callback: Callable[[str], None], debounce_seconds: float = 2.0):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._triggered_path = ""

    def on_any_event(self, event: FileSystemEvent):
        # Ignore internal syncforge metadata folders
        p = str(event.src_path).replace("\\", "/")
        if any(ign in p for ign in [".syncforge_marker", ".syncforge_versions", ".tmp", ".git"]):
            return

        with self._lock:
            self._triggered_path = event.src_path
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        with self._lock:
            path = self._triggered_path
            self._timer = None
        try:
            self.callback(path)
        except Exception as e:
            logger.error("Error in fs watcher callback", error=str(e))


class DirectoryWatcher:
    """Watches configured source directories and notifies subscribers on changes."""

    def __init__(self, debounce_seconds: float = 2.0):
        self.debounce_seconds = debounce_seconds
        self.observer: Optional[Observer] = None
        self._watches: Dict[str, object] = {}
        self._callbacks: Set[Callable[[str], None]] = set()
        self._running = False

    def subscribe(self, callback: Callable[[str], None]):
        self._callbacks.add(callback)

    def unsubscribe(self, callback: Callable[[str], None]):
        self._callbacks.discard(callback)

    def start(self):
        if self._running:
            return
        self.observer = Observer()
        self.observer.daemon = True
        self.observer.start()
        self._running = True
        logger.info("DirectoryWatcher started")

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2.0)
            self.observer = None
        self._watches.clear()
        logger.info("DirectoryWatcher stopped")

    def watch_path(self, path_str: str):
        if not self._running or not self.observer:
            return
        path = Path(path_str).resolve()
        if not path.exists() or not path.is_dir():
            return
        norm_key = str(path).lower()
        if norm_key in self._watches:
            return

        handler = DebouncedChangeHandler(self._on_change, self.debounce_seconds)
        try:
            watch = self.observer.schedule(handler, str(path), recursive=True)
            self._watches[norm_key] = watch
            logger.info("Watching directory for changes", path=str(path))
        except Exception as e:
            logger.warning("Could not schedule directory watch", path=str(path), error=str(e))

    def _on_change(self, changed_path: str):
        logger.debug("Filesystem change detected and debounced", path=changed_path)
        for cb in list(self._callbacks):
            try:
                cb(changed_path)
            except Exception as e:
                logger.error("Callback failed in DirectoryWatcher", error=str(e))
