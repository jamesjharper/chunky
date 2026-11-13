# Run Binding and Execution Internals:

## Purpose

This internal note describes how a fixed plan becomes concrete runtime artifact
bindings. Public contracts are owned by the artifact, planning, run, and storage
specifications.

## Binding flow

For each planned input edge, the runner has:

```text
PlannedArtifactBinding
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  cardinality and publication capability
  selected source or producer invocation
  expected binding provenance
```

It binds an `ArtifactBindingReader` from supplied content, a completed cache
binding, durable content, or a live upstream production.

For each planned output edge, it creates an `ArtifactBindingWriter` from the
publication plan. Writers may publish/verify cache state, retain durable
requested-output members, and materialize local targets.

## Durable inputs

Durable inputs were already verified and protected at task acceptance. The
runner, therefore, does not use the historical lazy-missing-root behavior.

It opens content lazily for actual consumption where useful, but the task's
retention protection already covers every supplied binding member.

For each member the runner creates internal
`ResolvedArtifactInterpretation {schema: ArtifactSchemaId, content: ContentId}`.
Acceptance proved content presence/readability, not that the selected codec can
decode it. Representation decoding and semantic validation remain lazy and may
fail during consumption.

Before any upstream traversal, planning resolves supplied scoped bindings by
longest-applicable-suffix. A selected binding shadows only its deeper branch and
cuts off upstream production for the satisfied requirement.

## Cache resolution

Cache resolution occurs by expected binding provenance.

- completed hit -> bind ordered content members;
- active production -> join/wait according to shared-production semantics;
- miss -> acquire production ownership.

Partial output hits never remove writers from the resolved output set. Hit
outputs become verification writers if sibling misses require execution.

`ContentId` is physical identity. `CompletedBinding` and expected provenance
remain execution-reuse identity and include effective hierarchical bindings,
exact schema, selected producer behavior, settings, and resolved output set.

## Execution

A stage invocation starts only after dependency readiness and scheduler resource
admission.

Incremental upstream publication may make a consumer dependency ready before
upstream completion. Resource waits still apply normally.

For the MVP, acquired stage resource leases remain held until the invocation
ends.

## Output completion

Each `write_next(...)` commits one final content member. A successful executor
return validates minimum cardinality and closes the binding. Only then may an
execution-cache `CompletedBinding` be committed.

Failure/cancellation leaves committed members intact but the binding incomplete.
Task-output history may expose those members according to retention policy.

## Cancellation

Live input waits, cache waits and resource waits are cancellation-aware. Stage
code receives the same cooperative cancellation context and should check it at
safe boundaries.

Shared producer cancellation follows surviving demand rather than whichever task
first created the producer.
