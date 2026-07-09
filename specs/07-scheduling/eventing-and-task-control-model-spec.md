# Eventing and Task Control Model

## Purpose

This document defines Chunky's public task-control and progress-feedback contract.

It covers:

- synchronous and asynchronous task execution;
- task handles and task lifecycle state;
- fixed fail-fast behavior and terminal results;
- cancellation requests and cooperative cancellation;
- stage progress reporting;
- progress snapshots for pending, running, and blocked stages;
- progress subscriptions and isolation of handler failures from pipeline execution.

Graph creation defines what work exists. Scheduling decides when planned stage invocations may run. Artifact and cache handles own concrete I/O and reservation lifecycles. Task control coordinates these systems and exposes one stable public view of the run.

## Design Boundaries

Task control is responsible for:

- allocating and exposing a task ID;
- owning the task lifecycle state;
- exposing synchronous and asynchronous completion;
- accepting cancellation requests;
- providing task and progress snapshots;
- collecting terminal output results, committed retrieval references, target materialization results, and errors;
- building the public progress snapshot from scheduler state and stage-reported progress;
- notifying progress subscribers whenever that snapshot changes, without blocking scheduler state transitions.

Task control is not responsible for:

- changing the planned graph;
- selecting artifact producers;
- deciding whether an invocation is ready for resource admission;
- resolving cache hits or acquiring cache reservations;
- serializing or materializing artifacts itself;
- interpreting provider-specific cancellation behavior.

Those behaviors remain owned by graph creation, scheduling, cache coordination, artifact handles, and stage adapters.

The initial implementation does not require:

- durable tasks that survive process restart;
- distributed task queues or cross-process progress delivery;
- user-controlled pause and resume;
- per-output await handles;
- automatic task retry or runtime graph replanning;
- persistent progress history after the owning process exits.

A future persisted-task layer sits above this live task-control model. It stores normalized request/configuration records and planning-visible compatibility metadata, not live scheduler state, partial stage state, or in-flight progress snapshots. Compatibility-waiting belongs to that persisted layer and must not expand the live `TaskState` enum.

## Task Model

A **task** is one accepted `RunRequest` and the lifecycle of satisfying that request.

The task is created before planning begins so that planning failures, cancellation during planning, progress reporting, and diagnostics can all use the same task ID.

Conceptually, task control owns one mutable task record:

```python
@dataclass
class TaskRecord:
    task_id: str
    state: "TaskState"
    cancellation: "CancellationSource"
    progress_dispatcher: "RunProgressDispatcher"
    completion: "CompletionCell[RunResult]"
    execution_manifest: "ExecutionManifest | None" = None
    artifact_manifest: "ArtifactManifest | None" = None
    result: "RunResult | None" = None
```

`TaskRecord` is an internal coordination object. Public callers interact through a `RunHandle`, which exposes read-only snapshots and control operations.

### Task Identity

`task_id` is generated when the request is accepted. It must be unique within the configured task manager and stable for the lifetime of the live execution attempt.

Task IDs are used for:

- progress and diagnostic correlation;
- cache reservation ownership;
- diagnostics and manifest lookup;
- cancellation routing;
- associating a `RunResult` with its request.

The format is implementation-defined. It must not be used as part of semantic artifact identity or artifact fingerprints. Future durable persisted tasks have a separate stable persisted-task ID; every restart creates a fresh live `task_id`, and persisted-task events/results carry both IDs when both exist.

## Task State

Task state is intentionally coarse:

