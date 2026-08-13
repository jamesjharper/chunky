# Chunky Run Contract — Overview

## Purpose
What is chunky.
Its is an orchstration platform for Long running data trasformation tasts.
It provided artifact management, compute resource management, task scheduling management 


## Usage
Describe the local usage pattern. For approachability to users describe this as if it where the default and most simple pattern.
Its designed for uses cases where the integrator want to take a simple sets of input resources and output resources. This might be programatic, or simple input and out files


```python
request = (
    LocalRunRequestBuilder()
      .include_input( "source/audio", SourceAudioArtifact.from_file("input.wav"),)
      .expect_output_target("render/webvtt_transcript", VttTargetArtifact.to_file("transcript.vtt"))
      .build()
)

await runtime.run(request)
```




## Durable usage pattern

A service-oriented caller first ensures every input is a committed
`a-instance`. It may publish inputs through direct artifact-cache I/O or
explicitly prepare a targetless local request for durable submission. It then
submits the resulting data-only durable request and retains the returned receipt.

The serializable receipt contains the durable task ID and sorted requested
output `a-task` locators. The client later queries status or resolves those
locators through the durable service.

Canonical requests contain no client paths, target handles, provider
credentials, or runtime-manifest identifier. Services must share the logical
cache namespace referenced by their locators or explicitly import the inputs.

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

An `a-task` becomes resolvable only through task lifecycle and committed cache
metadata. It does not itself assert that output bytes exist.

## Request and execution boundaries

`LocalRunRequest` may contain live source and target handles. Local inputs are not
implicitly uploaded to durable storage. Backward planning reads only selected
inputs, and a selected supplied input is authoritative over defaults, cache
alternatives, or a producing stage.

`DurableRunRequest` contains committed input locators and durable semantic
output requests only. Canonicalization is an explicit consuming I/O operation;
it is not hidden in request building or local execution.

The two forms bind into a private shared execution request. Cacheable produced
outputs commit before local target materialization or durable availability is
reported.

## Output delivery

Multiple local targets are an ordered fail-fast convenience. Each target is
transactional independently. A target failure does not roll back an earlier
target or an already committed semantic cache entry.

Targetless local outputs require `DEFAULT` or `REFRESH` cache behavior and inherit
the provider's lifetime. Canonical outputs are always targetless and require a
provider capable of the durable task contract.

## Cache behavior

Every configured runtime has a cache provider. If none is supplied, the
runtime builder installs a runtime-scoped workspace provider. An owned default
workspace is deleted when the runtime closes and is therefore unsuitable for
canonical durability.

Produced-artifact cache policy has four modes:

| Mode | Reuse shared entry | Publish new entry |
|---|---:|---:|
| `DEFAULT` | yes | yes |
| `REFRESH` | no | yes |
| `READ_ONLY` | yes | no |
| `BYPASS` | no | no |

The optional TTL is a best-effort hint for a newly created entry. Cache policy
does not apply to supplied inputs and cannot shorten durable task retention.

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
- [Durable Request Preparation](../01-runtime/02-run-request-building-surface.md)
- [Artifact Model](../02-artifacts/01-artifact-model-surface.md)
- [Artifact I/O](../02-artifacts/02-artifact-io-surface.md)
- [Cache Management](../03-bindings/02-cache-management.md)
- [Task Planning](../06-planning/01-task-planning.md)
- [Eventing and Task Control](../07-scheduling/eventing-and-task-control-model-spec.md)
- [Persisted Task Lifecycle](../07-scheduling/02-persisted-task-lifecycle.md)

## Invariants

1. Local and durable entrypoints remain distinct even when both can be awaited.
2. Request builders perform no I/O and are independent of a runtime manifest.
3. Local input publication and durable preparation are always explicit.
4. Only committed readable cache entries receive externally useful
   `a-instance` resolution.
5. Target order affects local delivery; durable collection order does not
   affect request meaning.
6. Cache state can skip work but cannot change graph shape.
