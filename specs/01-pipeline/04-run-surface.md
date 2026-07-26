# Run Surface

## Purpose

This specification defines the public surface for starting, observing,
awaiting, cancelling, and consuming local and durable runs.

It owns:

- local `run(...)` and `start(...)`;
- `RunHandle`;
- durable submission and task-ID control operations;
- `DurableSubmissionReceipt`;
- the shared `RunState`, `Progress`, `RunResult`, `OutputState`, and
  `TargetState` data contracts; and
- call-level lifetime and failure behavior.

It does not own scheduler transitions, attempt recovery, durable record
storage, artifact locator semantics, or the universal failure envelope. Those
contracts live in:

- [Eventing and Task Control Model](../07-scheduling/eventing-and-task-control-model-spec.md);
- [Persisted Task Lifecycle](../07-scheduling/02-persisted-task-lifecycle.md);
- [Artifact Model](../02-artifacts/01-artifact-model-surface.md);
- [Artifact I/O](../02-artifacts/02-artifact-io-surface.md); and
- [Failure Contract](07-failure-surface.md).

Bindings may use idiomatic names, native tagged unions, exceptions, futures,
and time types. They MUST preserve the language-neutral behavior and data
defined here.

Public service APIs SHOULD remain relatively flat: callers reach operations
through a small number of top-level service objects rather than nested service
hierarchies. This keeps the surface clean and simplifies bindings across DLL
and shared-object boundaries. Bindings may provide idiomatic facades without
changing the underlying service contracts.

## Task execution

A configured pipeline supports local run-to-completion, detached local
execution, and submission to a durable task sub system:

```text
pipeline.run_task(request: LocalRunRequest) -> terminal RunState
pipeline.start_task(request: LocalRunRequest) -> RunHandle
pipeline.submit_task(request: DurableRunRequest, active_ttl?: positive-whole-seconds) -> DurableSubmissionReceipt
```

### Shared execution contract

Each operation either produces its declared return value or ends with a call-level `Failure`. In exception-oriented bindings call-level failures are thrown as exceptions carrying the universal

[Failure Contract](07-failure-surface.md); other bindings may expose the same
data through their idiomatic error channel.

**Local execution:**
For local execution, call conversion, structural validation, and task allocation occur before `run_task(...)` can produce a terminal `RunState` or `start_task(...)` can produce a `RunHandle`

`call entry(..)` -> `[conversion / validation]` -> **`[local allocation]`** -> `[planning / execution / cleanup]`

A failure before successful local allocation is call-level: 
no task identity, handle, or terminal state exists, and the local request remains unconsumed.

After successful local allocation, planning, execution, and cleanup failures are task outcomes represented by a terminal `RunState` containing a `RunResult`; they are not thrown in place of that state.

**Durable submission:**
Durable submission has an equivalent public boundary at atomic acceptance:

`call entry(..)` -> `[conversion / validation]` -> **`[durable acceptance]`** -> `[receipt]` -> `[planning / execution / cleanup]`

Rejection before durable acceptance is a call-level failure and creates no task. `submit_task(...)` returns a `DurableSubmissionReceipt` only after acceptance commits.

If acceptance commits but cancellation, disconnection, or transport failure prevents the caller from receiving the receipt, the call may fail with an indeterminate submission outcome even though the durable task exists; the commit is not rolled back. Once the caller has a receipt, later task failures are observed through the task's terminal `RunState`.

### Task run

`run_task(...)` allocates a local task and waits for its terminal state. It does not return intermediate progress or local cancellation authority.

**Async Behavior:**
Local runs may be long-lived. Cancelling, timing out, or abandoning the completion wait stops only that caller's observation; it does not cancel the manager-owned task.

This preserves task execution when a waiter is lost and allows requested output artifacts to complete.

A caller that requires per-task cancellation authority MUST use `start_task(...)` and retain its `RunHandle`.
Orderly pipeline shutdown remains manager-wide lifecycle control, not a substitute for per-task cancellation.

The `run_task(...)` method is semantically equivalent to

