# Run Surface

## Purpose

This specification defines starting, observing, awaiting, cancelling and
consuming local and durable runs under the binding-oriented artifact model.

Local and durable execution share one semantic execution model. They differ in
how requests are supplied, retained and observed.

## Entry points

```text
runtime.run_task(LocalRunRequest) -> terminal RunState
runtime.start_task(LocalRunRequest) -> RunHandle
runtime.submit_task(DurableRunRequest, active_ttl?) -> DurableSubmissionReceipt
```

### Shared execution contract

An entry-point call either returns its declared value or fails with the common
call-level `Failure` envelope. Failure before local allocation or durable
acceptance means no task exists. Once allocation/acceptance succeeds, planning,
stage, target and task-control failures are represented by that task's
`RunState` and stable identity rather than replacing it with a call-level
exception.

Cancellation, timeout or disconnect of the caller's wait does not implicitly
cancel manager-owned work. It only ends that observation unless the caller also
invokes the explicit task-cancellation operation.

### Local run-to-completion

`run_task(...)` allocates a local task and waits for terminal state. Cancelling
or dropping the caller's wait does not itself cancel the manager-owned task.
Callers needing explicit task cancellation or live output access use
`start_task(...)`.

### Local start

`start_task(...)` returns a process-local `RunHandle` after successful local task
allocation.

```text
RunHandle
  task_id
  get_state() -> RunState
  await_to_completion() -> terminal RunState
  cancel() -> bool
  get_output(ScopedArtifactRequest) -> LocalOutputBindingHandle
```

A local output binding handle may expose live incremental members and typed
artifact readers. It is process-local and never serialized into durable
`RunState`/`RunResult`.

The `RunHandle` owns local cancellation authority and process-local live-output
capabilities. Dropping one observer does not cancel the task while the runtime
still owns it. Implementations must make handle close/drop behavior explicit and
must release observation resources without abandoning task cleanup.

### Durable submission

`submit_task(...)` atomically accepts a data-only request.

Acceptance validates presence, readability, and authorization and protects
every explicitly supplied `ContentId`. Missing, expired, corrupt, unreadable,
unauthorized, or unclaimable content rejects submission and no task exists.
Protection, applicable upload-grace deletion, and task creation are atomic;
failure performs no partial mutation.

Only after acceptance commits may the operation return:

```text
DurableSubmissionReceipt
  task_id
  requested_outputs: sorted list<ScopedArtifactRequest>
  completion_deadline
  task_record_retention_duration_seconds
  artifact_retention_duration_seconds
```

Each entry preserves one original complete canonical scoped request.

Every successful durable submission creates a new task identity. Equivalent
requests may share/reuse deterministic cache bindings but are not task-ID
deduplicated.

## Task artifact lookup

Durable/public task output lookup is metadata-first:

```text
GET /task/{task_id}/outputs
GET /task/{task_id}/outputs?request={encoded-ScopedArtifactRequest}
```

The API returns a binding snapshot, not a byte stream.

```text
TaskArtifactBinding
  requested: ScopedArtifactRequest
  status
  members: ordered list<TaskArtifactMember>
  failure?

TaskArtifactMember
  ordinal
  content_id: ContentId
```

`ordinal` is explicit and must match list position.

Exact schema, selected producer/invocation, binding provenance, and cache
resolution are persisted internally and omitted from this public response.

### Binding states

```text
PENDING
PRODUCING
AVAILABLE
FAILED
CANCELLED
EXPIRED
```

- `PENDING`: no committed member yet.
- `PRODUCING`: at least one member is committed; successful closure has not
  occurred.
- `AVAILABLE`: successful binding closure; members are complete.
- terminal non-available states retain any committed prefix while its durable
  retention remains valid.

There is no failed-member list. Every listed member is a committed final content
object.

A binding may remain `AVAILABLE` even if another task output later makes the
overall task terminally `Failed`.

## Raw content download

Raw byte retrieval is by content identity:

```text
GET /content/blake3/{unpadded-base64url-digest}
```

Task binding lookup and byte download are separate operations. For plural
outputs the caller downloads members individually; Chunky does not implicitly
concatenate them or synthesize an archive/container.

Typed internal decoding may use `ResolvedArtifactInterpretation`, but raw byte
retrieval requires only `ContentId`.

There is no public provenance lookup/download endpoint in the MVP.

## Run state

`RunState` remains data-only:

```text
InProgress(Progress)
Completed(RunResult)
Failed(RunResult)
Cancelled(RunResult)
Expired(RunResult)
Interrupted(RunResult)      # local only
```

