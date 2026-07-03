from __future__ import annotations

"""Public run handle and compact terminal result model."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from ..Stages.stage_models import CancellationSource
from .progress_models import (
    ProgressDispatcher,
    ProgressSubscription,
    RunProgressHandler,
    RunProgressSnapshot,
    RunProgressTracker,
    TaskState,
)

T = TypeVar("T")


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunError:
    code: str
    message: str
    stage_id: str | None = None


@dataclass(frozen=True)
class RunResult(Generic[T]):
    task_id: str
    status: RunStatus
    output: T | None = None
    errors: tuple[RunError, ...] = ()


class RunHandle(Generic[T]):
    def __init__(
        self,
        task_id: str,
        cancellation: CancellationSource,
        tracker: RunProgressTracker,
        dispatcher: ProgressDispatcher,
        completion: "asyncio.Future[RunResult[T]]",
    ) -> None:
        self.task_id = task_id
        self._cancellation = cancellation
        self._tracker = tracker
        self._dispatcher = dispatcher
        self._completion = completion

    def cancel(self, reason: str | None = None) -> bool:
        changed = self._cancellation.cancel(reason)
        if changed:
            self._tracker.set_task_state(TaskState.CANCELLING)
        return changed

    def get_progress(self) -> RunProgressSnapshot:
        return self._tracker.get_snapshot()

    def get_result(self) -> RunResult[T] | None:
        if not self._completion.done():
            return None
        return self._completion.result()

    def subscribe_progress_events(
        self,
        handler: RunProgressHandler,
    ) -> ProgressSubscription:
        subscription = self._dispatcher.subscribe(handler)
        # A new subscriber immediately receives the current complete snapshot.
        self._dispatcher.publish(self._tracker.get_snapshot())
        return subscription

    async def await_to_completion(self) -> RunResult[T]:
        return await asyncio.shield(self._completion)

    def __await__(self):
        return self.await_to_completion().__await__()
