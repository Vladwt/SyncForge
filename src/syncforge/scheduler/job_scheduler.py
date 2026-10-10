"""Background job scheduler and automated device-triggered synchronization."""

import threading
import time
from datetime import datetime
from typing import Dict, List, Optional
import structlog
from syncforge.core.models import SyncProfile
from syncforge.devices.device_service import DeviceService
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.storage.repositories import ProfileRepository, DeviceRepository

logger = structlog.get_logger(__name__)


class JobScheduler:
    """Manages scheduled periodic jobs and device connect triggers."""

    def __init__(
        self,
        profile_repo: ProfileRepository,
        device_repo: DeviceRepository,
        device_service: DeviceService,
        orchestrator: SyncOrchestrator,
        poll_interval_sec: int = 10
    ):
        self.profile_repo = profile_repo
        self.device_repo = device_repo
        self.device_service = device_service
        self.orchestrator = orchestrator
        self.poll_interval_sec = poll_interval_sec

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_profile_runs: Dict[str, float] = {}
        self._known_connected_devices: set = set()

    def start(self):
        """Starts background monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="SyncForgeScheduler")
        self._thread.start()
        logger.info("Job scheduler started")

    def stop(self):
        """Stops background monitoring thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Job scheduler stopped")

    def _run_loop(self):
        while self._running:
            try:
                self._check_device_triggers()
                self._check_periodic_schedules()
            except Exception as e:
                logger.error("Error in scheduler loop", error=str(e))

            time.sleep(self.poll_interval_sec)

    def _check_device_triggers(self):
        """Detects freshly connected enrolled devices and triggers auto_sync_on_connect jobs."""
        current_volumes = self.device_service.discover_connected_volumes()
        currently_verified_ids = set()

        enrolled_devices = self.device_repo.list_all()
        for enrolled in enrolled_devices:
            verified, mount, _ = self.device_service.verify_device(enrolled, current_volumes)
            if verified and mount:
                currently_verified_ids.add(enrolled.device_id)

                # Check if device was just plugged in
                if enrolled.device_id not in self._known_connected_devices:
                    logger.info("Enrolled device plugged in", device=enrolled.friendly_name, id=enrolled.device_id)
                    self._trigger_device_connect_profiles(enrolled.device_id)

        self._known_connected_devices = currently_verified_ids

    def _trigger_device_connect_profiles(self, device_id: str):
        profiles = self.profile_repo.list_profiles()
        for p in profiles:
            if p.enabled and p.auto_sync_on_connect and p.target_device_id == device_id:
                logger.info("Triggering automatic connect sync for profile", profile=p.name)
                try:
                    self.orchestrator.execute_sync(p)
                except Exception as e:
                    logger.error("Auto-sync execution failed", profile=p.name, error=str(e))

    def _check_periodic_schedules(self):
        """Checks profiles with auto_sync_interval_mins configured."""
        now = time.time()
        profiles = self.profile_repo.list_profiles()
        for p in profiles:
            if not p.enabled or p.auto_sync_interval_mins <= 0:
                continue

            interval_sec = p.auto_sync_interval_mins * 60
            last_run = self._last_profile_runs.get(p.id, 0.0)
            if now - last_run >= interval_sec:
                logger.info("Triggering periodic sync for profile", profile=p.name)
                self._last_profile_runs[p.id] = now
                try:
                    self.orchestrator.execute_sync(p)
                except Exception as e:
                    logger.error("Periodic sync execution failed", profile=p.name, error=str(e))
