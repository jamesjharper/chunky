# Run Surface Interview Record

> **Historical interview record.** Public task-output identity and result shape
> are now defined by the active run/download/task specifications.

This working record preserves decisions made while reviewing
`04-run-surface.md`. It is not the authoritative run-surface specification.
The target specification must not be changed until the user confirms that the
interview has reached a shared understanding.

## Confirmed decisions

### 1. Every allocated local task completes with `RunResult`

Once a local task ID has been allocated, all equivalent waiting forms return the
same terminal `RunResult`:

```text
runtime.run(request)
runtime.start(request).await_to_completion()
await runtime.start(request)
```

This applies to `COMPLETED`, `FAILED`, and `CANCELLED` outcomes. Planning,
execution, artifact, and target-materialization failures are domain outcomes in
the result rather than exceptions used in place of the result. Structured
partial-output and error information therefore remains available uniformly.

An exception remains appropriate when the caller cannot invoke or use the local
task-control surface and no domain result exists, including rejection before
task allocation.

The older Python POC is not authoritative here: its `run` method unwraps the
successful task output and raises `RunExecutionError` for non-success results.

Status: confirmed by the user.

Decision 15 refines the return shape: callers receive a terminal `RunState`
variant containing the `RunResult`, rather than the bare result.

### 14. Local handles support repeated and concurrent awaiting

Multiple callers may await the same `RunHandle` concurrently, and a caller may
await it again after completion. Every successful wait returns the same
immutable terminal `RunResult` for the task.

Awaiting observes completion; it does not consume the handle or result.
Cancellation or timeout of one wait does not affect other waiters.

Status: confirmed by the user.

### 15. Polling and awaiting return the same discriminated run-state value

The optional `RunHandle.get_result() -> RunResult | None` shape is removed.
Non-blocking inspection instead returns an always-present discriminated value:

```text
RunState
  InProgress
  Completed(RunResult)
  Failed(RunResult)
  Cancelled(RunResult)
```

Other distinct terminal outcomes receive variants where applicable. Terminal
variants retain the complete `RunResult`, including outputs and structured
errors.

`await_to_completion()` returns this same object and guarantees that its value
is a terminal variant. `runtime.run(request)` remains equivalent to starting
and awaiting, so it returns the same terminal run-state value. This refines
decision 1: the authoritative terminal `RunResult` is carried by the returned
terminal variant rather than returned bare.

The concrete method and type names remain to be decided.

Status: confirmed by the user.

### 13. Durable receipts also use `output_artifacts`

The durable receipt uses the same field name and meaning as the local handle:

```text
DurableSubmissionReceipt
  task_id
  output_artifacts: sorted list<a-task>
```

This replaces `output_task_locators`. The field is a deterministic sorted list
of early stable task locators and does not assert planning resolution or
instance availability.

Status: confirmed by the user.

### 12. `RunHandle.output_artifacts` lists immediate output `a-task` locators

`RunHandle` exposes:

```text
RunHandle
  task_id
  output_artifacts: sorted list<a-task>
```

`output_artifacts` contains one stable task locator for every requested output.
It does not assert that the planner has produced a Completed Value or that a
committed instance is available. Each locator already embeds its requested
artifact reference, so a map keyed by the same reference would be redundant.

The list is sorted deterministically by canonical locator representation.

Status: confirmed by the user.

### 10. The handle does not directly expose artifact instance IDs

`RunHandle` has no direct artifact-instance-ID properties or lookup methods.
Committed output instance locators are exposed by progress snapshots or the
terminal `RunResult`, where availability and output outcome are represented.

The handle may contain `a-task` locators because task-relative output identity
can be established near task allocation. For outputs whose requested artifact
reference still contains an unresolved phase selector, however, the early
`a-task` cannot itself guarantee a fully resolved artifact value; planning is
required to determine that value. The exact query behavior for early
unresolved versus fully resolved task locators remains open.

Status: confirmed by the user. Decision 11 resolves the phased `a-task`
question.

### 11. An early `a-task` remains stable while its resolution develops

The `RunHandle` may expose stable `a-task` locators immediately. Each locator is
formed from the task ID and the requested artifact reference, which may retain
an unresolved phase selector such as `any`. Planning does not rewrite that
locator.

Dereferencing or querying the `a-task` must distinguish:

- the stable requested reference, which may be incomplete;
- the planner-resolved Completed Value, once known; and
- the committed `a-instance`, once available.

The task binding therefore acts as an indirection from an early stable requested
identity to later resolution and availability information. The detailed query
API belongs to the artifact or cache surface, while the run surface may rely on
this behavior when returning early task locators.

Status: confirmed by the user.

### 9. `start()` allocates the local task before operational work

`runtime.start(request)` allocates a task ID and returns its `RunHandle` before
operational binding, planning, cache access, or execution begins.

Only inability to invoke the surface, such as a supplied value that cannot be
treated as a `LocalRunRequest`, may fail before allocation. After allocation,
operational failures are terminal `RunResult` outcomes, and an immediate
`cancel()` can cover the complete lifecycle.

Because artifact binding and cache resolution have not occurred at this
boundary, the newly returned handle cannot contain resolved artifact instance
IDs.

Status: confirmed by the user.

### 8. Callers use `start()` when they need local cancellation authority

`runtime.run(request)` does not accept a caller-provided cancellation source or
token. A caller that may need to request cancellation uses
`runtime.start(request)`, retains the returned `RunHandle`, and calls
`handle.cancel()`.

This preserves a single task-owned cancellation authority and keeps `run()` as
the convenience form equivalent to starting the task and awaiting its terminal
result without retaining its handle.

Status: confirmed by the user.

