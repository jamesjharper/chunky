# Run Binding and Execution

## Purpose

This document defines the private boundary at which local and durable requests
converge. `BoundRunRequest` is an internal execution value, not a public API or
wire format.

```text
LocalRunRequest --- local binding ---+
                                    +--> BoundRunRequest --> planner --> runner
DurableRunRequest - durable binding -+
```

Both paths use the runtime's frozen manifest and configured artifact services.
They do not convert through one another.

## Local binding

Local binding takes ownership of the consumed `LocalRunRequest` and preserves its
local delivery targets. It resolves selected inputs lazily:

- a selected source handle becomes a task-owned supplied artifact;
- a selected `a-instance` is opened and validated through configured artifact
  persistence;
- an unselected source is neither read nor fingerprinted; and
- every owned handle is eventually closed or aborted.

Local source input is not published to durable artifact persistence merely because it was
supplied. An implementation may use a private task-scoped workspace for seeking,
repeat reads, codec conversion, or stage interoperability. That does not create
a public `a-instance` or make the request replayable.

When selected, an explicitly supplied root is authoritative over a default,
compatible cache entry, or producer path. Produced-artifact cache policy never
changes that precedence.

## Durable binding

Durable binding records each supplied `a-ref` and upload fingerprint without
eagerly opening the cache entry. The fingerprint remains useful for graph and
downstream fingerprint calculation even if the uploaded root has been evicted.

For each planned stage, the runner resolves its output cache state before
opening its inputs. A complete set of acceptable downstream cache hits skips the
stage, so none of that stage's otherwise-required roots need to exist. When the
stage must execute, durable binding opens, claims, and validates every selected
input for that stage before invoking it. A missing or invalid required input is
a task failure; a missing input used only by skipped stages is non-failing.

Durable requests contain no local delivery targets. Their requested outputs
must be published through artifact persistence before success can be recorded
or an `a-task` binding can resolve. Cache population is secondary and cannot
satisfy this durable success boundary.

## Shared execution shape

`BoundRunRequest` provides the planner and runner with the selected supplied
artifacts, semantic output requests, request policies, task control surfaces,
and access to the configured cache, representation store, and artifact
persistence service. It must not expose builder state or retain canonical wire
concerns.

Planning owns manifest-dependent validation, graph expansion, default
resolution, phase selection, and graph satisfiability. `RunPolicy` may affect
admission and scheduling only; it cannot change the graph.

Produced artifacts use the request's exact cache policy independently of
durable publication. Requested durable outputs commit to artifact persistence
before durable success or availability is reported. Intermediate artifacts are
not durably published unless they are independently uploaded or explicitly
requested outputs. Successful `a-task` bindings refer only to committed,
readable durable instances.

The runner moves compatible representation bytes through
`ArtifactTransferSource` and `ArtifactTransferTarget` without requiring a
typed-value round trip. Typed codec conversion may materialize a value only
when representation conversion is actually required and the codec declares
that buffering behavior.

## Failure and cleanup

Binding and execution are cancellation-aware. On failure or cancellation the
runtime:

- closes all owned source handles, including unused ones;
- aborts uncommitted target, cache, and durable-persistence writes;
- releases reservations and wait registrations; and
- preserves already committed cache entries and completed target writes.

Cleanup is required across binding, planning, execution, and materialization.
No cross-target or cache-to-target rollback is promised.

## Invariants

1. Local and durable requests share planning and execution after binding.
2. Local input publication is explicit, never an implicit consequence of
   execution.
3. Selected supplied inputs are authoritative; unused supplied inputs are lazy.
4. `BoundRunRequest` remains private and process-local.
5. Only committed readable durable instances may become externally resolvable.
6. A durable task cannot succeed until every requested output is durably
   published and bound to its `a-task` locator.
7. Cache presence is checked only for artifacts required by stages that will
   execute.