```python
class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

`RUNNING` includes scheduling, waiting, stage execution, cache coordination, and target materialization.

Wait states such as `waiting_for_artifacts`, `waiting_for_global_cache`, and `waiting_for_resources` belong to individual stage invocations, not the task as a whole. One task may have one invocation running while another waits for a cache writer and a third waits for GPU resources. Representing those conditions as one task state would be ambiguous and would cause the public task state to oscillate.

The current mixture of task activity is exposed by `RunProgressSnapshot`.

### State Transitions

Normal completion follows:

```text
created -> running -> completed
```

Planning or execution failure follows:

```text
created -> failed
created -> running -> failed
```

Cancellation follows:

```text
created -> cancelling -> cancelled
running -> cancelling -> cancelled
```

Task-failure shutdown may use `cancelling` while stopping remaining work but still retain failure as the terminal outcome:

```text
running -> cancelling -> failed
```

A terminal state is immutable:

```text
completed
failed
cancelled
```

No transition is allowed out of a terminal state.

State mutation must be centralized through task control. The scheduler, planner, handlers, and stages must not directly mutate public task state.

## Synchronous and Asynchronous Execution

`chunky_run` and `chunky_run_away` use the same task controller, planner, scheduler, cache coordinator, progress dispatcher, and result model.

Language-neutral meaning:

```text
chunky_run(request) == chunky_run_away(request).await_to_completion()
```

`chunky_run` creates a task and blocks the calling thread until its `RunResult` is available.

`chunky_run_away` creates the same task and immediately returns a `RunHandle` for in-process observation and control.

`chunky_run_away` does not imply a durable background job. If the owning process exits, the initial implementation does not guarantee that the task continues or can be reattached.

### Python Example

```python
from chunky import chunky_run_away


async def main() -> None:
    handle = chunky_run_away(
        inputs={
            "source//audio": AudioSource.from_file_path("input.wav"),
        },
        outputs={
            "render//webvtt_transcript": WebVttTarget.to_file_path(
                "output.vtt"
            ),
        },
    )

    print(handle.task_id)
    result = await handle

    if result.status is RunStatus.COMPLETED:
        print("complete")
```

Python bindings should make `RunHandle` directly awaitable. An explicit `await_to_completion()` method may also be provided for parity with other bindings.

### Rust Example

```rust
let handle = chunky_run_away(request)?;
println!("{}", handle.task_id());

let result = handle.await_to_completion().await?;
if result.status == RunStatus::Completed {
    println!("complete");
}
```

Binding-level errors returned by `?` represent failure to use the local task-control mechanism, such as an unavailable runtime or a closed task manager. Planning, stage, artifact, and materialization failures are represented by a terminal `RunResult`.

A binding may reject a call before task creation when the supplied values cannot be converted into a valid `RunRequest`, such as a Python argument with the wrong programmatic type. In that case no task ID or `RunResult` exists. Once the request is accepted and a task ID is allocated, subsequent domain failures are represented through task state, progress snapshots, and `RunResult`.

## Run Handle

A `RunHandle` is a lightweight reference to one task controlled by the task manager.

Conceptually:

```python
class RunHandle(Generic[T]):
    task_id: str

    def get_progress(self) -> "RunProgressSnapshot": ...
    def get_result(self) -> "RunResult[T] | None": ...

    def cancel(
        self,
        reason: str | None = None,
    ) -> bool: ...

    async def await_to_completion(self) -> "RunResult[T]": ...

    def subscribe_progress_events(
        self,
        handler: "RunProgressHandler",
    ) -> "ProgressSubscription": ...

    def __await__(self): ...
```

Bindings may rename these methods to match local conventions.

### Handle Semantics

- `get_progress()` returns a consistent complete snapshot from the task-owned progress tracker.
- `get_result()` returns `None` until the task reaches a terminal state.
- `await_to_completion()` waits for any terminal state and returns the same `RunResult` stored by `get_result()`.
- awaiting the handle delegates to `await_to_completion()` and does not implicitly cancel the task if the caller's await is cancelled.
- `cancel()` requests cancellation and returns immediately. It returns `True` when this call first requests cancellation and `False` when cancellation was already requested or the task is already terminal.
- `subscribe_progress_events()` registers a callback, immediately schedules the current complete snapshot, and then receives future changed snapshots.

`ProgressSubscription` is an idempotent detach handle. Detaching the subscription stops future callbacks and does not cancel or otherwise modify the task.

Progress handlers run outside the scheduling path. Handler failures are logged and do not fail the task. Python handlers that signal event-loop-owned objects must use thread-safe loop signalling, such as `loop.call_soon_threadsafe(...)`.

The focused example does not demonstrate durable reattachment or cross-process handle sharing. Dropping a handle does not cancel the task.

## Failure Behavior

Chunky uses fail-fast behavior for every task. Failure behavior is not configurable in the initial contract.

When a task-fatal failure occurs:

1. The first task-fatal failure is recorded.
2. The scheduler stops admitting new stage invocations and new materialization work.
3. Running invocations receive cancellation with origin `task_failure`.
4. Unwritten artifact targets are aborted.
5. Work on independent branches is not deliberately continued.
6. Outputs that cannot complete because execution was stopped are reported as `blocked`.
7. The task becomes `failed` after required cleanup and after running work has stopped or has been safely abandoned by its adapter.

Already committed semantic artifacts remain valid. A failure elsewhere in the task does not invalidate a successfully committed cache entry.

## Cancellation

Cancellation is a request, not an immediate forced state transition.

```python
class CancellationOrigin(str, Enum):
    CALLER = "caller"
    TASK_FAILURE = "task_failure"
    PROCESS_SHUTDOWN = "process_shutdown"
    INTERNAL = "internal"