### 7. Local cancellation has no caller-provided reason

For now, `RunHandle.cancel()` accepts no human-readable reason or other
caller-provided cancellation metadata. The local surface remains:

```text
cancel() -> bool
```

Status: confirmed by the user.

### 6. Local `cancel()` returns a boolean for now

`RunHandle.cancel()` returns a boolean rather than a three-state result:

- `true` means this call newly registered the cancellation request; and
- `false` means cancellation was already requested or the task was already
  terminal.

This intentionally leaves those two `false` cases collapsed for now. It
supersedes the proposed `CancelRequestResult` enum for this surface and
conflicts with the current draft of Eventing and Task Control; that draft must
be reconciled after the interview.

Status: confirmed by the user.

### 5. Local `cancel()` requests cancellation and returns immediately

`RunHandle.cancel()` is a non-blocking cancellation request. It immediately
reports whether this call newly registered the request; it does not promise
that cancellation has completed.

The task may continue while cooperative cancellation and cleanup proceed. The
caller awaits the handle to obtain the authoritative terminal `RunResult`.
Cancellation may lose a race to completion or failure.

Status: confirmed by the user.

### 3. Local wait timeouts are binding capabilities, not a mandatory core parameter

The language-neutral `RunHandle.await_to_completion()` contract does not require
a timeout parameter when a binding's normal waiting facilities can safely
time-bound the wait. A binding may expose an explicit timeout when its language
cannot otherwise express that behavior adequately.

In every binding, timing out the wait must preserve decision 2: it stops only
the wait and does not request task cancellation.

This is particularly important because runs may take many hours. The caller
should be aware of the run's approximate duration before choosing how to wait;
the source and contract for that duration information remain to be decided.

Status: confirmed by the user, subject to language capabilities.

### 4. The run surface warns about long duration but does not estimate it

Callers must treat local and durable runs as potentially lasting many hours.
They are responsible for choosing `run`, `start`, durable submission, and any
binding-level wait timeout accordingly.

The run surface does not calculate or return an approximate duration. A credible
programmatic estimate would depend on planning, cache state, resource
availability, and stage cost information and therefore belongs to a separate
planning or scheduling capability.

Status: confirmed by the user.

### 2. Cancelling or timing out a wait does not cancel the local task

Cancellation or timeout of a caller's wait stops only that wait. The local task
continues running unless cancellation is requested explicitly through the task
control surface.

This rule applies consistently to `runtime.run(request)`, awaiting a
`RunHandle` directly, and `RunHandle.await_to_completion()`. It prevents one
waiter from changing task state for other observers and preserves the
distinction between observing completion and requesting cancellation.

Status: confirmed by the user.

### 16. The shared type is `RunState`, inspected through `get_state()`

The public names are:

```text
RunHandle.get_state() -> RunState
RunHandle.await_to_completion() -> RunState
runtime.run(request) -> RunState
```

The variants include:

```text
InProgress
Completed(RunResult)
Failed(RunResult)
Cancelled(RunResult)
```

Other distinct terminal outcomes receive variants where applicable.
`get_state()` may return any variant. `await_to_completion()` and
`runtime.run()` guarantee a terminal variant.

Status: confirmed by the user.

### 17. `InProgress` carries progress and replaces separate progress polling

`RunState` has the conceptual shape:

```text
InProgress(progress: RunProgressSnapshot)
Completed(result: RunResult)
Failed(result: RunResult)
Cancelled(result: RunResult)
```

`RunHandle.get_progress()` is removed. Callers obtain an atomic current
in-progress snapshot from `get_state()`. Progress subscriptions remain available
for receiving future snapshot changes without polling.

Status: confirmed by the user.

### 18. Terminal status exists only on the enclosing `RunState`

`RunResult.status` is removed. The enclosing terminal variant is the
authoritative status:

```text
Completed(RunResult)
Failed(RunResult)
Cancelled(RunResult)
```

This prevents contradictory duplicated status values. `RunResult` retains the
task identity, requested-output results, structured errors, and any other
terminal details that are independent of the outcome variant.

Status: confirmed by the user.

### 19. `Interrupted` is emitted only for explicit orderly shutdown

The local terminal variants include:

```text
Interrupted(RunResult)
```

This outcome is distinct from caller-requested cancellation. It is produced
only when Chunky's task manager receives an explicit orderly shutdown request
and initiates task interruption itself.

The surface does not attempt to infer abrupt process termination portably. If
the process is killed, crashes, loses power, or otherwise disappears before
publishing a result, a local task has no observable terminal `RunState`.

Status: confirmed by the user.

### 20. A `RunHandle` does not own or cancel its task

Dropping or garbage-collecting the last caller-held `RunHandle` leaves the local
task running under runtime or task-manager ownership. Cancellation remains an
explicit `cancel()` operation.

In this sense, run handles are weak references to their underlying tasks: they
provide observation and control but their release does not determine task
lifetime. Whether a still-live handle may itself become invalid because the
task manager releases retained state remains to be decided.

Status: confirmed by the user.

### 21. A live handle remains valid while its task manager is active

A caller-held `RunHandle` remains valid for state inspection, cancellation
requests, subscription, and awaiting while the owning runtime or task manager
is active.

The handle is weak only with respect to lifecycle authority: releasing it does
not cancel or otherwise determine the task's lifetime. The manager does not
discard the underlying task or terminal result while a live handle may still
use it.

Status: confirmed by the user.

### 22. Durable observation and control are service operations by task ID

The interoperable durable surface is:

```text
durable_task_service.get_state(task_id)
durable_task_service.await_to_completion(task_id)
durable_task_service.cancel(task_id)
```

