# Artifact Terminology

**Content** — One finite ordered logical byte sequence.

**ContentId** — Universal exact-byte identity and public upload/download
address, canonically `blake3/{unpadded-base64url}`.

**ArtifactSchema** — One complete immutable versioned interpretation/write
contract.

**ArtifactSchemaId** — Exact identity of one `ArtifactSchema` version, used by
frozen planning, persistence, replay, and diagnostics.

**ArtifactRequest** — Unscoped versionless `{domain}/{content-kind}` family
selector.

**ScopedArtifactRequest** — Arbitrary-depth semantic hierarchy ending in an
`ArtifactRequest`; the canonical binding-occurrence key.

**SuppliedArtifactBinding** — One scoped request bound to an ordered non-empty
sequence of `ContentId` members.

**TaskArtifactBinding** — Public state for one requested scoped binding:
original request, status, public failure, and ordered committed members.

**ResolvedArtifactInterpretation** — Internal/persisted exact
`ArtifactSchemaId + ContentId` relationship. It is not primary content identity
or a public download locator.

**PlannedArtifactBinding** — Internal exact schema, edge cardinality/publication
capability, selected source/producer, scoped occurrence, and provenance.

**Artifact binding** — Ordered zero/one/many independent content members
satisfying an edge or requested output.

**Cardinality** — Edge-owned `ONE`/`ONE_OR_MORE` plus optionality.

**Committed member** — Final member with runtime-assigned ordinal and
`ContentId`; it remains valid after later binding failure.

**Binding provenance fingerprint** — Internal deterministic identity of one
complete ordered production context.

**Content Store** — Immutable physical byte-object layer keyed by `ContentId`.
