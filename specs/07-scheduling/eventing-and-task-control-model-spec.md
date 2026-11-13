# Eventing and Task Control Model

## Purpose

This specification defines scheduler-owned task/attempt transitions, fail-fast
coordination, cooperative stopping, resource admission, shared active binding
production, and progress snapshots.

## Task and attempt model

A task is one accepted run request. An attempt is one concrete planning/execution
try.

```text
AttemptRecord
  task_id
  attempt_id
  phase
  stop_source
  scheduling_progress
  active binding demand
```

Task/attempt IDs are operational and never contribute to `ContentId`, scoped
binding identity, or binding provenance.

## Attempt phases

```text
CREATED
RUNNING
STOPPING
FINISHED
```

`RUNNING` includes planning, dependency waits, cache coordination, resource
admission and stage execution.

`STOPPING` prevents new work while cancellation, expiry, task failure or orderly
shutdown cleans up. Public task state remains `InProgress` until cleanup reaches
one terminal outcome.

## Run policy

`RunPolicy` controls non-semantic execution behavior, including:

- task/resource concurrency limits;
- scheduler admission constraints; and
- requested publication visibility (`DEFAULT`, `AT_COMPLETION`,
  `INCREMENTAL`).

It must not change graph topology, producer selection, exact artifact schema,
binding provenance, requested outputs or content identity.

## Dependency readiness for streaming

A consuming stage is dependency-ready when each required input is sufficiently
ready for its binding contract.

For a live incremental binding, readiness may occur when the first committed
member becomes consumable rather than waiting for complete binding closure.

An optional binding that legitimately closes empty also satisfies readiness.

Dependency readiness does not grant execution resources. The invocation then
passes through normal GPU/RAM/CPU/concurrency admission.

## Resource leases

For the MVP, an invocation holds acquired scheduler resource leases for its
whole lifetime, including time spent awaiting later live binding members.

Stages cannot dynamically release/reacquire resource leases through
`StageExecutionContext`. Such a capability may be designed later.

## Shared active binding production

The scheduler/cache coordinates one active producer per expected binding
provenance. That provenance includes the scoped binding occurrence and all
effective hierarchical bindings, so different branch settings do not collapse
into one active production.

```text
ActiveBindingProduction(P)
  producer invocation
  scoped binding occurrence and effective binding identity
  production_mode
  demanders
```

Production is owned by the shared binding-production claim, not whichever task
created it first.

If one demander is cancelled while another remains, the producer continues.
When no demand remains, task control may stop it.

### Publication-mode joining

The first active production fixes its operational production mode.

- Later `AT_COMPLETION` consumers can join an incremental producer and withhold
  member visibility until closure.
- A later `INCREMENTAL` consumer joining a completion-only producer cannot
  retroactively upgrade it; it shares the production and waits for completion.
- The scheduler never starts a duplicate semantic producer merely to satisfy a
  different visibility preference.

## Fail-fast behavior

When task-fatal failure occurs:

1. record the causal failure;
2. stop new stage/target admission;
3. send cooperative stop to running work;
4. abort unwritten outputs;
5. preserve already committed content members/targets;
6. mark prevented requested outputs accordingly; and
7. publish terminal task failure only after required cleanup.

A successful output binding remains successful even when another branch fails.

## Cooperative stopping

The internal stop token supports cancellation-aware waiting:

```text
is_stopping
origin
wait_stopping()
wait_for(awaitable)
raise_if_stopping()
```

Artifact live-iterator waits, cache waits and resource waits observe the token.
Cancelling one wait does not cancel an unrelated/shared producer.

Stages should still explicitly check cancellation around expensive provider
work and publication boundaries.

### Live upstream terminal propagation

A stage iterating a live input observes:

```text
successful upstream close -> EOF
upstream failure          -> committed prefix then failure
upstream cancellation     -> committed prefix then cancellation
```

Task-wide cancellation takes precedence when the consumer's own task stop token
is already active.

## Input retention during stopping

Durable accepted input content remains protected while an attempt/task is
stopping. Active TTL expiry or cancellation does not release task input
protection before terminalization/cleanup completes.

## Scheduling progress

Public scheduling progress remains a coherent snapshot:

```text
pending
running
blocked
finished count
```

Useful blocked reasons include resource/capacity waits and waiting for a shared
binding production owned by another task.

A streaming stage waiting on unfinished same-task upstream content before first
readiness remains pending; once dependency-ready but resource-constrained it is
blocked.

Stage-defined subtask progress remains optional and is never combined into a
fabricated overall percentage.

## Recovery

Durable service restart discards live attempt/scheduler state. An eligible task
creates a fresh attempt. Completed provenance cache state may be reused;
incomplete binding prefixes are not execution checkpoints in the MVP.

## Invariants

1. Task control alone publishes terminal outcomes.
2. Run policy is operational and non-semantic.
3. Incremental publication affects dependency readiness, not resource-policy
   semantics.
4. One provenance fingerprint has at most one active producer.
5. Shared producer lifetime follows surviving demand, not first-task ownership.
6. Committed content is preserved across fail-fast stopping.
7. Accepted durable input protection survives through STOPPING until task
   terminalization.