`DurableSubmissionReceipt` remains serializable data and does not own these
methods. A binding may construct a process-local convenience handle from the
service and receipt, but that wrapper is optional and must not be required for
cross-process reattachment.

Status: confirmed by the user.

### 23. Local and durable observation share the `RunState` family

`durable_task_service.get_state(task_id)` and
`durable_task_service.await_to_completion(task_id)` return the same `RunState`
family used by local handles. The durable surface adds terminal variants for
durable lifecycle outcomes that cannot occur during ordinary local execution.

The exact distinction among `Unplannable`, `Incompatible`, and ordinary
`Failed` remains open. In particular, the user expects the surface to be
specific about a previously submitted task that is automatically attempted
after restart against a runtime configuration that no longer supports it, and
notes that such tasks may be routed to an internal dead-letter queue.

Status: shared state family confirmed. Decision 24 resolves the top-level
durable terminal taxonomy.

### 24. Planning and recovery incompatibilities are structured failures

`Unplannable` and `Incompatible` are not public `RunState` variants. Both use:

```text
Failed(RunResult)
```

The `RunResult.errors` collection retains structured codes such as
`UNPLANNABLE` and `RUNTIME_INCOMPATIBLE` so callers and operators can
distinguish the causes.

Scheduling may route either class to a dead-letter queue or another operational
workflow. That routing is internal and does not become a public task state.

Status: confirmed by the user.

### 25. `UNPLANNABLE` and `RUNTIME_INCOMPATIBLE` have distinct scopes

The structured failure codes mean:

- `UNPLANNABLE`: the accepted request is understood and valid, but the runtime
  manifest used for this attempt cannot produce a plan. This applies on an
  initial attempt and on a recovery attempt after configuration drift.
- `RUNTIME_INCOMPATIBLE`: recovery cannot safely interpret or honor the
  previously accepted request under the current service contract, so meaningful
  planning cannot begin.

A task automatically attempted after restart against a changed manifest that no
longer supports its requested graph therefore fails with `UNPLANNABLE`. A
deeper persisted-contract or service-compatibility mismatch uses
`RUNTIME_INCOMPATIBLE`.

Status: confirmed by the user.

### 26. Interrupting a durable wait never cancels the durable task

Cancellation or timeout of the client wait, client disconnection, and transport
or RPC failure stop only that observation attempt. None implicitly requests
durable task cancellation.

Durable cancellation requires a separate call to:

```text
durable_task_service.cancel(task_id)
```

Status: confirmed by the user.

### 27. Durable awaiting is repeatable across callers and processes

Callers may invoke
`durable_task_service.await_to_completion(task_id)` repeatedly, concurrently,
and from different processes. Every successful wait returns the same immutable
terminal `RunState` while the durable task record is retained.

After an interrupted wait, the caller may safely reconnect and wait again using
the same task ID without changing or resubmitting the task.

Status: confirmed by the user.

### 28. Missing durable task identity is a service-level lookup error

If a durable task ID is unknown or its retained record has expired,
`get_state`, `await_to_completion`, and `cancel` fail at the service lookup
boundary. They do not synthesize `Failed(RunResult)`.

`Failed` remains reserved for a known accepted task that reached a terminal
domain outcome.

Status: confirmed by the user.

### 29. Durable `cancel` acknowledges durable recording with a boolean

The durable cancellation surface is:

```text
await durable_task_service.cancel(task_id) -> bool
```

- `true` means this call durably recorded the first cancellation request.
- `false` means cancellation was already recorded or the task was already
  terminal.

Returning does not wait for workers or cleanup to finish. The caller uses
`await_to_completion(task_id)` to obtain the authoritative terminal `RunState`.

Status: confirmed by the user.

### 30. Durable cancellation carries no caller-provided reason

`durable_task_service.cancel(task_id)` accepts no caller-provided reason or
metadata. This matches the intentionally minimal local `RunHandle.cancel()`
surface.

Authentication and operational audit systems may record the caller outside this
run-surface contract.

Status: confirmed by the user.

### 31. Successful cancellation acknowledgement does not determine outcome

A `true` result from local or durable `cancel()` means only that this call first
accepted or durably recorded the cancellation request. It does not guarantee a
terminal `Cancelled` variant.

A concurrently completed task, a non-cancellation failure that already made the
task unsatisfiable, or a serious cleanup failure may still produce `Completed`
or `Failed`. The terminal `RunState` is authoritative.

Status: confirmed by the user.

### 32. Cancelled results preserve every requested output outcome

`Cancelled(RunResult)` contains exactly one `OutputResult` for every requested
semantic output.

An output committed before cancellation remains available and retains its
committed `a-instance` locator. Outputs that did not commit report cancellation
or non-attempt, as applicable. Cancellation does not roll back committed cache
artifacts or caller targets that already committed transactionally.

Partial output availability does not change the overall `Cancelled` state.

Status: confirmed by the user.

### 33. Acknowledged durable cancellation survives restart

After `durable_task_service.cancel(task_id)` returns `true`, the cancellation
request survives service restart and prevents new planning, stage execution, or
target materialization for that task.

Recovery may perform required cleanup and terminal finalization but does not
resume ordinary work. This guarantee applies only while the durable task remains
within its retention lifetime; the task TTL and expiration behavior remain to
be decided.

Status: confirmed by the user. Decision 34 separates the relevant lifetimes.

### 34. Durable tasks have separate active and terminal lifetimes

The durable surface distinguishes:

- an active/recovery lifetime, which limits how long a nonterminal task remains
  eligible for execution or restart recovery; and
- a terminal retention lifetime, which controls how long the final `RunState`,
  task identity, and `a-task` resolution remain queryable after termination.

