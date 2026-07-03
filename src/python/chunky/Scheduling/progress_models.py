from __future__ import annotations

"""Public progress snapshots and the small internal tracker that builds them."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import logging
from threading import RLock
from typing import Callable, Iterator, TypeAlias
from uuid import uuid4

LOGGER = logging.getLogger(__name__)


class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageBlockedReason(str, Enum):
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    WAITING_FOR_OTHER_TASK = "waiting_for_other_task"


@dataclass(frozen=True)
class StageSubtaskProgress:
    subtask_id: str
    name: str
    percentage: float | None = None


@dataclass(frozen=True)
class StageProgressSnapshot:
    invocation_id: str
    stage_id: str
    display_name: str
    subtasks: tuple[StageSubtaskProgress, ...] = ()
    blocked_reason: StageBlockedReason | None = None
    blocked_description: str | None = None
    blocking_task_id: str | None = None


@dataclass(frozen=True)
class RunProgressSnapshot:
    task_id: str
    task_state: TaskState
    total_stage_count: int
    finished_stage_count: int
    pending_stage_count: int
    running_stage_count: int
    blocked_stage_count: int
    pending_stages: tuple[StageProgressSnapshot, ...]
    running_stages: tuple[StageProgressSnapshot, ...]
    blocked_stages: tuple[StageProgressSnapshot, ...]


RunProgressHandler: TypeAlias = Callable[[RunProgressSnapshot], None]


class StageSubtaskReporter:
    def __init__(
        self,
        parent: "StageProgressReporter",
        subtask_id: str,
        name: str,
    ) -> None:
        self._parent = parent
        self.subtask_id = subtask_id
        self.name = name
        self._finished = False
        self.update()

    def update(
        self,
        *,
        name: str | None = None,
        percentage: float | None = None,
    ) -> None:
        if self._finished:
            return
        if name is not None:
            self.name = name
        if percentage is not None and not 0.0 <= percentage <= 100.0:
            raise ValueError("percentage must be between 0 and 100")
        self._parent._update(self.subtask_id, self.name, percentage)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._parent._remove(self.subtask_id)


class StageProgressReporter:
    """Scoped reporter for one planned stage invocation."""

    def __init__(
        self,
        tracker: "RunProgressTracker",
        invocation_id: str,
    ) -> None:
        self._tracker = tracker
        self._invocation_id = invocation_id

    def start_subtask(
        self,
        subtask_id: str,
        name: str,
        *,
        percentage: float | None = None,
    ) -> StageSubtaskReporter:
        reporter = StageSubtaskReporter(self, subtask_id, name)
        if percentage is not None:
            reporter.update(percentage=percentage)
        return reporter

    def _update(
        self,
        subtask_id: str,
        name: str,
        percentage: float | None,
    ) -> None:
        self._tracker.update_subtask(
            self._invocation_id,
            StageSubtaskProgress(
                subtask_id=subtask_id,
                name=name,
                percentage=percentage,
            ),
        )

    def _remove(self, subtask_id: str) -> None:
        self._tracker.remove_subtask(self._invocation_id, subtask_id)


class ProgressSubscription:
    def __init__(self, detach: Callable[[], None]) -> None:
        self._detach = detach
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._detach()


class ProgressDispatcher:
    """Minimal non-blocking callback dispatcher.

    Handler exceptions are logged and never flow back into task execution.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, RunProgressHandler] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="chunky-progress-example",
        )

    def subscribe(self, handler: RunProgressHandler) -> ProgressSubscription:
        key = uuid4().hex
        with self._lock:
            self._handlers[key] = handler
        return ProgressSubscription(lambda: self._detach(key))

    def _detach(self, key: str) -> None:
        with self._lock:
            self._handlers.pop(key, None)

    def publish(self, snapshot: RunProgressSnapshot) -> None:
        with self._lock:
            handlers = tuple(self._handlers.values())
        for handler in handlers:
            self._executor.submit(self._safe_call, handler, snapshot)

    @staticmethod
    def _safe_call(
        handler: RunProgressHandler,
        snapshot: RunProgressSnapshot,
    ) -> None:
        try:
            handler(snapshot)
        except Exception:
            LOGGER.exception("progress handler failed")

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


class NullProgressDispatcher(ProgressDispatcher):
    """Dispatcher variant used when callers do not need progress events."""

    def __init__(self) -> None:
        pass

    def subscribe(self, handler: RunProgressHandler) -> ProgressSubscription:
        return ProgressSubscription(lambda: None)

    def publish(self, snapshot: RunProgressSnapshot) -> None:
        return

    def close(self) -> None:
        return


@dataclass
class _MutableStageProgress:
    invocation_id: str
    stage_id: str
    display_name: str
    state: StageState = StageState.PENDING
    subtasks: dict[str, StageSubtaskProgress] | None = None
    blocked_reason: StageBlockedReason | None = None
    blocked_description: str | None = None
    blocking_task_id: str | None = None

    def __post_init__(self) -> None:
        if self.subtasks is None:
            self.subtasks = {}