```python
async def run_task(request):
    handle = start_task(request)
    return await handle.await_to_completion()
```

#### Expected usage

```python
state = await pipeline.run_task(request)

match state:
    case Completed(result):
        consume(result)
    case Failed(result) | Cancelled(result) | Interrupted(result):
        inspect(result)
```

### Task start

`start_task(...)` allocates the same kind of local task as `run_task(...)` however differs in that it synchronous returns its process-local `RunHandle` once task `allocation` is complete.

Callers use `start_task(...)` when they need intermediate observation, explicit local cancellation authority, or a separately managed completion wait.

#### Expected usage

```python
handle = pipeline.start_task(request)
terminal = await handle.await_to_completion()
```
*or long polling for state updates*

```python
handle = pipeline.start_task(request)

while True:
    await asyncio.sleep(2)
    state = handle.get_state()

    if isinstance(state, InProgress):
        # process progress state
        # or cancel via state.cancel()

        continue

    terminal = state
    break
```

### Task submit

`submit_task(...)` validates and atomically accepts a `DurableRunRequest`.

> `DurableRunRequest` is immutable, cloneable, and reusable; submission does not consume it.

Every successfully accepted submission creates a new durable task. Equivalent tasks may reuse artifacts if their cache policies permit it.

**Expected behavior:**
1. Caller cancellation, timeout, or disconnect may prevent acceptance only before the atomic acceptance commit.
2. Losing the response afterward does not roll back the task.
3. Retrying after a failed call may create a different task. A lost receipt may leave the original task undiscoverable by that caller.

#### `DurableSubmissionReceipt`

After atomic acceptance, submission returns the data-only receipt:

```text
DurableSubmissionReceipt
  task_id: durable-task-id
  output_artifacts: sorted list<a-task>
  completion_deadline: timestamp
  retention_duration_seconds: positive-whole-seconds
```

**`output_artifacts`:**
contains one immediate stable `a-task` locator for each requested output, sorted by canonical locator representation.

> Note: A locator will preserve an unresolved requested phase selector.  Only after planning determines *Completed Value* can a instance becomes available .

**`completion_deadline`** is the effective absolute active/recovery deadline. After this deadline if the task is still active it should it is automatically canceled. Tasks will also not automatically resume after this deadline, but can be restarted until the task reaches its retention duration deadline.

**`retention_duration_seconds`** is the effective duration for retaining the task record after terminalization. Terminalization of a task results in all traces of this task removed from the system persistence store, but does not remove artifact cache entries.

#### Expected usage

```python
receipt = await pipeline.submit_task(request, active_ttl=3_600)

# Save `receipt.task_id` for later status lookup.
persist(receipt.task_id)

# Save stable output locators for later artifact retrieval.
persist(receipt.output_artifacts)
```

Failure to obtain a receipt is a call-level failure surfaced through the binding's ordinary error mechanism.

## Task waiters

Local and durable tasks expose the same `RunState` family for polling and awaiting.

- Local callers observe through `RunHandle`;
- Durable callers use a task ID with the durable task service.

> Design Note: The initial run surface has no state, progress, or event subscription API. Callers poll or await completion.

### Shared waiting semantics