Expiring unfinished work and garbage-collecting completed history are separate
policies and must not be represented by one ambiguous TTL.

Status: confirmed by the user.

### 35. Active-lifetime expiry terminalizes the durable task

When a durable task's active/recovery lifetime expires, the task does not simply
disappear. It stops new work, requests cleanup of active work, and reaches:

```text
Expired(RunResult)
```

The result preserves every requested output outcome, including committed
partial outputs. The separate terminal-retention lifetime begins after this
terminal transition.

Status: confirmed by the user.

### 36. Durable submission accepts an optional active TTL

Durable submission has the conceptual shape:

```text
submit(request, active_ttl?)
  -> DurableSubmissionReceipt
       task_id
       output_artifacts
       active_expires_at
```

`active_ttl` is optional. When omitted, the durable task service selects its
configured default. When provided, the service resolves it against its
configured constraints, including any maximum. The receipt returns the
effective absolute `active_expires_at` deadline rather than merely echoing the
request.

The active TTL is submission lifecycle policy, not semantic runtime work.

Status: confirmed by the user.

### 37. Terminal task-record retention is service-owned

The caller cannot override the terminal task-record retention lifetime. The
durable task service determines it from operational configuration.

`DurableSubmissionReceipt` exposes the effective terminal-retention duration.
Once terminal, the returned `RunState` exposes the absolute record expiry.
Artifact-instance retention remains separately governed by request cache policy
and is not extended merely because task history remains queryable.

Status: confirmed by the user.

### 38. The first recorded control event wins cancellation versus expiry

Assuming completion or failure has not already won:

- caller cancellation durably recorded before active expiry leads toward
  `Cancelled(RunResult)`; and
- active expiry recorded before caller cancellation leads toward
  `Expired(RunResult)`.

A cancellation request after expiry has been recorded returns `false`. As with
decision 31, a concurrently completed task or serious failure may still produce
`Completed` or `Failed`; the terminal `RunState` remains authoritative.

Status: confirmed by the user.

### 39. Durable recovery creates fresh progress from a fresh attempt

While a durable task is nonterminal, `get_state(task_id)` returns a current
coherent `InProgress(progress)`. Detailed plans, stage progress, and percentages
do not promise continuity across service restart.

Recovery behaves as though execution has just started. It creates a fresh
progress snapshot and evaluates the complete artifact graph and its stages
again. Cache hits are expected to make previously committed work complete
quickly, but that reuse does not turn the recovered attempt into progress
continuation or historical event replay.

Status: confirmed by the user.

### 40. Progress snapshots identify the current execution attempt

Each `InProgress(progress)` snapshot carries:

```text
task_id
attempt_id
```

`task_id` remains stable for the durable task. `attempt_id` changes whenever
recovery creates a fresh execution attempt. This lets callers distinguish a
legitimate progress reset from regression or out-of-order delivery within one
attempt.

The attempt identity is progress metadata; it does not create a new submitted
task or change the task's output `a-task` locators.

Status: confirmed by the user.

### 41. The durable surface has no public progress-event subscription

The initial interoperable durable control surface remains:

```text
get_state(task_id)
await_to_completion(task_id)
cancel(task_id)
```

Implementations may use polling, long-polling, streams, or notifications to
implement these operations, but transport mechanics, event replay, cursors, and
cross-process event ordering are not part of this run-surface contract.

Status: confirmed by the user.

### 42. Local subscriptions deliver complete `RunState` values

The local subscription surface is:

```text
handle.subscribe_state_changes(handler)
```

A new subscriber is asynchronously given the current immutable `RunState`.
Thereafter it receives changed `InProgress(progress)` values and one terminal
state. Polling through `get_state()`, awaiting, and subscription therefore
observe the same state model.

Status: confirmed by the user.

### 43. Detaching a subscription never changes the task

`subscribe_state_changes(handler)` returns an idempotent detach handle. Detach
stops future callbacks to that subscriber but does not cancel, pause, or
otherwise modify the task.

Releasing or garbage-collecting the subscription handle has the same effect as
explicit detach where the binding can provide deterministic cleanup.

Status: confirmed by the user.

### 44. State handlers are isolated from the task, not necessarily each other

If local state subscriptions remain in the design, slow or failing handlers
cannot delay or fail planning, execution, cancellation, cleanup, or terminal
result production. Handler failures are logged diagnostically and are not added
to `RunResult`.

For simplicity, handlers may share one dispatcher queue or thread. A slow
handler may therefore delay delivery to other handlers; per-handler execution
is not guaranteed.

The user has reopened whether the subscription surface should exist at all.
Decisions 42-44 are conditional until that question is resolved.

Status: task isolation confirmed; subscription retention remains open.

### 45. The initial run surface has no subscription API

Local state and progress subscriptions are removed from the initial run surface.
Callers use:

```text
handle.get_state()
handle.await_to_completion()
```

Bindings may poll `get_state()` when they need live progress. A future event API
may choose callbacks, asynchronous iterators, channels, or streams based on
demonstrated language-specific needs.

This supersedes decisions 42-44. The existing POC callback dispatcher is an
older implementation detail and is not authoritative.

Status: confirmed by the user.

### 46. Interrupted submission does not roll back durable acceptance

If the caller's `submit(...)` wait is cancelled, times out, disconnects, or
otherwise loses its response after atomic durable acceptance, the accepted task
continues to exist. Loss of the response channel cannot roll back the acceptance
record.

The caller therefore needs a safe receipt-recovery or idempotent-retry
mechanism. Whether equal requests automatically reuse tasks, and how cancelled
or paused work participates, remains open.

