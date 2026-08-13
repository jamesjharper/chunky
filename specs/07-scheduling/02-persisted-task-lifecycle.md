# Persisted Task Lifecycle

## Purpose

This specification defines durable acceptance, persisted task records,
active/recovery lifetime, recovery attempts, cancellation persistence, terminal
record retention, and deletion.

The public submission, receipt, service operations, and `RunState` data shapes
are defined by the [Run Surface](../01-runtime/04-run-surface.md). Live
scheduler state and attempt transitions are defined by the
[Eventing and Task Control Model](eventing-and-task-control-model-spec.md).

## Atomic acceptance

Durable submission accepts an immutable `DurableRunRequest` and an optional
positive-whole-second active TTL.

Acceptance is one atomic durable boundary. It commits:

- a new stable task ID;
- the immutable canonical request;
- one stable `a-task` identity per requested output;
- the effective absolute `active_expires_at`;
- the effective service-owned `terminal_retention_seconds`;
- the effective request/default `artifact_retention_seconds`; and
- active artifact claims for every validated durable input.

Only after that commit may the public operation return a
`DurableSubmissionReceipt`. Rejection before committing is a call-level `Failure`
and creates no task.

Caller cancellation, timeout, or disconnect may prevent acceptance only before
the commit. Once committed, loss of the response does not roll back the task;
the caller may receive a call-level failure with an indeterminate submission
outcome even though the task exists.

Every accepted submission creates a new task. Equal requests are not
task-deduplicated, and the service accepts no idempotency key. A retry after a
lost receipt therefore creates another task.

## Durable record

The stable record contains:

- `task_id`;
- the canonical request;
- the requested output `a-task` identities;
- `active_expires_at`;
- `terminal_retention_seconds`;
- `artifact_retention_seconds`;
- active durable artifact claims for accepted inputs and committed requested
  outputs;
- an optional recorded cancellation or expiry control event; and
- at most one immutable terminal state with `record_expires_at`.

Task identity, request semantics, requested output identities, and effective
lifetime policy do not change after acceptance.

Plans, scheduler queues, reservations, stage checkpoints, percentages, and
progress snapshots are attempt-local and are not persisted as resumable
execution state.

## Active and recovery lifetime

`active_expires_at` is an absolute UTC wall-clock deadline and continues to
elapse during service downtime. It marks when the service MUST stop starting new
work and begin expiry handling; it is not a promise that cleanup has already
finished.

Before that deadline, a nonterminal durable task remains eligible for recovery.
At or after it:

1. no new execution attempt starts;
2. active attempt work receives an expiry cleanup request;
3. public state remains `InProgress` while cleanup safely releases task-owned
   resources; and
4. the task terminates as `Expired(RunResult)`.

If the service recovers after the deadline, it begins at step 2 without
creating a new attempt.

## Recovery attempts

Recovery before active expiry creates a fresh execution attempt:

- `task_id` stays stable;
- a new required `attempt_id` is allocated;
- admission, planning, scheduling, and progress start fresh; and
- no in-memory plan, stage, reservation, or progress state is resumed.

Recovery rebinds the immutable canonical request against the current compatible
runtime environment. It may reuse committed artifacts under the request cache
policy and task-instance rules.

A same-task committed instance may be reused even when the request's cache mode
would reject shared entries from other tasks. Reuse requires matching resolved
artifact identity, fingerprint, and cache metadata. While the task remains
nonterminal, a stale task-instance relation may be removed or replaced.
Terminal mappings are immutable until the terminal record is deleted.

If the accepted request cannot run against the available runtime or
environment during recovery, the known task terminalizes as
`Failed(RunResult)` with a `RUNTIME_INCOMPATIBLE` issue. This is distinct from
`UNPLANNABLE`, which means the task's requested work could not be planned in the
environment in which that attempt began.

Orderly service shutdown interrupts only the current attempt. The stable task
remains nonterminal and eligible for another attempt; durable tasks never use
the local-only `Interrupted` terminal variant.

## Cancellation and expiry arbitration

Durable cancellation is one idempotent control write with no caller-provided
reason.

If the task is nonterminal and expiry has not already won, the service durably
records cancellation and returns `true`. That record survives restart and MUST
be visible to later state reads before acknowledgement is returned.

The operation returns `false` when cancellation was already recorded, expiry
was recorded first, or a terminal state already exists.

Assuming completion or failure has not already won, the first durably recorded
control event determines the outcome:

- cancellation first leads toward `Cancelled(RunResult)`; and
- active expiry first leads toward `Expired(RunResult)`.

Both paths remain `InProgress` during cooperative cleanup. A successful
cancellation acknowledgement does not prevent concurrently completed work or a
serious failure from becoming the authoritative terminal outcome.

## Terminal outcomes

A durable task terminalizes exactly once as:

```text
Completed(RunResult)
Failed(RunResult)
Cancelled(RunResult)
Expired(RunResult)
```

The terminal record is immutable. Every `RunResult` preserves the complete
requested-output list, including committed partial outputs and outputs blocked,
cancelled, or expired before completion.

`Completed` requires every requested semantic output to succeed. Durable tasks
have no local target deliveries.

## Retention and deletion

Active/recovery lifetime, terminal-record lifetime, and artifact-instance
retention are separate.

Accepted input instances and newly committed requested outputs are protected by
active task artifact claims while the task is nonterminal. At terminalization,
on success, failure, cancellation, or expiry, those claims become expiring
claims:

```text
artifact_claim_expires_at =
  terminalized_at + artifact_retention_seconds
```

Cache retention remains independent. Keeping task history queryable does not
itself pin artifact instances, and deleting the task record does not delete
still-claimed artifacts.

Terminal-record retention begins only when the task actually terminalizes.
At that transition:

```text
record_expires_at =
  terminalized_at + terminal_retention_seconds
```

The retained terminal `RunState` exposes `record_expires_at`. After that instant
the service may delete the complete task record, request, control history, and
task-instance relations according to its cleanup policy.

After deletion, `get_state`, `await_to_completion`, and `cancel` return the same
`TASK_NOT_FOUND` failure used for a never-known ID. The service retains no
public expiry tombstone solely to distinguish the two cases.

## Time representation

`active_ttl`, `terminal_retention_seconds`, and
`artifact_retention_seconds` are positive whole seconds.
`active_expires_at` and `record_expires_at` serialize as UTC RFC 3339 timestamps
with millisecond precision.

## Invariants

1. Acceptance is atomic and precedes all operational work.
2. Every accepted submission has a new stable task ID.
3. The canonical request and lifetime policy are immutable after acceptance.
4. Recovery creates a fresh attempt and never resumes scheduler memory.
5. Service downtime never extends active lifetime.
6. Cancellation and expiry are durable control events; the first recorded one
   wins their race.
7. A task has at most one immutable terminal outcome.
8. Terminal retention begins at terminalization, not acceptance or active
   expiry.
9. Task-record retention does not imply artifact-instance retention.
10. Active readers prevent representation collection even after the final
    artifact claim expires.
