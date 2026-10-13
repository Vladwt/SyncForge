"""Background synchronization orchestrator and execution worker."""

import uuid
from datetime import datetime
from typing import List, Optional, Callable
import structlog
from syncforge.core.models import (
    SyncProfile, PlannedOperation, ExecutionStatus, SyncMode
)
from syncforge.core.planner import SyncPlanner
from syncforge.core.transfer_engine import TransferEngine, TransferProgress
from syncforge.devices.device_service import DeviceService
from syncforge.storage.repositories import BaselineRepository, HistoryRepository, DeviceRepository, TombstoneRepository, RuleRepository

logger = structlog.get_logger(__name__)


class SyncOrchestrator:
    """Orchestrates device verification, planning, dry-run, and safe execution."""

    def __init__(
        self,
        device_service: DeviceService,
        device_repo: DeviceRepository,
        baseline_repo: BaselineRepository,
        history_repo: HistoryRepository,
        tombstone_repo: Optional[TombstoneRepository] = None,
        rule_repo: Optional[RuleRepository] = None,
        planner: Optional[SyncPlanner] = None,
        engine: Optional[TransferEngine] = None
    ):
        self.device_service = device_service
        self.device_repo = device_repo
        self.baseline_repo = baseline_repo
        self.history_repo = history_repo
        self.tombstone_repo = tombstone_repo
        self.rule_repo = rule_repo
        self.planner = planner or SyncPlanner(baseline_repo=baseline_repo, tombstone_repo=tombstone_repo)
        self.engine = engine or TransferEngine(
            baseline_repo=baseline_repo,
            history_repo=history_repo,
            tombstone_repo=tombstone_repo,
            rule_repo=rule_repo
        )

    def cancel(self):
        """Cancels active transfer execution."""
        self.engine.cancel()

    def pause(self):
        """Pauses active transfer execution."""
        self.engine.pause()

    def resume(self):
        """Resumes paused transfer execution."""
        self.engine.resume()

    def is_paused(self) -> bool:
        """Checks if transfer is currently paused."""
        return getattr(self.engine, "_is_paused", False)

    def prepare_dry_run(
        self,
        profile: SyncProfile,
        rule_engine: Optional[object] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[PlannedOperation]:
        """
        Generates a dry-run plan.
        Enforces strict safety check: verifies target device before generating plan.
        """
        active_dest_root = self._resolve_and_verify_dest_root(profile)
        return self.planner.generate_plan(
            profile,
            active_dest_root,
            rule_engine=rule_engine,
            progress_callback=progress_callback
        )

    def execute_sync(
        self,
        profile: SyncProfile,
        operations: Optional[List[PlannedOperation]] = None,
        progress_callback: Optional[Callable[[TransferProgress], None]] = None
    ) -> ExecutionStatus:
        """
        Executes synchronization safely.
        Re-verifies device identity immediately before executing writes.
        """
        job_id = str(uuid.uuid4())
        started_at = datetime.utcnow()

        try:
            # SAFETY RE-VERIFICATION immediately before writes
            active_dest_root = self._resolve_and_verify_dest_root(profile)
            profile.dest_root = active_dest_root

            if operations is None:
                operations = self.planner.generate_plan(profile, active_dest_root)

            status = self.engine.execute_plan(
                job_id=job_id,
                profile=profile,
                operations=operations,
                progress_callback=progress_callback
            )

            files_proc = sum(1 for op in operations if op.status == ExecutionStatus.SUCCESS)
            bytes_trans = sum(op.size_bytes for op in operations if op.status == ExecutionStatus.SUCCESS)
            errs = sum(1 for op in operations if op.status == ExecutionStatus.FAILED)
            confls = sum(1 for op in operations if op.status == ExecutionStatus.CONFLICT)

            # Build informative audit notes
            abort_reason = getattr(self.engine, "last_abort_reason", None)
            if abort_reason:
                notes = f"Перервано: {abort_reason}"
            elif status == ExecutionStatus.FAILED:
                first_err = next((op.error_message for op in operations if op.status == ExecutionStatus.FAILED and op.error_message), None)
                err_detail = f" ({first_err})" if first_err else ""
                notes = f"Частково: {files_proc} успішно, {errs} помилок{err_detail}"
            elif status == ExecutionStatus.CANCELLED:
                notes = f"Скасовано користувачем (успішно: {files_proc})"
            elif confls > 0:
                notes = f"Завершено: {files_proc} скопійовано, {confls} конфліктів"
            else:
                notes = "Успішно виконано"

            self.history_repo.record_job(
                job_id=job_id,
                profile_id=profile.id,
                mode=profile.mode.value,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                status=status.value,
                files_processed=files_proc,
                bytes_transferred=bytes_trans,
                errors_count=errs,
                conflicts_count=confls,
                notes=notes
            )
            return status

        except Exception as e:
            logger.error("Job execution aborted due to safety or transfer failure", job_id=job_id, error=str(e))
            self.history_repo.record_job(
                job_id=job_id,
                profile_id=profile.id,
                mode=profile.mode.value,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                status=ExecutionStatus.FAILED.value,
                files_processed=0,
                bytes_transferred=0,
                errors_count=1,
                conflicts_count=0,
                notes=str(e)
            )
            raise e

    def _resolve_and_verify_dest_root(self, profile: SyncProfile) -> str:
        """
        Verifies target device identity signals and dynamically resolves its current mount path.
        SAFETY REQUIREMENT: If identification fails or is ambiguous, BLOCK writing!
        """
        if profile.target_device_id:
            enrolled = self.device_repo.get(profile.target_device_id)
            if not enrolled:
                raise PermissionError(f"Target device '{profile.target_device_id}' is not enrolled in database.")

            verified, mount_path, explanation = self.device_service.verify_device(enrolled)
            if not verified or not mount_path:
                raise PermissionError(f"SAFETY POLICY VIOLATION: Device verification failed! {explanation}")

            # Return active mount path
            return mount_path
        elif profile.dest_root:
            return profile.dest_root
        else:
            raise ValueError("No destination root or target device configured for profile.")