class RunProgressTracker:
    """Builds and publishes a new snapshot only when public progress changes."""

    def __init__(self, task_id: str, dispatcher: ProgressDispatcher) -> None:
        self.task_id = task_id
        self._dispatcher = dispatcher
        self._task_state = TaskState.CREATED
        self._stages: dict[str, _MutableStageProgress] = {}
        self._last_snapshot: RunProgressSnapshot | None = None
        self._lock = RLock()
        self._batch_depth = 0
        self._batch_dirty = False

    @contextmanager
    def batch_update(self) -> Iterator[None]:
        with self._lock:
            self._batch_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._batch_depth -= 1
                if self._batch_depth == 0 and self._batch_dirty:
                    self._batch_dirty = False
                    self._publish_if_changed()

    def register_stage(
        self,
        invocation_id: str,
        stage_id: str,
        display_name: str,
    ) -> None:
        with self._lock:
            self._stages[invocation_id] = _MutableStageProgress(
                invocation_id=invocation_id,
                stage_id=stage_id,
                display_name=display_name,
            )
            self._publish_if_changed()

    def set_task_state(self, state: TaskState) -> None:
        with self._lock:
            self._task_state = state
            self._publish_if_changed()

    def mark_pending(self, invocation_id: str) -> None:
        self._set_stage_state(invocation_id, StageState.PENDING)

    def mark_running(self, invocation_id: str) -> None:
        self._set_stage_state(invocation_id, StageState.RUNNING)

    def mark_finished(self, invocation_id: str) -> None:
        self._set_stage_state(invocation_id, StageState.FINISHED)

    def mark_failed(self, invocation_id: str) -> None:
        self._set_stage_state(invocation_id, StageState.FAILED)

    def mark_cancelled(self, invocation_id: str) -> None:
        self._set_stage_state(invocation_id, StageState.CANCELLED)

    def mark_blocked(
        self,
        invocation_id: str,
        reason: StageBlockedReason,
        description: str,
        *,
        blocking_task_id: str | None = None,
    ) -> None:
        with self._lock:
            stage = self._stages[invocation_id]
            stage.state = StageState.BLOCKED
            stage.blocked_reason = reason
            stage.blocked_description = description
            stage.blocking_task_id = blocking_task_id
            self._publish_if_changed()

    def clear_blocked(self, invocation_id: str) -> None:
        with self._lock:
            stage = self._stages[invocation_id]
            if stage.state == StageState.BLOCKED:
                stage.state = StageState.PENDING
            stage.blocked_reason = None
            stage.blocked_description = None
            stage.blocking_task_id = None
            self._publish_if_changed()

    def update_subtask(
        self,
        invocation_id: str,
        subtask: StageSubtaskProgress,
    ) -> None:
        with self._lock:
            self._stages[invocation_id].subtasks[subtask.subtask_id] = subtask
            self._publish_if_changed()

    def remove_subtask(self, invocation_id: str, subtask_id: str) -> None:
        with self._lock:
            self._stages[invocation_id].subtasks.pop(subtask_id, None)
            self._publish_if_changed()

    def get_snapshot(self) -> RunProgressSnapshot:
        with self._lock:
            return self._build_snapshot()

    def _set_stage_state(self, invocation_id: str, state: StageState) -> None:
        with self._lock:
            stage = self._stages[invocation_id]
            stage.state = state
            if state != StageState.BLOCKED:
                stage.blocked_reason = None
                stage.blocked_description = None
                stage.blocking_task_id = None
            self._publish_if_changed()

    def _publish_if_changed(self) -> None:
        if self._batch_depth:
            self._batch_dirty = True
            return
        snapshot = self._build_snapshot()
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        self._dispatcher.publish(snapshot)

    def _build_snapshot(self) -> RunProgressSnapshot:
        pending: list[StageProgressSnapshot] = []
        running: list[StageProgressSnapshot] = []
        blocked: list[StageProgressSnapshot] = []
        finished_count = 0

        for stage in sorted(self._stages.values(), key=lambda item: item.invocation_id):
            snapshot = StageProgressSnapshot(
                invocation_id=stage.invocation_id,
                stage_id=stage.stage_id,
                display_name=stage.display_name,
                subtasks=tuple(sorted(stage.subtasks.values(), key=lambda item: item.subtask_id)),
                blocked_reason=stage.blocked_reason,
                blocked_description=stage.blocked_description,
                blocking_task_id=stage.blocking_task_id,
            )
            if stage.state == StageState.PENDING:
                pending.append(snapshot)
            elif stage.state == StageState.RUNNING:
                running.append(snapshot)
            elif stage.state == StageState.BLOCKED:
                blocked.append(snapshot)
            elif stage.state == StageState.FINISHED:
                finished_count += 1
            elif stage.state in (StageState.FAILED, StageState.CANCELLED):
                finished_count += 1

        total = len(self._stages)
        return RunProgressSnapshot(
            task_id=self.task_id,
            task_state=self._task_state,
            total_stage_count=total,
            finished_stage_count=finished_count,
            pending_stage_count=len(pending),
            running_stage_count=len(running),
            blocked_stage_count=len(blocked),
            pending_stages=tuple(pending),
            running_stages=tuple(running),
            blocked_stages=tuple(blocked),
        )


def ensure_progress_tracker(
    task_id: str,
    progress_tracker: RunProgressTracker | None,
) -> RunProgressTracker:
    if progress_tracker is not None:
        return progress_tracker
    return RunProgressTracker(task_id, NullProgressDispatcher())


__all__ = [
    "ensure_progress_tracker",
    "NullProgressDispatcher",
    "ProgressDispatcher",
    "ProgressSubscription",
    "RunProgressHandler",
    "RunProgressSnapshot",
    "RunProgressTracker",
    "StageBlockedReason",
    "StageProgressReporter",
    "StageProgressSnapshot",
    "StageSubtaskProgress",
    "StageSubtaskReporter",
    "TaskState",
]
