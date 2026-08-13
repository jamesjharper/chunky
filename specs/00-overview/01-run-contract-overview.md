# Chunky Run Contract — Overview

## Purpose

Chunky exposes one semantic runtime contract through two execution patterns:

- local, process-local execution for callers that already own input and output
  handles; and
- durable submission for callers that need replay, recovery, or later result
  retrieval.

Both patterns use the same artifact model, frozen runtime manifest, planner,
binding contract, and runner after their inputs are bound. Cache and durable
artifact persistence remain separate services.

## Local execution pattern

A local caller builds a `LocalRunRequest` from live source handles, requested
outputs, and optional local target handles. It then either awaits execution or
starts the same in-process work and retains a handle:

```python
result = await runtime.run(request)
```

```python
handle = runtime.start(request)
result = await handle.await_to_completion()
```

`run` and `start` consume the request. `start` is detached from waiting, not
from the process: its handle, progress, and unfinished work do not survive
process loss.

A targetless local output is retained only according to its cache policy. The
default workspace provider does not promise survival beyond the runtime.

## Durable service pattern

A service-oriented caller uploads each external input independently and
receives one cache fingerprint per upload. It then binds those fingerprints to
semantic `a-ref` values, submits the data-only durable request, and retains the
returned receipt.

An uploaded root may be deleted after its grace and retention windows. Its
fingerprint remains acceptable when retained downstream intermediates satisfy
the requested work; the runtime requires cache presence only for artifacts
consumed by stages that must execute.

The serializable receipt contains the durable task ID and sorted requested
output `a-task` locators. The client later queries status or resolves those
locators through the durable service.

Durable requests contain no client paths, target handles, provider
credentials, or runtime-manifest identifier. Services must share the logical
artifact-persistence namespace referenced by their locators.

## Artifact addressing

The public locator family separates meaning, committed content, and task
results:

```text
a-ref:/global/{domain}/{content_kind}/{phase-or-any}
a-instance:/global/{domain}/{content_kind}/{phase-or-none}/fingerprint/{fingerprint}
a-task:/global/{domain}/{content_kind}/{requested-phase}/task/{task_id}
```

- `a-ref` names a semantic artifact request;
- `a-instance` names committed content with a concrete resolved identity; and
- `a-task` is a stable requested-output pointer whose requested phase may still
  be `any`.

An `a-task` becomes resolvable only through task lifecycle and a committed
durable artifact binding. It does not itself assert that output bytes exist.

## Request and execution boundaries

`LocalRunRequest` may contain live source and target handles. Local inputs are not
implicitly uploaded to durable storage. Backward planning reads only selected
inputs, and a selected supplied input is authoritative over defaults, cache
alternatives, or a producing stage.

`DurableRunRequest` contains committed durable input locators and durable
semantic output requests only. Upload is explicit per artifact; it is not
hidden in request building or local execution.

The two forms bind into a private shared execution request. Cacheable produced
outputs commit to cache before targetless local availability. Requested durable
outputs commit to artifact persistence before durable success or availability;
cache population is secondary.

## Output delivery

Multiple local targets are an ordered fail-fast convenience. Each target is
transactional independently. A target failure does not roll back an earlier
target or an already committed semantic cache entry.

Targetless local outputs require a cache-writing policy and inherit the cache
provider's lifetime. Durable outputs are always targetless and require
configured artifact persistence regardless of cache policy.

## Cache behavior

Every configured runtime has a cache provider. If none is supplied, the
runtime builder installs a runtime-scoped workspace provider. An owned default
workspace is deleted when the runtime closes. Durable operations additionally
require an artifact-persistence service; cache durability does not substitute
for it.

Produced-artifact cache policy resolves to four operational modes, with
`INHERITED` selecting the enclosing policy:

| Mode | Reuse shared entry | Publish new entry |
|---|---:|---:|
| `CACHE` | yes | yes |
| `OVERWRITE` | no | yes |
| `DRY_RUN` | yes | no |
| `NO_CACHE` | no | no |

The optional TTL is a best-effort retention request. It refreshes entry expiry
whenever a task successfully uses a cached artifact or commits it as an output.
It cannot shorten durable artifact claims or override provider limits.

## Scheduling and task lifecycle

`RunPolicy` governs scheduling and admission only. It cannot alter graph shape,
producer selection, artifact identity, fingerprints, cache behavior, or output
materialization.

Local-run progress and cancellation cover the complete in-process lifecycle.
Durable tasks add atomic acceptance, persisted cancellation, retention claims,
and restart-from-request recovery. Plans and progress are not persisted; only
fully committed artifacts carry reusable work across attempts.

## Contract map

- [Run Request Building](../01-runtime/02-run-request-building-surface.md)
- [Run Surface](../01-runtime/04-run-surface.md)
- [Run Binding and Execution](../01-runtime/99-internals/05-run-binding-and-execution.md)
- [Durable Artifact Upload](../01-runtime/03-durable-artifacts-upload-surface.md)
- [Durable Artifact Download](../01-runtime/05-durable-artifact-download-surface.md)
- [Artifact Model](../02-artifacts/01-artifact-model-surface.md)
- [Artifact I/O](../02-artifacts/02-artifact-io-surface.md)
- [Cache Management](../03-bindings/02-cache-management.md)
- [Representation Store](../03-bindings/03-representation-store.md)
- [Artifact Persistence](../03-bindings/04-artifact-persistence.md)
- [Task Planning](../06-planning/01-task-planning.md)
- [Eventing and Task Control](../07-scheduling/eventing-and-task-control-model-spec.md)
- [Persisted Task Lifecycle](../07-scheduling/02-persisted-task-lifecycle.md)

## Invariants

1. Local and durable entrypoints remain distinct even when both can be awaited.
2. Request builders perform no I/O and are independent of a runtime manifest.
3. Local input publication and durable upload are always explicit.
4. Only committed readable durable artifact records satisfy durable
   `a-instance` resolution.
5. Target order affects local delivery; durable collection order does not
   affect request meaning.
6. Cache state can skip work but cannot change graph shape.