Status: confirmed by the user. Decision 49 resolves duplicate-submission
behavior without a receipt-recovery mechanism.

### 47. Request equality does not automatically deduplicate submissions

Submitting equal `DurableRunRequest` values creates distinct tasks by default.
Callers express retry identity through a separate submission idempotency key
rather than request equality.

This preserves intentional reruns with independent task identity, active TTL,
cancellation, progress, and audit history. Artifact cache reuse remains
responsible for avoiding unnecessary recomputation of equal semantic work.

There is no pause/resume surface in the initial contract. Cancelled tasks are
terminal and are not silently restarted by submitting an equal request.

Status: confirmed by the user.

### 48. Durable submission accepts an optional idempotency key

The durable submission surface accepts an optional caller-generated
`idempotency_key`:

```text
submit(request, active_ttl?, idempotency_key?)
```

While the key mapping is retained, retrying with the same key and identical
submission parameters returns the original `DurableSubmissionReceipt` rather
than creating another task. Omitting the key always creates a new task, even for
an equal request.

Status: superseded by decision 49.

### 49. Every durable submission creates a new task

The durable submission surface has no idempotency key. Every accepted call
creates a new task, including repeated submission of an equal request after a
timeout or lost response.

The surface therefore has no idempotency-key conflict behavior and does not
automatically reuse, restart, or return an existing equal task.

Status: confirmed by the user, superseding decision 48.

### 50. A lost receipt may create an undiscoverable duplicate task

If durable acceptance commits but the caller never receives the receipt, the
caller may be unable to discover that task. Retrying submission creates another
task.

For identical semantic requests, artifact-level cache reuse is expected to
avoid repeating already committed work where cache policy permits it. This does
not merge task identity: cancellation, progress, lifetime, result, and audit
history remain independent for each task.

Status: confirmed by the user. Decision 51 confirms the cache-policy
qualification.

### 51. Duplicate tasks respect cache policy

Equal tasks reuse committed artifacts or converge on in-flight artifact work
only where their resolved cache policy permits reads.

An equal request using `REFRESH` or `BYPASS` may intentionally recompute.
Submission duplication does not override explicit cache intent.

Status: confirmed by the user.

### 52. Submission cancellation may abort only before atomic acceptance

Atomic acceptance is one indivisible commit point, not the whole duration of
the `submit(...)` call. Before commit, the service may deserialize, validate,
verify input instances, resolve lifetime policy, and prepare retention claims.

If cancellation reaches the service during that pre-commit work, the service
may abort and create no task. If cancellation races the acceptance transaction,
either the complete acceptance record commits or the transaction rolls back;
there is never a partially accepted task. Once commit succeeds, decision 46
applies and loss or cancellation of the caller's wait does not remove the task.

Status: confirmed by the user.

### 53. Durable submission returns an explicit acceptance outcome

Durable submission originally returned:

```text
SubmitResult
  Accepted(DurableSubmissionReceipt)
  Rejected(SubmissionErrors)
```

`Rejected` represents an expected pre-acceptance domain rejection and creates no
task ID, output `a-task` locators, or `RunState`. `RunState` applies only after
`Accepted`.

Transport failure, service unavailability, and invalid use of the API remain
call-level errors because the caller cannot rely on receiving a domain value in
those cases.

This mirrors the local allocation boundary conceptually: local `start()` is
synchronous and atomic through in-memory task allocation, while durable
acceptance is awaited because its atomic boundary requires durable I/O.

Status: confirmed by the user.

Superseded by the user on 2026-07-24. Durable submission now returns
`DurableSubmissionReceipt` directly after atomic acceptance. Rejection before
acceptance is a call-level `Failure`, using the same binding error mechanism as
other operations that cannot produce their declared return value. If the
acceptance commit succeeds but the receipt is lost, the call may fail with an
indeterminate submission outcome even though the task exists.

### 54. All domain failures use one versioned data-only envelope shape

Request validation, submission rejection, and terminal run failure conform to
the same structural contract:

```text
Failure
  kind
  message
  version
  issues: ordered-list<FailureIssue>

FailureIssue
  code
  message
  context_type
  context: object selected by context_type
```

`kind` and `context_type` select domain-specific categories and context objects.
The existing `RequestValidationFailure` is the `REQUEST_VALIDATION` member of
this family. Submission rejection and run failure use the same versioned,
lossless, data-only representation rather than separate ad hoc error schemas.

Status: confirmed by the user.

### 55. Failure issue context replaces error-ID correlation

`OutputResult.error_ids` and `TargetMaterializationResult.error_ids` are removed.
Each universal `FailureIssue` carries any stage, artifact, requested output, or
target association inside its typed `context`.

Output and target status provide the local outcome. Detailed causes are found by
examining the failure envelope's deterministically ordered issues and their
contexts. The run surface does not add a second error-ID correlation system.

Status: confirmed by the user.

### 56. `RunResult` carries at most one aggregated failure envelope

The terminal result has the conceptual shape:

```text
RunResult
  task_id
  outputs
  failure: Failure?
```

One failure envelope aggregates every independently reportable run issue in its
deterministically ordered `issues` collection. The run result does not contain a
collection of failure envelopes.

Status: confirmed by the user.

### 57. Terminal variants constrain failure presence

The terminal state invariants are:

- `Completed` has no failure envelope.
- `Failed` has one failure envelope containing at least one issue.
- `Cancelled`, `Expired`, and `Interrupted` may omit the failure envelope because
  the variant explains the primary outcome; they include one only when
  additional reportable problems occurred.

Status: confirmed by the user.

### 58. The complete shared `RunState` family is data-only

