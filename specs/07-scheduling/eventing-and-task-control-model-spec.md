# Eventing and Task Control Model

## Purpose

This specification defines scheduler-owned task and attempt transitions,
fail-fast coordination, cooperative stopping, run-policy enforcement, and the
stage-level scheduling progress used by the public run state.

The [Run Surface](../01-runtime/04-run-surface.md) owns `run`, `start`,
`RunHandle`, durable service methods, cancellation acknowledgements, and the
canonical `RunState`, `Progress`, and `RunResult` shapes.

The [Persisted Task Lifecycle](02-persisted-task-lifecycle.md) owns durable
acceptance, recovery eligibility, control-event persistence, active expiry, and
terminal record retention.

## Design boundaries

Task control is responsible for:

- allocating local task and attempt identity;
- creating a fresh attempt for an eligible durable task;
- centralizing task and attempt state mutation;
- applying run policy during admission and scheduling;
- routing cancellation, expiry, task-failure, and orderly-shutdown stop signals;
- stopping admission and coordinating safe cleanup;
- maintaining a coherent current stage-progress snapshot;
- collecting output and target outcomes for the run-surface result builder; and
- publishing exactly one terminal outcome to the owning local or durable task
  record.

Task control is not responsible for:

- changing the graph or selecting artifact producers;
- changing requested outputs, cache policy, or artifact identity;
- persisting or recovering scheduler queues, plans, reservations, or progress;
- defining public handle, receipt, state, result, or failure-envelope shapes;
- serializing or materializing artifacts itself; or
- interpreting provider-specific cancellation beyond the adapter contract.

Graph construction, planning, cache coordination, artifact I/O, and stage
adapters retain those responsibilities.

The initial model has no public progress subscription or event-replay surface.
Task control maintains a current immutable snapshot for polling and awaiting
through the run surface.

## Task and attempt model

A task is one accepted run request and the lifecycle of satisfying it. Task
identity is allocated before planning so that planning, execution, output
states, cancellation, and diagnostics share one stable `task_id`.

Every execution has a required `attempt_id`:

- a local task has one task ID and one attempt ID;
- a durable task keeps its task ID across service restarts; and
- every durable recovery creates a new attempt ID.

Task and attempt IDs are operational identity. They MUST NOT affect semantic
artifact identity or fingerprints.

Conceptually, a live attempt owns:

```text
AttemptRecord
  task_id
  attempt_id
  phase
  stop_source
  scheduling_progress
  completion_cell
  execution_manifest?
  artifact_manifest?
```

This is an internal coordination object, not a public serialized contract.

## Attempt phases and transitions

Attempt phase is intentionally coarse:

```text
CREATED
RUNNING
STOPPING
FINISHED
```

`RUNNING` covers planning, dependency waiting, resource admission, cache
coordination, stage execution, and local target delivery. Detailed waiting and
blocked conditions belong to individual stage invocations.

`STOPPING` means no new work is admitted while cancellation, expiry,
task-failure, or orderly local shutdown cleanup runs. The public run state
remains `InProgress` during this phase.

Typical internal transitions are:

```text
CREATED -> RUNNING -> FINISHED
CREATED -> STOPPING -> FINISHED
RUNNING -> STOPPING -> FINISHED
```

The terminal public variant is selected separately from the internal stop
phase. For example, task-failure cleanup uses `STOPPING` but produces `Failed`,
not `Cancelled`.

State mutation MUST be centralized through task control. Planners, schedulers,
stages, progress reporters, and callbacks MUST NOT directly terminalize a task.

## Run policy

`RunPolicy` controls admission and scheduling only. Implementations may use it
for resource classes, concurrency limits, fairness, or other execution
constraints, subject to runtime-wide capacity.

It MUST NOT change graph topology, producer selection, artifact identity,
fingerprints, requested outputs, cache behavior, or target delivery.
Conflicting or unsatisfiable scheduling constraints fail explicitly rather than
silently changing semantic work.

## Fail-fast behavior

Chunky uses fail-fast behavior for every task. It is not configurable in the
initial contract.

When a task-fatal failure occurs:

1. task control records the causal failure;
2. new stage invocations and target deliveries stop being admitted;
3. running work receives a `TASK_FAILURE` stop signal;
4. unwritten artifact targets are aborted;
5. independent branches are not deliberately continued;
6. requested outputs that cannot complete because of the failure become
   `PREVENTED_BY_FAILURE`;
7. unstarted local targets whose semantic output failed become
   `PREVENTED_BY_FAILURE`; and
8. the task becomes `Failed` only after required cleanup finishes or an adapter
   safely abandons its work.

Already committed semantic artifacts remain valid. Failure elsewhere in the
task does not invalidate a successful committed artifact or change its
`OutputState` from `SUCCEEDED`.

All detailed causes are aggregated into the single universal failure envelope
defined by the [Failure Surface](../01-runtime/07-failure-surface.md).

## Cooperative stopping

### Stop origins

The internal stop source distinguishes:

```text
CALLER
ACTIVE_EXPIRY
TASK_FAILURE
ORDERLY_SHUTDOWN
INTERNAL_FAILURE
```

The public cancellation operations accept no caller-provided reason.
Implementations may retain internal diagnostic details, but those details do
not alter the public cancellation request.

The scheduler and each stage invocation receive a read-only token:

```python
class StopToken(Protocol):
    @property
    def is_stopping(self) -> bool: ...

    @property
    def origin(self) -> StopOrigin: ...

    async def wait_stopping(self) -> None: ...
    async def wait_for(self, awaitable: Awaitable[T]) -> T: ...
    def raise_if_stopping(self) -> None: ...
```

`wait_for()` makes resource and cache waits responsive without cancelling
another task that owns an in-flight cache write.

Stages SHOULD check the token:

- before expensive provider work;
- between independent batches or chunks;
- before preparing or committing outputs; and
- before returning from long-running loops.

Provider adapters SHOULD forward stopping to external APIs, subprocesses,
threads, or model runtimes when supported.

### Stop flow

After a stop event wins:

1. the attempt enters `STOPPING`;
2. task control stops new invocation and target-delivery admission;
3. dependency, resource, and cache wait registrations are released;
4. waiting invocations receive the corresponding terminal status;
5. running invocations observe their token;
6. unwritten output handles are aborted;
7. task-owned source handles and all cache or durable read reservations close;
8. resource leases release as running work stops; and
9. task control publishes the selected terminal state after cleanup.

A provider call that cannot be cancelled keeps the task `InProgress` until it
returns unless its adapter can prove safe abandonment. Safely abandoned work
MUST be unable to publish artifacts, retain leases, or commit a target after
finalization.

### Artifact and target races

Stopping does not roll back committed artifacts or atomically committed caller
targets:

- an artifact committed before the stop is observed remains valid;
- a prepared but unwritten output is aborted;
- a stage result returned after stopping is ignored unless it was already
  committed through its bound writer;
- an unstarted caller target is not attempted; and
- an atomically committed caller target is not deleted.

These rules prevent partial publication while preserving valid completed work.

### Outcome precedence

The authoritative terminal outcome is chosen from durable or local ordering
facts, not merely from which cleanup callback returns first.

- completion already committed remains `Completed`;
- a task-fatal failure that made the task unsatisfiable before caller
  cancellation normally produces `Failed`;
- accepted caller cancellation normally produces `Cancelled`;
- durable expiry produces `Expired`;
- explicit orderly local shutdown may produce `Interrupted`; and
- an unrelated serious cleanup failure may produce `Failed` when safe resource
  release cannot be established.

The persisted lifecycle owns the durable first-recorded cancellation-versus-
expiry rule.

## Scheduling progress

Progress answers:

1. which planned invocations are pending, running, or blocked;
2. what active subtasks running stages report;
3. why ready stages are blocked; and
4. how many planned invocations are finished or active.

Task control rebuilds one immutable `PlanProgressState` whenever a visible
value changes. The run surface embeds the current value in
`InProgress(Progress)`; it does not expose a subscription API.

### Public stage categories

Every unfinished planned invocation appears in exactly one category:

- `pending`: it has not started because an upstream artifact in this task is
  unavailable;
- `running`: it has started and is not terminal; or
- `blocked`: its required artifacts are available, but an external scheduling
  constraint prevents admission.

Finished invocations are absent from active lists and contribute to
`finished_stage_count`.

