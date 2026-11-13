# Stage Terminology

**Stage registration** — Immutable bootstrap-time contract for one producer.

**Producer ID** — Stable stage identity using
`[a-z0-9]+(?:[._-][a-z0-9]+)*`, used internally for selection and provenance.

**Binding-scope segment** — Stable manifest-declared semantic occurrence name
owned by an input/output edge, independent of producer ID.

**Scope transition** — An input requirement's `INHERIT` or `EXTEND(segment)`
rule for propagating a scoped binding occurrence upstream.

**Display name** — Presentation-only human-readable stage label.

**Behavior version** — Version that changes when equal semantic production
context may legitimately produce different output bindings.

**Registration version** — Version of manifest/planning-visible registration
contract.

**Stage invocation** — One concrete planned execution with fixed input bindings,
resolved output set, effective settings, resource policy and expected binding
provenance.

**Artifact requirement** — Input-edge declaration containing artifact family,
cardinality/optionality and optional `max_processing_order`.

**Artifact output capability** — Output-edge declaration containing exact
artifact family/schema, cardinality, optional `processing_order`, incremental
capability and default publication behavior.

**Processing order** — Sparse ordering metadata used for producer selection. It
is not artifact identity and is not an exact dependency selector.

**ArtifactBindingReader** — Ordered asynchronous stage input reader.

**ArtifactBindingWriter** — Runtime-owned ordered stage output writer. Runtime
assigns ordinals and validates binding closure/cardinality.

**Incremental publication** — Exposure of irrevocably committed members before
whole stage completion.

**Verification writer** — Writer used to deterministically verify a cached
output binding when sibling cache misses require the stage to rerun with its
original resolved output set.

**StageExecutionContext** — Small stage-facing context carrying task/invocation
identity, cancellation, progress, logging and optional scratch workspace. It
does not expose planning/cache/resource-control mutation APIs.