`RunState`, `RunProgressSnapshot`, `RunResult`, output and target results, and
failure envelopes all have lossless data-only representations suitable for
transport and persistence.

They contain no live handles, callbacks, exceptions, builders, parsed runtime
objects, or other process-local state. Local bindings may provide idiomatic
wrappers while preserving the same interoperable data.

Status: confirmed by the user.

### 59. Cancellation and expiry cleanup remain `InProgress`

After caller cancellation or active expiry is recorded, but before cleanup and
terminal finalization complete, `get_state()` returns:

```text
InProgress(progress)
```

The progress snapshot carries a control state such as `CANCELLING` or
`EXPIRING`. Only completed finalization produces terminal `Cancelled` or
`Expired`.

Status: confirmed by the user.

### 60. Successful cancellation has read-after-write visibility

After local or durable `cancel()` returns `true`, every subsequent
`get_state()` observes either:

- `InProgress(progress)` with cancellation underway; or
- a terminal state that won the race.

It must not return an ordinary running state that omits the accepted
cancellation request.

Status: confirmed by the user.

### 61. `active_expires_at` starts expiry handling

`DurableSubmissionReceipt.active_expires_at` is the deadline at which the
service must stop starting new work for the task and begin expiry handling. It
is not a promise that cleanup and terminal finalization have completed at that
instant.

After the deadline, `get_state()` may remain `InProgress` with `EXPIRING` while
safe cleanup completes. Terminal-retention timing begins only when the task
actually reaches its terminal state.

Status: confirmed by the user.

### 62. Orderly local shutdown awaits safe task cleanup

The runtime or local task manager exposes an awaitable orderly shutdown.
Shutdown completes only after every local task has either reached a terminal
`RunState` or safely released all task-owned resources.

Tasks interrupted by this known shutdown path may produce
`Interrupted(RunResult)` as defined by decision 19.

Status: confirmed by the user.

### 63. Unawaited runtime disposal provides only best-effort cleanup

The contract guarantees observable terminal results and completed resource
cleanup only when the caller awaits orderly shutdown.

Dropping the runtime, releasing it through garbage collection, or abruptly
ending its runtime may trigger binding-specific best-effort cleanup but does not
promise an observable `Interrupted` result or completed asynchronous cleanup.

Status: confirmed by the user.

### 64. A configured runtime supports concurrent local tasks

One configured runtime supports concurrent calls to `run()` and `start()` from
multiple callers. Each successful call allocates an independent task.

The runtime and its shared services make caller-visible mutable operations safe
for concurrent use. Shared scheduler, provider, and cache constraints coordinate
the tasks without conflating their identity, state, cancellation, or results.

Status: confirmed by the user.

### 65. Local request consumption occurs at successful task allocation

`runtime.start(request)` and therefore `runtime.run(request)` consume the
`LocalRunRequest` only when local task allocation succeeds.

If invocation fails before allocation, the request and every handle it owns
remain valid for retry or explicit cleanup. After allocation, the request
remains consumed regardless of completion, failure, cancellation, interruption,
or expiry outcome.

Status: confirmed by the user.

### 66. Durable requests are reusable and cloneable values

`durable_task_service.submit(request, ...)` reads rather than consumes the
immutable data-only `DurableRunRequest`.

The request is cloneable, and the same instance or equal clones may be submitted
repeatedly. Each accepted submission creates an independent durable task under
decision 49.

Status: confirmed by the user.

### 67. Output results distinguish requested and resolved artifact identity

The ambiguous `OutputResult.artifact_id` field is replaced by:

```text
OutputResult
  task_locator
  requested_artifact
  resolved_artifact?
```

`requested_artifact` is the stable requested reference and may retain an
unresolved phase selector. `resolved_artifact` is the planner-selected Completed
Value. It is absent when planning never resolves the output, including early
cancellation and unplannable failure.

These fields preserve the same indirection described for `a-task` query results
in decision 11.

Status: confirmed by the user.

### 68. `RunResult.outputs` is a deterministic sorted list

`RunResult.outputs` is a list containing exactly one `OutputResult` for every
requested semantic output. It is sorted by canonical `task_locator`
representation.

The result is not a map keyed by requested artifact because each item already
contains both its stable task locator and requested artifact reference. Bindings
may provide derived lookup helpers without changing the interoperable
representation.

Status: confirmed by the user.

### 69. Output state distinguishes nonterminal pending work from terminal outcomes

Requested-output state includes nonterminal `PENDING`, representing an output
whose required work has not begun.

`PENDING` appears only in the requested-output view carried by
`InProgress(progress)`. It is prohibited in terminal `RunResult.outputs`, where
every requested output resolves to one of:

```text
SUCCEEDED
FAILED
PREVENTED_BY_FAILURE
CANCELLED
EXPIRED
INTERRUPTED
```

Expiry and orderly shutdown remain distinct from caller cancellation.

Status: confirmed by the user.

### 70. Active requested outputs use `RUNNING`

The in-progress requested-output state also includes `RUNNING`, used when work
needed for that output has begun but its final outcome is not yet known. This
may include provider execution, cache resolution, or local materialization.

The basic nonterminal progression is:

```text
PENDING -> RUNNING -> terminal output status
```

Status: confirmed by the user.

### 71. Progress snapshots contain every requested output

Every `InProgress(progress)` snapshot contains exactly one requested-output
state entry for every requested semantic output. The entries are sorted by
canonical task locator.

This replaces the current `available_outputs`-only list. Callers do not infer
that an omitted output is pending; they observe its explicit `PENDING`,
`RUNNING`, or completed state.

Status: confirmed by the user.

### 72. Progress and terminal results share `OutputState`

