# Content Identity and Binding Provenance

## Purpose

Chunky uses two deliberately separate identity systems:

- `ContentId` identifies and publicly addresses one exact finite byte sequence; and
- `ProvenanceFingerprint` identifies one deterministic complete artifact
  binding produced or supplied in a particular semantic context.

The previous model in which one artifact fingerprint mixed declaration identity,
codec information, content contribution and production pedigree is removed.

## Content identity

```text
ContentId
  algorithm
  value
```

The initial required algorithm is BLAKE3-256.

```text
content_id = "blake3/" + base64url_unpadded(BLAKE3-256(exact logical bytes))
```

The canonical textual digest payload is unpadded Base64URL. Byte length is
stored and verified independently and does not participate in digest equality.

`ContentId` excludes:

- artifact schema/version;
- artifact role;
- codec identity/version;
- producer identity;
- processing order;
- provenance;
- task/invocation identity; and
- storage metadata.

## Binding provenance

A `ProvenanceFingerprint` identifies the deterministic result of one whole
ordered binding production.

```text
ProvenanceFingerprint
  version
  value
```

The initial version uses BLAKE3-256 over a domain-separated canonical payload
and uses unpadded Base64URL for textual representation.

Conceptually:

```text
binding provenance P
    -> exact ArtifactSchemaId
    -> ordered ContentIds [C0, C1, ...]
```

Individual members do not require their own provenance fingerprints.

## Canonical hash protocol

Provenance hashing must be reproducible across bindings and implementation
languages. Implementations must not hash arbitrary language-specific JSON
serialization.

Version 1 hashes a canonical structured payload under an explicit domain:

```text
domain = "chunky:binding-provenance:v1"
P = BLAKE3-256(domain || canonical_payload)
```

The exact canonical encoder must preserve:

- field/variant identity;
- integer encoding;
- string byte encoding;
- collection length;
- canonical field order; and
- list order.

Changing canonical encoding or provenance semantics requires a new provenance
version.

## Produced binding provenance

The planner can determine produced binding provenance before output bytes exist.
The payload includes the semantic production context:

```text
ProducedBindingProvenanceV1
  producer_id
  behavior_version
  output_artifact_schema_id
  output_scoped_request
  ordered effective input bindings
  effective_settings_fingerprint
  resolved_output_set
```

### Producer

`producer_id` is the stable URL-safe stage identity. `behavior_version` changes
whenever identical semantic inputs/settings may legitimately produce a
different result.

`processing_order` is excluded. It helps the planner select a producer but does
not identify what the selected producer actually was.

### Inputs

Each selected semantic input contributes its complete effective
`ScopedArtifactRequest`, exact `ArtifactSchemaId`, and binding provenance in
stable input-capability order. Every semantically effective hierarchical
binding contributes, including the scoped settings selection that caused a
stage to run as a distinct invocation. Optional absence contributes an explicit
canonical `ABSENT` marker rather than disappearing ambiguously.

A plural input's binding provenance already commits to the member sequence, so
individual member digests are not re-expanded into downstream stage provenance.

### Settings

Settings remain a separate provenance contribution because planner defaults and
overrides may be merged before complete stage requirements are known.

```text
effective_settings_fingerprint =
    hash_v1(canonical final effective semantic settings)
```

Only the final effective semantic settings matter. Identical effective values
produce the same fingerprint regardless of whether they came from defaults,
caller overrides, or inherited planner behavior.

Runtime-only operational configuration never contributes unless the stage
contract explicitly promotes it to semantic settings.

### Resolved output set

The canonical set/list of outputs presented to the executor contributes to the
production context. A stage is therefore not assumed to produce byte-identical
`B` when invoked for `[A, B]` versus `[B]` unless those are the same resolved
production context.

This is why partial cache hits do not silently remove cached sibling writers
from an invocation.

## Externally supplied binding provenance

Upload does not create provenance. It creates `ContentId` values only.

When a task binds an explicit ordered content list to a scoped artifact request,
the planner resolves the exact `ArtifactSchemaId` and derives an external binding
provenance:

```text
ExternalBindingProvenanceV1
  scoped_request
  artifact_schema_id
  ordered_content_ids
```

Example:

```text
source/audio -> [C0, C1, C2]

P_external = hash_v1(
  scoped source/audio request,
  exact source/audio ArtifactSchemaId,
  [C0, C1, C2]
)
```

Changing member order or schema changes the provenance fingerprint.

Calculating external provenance does **not** require persisting a provenance
index record. The value exists so downstream planning can have deterministic
input identity.

## Completed binding records

A persisted reusable result is:

```text
CompletedBinding
  provenance_fingerprint
  scoped_request
  artifact_schema_id
  ordered content_ids
```

A record is published only after successful binding closure.

During incremental production, task/execution state may expose a committed
prefix, but that prefix is not a completed provenance result. Failed or
cancelled bindings do not publish `CompletedBinding` even though their committed
content members remain valid.

## Determinism

A persisted provenance fingerprint may resolve to exactly one complete binding.

If recomputation of the same provenance produces a different valid member list
or different `ContentId` values, Chunky reports a nondeterminism/integrity failure.
`REFRESH` may recompute, verify or repair missing/corrupt storage; it never
redefines a valid provenance result.

Multiple provenance fingerprints may legitimately resolve to the same ordered
content list.

## Public surface

For the MVP, provenance is primarily an internal planning/cache identity.

The MVP does not require:

- a public `/artifact/provenance/...` endpoint;
- provenance-based durable request inputs; or
- task-output responses to expose provenance.

These can be added later without changing content identity or the internal
completed-binding model.

## Invariants

1. `ContentId` and provenance are never conflated.
2. `ContentId` is exact-byte identity only; hierarchy is never hashed into it.
3. Provenance identifies a complete ordered binding, not an individual member.
4. Produced provenance is predictable before output content exists.
5. External provenance may be derived without persistence.
6. Completed provenance records exist only after successful binding closure.
7. One provenance fingerprint never maps to two different valid bindings.
8. Processing order is excluded as an identity substitute; the actual selected
   producer ID and behavior version are included.
9. Effective scoped bindings, exact selected schemas, ordered members and
   duplicates, effective settings, and resolved output set are canonical
   provenance inputs.