After successful task allocation/acceptance, planning and execution failures are
task outcomes represented by `RunState`, not call-level exceptions in place of
the task.

## Progress and output state

Progress contains current scheduling state plus one `OutputState` for every
requested output.

```text
OutputState
  requested: ScopedArtifactRequest
  status
  members: ordered list<TaskArtifactMember>
  local_targets?          # local data-only target state only
```

The logical output-binding shape is shared by local and durable observation.
Only local `RunHandle`/`LocalOutputBindingHandle` adds live process-local
capabilities.

Task progress is a point-in-time snapshot, not an event log.

Each snapshot contains one output entry for every original requested output,
including outputs that are still pending or later prevented by task failure.
Lists are deterministically ordered by canonical request identity; member lists
are ordered by ordinal. A snapshot must not combine fields observed from
different task-state revisions.

## Local output handles

Conceptually:

```text
LocalOutputBindingHandle<T>
  get_state() -> OutputState
  await_terminal() -> OutputState
  asynchronous iteration -> committed ArtifactMember<T>
```

When incremental visibility is active, iteration may yield members before the
stage or task completes. Waiting for a member is cancellation-aware with respect
to the local observation/handle contract without implicitly cancelling the
underlying task unless the caller explicitly invokes task cancellation.

## Cancellation

Task cancellation is explicit control:

```text
RunHandle.cancel() -> bool
runtime.cancel_task(task_id) -> bool
```

Cancelling a wait, HTTP request, poll or download does not cancel the task.

A task cancellation request stops new work and propagates cooperative
cancellation to running stages. Already committed content members remain valid.
Cleanup completes before the task publishes its terminal state.

Cancellation is idempotent from the caller's perspective. The result indicates
whether this call initiated/confirmed a stop request, but callers determine the
eventual outcome by observing terminal `RunState`. A concurrent successful
completion may win; cancellation never rewrites a task that already committed a
terminal state.

## Output and task status independence

Overall task state and individual requested-output binding state are related but
not identical.

Example:

```text
Output A closes successfully -> AVAILABLE
Output B later fails
Task -> Failed
Output A remains AVAILABLE
```

Conversely, an output may have committed members but terminate `FAILED`; the
runtime exposes the valid committed prefix without claiming it is complete.

## Durable waiting

Durable observation uses stable task ID:

```text
runtime.get_task_state(task_id) -> RunState
runtime.await_task_to_completion(task_id) -> terminal RunState
```

Waiting is repeatable and does not consume the task/result. Unknown or purged
IDs use the common task-not-found failure.

### Waiting, deadlines and disconnects

Local and durable waiters observe the same state family. A waiter may impose a
caller-side timeout/deadline, but that deadline bounds the wait rather than task
execution. After a timeout or transport disconnect, the caller can observe the
same local handle or durable task ID again.

Durable waiting must not hold a database transaction or content reservation for
the whole wait. Implementations may poll, long-poll or subscribe internally as
long as each returned state is coherent and the public semantics remain
repeatable. The MVP exposes no separate public progress-event subscription
contract.

## Result and target diagnostics

Terminal results retain the stable `task_id`, current/final attempt identity,
requested-output states and at most one causal task failure envelope. Local
results may additionally report ordered target delivery states, but never
serialize live target handles.

A local target is tracked independently from its logical output binding. The
binding may be `AVAILABLE` while a particular target failed to materialize, and
one target may have committed before another target failure makes the task
terminally failed. No rollback of a committed target is promised.

## Content retention in task results

Requested output members are retained according to durable task artifact
retention. Supplied inputs are protected for active/recoverable execution but
are not automatically retained for the output artifact-retention period after
terminalization.

An explicitly requested intermediate is persisted, observed, and retained by
these same rules. Incidental produced intermediates are absent from the MVP
public task-output lookup even when an internal cache record exists.

Task-record retention and content retention are independent.

## Invariants

1. Local and durable execution have one semantic model.
2. Durable acceptance validates/protects all supplied content before returning a
   receipt.
3. The original `ScopedArtifactRequest` correlates a requested output with its
   `TaskArtifactBinding`; no output key or public task-artifact locator exists.
4. Task artifact lookup returns metadata; raw content download is separate.
5. Every listed output member is already committed and final.
6. `RunState` remains data-only.
7. Local live output capabilities remain on process-local handles.
8. Output-binding status may remain successful when the overall task fails
   elsewhere.
9. Call-level wait cancellation, timeout and disconnect never imply task
   cancellation.
10. Progress and terminal output lists are complete and deterministically
    ordered.