`InProgress(progress)` and terminal `RunResult.outputs` use the same
`OutputState` record shape. It contains the stable and resolved artifact fields,
status, optional fingerprint and instance locator, and local target states.

Progress permits `PENDING` and `RUNNING`. Terminal run results prohibit those
two nonterminal statuses. A separate `OutputAvailabilitySnapshot` or
`OutputResult` schema is not needed.

Status: confirmed by the user.

### 73. Local target state is complete and ordered

For local execution, every `OutputState` contains exactly one `TargetState` for
each declared target, in declaration order. Target states use the shared status
family, including `PENDING` and `RUNNING` while the overall run is nonterminal.

Targetless local outputs and all durable outputs have an empty target-state
list.

Status: confirmed by the user.

### 74. Semantic output and target delivery have independent status

`OutputState.status` describes only the semantic artifact's state.
`TargetState.status` independently describes delivery to each local target.

A semantic artifact may be `SUCCEEDED`, retain its fingerprint and committed
`a-instance`, and remain reusable even when a target is `FAILED`. The target
failure may still make the enclosing task `Failed`.

Status: confirmed by the user.

### 75. Diagnostic manifests are separate from `RunResult`

The core `RunResult` omits embedded `manifest`, `manifest_location`, and
equivalent optional diagnostic fields.

Deployments that retain detailed execution or artifact manifests expose them
through a separate diagnostic capability addressed by task identity. Diagnostic
retention and retrieval may evolve without changing the compact run-result
contract.

Status: confirmed by the user.

### 76. Results use stable task identity plus attempt identity

`RunResult` removes `persisted_task_id` and uses:

```text
task_id
attempt_id?
```

For durable tasks, `task_id` remains stable across recovery and `attempt_id`
identifies the execution attempt that produced the terminal result.

Status: confirmed by the user.

### 77. Attempt identity is required for local and durable state

`attempt_id` is required on both `RunProgressSnapshot` and `RunResult`, including
local tasks that have exactly one attempt.

This keeps the shared state representation uniform and gives progress,
diagnostics, and terminal results one correlation key without branching on
durability.

Status: confirmed by the user.

### 78. `Completed` requires every requested delivery to succeed

`Completed(RunResult)` requires every requested semantic `OutputState` and every
declared local `TargetState` to have terminal `SUCCEEDED` status.

A failed target makes the enclosing task `Failed` even when its semantic
artifact committed successfully. The semantic output remains `SUCCEEDED` and
retains its fingerprint and `a-instance` when available.

Status: confirmed by the user.

### 79. A successful local `a-task` need not resolve to an instance

For a successful local output that is non-cacheable or uses `BYPASS`, querying
its `a-task` reports semantic `SUCCEEDED`. It includes the resolved artifact and
fingerprint when known but has no `a-instance` locator.

This records the task-relative output outcome without claiming durable byte
retrieval. The absence of an instance is not itself failure or
unresolvability.

Status: confirmed by the user.

### 80. Durable waiting has no language-neutral timeout parameter

`durable_task_service.await_to_completion(task_id)` has no timeout parameter in
the language-neutral contract.

Callers use binding or transport deadlines and may safely retry waiting under
decision 27. The task's optional `active_ttl` is the separate control that can
expire the task itself.

Status: confirmed by the user.

### 81. Direct handle awaiting is binding-level sugar

In bindings whose languages support custom awaitable objects:

```text
await handle
```

is exact sugar for:

```text
await handle.await_to_completion()
```

Bindings without custom awaitable objects expose the named method only. Direct
awaiting preserves all agreed wait-interruption and non-cancellation semantics.

Status: confirmed by the user.

### 82. Local run handles are cloneable shared references

Bindings with explicit value cloning make `RunHandle` cloneable. Every clone
refers to the same underlying task, `task_id`, `attempt_id`,
`output_artifacts`, `RunState`, and cancellation authority.

Cloning does not create or own another task. Cancellation remains idempotent
across all clones; only the first accepted request returns `true`.

Status: confirmed by the user.

### 83. A handle retains an observed terminal state

Once a local `RunHandle` has received or observed its terminal `RunState`, it
retains that compact immutable data. It remains inspectable and repeatedly
awaitable after orderly runtime or task-manager shutdown.

Retaining the terminal value does not keep runtime execution, cache providers,
or task-artifact query services alive. Provider-lifetime `a-task` resolution may
therefore expire independently.

Status: confirmed by the user.

### 84. Abrupt local manager loss is a call-level error

If the local task manager becomes unavailable before producing a terminal
`RunState`, subsequent `get_state()` and awaiting fail with a task-unavailable
call-level error.

They do not synthesize `Interrupted` or `Failed`. Only explicit orderly shutdown
can authoritatively produce `Interrupted`; abrupt loss leaves no truthful domain
outcome.

Status: confirmed by the user.

### 85. All Chunky-originated errors use the universal failure envelope

Every error produced by Chunky itself, including task unavailable, task not
found, submission rejection, service unavailable, request validation, and
terminal run failure, carries the universal versioned failure-envelope data from
decision 54.

Bindings may throw or return that data idiomatically. Language/runtime
cancellation and transport-library failures originating outside Chunky may
remain native errors.

This cross-cutting contract must be enshrined in a dedicated authoritative
specification. The run surface will reference that document rather than
redefine the envelope.

Status: confirmed by the user. Decision 86 assigns the owning document.

### 86. The universal failure contract is owned by an overview specification

The authoritative platform-wide failure contract will live at:

```text
specs/00-overview/02-failure-contract.md
```

It owns the envelope, serialization, and binding/transport invariants.
Module-specific specifications define only their allowed `kind`, issue `code`,
`context_type`, and typed context payloads.

