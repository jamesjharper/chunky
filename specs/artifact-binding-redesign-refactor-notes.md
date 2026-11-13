# Artifact Binding Redesign — Refactor Notes

> **Historical/superseded after Q139.** The `ContentDigest`, public `a-ref`,
> `a-instance`, `a-task`, and producer-constrained request directions below are
> retained as an implementation-history snapshot. They are superseded by the
> active normative surfaces and the post-Q139 reconciliation in
> `Chunky-artifact-binding-redesign-Q1-Q139.md`.

Current implementation targets are `ContentId`, `ArtifactSchema` /
`ArtifactSchemaId`, `ArtifactRequest`, arbitrary-depth
`ScopedArtifactRequest`, `SuppliedArtifactBinding`, public
`TaskArtifactBinding`, and internal `ResolvedArtifactInterpretation` /
`PlannedArtifactBinding`. Durable acceptance verifies and protects every
supplied member atomically with task creation. Exact producer selection,
schema, provenance, and cache resolution remain internal for MVP.

## Purpose

This document summarizes implementation changes implied by the reconciled
specifications. It is an implementation handoff, not an additional source of
design authority.

## Identity model

Replace the previous final semantic artifact fingerprint with two distinct
concepts:

```text
ContentDigest
  BLAKE3-256(exact bytes)

ProvenanceFingerprint
  deterministic identity of one complete ordered binding production
```

`a-instance` becomes exact `ArtifactRef` + `ContentDigest`.

Remove phase, projection context, provenance resolver kind, sequence index and
old semantic fingerprint fields from locator identity.

## Artifact request/declaration model

Introduce a versionless `ArtifactRequest` for caller intent:

```text
{domain}/{content_kind}
{domain}/{content_kind}/producer/{producer_id}
```

Keep exact immutable schema as:

```text
a-ref:{domain}/{content_kind}@{version}
```

Move old numeric phase semantics into stage capability metadata:

```text
processing_order
max_processing_order
```

Remove `AT_MOST`/`EXACT` phase selector machinery and terminal phase remapping.

## Stage bindings

Replace singular stage I/O with ordered bindings:

```text
ArtifactBindingReader
ArtifactBindingWriter.write_next(...)
```

Carry edge cardinality:

```text
ONE | ONE_OR_MORE
optional: bool
```

Runtime owns ordinals and successful binding close. Optional absence is an
empty closed input binding.

Add per-output incremental capability/default publication metadata.

## Planning

Replace planning of predictable output artifact fingerprints with planning of
`PlannedArtifactBinding` and predictable binding provenance.

Produced provenance must include:

- selected `producer_id`;
- behavior version;
- exact output a-ref;
- input binding provenances;
- effective settings fingerprint; and
- complete resolved output set.

The planner does not predict output content digests.

## Cache

Change cache key/value from singular semantic artifact fingerprint/entry to:

```text
ProvenanceFingerprint -> CompletedBinding
```

`CompletedBinding` stores exact a-ref + ordered content digests and exists only
after successful binding closure.

Implement one shared active production claim per provenance key with multiple
demanders.

Rename cache behaviors:

```text
OVERWRITE -> REFRESH
DRY_RUN   -> READ_ONLY
```

On partial stage-output hits, preserve the original resolved output set and use
verification writers for hit outputs.

## Content storage

Rename/refactor `RepresentationStore` into `ContentStore`.

Primary object identity becomes `ContentDigest`; exact byte length remains
metadata. Cache and durable persistence reference the same content objects when
composed over one shared store.

## Durable upload

Remove schema-dependent publication and caller-assigned semantic upload
fingerprints.

Upload returns:

```text
content_digest
byte_length
grace_retained_until
retention_deadline?
```

Keep direct, resumable multipart and live-capture transfer mechanics beneath
this content publication contract.

## Durable request acceptance

Replace singular `{a-ref, upload fingerprint}` roots with one versionless
artifact request + ordered digest members.

At atomic task acceptance:

- verify every supplied content member is present/claimable;
- protect all members for the active/recoverable task;
- delete applicable upload-grace claims in the same transaction; and
- reject submission entirely if any supplied member cannot be protected.

The previous lazy acceptance of missing roots is removed from MVP.

## Task outputs and download

Replace `a-task -> a-instance` with `a-task -> TaskArtifactBinding`.

Each binding snapshot contains binding-level status plus ordered committed
members `{ordinal, content_digest}`.

Raw download becomes digest-oriented:

```text
GET /artifact/content/blake3/{base64url}
```

Task artifact lookup returns metadata and never auto-concatenates plural
members.

No public provenance endpoint or provenance-based durable caller input is
required for MVP.

## Scheduling and cancellation

Live binding waits must observe the stage cancellation token. A live consumer
receives committed prefix then upstream failure/cancellation rather than normal
EOF.

Incremental readiness does not bypass resource admission. For MVP, scheduler
resource leases remain held for the whole invocation.

Shared active production lifetime follows surviving demanders rather than the
first task that created it.

## Persistence and retention

Task input protection remains through STOPPING and is released at actual task
terminalization. It is independent of ordinary content retention TTL.

After terminalization, supplied inputs are not automatically retained by the
requested-output artifact TTL. Committed requested-output members are retained
according to task output policy even when their binding later fails.
