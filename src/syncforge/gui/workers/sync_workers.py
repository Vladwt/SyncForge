"""Background Qt worker threads for scanning and executing synchronization."""

from typing import List, Optional, Tuple
import structlog
from PySide6.QtCore import QThread, Signal

from syncforge.core.models import PlannedOperation, SyncProfile, ExecutionStatus
from syncforge.core.planner import _sort_plan_key
from syncforge.core.transfer_engine import TransferProgress
from syncforge.rules.rule_engine import RuleEngine
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.storage.repositories import RuleRepository, PlanRepository

logger = structlog.get_logger(__name__)


class PreviewScanWorker(QThread):
    """Background worker thread for generating dry-run preview plans without freezing the GUI."""
    progress = Signal(int, str)
    scan_finished = Signal(list, object)  # (plan, profile_or_none)
    scan_error = Signal(str)

    def __init__(
        self,
        orchestrator: SyncOrchestrator,
        rule_repo: RuleRepository,
        profiles: List[SyncProfile],
        plan_repo: Optional[PlanRepository] = None,
        is_all: bool = False
    ):
        super().__init__()
        self.orchestrator = orchestrator
        self.rule_repo = rule_repo
        self.profiles = profiles
        self.plan_repo = plan_repo
        self.is_all = is_all

    def run(self):
        try:
            if not self.profiles:
                self.progress.emit(100, "Немає профілів для аналізу")
                self.scan_finished.emit([], None)
                return

            if not self.is_all:
                profile = self.profiles[0]
                active_rules = self.rule_repo.list_rules(profile.id)
                engine = RuleEngine(active_rules)
                plan = self.orchestrator.prepare_dry_run(
                    profile,
                    rule_engine=engine,
                    progress_callback=self._emit_progress
                )
                if self.plan_repo:
                    self.progress.emit(98, "Збереження плану в базу даних...")
                    self.plan_repo.save_plan(profile.id, plan)
                self.progress.emit(100, "Аналіз завершено")
                self.scan_finished.emit(plan, profile)
            else:
                total_profs = len(self.profiles)
                all_ops: List[PlannedOperation] = []
                for idx, p in enumerate(self.profiles):
                    active_rules = self.rule_repo.list_rules(p.id)
                    engine = RuleEngine(active_rules)
                    prof_slice = 95.0 / total_profs
                    base_offset = idx * prof_slice

                    def sub_cb(pct, msg, b=base_offset, s=prof_slice, pname=p.name):
                        overall = int(b + (pct / 100.0) * s)
                        self.progress.emit(min(95, overall), f"[{pname}] {msg}")

                    p_ops = self.orchestrator.prepare_dry_run(
                        p,
                        rule_engine=engine,
                        progress_callback=sub_cb
                    )
                    all_ops.extend(p_ops)
                    if self.plan_repo:
                        self.plan_repo.save_plan(p.id, p_ops)

                all_ops.sort(key=_sort_plan_key)
                self.progress.emit(100, "Аналіз завершено")
                self.scan_finished.emit(all_ops, None)
        except Exception as e:
            logger.exception("Preview scan worker encountered error", error=str(e))
            self.scan_error.emit(str(e))

    def _emit_progress(self, percent: int, msg: str):
        self.progress.emit(percent, msg)


class SyncExecutionWorker(QThread):
    """Background worker thread for running plan execution safely without freezing the Qt GUI."""
    progress = Signal(object)  # TransferProgress
    execution_finished = Signal(object, list, list)  # (ExecutionStatus, failed_ops, successful_ops)
    execution_error = Signal(str)

    def __init__(
        self,
        orchestrator: SyncOrchestrator,
        items: List[Tuple[SyncProfile, List[PlannedOperation]]]
    ):
        super().__init__()
        self.orchestrator = orchestrator
        self.items = items
        self.is_aborted = False
        self.orchestrator.reset()

    def pause(self):
        """Pauses the underlying orchestrator execution."""
        self.orchestrator.pause()

    def resume(self):
        """Resumes the underlying orchestrator execution."""
        self.orchestrator.resume()

    def cancel(self):
        """Cancels the underlying orchestrator execution."""
        self.is_aborted = True
        self.orchestrator.cancel()

    def is_paused(self) -> bool:
        """Returns True if execution is paused."""
        return self.orchestrator.is_paused()

    def run(self):
        try:
            total_ops_count = sum(len(ops) for _, ops in self.items)
            completed_overall = 0
            all_failed: List[PlannedOperation] = []
            all_successful: List[PlannedOperation] = []
            last_status = ExecutionStatus.SUCCESS

            for profile, ops in self.items:
                if self.is_aborted or getattr(self.orchestrator, "_is_cancelled", False):
                    last_status = ExecutionStatus.CANCELLED
                    break

                def sub_progress(p: TransferProgress, base=completed_overall, total=total_ops_count):
                    # Wrap progress to reflect overall batch progress
                    cur_completed = base + p.completed_operations
                    p_copy = TransferProgress(
                        total_operations=total,
                        completed_operations=cur_completed,
                        total_bytes=p.total_bytes,
                        transferred_bytes=p.transferred_bytes,
                        current_file=p.current_file,
                        current_speed_bps=p.current_speed_bps,
                        status_text=p.status_text
                    )
                    self.progress.emit(p_copy)

                status = self.orchestrator.execute_sync(profile, ops, sub_progress)
                if status == ExecutionStatus.FAILED:
                    last_status = ExecutionStatus.FAILED
                elif status == ExecutionStatus.CANCELLED:
                    last_status = ExecutionStatus.CANCELLED

                for op in ops:
                    if op.status == ExecutionStatus.FAILED:
                        all_failed.append(op)
                    elif op.status == ExecutionStatus.SUCCESS:
                        all_successful.append(op)

                completed_overall += len(ops)

            if last_status == ExecutionStatus.CANCELLED or self.is_aborted or getattr(self.orchestrator, "_is_cancelled", False):
                final_status = ExecutionStatus.CANCELLED
            elif not all_failed and last_status == ExecutionStatus.SUCCESS:
                final_status = ExecutionStatus.SUCCESS
            else:
                final_status = ExecutionStatus.FAILED

            self.execution_finished.emit(final_status, all_failed, all_successful)

        except Exception as e:
            logger.error("Sync execution worker crashed", error=str(e))
            self.execution_error.emit(str(e))