The document will be created only after the user confirms shared understanding
for this interview.

Status: confirmed by the user.

### 87. Durable service shutdown does not terminalize the task

Orderly durable-service shutdown interrupts only the current execution attempt.
It does not produce terminal `Interrupted` for the stable durable task.

The task remains `InProgress` and eligible for a fresh attempt after restart
unless cancellation, active expiry, completion, or failure has already won.
`Interrupted` is a local-only terminal outcome.

Status: confirmed by the user.

### 88. Active expiry continues through service downtime

`active_expires_at` is an absolute wall-clock deadline and continues to elapse
while the durable task service is stopped or unavailable. Service downtime does
not extend the task's active/recovery lifetime.

On recovery, if the deadline has already passed, the service enters expiry
handling without starting a new execution attempt. The task remains
`InProgress` with expiry cleanup state until it reaches `Expired(RunResult)`, as
defined by decisions 35 and 61.

Status: confirmed by the user.

### 89. Lifecycle durations and deadlines have canonical precision

The language-neutral lifecycle fields use these representations:

- `active_ttl` and `terminal_retention_seconds` are positive whole seconds; and
- `active_expires_at` and a terminal state's `record_expires_at` are UTC
  RFC 3339 timestamps with millisecond precision.

Bindings may expose idiomatic native duration and time types. Serialized data
uses the canonical representations above.

`DurableSubmissionReceipt` names the effective service-owned retention duration
`terminal_retention_seconds`. Once the task terminalizes, its retained terminal
`RunState` exposes the resulting absolute `record_expires_at`.

Status: confirmed by the user.

### 90. Output content fields are constrained by semantic success

Every `OutputState` always contains `task_locator` and `requested_artifact`.
`resolved_artifact` becomes present once planning resolves that output, but may
remain absent after cancellation, expiry, interruption, or planning failure.

`SUCCEEDED` requires both `resolved_artifact` and `fingerprint`.
`instance_locator` remains optional because successful local non-cache-backed
delivery need not create a durable artifact instance. Every non-`SUCCEEDED`
output status prohibits both `fingerprint` and `instance_locator`.

These invariants distinguish evidence that the semantic artifact completed from
evidence that it is durably available through an `a-instance`.

Status: confirmed by the user.

### 91. Target delivery begins only after semantic success

A `TargetState` remains `WAITING` while its enclosing semantic `OutputState` is
`WAITING` or `RUNNING`. Target delivery may become `RUNNING` only after the
semantic output reaches `SUCCEEDED`.

If semantic production reaches `FAILED`, each unstarted target becomes
`BLOCKED`. If semantic production reaches `CANCELLED`, `EXPIRED`, or
`INTERRUPTED`, each unstarted target receives that same status.

After semantic success, every target independently reaches `SUCCEEDED`,
`FAILED`, `CANCELLED`, `EXPIRED`, or `INTERRUPTED`. This distinguishes a target
delivery failure from a delivery that could not start because semantic
production failed.

Status: confirmed by the user.

### 92. `RunState` has one strict versioned canonical serialization

Local and durable observation use the same canonical root representation:

```text
schema: "chunky.run-state"
version: 1
state: "in_progress" | "completed" | "failed" |
       "cancelled" | "expired" | "interrupted"
data: Progress | RunResult
record_expires_at?: timestamp
```

`record_expires_at` is present only on retained durable terminal states.
Bindings may expose idiomatic native tagged unions without changing this
canonical data.

Deserialization rejects unsupported versions, unknown fields, unknown variants,
and values that violate the `RunState`, `OutputState`, or `TargetState`
invariants.

Status: confirmed by the user.

### 93. Purged and never-known task IDs share one lookup failure

After terminal-record retention ends, durable `get_state`,
`await_to_completion`, and `cancel` return the same `TASK_NOT_FOUND` failure
used for a task ID that was never known.

The service does not retain a public expiry tombstone merely to preserve that
distinction. While a terminal record exists, its `record_expires_at` tells the
caller when this lookup boundary will be reached.

Status: confirmed by the user.

### 94. Public shapes and underlying semantics have distinct owners

`specs/01-runtime/04-run-surface.md` owns the public local and durable
operations, `RunHandle`, receipts, canonical
`RunState`/progress/result/output/target data, awaiting, and call-level
behavior.

`specs/07-scheduling/eventing-and-task-control-model-spec.md` owns lifecycle
transitions, progress production, cancellation-versus-expiry arbitration, and
execution-attempt semantics.

`specs/07-scheduling/02-persisted-task-lifecycle.md` owns atomic acceptance,
durable records, recovery, active lifetime, and terminal-record retention.

The planned `specs/00-overview/02-failure-contract.md` owns the universal
failure envelope and its serialization rules.

Artifact specifications own locator grammar, planner-resolved Completed Values,
fingerprints, instance availability, and task-locator query semantics. The run
surface references those rules rather than redefining them.

The consistency review found stale conflicting text in the current
run-surface, scheduling, and persisted-lifecycle specifications, including old
subscription APIs, `RunResult.status`, submission idempotency, and superseded
output semantics. Those documents remain unchanged until the user confirms
shared understanding.

Status: confirmed by the user.

### 95. Shared understanding is confirmed

The user confirms that decisions 1-94, including their recorded supersession
history, represent the shared understanding for the run surface.

The interview gate is closed. The decisions may now be graduated into
`specs/01-runtime/04-run-surface.md`; the universal failure contract may be
created at `specs/00-overview/02-failure-contract.md`; and directly affected
scheduling and persisted-lifecycle specifications may be reconciled with the
confirmed ownership boundaries.

Status: confirmed by the user.
