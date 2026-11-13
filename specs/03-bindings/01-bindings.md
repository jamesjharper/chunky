# Artifact Bindings

## Purpose

Bindings connect hierarchy-qualified planning decisions to ordered execution
readers/writers while storage, cache, provenance, and retention remain separate.

## Hierarchical binding keys

Every binding occurrence is keyed by one complete `ScopedArtifactRequest`:

```text
{scope-segment...}/{domain}/{content-kind}
```

The final two segments form the artifact family. Preceding segments are stable
manifest-declared semantic scopes written downstream-to-upstream. Scope names
are not producer IDs or storage locations.

Each identical complete path may be supplied or requested at most once. A
binding contains an ordered sequence of independent `ContentId` members;
ordinals are contiguous, runtime-assigned, and duplicates are valid.

## Supplied-input resolution

For a planned requirement occurrence, candidates must have a compatible
artifact family and a complete key that is a suffix of that occurrence. Select
the candidate with the greatest segment count.

```text
occurrence:
  speech_processing/audio_preprocessing/audio_settings

candidates:
  audio_preprocessing/audio_settings
  speech_processing/audio_preprocessing/audio_settings  <- selected
```

Rules:

1. identical-path duplicates conflict; there is no last-write-wins behavior;
2. a deeper compatible binding shadows a shallower binding only in that branch;
3. values do not field-merge;
4. a selected supplied binding terminates upstream producer traversal for the
   satisfied requirement; and
5. schema compatibility, cardinality, optionality, ambiguity, and cycles are
   still validated.

The scoped-key mechanism applies to every artifact role. Resolution is
role-specific: supplied inputs shadow, while each requested output is an
independent planning root and does not shadow another requested output.

## Cardinality and members

Edge-owned `ONE`/`ONE_OR_MORE` plus optionality determine allowed member counts.
A supplied binding is non-empty when present; optional absence is represented
by no supplied binding. Produced member count may remain unknown until closure.
Transfer chunks, multipart parts, and capture segments never create members.

## Input readers and output writers

```text
ArtifactBindingReader
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  async ordered members

ArtifactBindingWriter
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  write_next(value) -> committed member
```

Stage code sees the same ordered interface whether content came from a local
source, `SuppliedArtifactBinding`, `CompletedBinding`, upstream committed
output, or a live producer. Runtime orchestration owns ordinals, exact-byte
encoding, publication, cache verification, and closure.

## Active and completed state

`TaskArtifactBinding` is mutable task observation: it exposes binding status,
public failure, and the evolving committed prefix. A failed or cancelled
binding may retain valid committed members.

`CompletedBinding` is immutable reusable cache state created only after
successful closure and final cardinality validation. Active, failed,
cancelled, or incomplete task bindings are never cache hits.

## Incremental behavior

Incremental publication is independent of cardinality. Late readers replay the
committed prefix then observe future commits in order. Successful closure
yields EOF; failure/cancellation arrives after the prefix. Once committed, a
member is final and cannot be revised or withdrawn.

Shared sources and active production remain alive while demanded. Cancelling
one consumer stops only that consumer unless demand analysis determines no
remaining consumer requires the producer.

## Invariants

1. Complete hierarchy is part of binding-key and invocation identity.
2. Content identity remains only `ContentId`.
3. Ordered member semantics are independent of storage topology and transfer
   chunking.
4. Stage code never coordinates cache/provenance/providers directly.
5. Runtime creates and closes output writers.
6. Terminal binding state is distinct from committed-member finality.