class CancelRequestResult(str, Enum):
    ACCEPTED = "accepted"
    ALREADY_REQUESTED = "already_requested"
    ALREADY_TERMINAL = "already_terminal"
```

`RunHandle.cancel()` is idempotent:

- the first valid request returns `accepted`;
- repeated requests return `already_requested`;
- requests after a terminal state return `already_terminal`.

### Cancellation Source and Token

Task control owns one cancellation source. The scheduler and each stage invocation receive a read-only token derived from that source.

```python
class CancellationToken(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    async def wait_cancelled(self) -> None: ...
    async def wait_for(self, awaitable: Awaitable[T]) -> T: ...
    def raise_if_cancelled(self) -> None: ...
```

The token may additionally expose the cancellation origin and user-provided reason.

The stage receives the token through `StageExecutionContext`, together with its scoped progress reporter. `wait_for()` is used for cancellation-aware resource and cache waits; cancelling the local wait does not cancel another task that owns an in-flight cache write.

Stages should check cancellation at safe boundaries, especially:

- before starting expensive provider work;
- between independent batches or chunks;
- before preparing outputs;
- before returning from long-running loops.

Provider adapters should forward cancellation to an external API, subprocess, thread, or model runtime when that provider supports cancellation.

### Cancellation Flow

When cancellation is accepted:

1. Task control atomically changes the task state to `cancelling`.
2. The updated progress snapshot is delivered to subscribers.
3. The scheduler stops starting new stage invocations and new materialization work.
4. Invocations waiting for artifacts, resources, or global cache writers become `cancelled`.
5. Running invocations observe the cancellation token.
6. Unwritten output handles are aborted through `ArtifactWriter.abort_if_unwritten()` or the equivalent runner cleanup.
7. Cache wait registrations are released.
8. Resource leases are released when running invocations stop.
9. Task-owned artifact source handles are closed, releasing cache read reservations.
10. The task becomes `cancelled` after cleanup completes.

A long-running provider call may not stop immediately when the provider does not support cancellation. In that case the task remains `cancelling` until the call returns, unless the provider adapter explicitly supports safe abandonment.

Safe abandonment means the abandoned work can no longer publish artifacts, retain resource leases, or commit a cache target after the task has been finalized. The scheduler must not assume that arbitrary threads or provider calls can be safely abandoned.

### Cancellation and Artifact Commit Races

Cancellation does not roll back already committed artifacts or materialized caller targets.

The default behavior is:

- an artifact committed before cancellation is observed remains valid and may remain cached;
- an output that is only prepared, or has not yet been written, is aborted;
- an output returned by a stage after cancellation is ignored unless it was already committed through its bound writer;
- caller materialization that has not started is not attempted;
- a caller target already atomically committed is not deleted.

These rules preserve cache correctness and avoid exposing partial outputs.

### Cancellation and Failure Precedence

A non-cancellation failure that made the task unsatisfiable before caller cancellation was accepted produces a final `failed` result.

When caller cancellation is accepted first, cancellation-induced errors are recorded as cancellation details and the task normally becomes `cancelled`.

An unrelated internal failure discovered during cancellation cleanup may still produce `failed` when the implementation cannot prove that task-owned reservations, handles, or resources were safely released.

## Run Result

Every terminal task produces one compact `RunResult`, including failed and cancelled tasks.

```python
class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SHUTDOWN_INTERRUPTED = "shutdown_interrupted"


@dataclass(frozen=True)
class RunResult:
    task_id: str
    status: RunStatus
    outputs: dict[ArtifactKey, "OutputResult"]
    errors: tuple["RunError", ...]
    manifest: "RunManifestSummary | None" = None
    manifest_location: "ArtifactLocation | None" = None
    persisted_task_id: str | None = None
```

The full execution and artifact manifests remain separate diagnostic data. The result contains only a compact summary or a location from which the full manifest can be loaded when diagnostics are enabled.

Awaiting a task returns `RunResult` for normal domain failures. Bindings may provide `raise_for_failure()` as a convenience, but the language-neutral contract must preserve failed and partial output information without requiring exception inspection.

### Output Result

`RunResult.outputs` contains one entry for every requested output artifact. It is keyed by requested semantic artifact ID, not by materialization target. Fan-out materialization details live inside the output result. A target-less requested output still has an `OutputResult`; its materialization list is empty.

```python
class OutputStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TargetMaterializationResult:
    target_index: int
    status: OutputStatus
    error_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputResult:
    artifact_id: ArtifactKey
    status: OutputStatus
    fingerprint: str | None
    cached_reference: CachedArtifactReference | None = None
    targets: tuple[TargetMaterializationResult, ...] = ()
    error_ids: tuple[str, ...] = ()
```

`RunResult.outputs` contains results only for artifacts requested in the run output map. It does not contain entries for intermediates, settings, diagnostics that were not requested as outputs, or incidental stage outputs.

The output statuses mean:

- `succeeded`: the semantic output was available and every configured caller target, if any, was materialized successfully. For target-less outputs, semantic availability plus durable cached-reference publication is enough.
- `failed`: this output failed directly while producing, validating, committing, reading, or materializing it. `error_ids` references the errors that describe the failure.
- `blocked`: this output did not complete because a different task-fatal failure caused fail-fast shutdown before this output could succeed. This is a terminal result, not a waiting-to-materialize state.
- `cancelled`: this output did not complete because caller, process-shutdown, or internal cancellation won the task's terminal outcome.

`fingerprint` is present when the semantic artifact fingerprint was known. `cached_reference` is present only after the artifact is committed and durably retrievable. It may therefore be present on a failed output when semantic production succeeded but caller-target materialization failed. In that case the semantic cache entry remains valid and reusable even though one configured target result is `failed`.

### Error Model

`RunResult.errors` contains compact, user-facing descriptions of failures. The error model is intentionally small because detailed exception data, stack traces, provider responses, and cleanup diagnostics belong in the optional run manifest.

```python
@dataclass(frozen=True)
class RunError:
    error_id: str
    code: str
    message: str
    stage_id: str | None = None
    artifact_id: ArtifactKey | None = None
```

- `error_id` allows an `OutputResult` to reference the error that caused it to fail.
- `code` is a stable programmatic identifier such as `planning_failed`, `stage_failed`, `artifact_invalid`, or `materialization_failed`.
- `message` is a concise human-readable explanation.
- `stage_id` is present when the failure belongs to a particular stage.
- `artifact_id` is present when the failure belongs to a particular input, produced artifact, or requested output.

The first task-fatal error determines the fail-fast outcome. Additional errors should be included only when they help explain cleanup or why a configured output failed.

## Progress Model

Progress reporting exists to give callers useful feedback while a potentially long ASR run is in progress. It answers:

1. Which stages are currently running?
2. What progress have those stages reported?
3. Which stages have not started yet?
4. Which stages are ready but blocked, and why?
5. How many planned stages are finished, pending, running, or blocked?

The public model is one immutable `RunProgressSnapshot`. It is rebuilt whenever one of those visible values changes and is delivered to handlers registered through `RunHandle.subscribe_progress_events(handler)`.

### Public Stage Categories

A planned stage invocation appears in exactly one active category:


- `pending`: the stage has not started because one or more upstream artifacts are not yet available. This is normal dependency waiting.
- `running`: the stage has started and has not yet reached a terminal state.
- `blocked`: the stage's required artifacts are available, but the scheduler cannot currently start it because of an external scheduling constraint.

Completed, skipped, failed, and cancelled invocations are no longer included in the active stage lists. They contribute to `finished_stage_count`.

### Blocked Reasons

The initial implementation needs only two blocked reasons:

```python
class StageBlockedReason(str, Enum):
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    WAITING_FOR_OTHER_TASK = "waiting_for_other_task"
```

- `waiting_for_compute`: the stage is ready, but a required resource or concurrency slot is unavailable. The description may name the relevant resource, such as GPU memory, GPU compute, CPU compute, or a stage-specific concurrency limit.
- `waiting_for_other_task`: another task currently owns the cache write reservation for an output this invocation would otherwise produce. The invocation waits for that task to commit or abandon the artifact before cache resolution is attempted again.

Waiting for upstream stages in the same task is `pending`, not `blocked`.

### Stage-Defined Progress

A stage defines its own useful units of progress. Task control does not attempt to infer a universal percentage from model internals, audio duration, or the number of graph nodes.

A running stage may report zero or more active subtasks. More than one subtask may be active at the same time, such as several ASR chunks or detector profiles running concurrently.

```python
@dataclass(frozen=True)
class StageSubtaskProgress:
    subtask_id: str
    name: str
    percentage: float | None = None
```

- `subtask_id` is stable within the invocation and identifies later updates or completion of the same subtask.
- `name` is a short stage-defined description.
- `percentage` is optional and, when present, is in the inclusive range `0.0` to `100.0`.
- `None` means the subtask is active but its completion cannot be estimated usefully.
The reporter exposed to a stage can remain small:

```python
class StageProgressReporter(Protocol):
    def start_subtask(
        self,
        subtask_id: str,
        name: str,
        *,
        percentage: float | None = None,
    ) -> "StageSubtaskReporter": ...


class StageSubtaskReporter(Protocol):
    def update(
        self,
        *,
        name: str | None = None,
        percentage: float | None = None,
    ) -> None: ...

    def finish(self) -> None: ...
```

`start_subtask()` adds the subtask. `update()` changes its name or percentage. `finish()` idempotently removes it. Task control clears any remaining subtasks automatically when the stage stops running.

Chunky does not calculate an overall task percentage from these values. Different stages measure different kinds of work, and their relative cost is not stable enough for a meaningful weighted percentage.

### Stage Progress Snapshot

```python
@dataclass(frozen=True)
class StageProgressSnapshot:
    invocation_id: str
    stage_id: str
    display_name: str
    subtasks: tuple[StageSubtaskProgress, ...] = ()
    blocked_reason: StageBlockedReason | None = None
    blocked_description: str | None = None
    blocking_task_id: str | None = None
```

`invocation_id` distinguishes multiple invocations of the same stage when settings or requested outputs cause the planner to create more than one invocation.

For pending and running stages, the blocked fields are `None`. For a blocked stage:

- `blocked_reason` identifies the broad reason;
- `blocked_description` provides a short human-readable explanation;
- `blocking_task_id` is populated when the blocker is another known Chunky task.

Only running stages normally contain subtasks.

### Run Progress Snapshot

```python
@dataclass(frozen=True)
class OutputAvailabilitySnapshot:
    artifact_id: ArtifactKey
    cached_reference: CachedArtifactReference


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
    available_outputs: tuple[OutputAvailabilitySnapshot, ...] = ()
```

The count fields describe planned stage invocations, not stage types. Therefore, two invocations of the same stage count as two stages in this snapshot.

The following relationship should always hold:

```text
total_stage_count =
    finished_stage_count
  + pending_stage_count
  + running_stage_count
  + blocked_stage_count
```

The list lengths must match their corresponding active counts. `available_outputs` contains requested outputs that have become durably retrievable. Entries are added only after cache commit and include the same concrete cached-instance reference later returned in `RunResult`.

Before planning has produced an execution plan, `total_stage_count` may be zero. Once the execution plan is fixed, it must not change during normal scheduling.

`finished_stage_count` includes invocations that completed successfully or were skipped because their requested outputs were already supplied or cache-resolved. On failed or cancelled tasks it also includes invocations that reached a terminal failed or cancelled state. Detailed terminal information remains in `RunResult` and the run manifest rather than expanding the progress model.

### Snapshot Change and Subscription Semantics

`subscribe_progress_events(handler)` is called every time the public `RunProgressSnapshot` changes.

A new snapshot is produced when, for example:

- task state changes;
- planning fixes the total stage count;
- a stage moves between pending, running, blocked, and finished;
- a blocked reason or description changes;
- a stage adds, updates, or clears a subtask;
- a reported subtask percentage changes.

Internal scheduler operations that do not change the public snapshot do not trigger the handler.

Conceptually:

```python
RunProgressHandler = Callable[[RunProgressSnapshot], None]
```

Delivery rules:

- snapshots are delivered in the order they were created for a task;
- the handler receives the complete immutable snapshot, not a delta;
- a handler failure is logged or added to diagnostics and does not fail the task;
- slow handlers must not block planning, scheduling, stage execution, or cancellation;
- detaching the returned `ProgressSubscription` stops future callbacks;
- callers use `get_progress()` when they need the current snapshot immediately after subscribing.

The initial implementation does not require a public low-level event stream, event replay, event sequence numbers, or an asynchronous event iterator. Resubscription promises a current coherent snapshot, including already available requested-output references, but not replay of every prior transient stage-progress change. A future event replay or cursor-resume extension is reserved.

### Example Progress Snapshot

```python
RunProgressSnapshot(
    task_id="task-123",
    task_state=TaskState.RUNNING,
    total_stage_count=8,
    finished_stage_count=2,
    pending_stage_count=2,
    running_stage_count=2,
    blocked_stage_count=2,
    pending_stages=(
        StageProgressSnapshot(
            invocation_id="compose-1",
            stage_id="transcript.compose",
            display_name="Compose transcript",
        ),
        StageProgressSnapshot(
            invocation_id="render-1",
            stage_id="render.webvtt",
            display_name="Render WebVTT",
        ),
    ),
    running_stages=(
        StageProgressSnapshot(
            invocation_id="asr-1",
            stage_id="asr.transcribe",
            display_name="Transcribe audio",
            subtasks=(
                StageSubtaskProgress("Transcribing frame 3", 72.0),
                StageSubtaskProgress("Transcribing frame 4", 28.0),
            ),
        ),
        StageProgressSnapshot(
            invocation_id="vad-1",
            stage_id="speech_detection.vad",
            display_name="Detect speech",
            subtasks=(
                StageSubtaskProgress("Running permissive VAD"),
            ),
        ),
    ),
    blocked_stages=(
        StageProgressSnapshot(
            invocation_id="diarize-1",
            stage_id="speaker_detection.diarize",
            display_name="Detect speakers",
            blocked_reason=StageBlockedReason.WAITING_FOR_COMPUTE,
            blocked_description="Waiting for GPU memory",
        ),
        StageProgressSnapshot(
            invocation_id="align-1",
            stage_id="alignment.words",
            display_name="Align transcript",
            blocked_reason=StageBlockedReason.WAITING_FOR_OTHER_TASK,
            blocked_description=(
                "Waiting for another task to finish producing prepared audio"
            ),
            blocking_task_id="task-117",
        ),
    ),
)
```

## Recommended Invariants

1. Task state remains coarse; dependency and scheduling waits are represented at stage-invocation scope.
2. Every planned invocation appears exactly once as pending, running, blocked, or finished.
3. A stage is pending while waiting for upstream artifacts in the same task.
4. A stage is blocked only when compute capacity or another task prevents its next progress; unfinished upstream work in the same task is represented as pending.
5. A stage may report several active subtasks concurrently.
6. Stage percentages are optional and are never combined into a synthetic task-wide percentage.
7. `subscribe_progress_events()` is invoked whenever the complete public progress snapshot changes.
8. Progress handlers cannot fail or delay the task.
9. The execution plan fixes `total_stage_count`; scheduling does not add or remove planned invocations.
10. `RunResult`, not the progress snapshot, remains the authoritative terminal outcome.
11. Requested-output availability snapshots and terminal results carry the same committed `CachedArtifactReference`.
12. A cached-reference availability event is emitted only after durable commit.
13. Future persisted-task resume uses current snapshot continuity, not historical event replay, unless a later replay extension is added.
14. Shutdown-interrupted durable attempts are distinguishable from ordinary cancellation or failure.
