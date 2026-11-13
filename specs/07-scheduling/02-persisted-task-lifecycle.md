# Persisted Task Lifecycle

## Purpose

This specification defines durable acceptance, active/recovery lifetime,
recovery attempts, cancellation/expiry arbitration, terminal retention, and the
lifetime of durable input/output content.

## Atomic acceptance

Durable submission accepts an immutable `DurableRunRequest` and optional active
TTL.

Acceptance is one atomic boundary. It commits:

- a new stable task ID;
- the immutable canonical request;
- one original canonical `ScopedArtifactRequest` and evolving
  `TaskArtifactBinding` per requested output;
- effective `active_expires_at`;
- terminal-record retention policy;
- requested-output artifact retention policy;
- active-task protection for **every member of every explicitly supplied input
  binding**; and
- deletion of corresponding upload-grace handoff claims where present.

Every supplied `ContentId` must be present, readable, authorized, and claimable
before acceptance commits. If any member fails a check, submission is rejected
and no task, partial protection, or partial grace deletion exists.

For plural bindings this is all-or-nothing. There is no state in which the task
owns only a prefix of an explicitly supplied input.

Only after acceptance commits may the public operation return its receipt.

## Durable record

The stable task record contains:

```text
task_id
canonical request
original and internally resolved scoped requested-output records
active_expires_at
terminal retention policy
requested-output retention policy
active input content protections
committed requested-output member protections
cancellation/expiry control state
terminal RunState when terminalized
```

Task identity, request intent and lifetime policy do not change after
acceptance.

Internal records retain the resolved hierarchy, exact `ArtifactSchemaId`,
selected producer invocation, effective bindings, and provenance so recovery
reconstructs the same binding paths and invocation identities. Public results
remain keyed only by the original scoped request.

Scheduler queues, live iterators, incomplete execution checkpoints and progress
snapshots remain attempt-local.

## Active input protection

Task acceptance protects every supplied content member for the task's active or
recoverable lifetime.

This protection is independent of the content's ordinary upload retention TTL.
If ordinary retention expires while the task is still active, the task keeps
content alive.

### Active expiry

`active_expires_at` is a deadline for beginning expiry handling, not an instant
content-release time.

At/after the deadline:

1. no new execution work starts;
2. the active attempt enters stopping/cleanup;
3. running stages observe expiry cancellation;
4. task input protection remains in force during cleanup; and
5. only when the task actually terminalizes as `Expired` is active task input
   protection released.

Open content read reservations continue to protect bytes independently until
they close.

## Recovery

Recovery before active expiry creates a fresh execution attempt:

- task ID remains stable;
- attempt ID changes;
- planning and scheduling start fresh;
- completed cache bindings may be reused according to policy; and
- incomplete live-binding prefixes are not resumed as checkpoints in the MVP.

Active supplied-input protection survives service restart while the task remains
recoverable.

## Cancellation and expiry

Cancellation is an idempotent durable control event. Expiry is likewise a task
control condition. Their existing first-recorded arbitration rules remain.

Both transitions keep the task publicly `InProgress` while cooperative cleanup
is still running.

Already committed requested-output content is not rolled back.

## Terminal outcomes

A durable task terminalizes exactly once as:

```text
Completed
Failed
Cancelled
Expired
```

Each terminal result preserves one output-binding state per requested output.
An output may be `AVAILABLE` even when the task is `Failed` because another
branch failed later.

An incomplete output may contain committed members while its binding state is
`FAILED`, `CANCELLED`, or `EXPIRED`.

## Terminal retention

### Task record

Terminal-record retention begins at terminalization:

```text
record_expires_at = terminalized_at + terminal_retention
```

After purge, task observation uses the common task-not-found behavior.

### Supplied inputs

Active task input protection ends at terminalization.

Supplied inputs are **not** automatically retained for the task's requested
output artifact-retention period. They survive only while another independent
retention reason remains, including:

- ordinary upload/content retention;
- another active task;
- another durable claim/reference; or
- an active read reservation.

### Requested outputs

Every committed requested-output member remains protected for the effective
artifact-retention period after terminalization, including members committed
before an incomplete/failed binding terminated.

Each explicitly requested intermediate has independent binding status and
retention under this rule. An output that closed successfully stays `AVAILABLE`
even when an unrelated branch later fails; committed prefixes of failed outputs
remain observable while retained.

Task-record retention and content retention are separate.

## Invariants

1. Durable acceptance atomically protects all explicit supplied members or
   creates no task.
2. Upload-grace removal and task input protection have no GC gap.
3. Active input protection survives service restart/recovery.
4. Active expiry starts cleanup; terminalization releases task input protection.
5. Inputs are not automatically retained for output artifact-retention TTL.
6. Committed requested-output members survive terminal failure/cancellation.
7. Durable recovery starts a fresh attempt rather than resuming live scheduler
   memory.
8. Requested outputs correlate by original canonical `ScopedArtifactRequest`;
   no caller output key is persisted.
