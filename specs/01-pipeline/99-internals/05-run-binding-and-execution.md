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

Both paths use the pipeline's frozen manifest and configured cache provider.
They do not convert through one another.

## Local binding

Local binding takes ownership of the consumed `LocalRunRequest` and preserves its
local delivery targets. It resolves selected inputs lazily:

- a selected source handle becomes a task-owned supplied artifact;
- a selected `a-instance` is opened and validated through the configured cache;
- an unselected source is neither read nor fingerprinted; and
- every owned handle is eventually closed or aborted.

Local source input is not published to durable cache merely because it was
supplied. An implementation may use a private task-scoped workspace for seeking,
repeat reads, codec conversion, or stage interoperability. That does not create
a public `a-instance` or make the request replayable.

When selected, an explicitly supplied root is authoritative over a default,
compatible cache entry, or producer path. Produced-artifact cache policy never
changes that precedence.

## Canonical binding

Canonical binding validates and opens every selected committed input locator in
the service's logical cache namespace. The locator and cache metadata must agree
on the fully resolved artifact identity and fingerprint.

Canonical requests contain no local delivery targets. Their requested outputs
must use cache-backed publication so the durable task can expose stable
`a-task` resolution.

## Shared execution shape

`BoundRunRequest` provides the planner and runner with the selected supplied
artifacts, semantic output requests, request policies, task control surfaces,
and access to the configured cache. It must not expose builder state or retain
canonical wire concerns.

Planning owns manifest-dependent validation, graph expansion, default
resolution, phase selection, and graph satisfiability. `RunPolicy` may affect
admission and scheduling only; it cannot change the graph.

Produced artifacts use the request's exact cache policy. Cacheable requested
outputs commit before local materialization or durable availability is
reported. `a-task` relations are recorded only for committed readable cache
instances.

## Failure and cleanup

Binding and execution are cancellation-aware. On failure or cancellation the
pipeline:

- closes all owned source handles, including unused ones;
- aborts uncommitted target and cache writes;
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
5. Only committed readable instances may become externally resolvable.