- Polling returns the current `RunState` and may observe `InProgress` or a terminal variant. Awaiting always returns a terminal variant.
- Awaiting observes completion without consuming the handle, task ID, or result. Every successful wait returns an immutable terminal RunState representing the same terminal outcome.
- No guarantee is made about the order in which concurrent awaiters resume, this maybe influenced by languages or runtime semantics of its specific async implementation.
- Cancelling, timing out, dropping, or disconnecting one wait stops only that observation attempt. It does not cancel the task, change its lifetime, or affect other waiters.
- A wait does not own or extend the lifetime of the pipeline instance. Local availability during shutdown or disposal follows Local task lifetime (#local-task-lifetime).

### `RunHandle`

`RunHandle` is a cloneable, shareable process-local observation and control reference.

#### Object structure

```text
RunHandle
  task_id: task-id
  output_artifacts: sorted list<a-task>

  get_state() -> RunState
  await_to_completion() -> terminal RunState
  cancel() -> boolean
```

**`output_artifacts`** has the same meaning and canonical sorting as on `DurableSubmissionReceipt`.

The handle exposes no direct `a-instance` property or instance lookup method. Resolved artifact identity and committed instance availability appear in `OutputState` and through the artifact query surface.

#### Observation and awaiting

**`get_state()`**
returns the current `RunState`.

**`await_to_completion()`**
always returns a terminal state.

> The handle does not own the task. Cloning or dropping a handle does not change the task lifetime. Once the handle observes a terminal state, it retains that state and remains inspectable and repeatedly awaitable after orderly pipeline shutdown.

#### Expected usage

```python
handle = pipeline.start_task(request)

while True:
    await asyncio.sleep(2)
    state = handle.get_state()

    if isinstance(state, InProgress):
        inspect_progress(state)
        continue

    terminal = state
    break
```

### Durable task service
Durable observation requires only the stable task ID.

#### Operations

```text
pipeline.get_task_state(task_id) -> RunState
pipeline.await_task_to_completion(task_id) -> terminal RunState
```

#### Observation and awaiting

Durable awaiting has no language-neutral timeout parameter. It is repeatable across callers and processes. Every successful wait returns the same immutable terminal `RunState` while the durable task record is retained.

An unknown task ID and an ID whose terminal record has been purged both produce the same `TASK_NOT_FOUND` call-level failure. The service retains no public expiry tombstone solely to distinguish those cases.

#### Expected usage

```python
current = await pipeline.get_task_state(receipt.task_id)

if isinstance(current, InProgress):
    terminal = await pipeline.await_task_to_completion(receipt.task_id)
```

## Task cancellation

Task cancellation is explicit control. It is separate from cancellation, timeout, or disconnection of a submission call or completion wait.

Cancellation operations have no caller-provided reason, are non-blocking, and are idempotent. A successful acknowledgement records a request; it does not promise the `Cancelled` terminal variant. Concurrent completion, expiry, or a serious failure may determine the terminal result.

### Local cancellation

Local cancellation authority exists only through a retained `RunHandle`:

```text
RunHandle.cancel() -> boolean
```

The operation returns:

- `true` when this call records the cancellation request; or
- `false` when cancellation is already in a terminal state. This indicates to the caller that awaiting `await handle.await_to_completion()` is not needed.

After `cancel()` returns `true`, a subsequent `get_state()` MUST observe either
`InProgress` with cancellation cleanup underway or a terminal state.

### Durable cancellation

Durable cancellation authority exists through the service and stable task ID:

```text
pipeline.cancel_task(task_id) -> boolean
```

The operation returns `true` only after the cancellation request is durably recorded and read-after-write visible to `get_state()`. It returns `false` when cancellation was already recorded, expiry won first, or the task was already terminal. A `true` acknowledgement survives service restart.

An unknown or purged task ID produces the same `TASK_NOT_FOUND` call-level
failure as durable observation.

### Expected usage

```python
handle = pipeline.start_task(request)

if handle.cancel():
    # optionally wait until task cancellation is completed
    terminal = await handle.await_to_completion()
```

## Task progress

`InProgress` carries a complete point-in-time progress snapshot. Progress is
data-only and uses the same output records as terminal results.

### `Progress`

```text
Progress
  task_id: task-id
  attempt_id: attempt-id
  outputs: sorted list<OutputState>
  scheduling: PlanProgressState
```

Every task and every attempt has an ID, including local tasks. A durable restart
keeps `task_id`, changes `attempt_id`, and replaces the prior attempt's
ephemeral progress.

#### Scheduling progress

Scheduling progress contains the stage counts and ordered stage summaries defined by the
[Eventing and Task Control Model](../07-scheduling/eventing-and-task-control-model-spec.md).

Active stage status is represented by membership in `pending_stages`, `running_stages`, or `blocked_stages`; each list contains ordered `StageProgressSnapshot` values.
Finished invocations contribute to `finished_stage_count` and are intentionally absent from the active lists.

#### Output progress

`outputs` contains every requested output, sorted by canonical `task_artifact_locator`.
Each `OutputState` and `TargetState` reports its current status according to the
rules in [Task results](#task-results).

Cancellation and expiry are not terminal until safe cleanup finishes. While
cleanup is underway, the task remains `InProgress`, and its progress snapshot
reflects the applicable cancellation or expiry work.

#### Expected usage

```python
match handle.get_state():
    case InProgress(progress):
        inspect_outputs(progress.outputs)
        inspect_scheduling(progress.scheduling)
```

## Task results

Local and durable execution use the same `RunState` family, terminal `RunResult`, output states, and target states.

### `RunState`

`RunState` is a data-only discriminated family. It contains no handles, callbacks, exceptions, runtime objects, provider clients, or live pipeline references.

#### Variants

```text
InProgress(Progress)
Completed(RunResult)
Failed(RunResult)
Cancelled(RunResult)
Expired(RunResult)
Interrupted(RunResult)
```

`get_state()` may return any applicable variant. `run(...)` and `await_to_completion(...)` guarantee a terminal variant.

`Interrupted` is local-only and means an explicit orderly local shutdown interrupted the task. Abrupt local manager loss is a call-level failure.
Durable service shutdown leaves the durable task `InProgress`.

Cancellation and expiry cleanup remain `InProgress`; `Cancelled` and `Expired` are terminal only after cleanup safely finishes.

#### Expected usage

```python
handle = await pipeline.get_task_state(receipt.task_id)

match handle.get_state():
    case InProgress(progress):
        inspect_progress(progress)
    case Completed(result):
        consume(result)
    case Failed(result) | Cancelled(result) | Expired(result) | Interrupted(result):
        inspect(result)
```

#### Canonical serialization

Local and durable states share one strict canonical representation:

```text
version: 1
state: "in_progress" | "completed" | "failed" |
       "cancelled" | "expired" | "interrupted"
data: Progress | RunResult
```

Canonical timestamps are UTC RFC 3339 strings with millisecond precision.
Bindings may expose native timestamp types.

An abridged serialized progress value is:

```json
{
  "version": 1,
  "state": "in_progress",
  "data": {
    "task_id": "task-123",
    "attempt_id": "attempt-2",
    "outputs": [
      {
        "task_artifact_locator": "a-task:/global/render/webvtt_transcript/any/task/task-123",
        "requested_artifact": "render/webvtt_transcript",
        "status": "PENDING",
        "targets": []
      }
    ],
    "scheduling": {
      "total_stage_count": 0,
      "finished_stage_count": 0,
      "pending_stage_count": 0,
      "running_stage_count": 0,
      "blocked_stage_count": 0,
      "pending_stages": [],
      "running_stages": [],
      "blocked_stages": []
    }
  }
}
```

### `RunResult`

`RunResult` is the terminal task payload:

```text
RunResult
  task_id: task-id
  attempt_id: attempt-id
  outputs: sorted list<OutputState>
  failure?: Failure
```

The enclosing terminal `RunState` variant is authoritative. `RunResult` has no
duplicated status, persisted task ID, error IDs, or embedded manifest.
Diagnostic execution and artifact manifests are separate.

`failure` contains at most one universal envelope. Its `issues` list aggregates
all independently reportable run issues in deterministic order:

- `Completed` prohibits `failure`;
- `Failed` requires `failure` with at least one issue; and
- `Cancelled`, `Expired`, and `Interrupted` include `failure` only when
  additional reportable problems occurred.

Planning failure uses `UNPLANNABLE`. A previously accepted durable task that can
no longer run against the available pipeline or environment uses
`PIPELINE_INCOMPATIBLE`. Both are structured task failures, not call-level
lookup failures.

### `OutputState`

Progress and terminal results use the same per-output record:

```text
OutputState
  task_artifact_locator: a-task (Non Completed Value)
  artifact_instance_locator?: a-instance
  artifact_status: OutputStatus
  targets: ordered list<TargetState>
```

**`task_artifact_locator`:**
This value can be used to retrieve artifact from the cache without the knowledge of the exact `a-instance` locator. This value is knowable immediately, and therefore is always present. This value should always be a canonical a-task which may retain an unresolved phase selector as per the provided `a-ref`.

**`artifact_instance_locator`**
This value can be used to retrieve artifact from the cache via the `a-instance` locator. This value only becomes present once planning resolves the complete output graph and therefore is contingent on the planning phase having completed. It may remain absent after cancellation, expiry, interruption, or planning failure.

**`artifact_status`**
The current availability status of this artifact. Can be one of the following enum values
```
{PENDING|RUNNING|SUCCEEDED|FAILED|PREVENTED_BY_FAILURE|CANCELLED|EXPIRED|INTERRUPTED}
```

**`targets`**
Contains the materialization status of request output targets.
Only available for non durable requests which provide output target handles.

#### Example
For example, a committed artifact remains reusable even though its local target
failed:

```text
OutputState
  artifact_status: SUCCEEDED
  task_artifact_locator: "a-task:/global/render/webvtt_transcript/any/task/task-123"
  artifact_instance_locator: "a-instance:/global/render/webvtt_transcript/none/fingerprint/b3d9..."
  targets:
    - target_index: 0
      status: FAILED
```

#### `OutputStatus`

The status describes semantic artifact availability when used by
`OutputState`, and delivery to one local target when used by `TargetState`

```
{PENDING|RUNNING|SUCCEEDED|FAILED|PREVENTED_BY_FAILURE|CANCELLED|EXPIRED|INTERRUPTED}
```

**`PENDING`**

The work required for this artifact or target has not begun. For an artifact,
planning, cache resolution, prerequisite work, producer admission, or producer
execution may still be pending. For a target, semantic artifact availability or
target-delivery admission may still be pending.

**`RUNNING`**

Work that may complete this artifact or target is active. For an artifact, this
includes cache resolution or producer execution. For a target, materialization
to the caller-provided destination has begun.

**`SUCCEEDED`**

The requested semantic artifact is available, or delivery to the target
completed successfully. Artifact success may come from cache reuse or new
execution; those are provenance differences, not different statuses. Target
failure does not change an already successful artifact from `SUCCEEDED`.

**`FAILED`**

Work directly responsible for this artifact or target was attempted and failed.
This is the causal output or target failure, rather than a consequence of
another task-fatal failure.

**`PREVENTED_BY_FAILURE`**

The artifact or target did not complete because another task-fatal failure
prevented its required work from starting or continuing. This is terminal and
is distinct from a scheduler stage being temporarily blocked while the task
remains active.

**`CANCELLED`**

The artifact or target did not complete because caller-requested task
cancellation won the task's stop race. Work may have started, but no successful
semantic artifact or target delivery completed before cancellation cleanup
finished.

**`EXPIRED`**

The artifact or target did not complete because the durable task's active
lifetime expired before completion. Work may have started, but no successful
semantic artifact or target delivery completed before expiry cleanup
finished. This status is durable-only.

**`INTERRUPTED`**

The artifact or target did not complete because explicit orderly shutdown
interrupted its local task. This status is local-only and is not used for abrupt
manager loss, which leaves the task outcome unavailable.

`PENDING` and `RUNNING` are permitted only in `InProgress`; terminal results
prohibit both. Every other status is terminal for that artifact or target.

`SUCCEEDED` requires `resolved_artifact` and `fingerprint`.
`artifact_instance_locator` is optional even on success because successful local
non-cache-backed delivery need not create a durable instance. Every other
status prohibits both `fingerprint` and `artifact_instance_locator`.

### `TargetState`

```text
TargetState
  target_index: nonnegative-integer
  status: OutputStatus
```

For local execution, `OutputState.targets` contains exactly one entry per
declared target in declaration order; `target_index` matches that order.
Targetless local outputs and all durable outputs use an empty list.

A target remains `PENDING` while its semantic output is `PENDING` or `RUNNING`.
Target delivery may become `RUNNING` only after semantic `SUCCEEDED`.

If semantic production becomes `FAILED`, every unstarted target becomes
`PREVENTED_BY_FAILURE`. If it becomes `CANCELLED`, `EXPIRED`, or `INTERRUPTED`, every
unstarted target receives that status. After semantic success, each target
independently ends as `SUCCEEDED`, `FAILED`, `CANCELLED`, `EXPIRED`, or
`INTERRUPTED`.

`Completed(RunResult)` requires every semantic output and every declared target
to be `SUCCEEDED`. A target failure therefore makes the task `Failed` without
changing the successful semantic output.

## Task lifetime, recovery, and retention

Task activity, recoverability, terminal-record availability, and artifact
availability are separate concerns.

Lifecycle durations are positive whole seconds. Bindings may expose native
duration types.

### Local task lifetime

A local task is owned by its local task manager, not by any `RunHandle`. A live
handle remains usable while that manager is active.

An awaitable orderly shutdown completes only after every local task has reached
a terminal state or safely released all task-owned resources. A task
interrupted by that explicit path may produce `Interrupted(RunResult)`.

Unawaited pipeline disposal provides only best-effort cleanup. If the task
manager disappears before a terminal state is produced, later observation and
awaiting fail with a `TASK_UNAVAILABLE` call-level failure; they do not invent
`Interrupted` or `Failed`.

### Durable active lifetime

A durable task has an active/recovery lifetime ending at
`active_expires_at`. This absolute wall-clock deadline continues to elapse
while the service is stopped or unavailable.

At the deadline, the service stops starting new work and begins safe expiry
cleanup. During cleanup the state remains `InProgress`. When cleanup finishes,
the task reaches `Expired(RunResult)`.

If recovery discovers that the deadline has passed, it enters expiry handling
without starting a new execution attempt.

Assuming completion or failure has not already won, the first durably recorded
control event determines caller cancellation versus active expiry.
Cancellation recorded first leads toward `Cancelled`; expiry recorded first
leads toward `Expired`.

### Durable recovery

Before `active_expires_at`, recovery of a nonterminal task creates a fresh
execution attempt with a fresh required `attempt_id` and fresh progress. The
stable `task_id` does not change.

Committed artifacts may be reused under the persisted-lifecycle and cache
contracts. In-memory plan, scheduler, and progress state are not resumed.

Orderly durable-service shutdown interrupts only the current attempt. It does
not terminalize the durable task and never produces durable `Interrupted`.

### Terminal-record retention

A durable task's terminal-record lifetime begins when the task actually
terminalizes. Its retained terminal state contains the absolute
`record_expires_at`.

Once that instant passes and the record is purged, all observation,
awaiting, and cancellation operations cross the `TASK_NOT_FOUND` boundary.

### Artifact retention boundary

Retaining a local terminal state does not keep the pipeline, cache providers,
or artifact query services alive. Provider-owned `a-task` resolution may expire
independently.

Durable terminal-record retention also does not determine artifact retention.
Artifacts remain governed by cache and artifact-lifecycle contracts.

## Cross-cutting invariants

1. A successfully allocated local task always produces a terminal `RunResult`
   unless abrupt manager loss makes that outcome unknowable.
2. Waiting cancellation, timeout, or disconnect never implicitly cancels a
   task.
3. Local cancellation authority exists only through a retained `RunHandle`;
   durable authority exists through the service and task ID.
4. Polling and awaiting use the same `RunState` family.
5. Every state carries stable task identity and current attempt identity.
6. Every progress and terminal output list is complete and deterministically
   sorted.
7. Durable submission acceptance is atomic, and every successfully accepted
   submission creates a new task.
8. Active expiry, terminal retention, and artifact retention are separate
   lifetimes.
9. `RunResult` never duplicates the terminal status and contains at most one
   failure envelope.
10. No public subscription API is part of the initial run surface.