### Blocked reasons

```python
class StageBlockedReason(str, Enum):
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    WAITING_FOR_OTHER_TASK = "waiting_for_other_task"
```

`WAITING_FOR_COMPUTE` covers an unavailable resource, capacity slot, or
stage-specific concurrency permit.

`WAITING_FOR_OTHER_TASK` means another task owns the cache write reservation for
an artifact this invocation would otherwise produce. The invocation waits for
commit or abandonment and then repeats cache resolution.

Waiting on unfinished upstream work in the same task is `pending`, not
`blocked`.

### Stage-defined progress

A stage defines its own useful units. Task control does not infer a universal
percentage from model internals, media duration, or graph size.

```python
@dataclass(frozen=True)
class StageSubtaskProgress:
    subtask_id: str
    name: str
    percentage: float | None = None
```

`subtask_id` is stable within the invocation. `percentage`, when present, is in
the inclusive range `0.0` through `100.0`. `None` means progress cannot be
estimated usefully. Several subtasks may be active concurrently.

The stage reporter may remain small:

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

`start_subtask()` adds a subtask, `update()` replaces its public fields, and
`finish()` idempotently removes it. Task control removes all remaining subtasks
when the invocation stops running.

Chunky does not combine stage percentages into a task-wide percentage.

### Stage snapshot

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

`invocation_id` distinguishes multiple invocations of one stage.

Pending and running snapshots have no blocked fields. A blocked snapshot has a
reason, may have a bounded human-readable description, and includes
`blocking_task_id` when another known Chunky task is the blocker. Only running
stages normally contain subtasks.

### Scheduling snapshot

```python
@dataclass(frozen=True)
class PlanProgressState:
    total_stage_count: int
    finished_stage_count: int
    pending_stage_count: int
    running_stage_count: int
    blocked_stage_count: int

    pending_stages: tuple[StageProgressSnapshot, ...]
    running_stages: tuple[StageProgressSnapshot, ...]
    blocked_stages: tuple[StageProgressSnapshot, ...]
```

Counts describe planned invocations, not stage types:

```text
total_stage_count =
    finished_stage_count
  + pending_stage_count
  + running_stage_count
  + blocked_stage_count
```

Each active list length MUST equal its corresponding count. Each list uses
stable planner invocation order.

Before planning fixes a plan, `total_stage_count` may be zero. Once fixed, it
does not change during that attempt. Recovery creates a new attempt and may
produce a different plan and stage count.

`finished_stage_count` includes successful, cache-resolved, skipped, failed, and
stopped invocations that reached an attempt-terminal condition. Detailed output
and terminal information remains in the run-surface `OutputState` and
`RunResult`.

Requested-output state is not duplicated here. The run surface places the
complete sorted `OutputState` list beside `PlanProgressState` in `Progress`.

## Local and durable attempt lifetime

An orderly local task-manager shutdown stops all attempts and awaits either a
terminal public state or safe release of task-owned resources. That path may
produce local `Interrupted`.

Abrupt task-manager loss cannot truthfully select a terminal outcome. It becomes
a run-surface `TASK_UNAVAILABLE` call failure.

Orderly durable-service shutdown stops only the current attempt. It does not
terminalize the durable task. Recovery before active expiry creates a fresh
attempt and fresh progress; recovery after active expiry enters expiry cleanup
without creating an attempt.

No progress history, event replay, plan, stage checkpoint, or scheduler queue is
carried across attempts.

## Invariants

1. Stable task identity and execution-attempt identity are distinct.
2. Task control alone mutates attempt phase and publishes terminal outcomes.
3. `STOPPING` remains publicly `InProgress` until safe cleanup completes.
4. Run policy changes scheduling only, never semantic work.
5. Fail-fast shutdown stops new work but preserves already committed artifacts
   and targets.
6. Every unfinished planned invocation is exactly one of pending, running, or
   blocked.
7. Same-task dependency waiting is pending; external capacity or task
   contention is blocked.
8. Stage percentages are optional and are never combined into an overall task
   percentage.
9. The execution plan fixes stage count for one attempt; recovery may create a
   different plan in a new attempt.
10. The initial contract exposes current snapshots through `RunState` and no
    public subscription or replay API.
