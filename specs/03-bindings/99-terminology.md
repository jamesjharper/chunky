# Binding Terminology

**Scoped binding key** — Complete canonical `ScopedArtifactRequest` naming one
semantic binding occurrence.

**Longest-applicable suffix** — Supplied-input selection rule choosing the most
specific compatible scoped key that is a suffix of a planned occurrence.

**Content Store** — Immutable exact-byte storage keyed by `ContentId`.

**CompletedBinding** — Successfully closed execution-cache result keyed by
`ProvenanceFingerprint`, containing exact `ArtifactSchemaId`, scoped occurrence,
and ordered `ContentId` members.

**ActiveBindingProduction** — Coordination state ensuring one producer per
provenance fingerprint while allowing multiple demanders.

**ArtifactBindingReader** — Ordered asynchronous stage input view.

**ArtifactBindingWriter** — Ordered runtime-owned output writer with
runtime-assigned ordinals.

**Verification writer** — Writer used to verify an immutable cache hit when
sibling misses require the original full invocation.

**TaskArtifactBinding** — Active/public observation of one requested scoped
binding; it may expose a committed prefix before terminal state.
