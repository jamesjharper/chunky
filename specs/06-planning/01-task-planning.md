# Task Planning

## Purpose

Planning converts hierarchy-qualified versionless requests and supplied content
bindings into a deterministic execution graph with exact artifact schemas,
selected producers and predictable binding provenance.

Planning does not know future output `ContentId` values.

## Inputs to planning

Planning receives:

- immutable `LocalRunRequest` or `DurableRunRequest` semantic intent;
- frozen stage/artifact manifest;
- resolved semantic settings/defaults as required by planning hooks;
- run policy only where it constrains non-semantic compatibility; and
- supplied input bindings keyed by `ScopedArtifactRequest` and represented by
  ordered `ContentId` values or local
  equivalent sources.

Live cache state is consulted by orchestration/cache resolution after semantic
producer selection; it does not change semantic graph meaning.

## Scoped artifact request resolution

Caller input/output binding keys are `ScopedArtifactRequest` values whose final
two segments form the versionless `ArtifactRequest`:

```text
transcript/token_stream
punctuation_repair/asr_transcribe/transcript/token_stream
```

Planning validates its manifest-declared hierarchy and resolves the family to an
exact immutable schema:

```text
transcript/token_stream@2
```

The complete original canonical scoped request remains the caller-facing task
result key. An unscoped output selects the unique declared default/terminal
occurrence; missing or equally applicable occurrences fail rather than being
selected arbitrarily.

## Producer selection

Producer selection considers:

- artifact family/schema compatibility;
- enabled stage registrations;
- `processing_order` ordering constraints;
- producer-selection metadata/preferences; and
- deterministic tie-breaking rules.

### Processing order

A stage input requirement with `max_processing_order=N` selects the highest
applicable producer order not exceeding N. Multiple producers may share one
order; normal producer-selection rules resolve the tie.

A requirement without a maximum selects the terminal/highest applicable
producer.

There is no caller-facing exact-producer mode and no processing order in scoped
requests. Scope names are semantic binding occurrences, not producer IDs.

## Planned artifact binding

The planner's concrete edge is an internal resolved binding record:

```text
PlannedArtifactBinding
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  expected_binding_provenance
  cardinality
  optional
  publication_capability
  source:
    supplied source | selected producer invocation
  processing_order?          # diagnostics/selection metadata only
```

This record is not a public locator.

Planning predicts the binding provenance for each concrete edge. Execution
later supplies the actual ordered `ContentId` values; planning never fabricates or
predicts those member identities.

## Plan result

The plan is one deterministic request-scoped dependency DAG containing:

```text
TaskExecutionPlan
  invocations
  planned input/output bindings
  original ScopedArtifactRequest -> planned binding mapping
  dependency and publication edges
  deterministic diagnostics
```

Each invocation has one selected producer, exact schema versions, complete
resolved output set, final effective settings, input bindings, output bindings
and resource/publication compatibility metadata. Requested outputs remain
mapped by their original `ScopedArtifactRequest` so task-result correlation is
stable.

The plan contains semantic execution structure, not active cache reservations,
provider handles, live iterators or future `ContentId` values.

## Supplied input provenance

For a supplied durable binding:

```text
ScopedArtifactRequest -> exact ArtifactSchemaId
ordered members -> [C0, C1, ...]
```

Planning derives deterministic external binding provenance from the complete
scoped key, exact schema, and ordered `ContentId` values.

Upload does not create this provenance and planning need not persist it in a
public provenance index.

Optional absence contributes an explicit canonical absence value when required
for downstream provenance.

## Produced binding provenance

For each produced output binding, planning derives a predictable
`ProvenanceFingerprint` from:

- selected `producer_id`;
- stage `behavior_version`;
- exact output `ArtifactSchemaId` and scoped occurrence;
- ordered effective input scoped bindings and provenances;
- final effective settings fingerprint; and
- complete resolved output set for the invocation.

`processing_order`, task ID, invocation ID, resource availability and publication
mode do not contribute.

## Settings

Settings require dedicated planning treatment because caller overrides and
declaration defaults may be merged before complete requirements/output sets are
known.

Planning exposes deterministic final effective settings to requirement/output
resolvers and hashes those final effective semantic values as a separate
production-provenance contribution.

Operational runtime settings are validated for active stages after planning but
do not affect semantic provenance unless explicitly modeled as semantic
settings artifacts.

## Cardinality

Each concrete planned edge carries its `ONE`/`ONE_OR_MORE` plus optionality
contract. Planning validates that producer/consumer capability shapes are
compatible.

For plural produced outputs, planning does not predict final member count.

## Incremental compatibility

Incremental publication does not change semantic graph shape or producer
selection.

Planning/orchestration validates that a forced run-policy `INCREMENTAL` request
is supported by the selected output capability. Incompatibility fails
explicitly.

A consumer may become scheduler-ready when an upstream binding has a committed
member, but resource admission remains scheduler-owned.

## Cache orchestration

After semantic graph/provenance is fixed, cache resolution probes
`CompletedBinding` by expected provenance.

- hit -> reuse completed ordered binding;
- active producer -> share/wait according to production mode;
- miss -> acquire production ownership.

### Partial output hits

The complete resolved output set is part of provenance and must not be changed
due to cache state.

For an invocation resolved to `[A, B]` where A hits and B misses, the same
invocation `[A, B]` remains. A is bound for deterministic verification and B for
normal production.

## Planning algorithm

Planning starts from every requested output and expands requirements backward
to compatible supplied bindings, semantic defaults and selected producers.
Expansion is a fixed-point work queue rather than recursion whose behavior
depends on incidental traversal order.

For each requirement occurrence, supplied binding resolution is deterministic:

1. retain supplied keys with the same artifact family whose complete segment
   sequence is a suffix of the occurrence;
2. discard schema/cardinality-incompatible candidates;
3. select the candidate with the greatest segment count;
4. fail on an identical-path conflict or unresolved equal-specificity
   ambiguity; and
5. when selected, bind it and stop traversal upstream for that requirement.

A deeper binding shadows a shallower binding only within the deeper branch and
never field-merges with it. Requested outputs are independent roots and do not
shadow one another. Manifest/stage validation defines which scope segments and
propagation paths exist.

For each artifact need the planner:

1. resolves the versionless family and scoped occurrence to a compatible exact
   schema;
2. applies longest-applicable-suffix supplied-binding resolution;
3. otherwise selects one producer using processing order, preferences and
   deterministic tie handling;
4. resolves that invocation's final effective settings and complete output set;
5. adds its required and applicable optional input needs; and
6. deduplicates an equivalent invocation already present in the same plan.

An optional need that has no supplied binding/default/producer becomes an
explicit empty closed binding. A required need with no resolution fails
planning. Supplied inputs that are never reached from a requested output remain
accepted request roots but do not create work.

Producer ambiguity is not resolved by traversal order. The planner applies all
configured selection rules; if equally valid candidates remain, it reports a
stable ambiguity failure with the candidate producer IDs. Cycles are detected
on semantic invocation/binding dependencies and reported with a stable cycle
path.

After expansion reaches a fixed point, the planner prunes unused candidate
branches, validates cardinality and incremental compatibility, computes final
binding provenances, and emits deterministic topological scheduling data.

Invocation deduplication is semantic: two graph paths share an invocation only
when producer, behavior version, exact schemas, effective hierarchical input
bindings (including member order and duplicates), effective settings, scoped
output occurrence, and complete resolved output set describe the same
production. Thus one stage/schema may require distinct invocations for speech
and music settings while transcription and diarization may share the same
speech-preparation invocation. Task/invocation allocation IDs and cache state
never decide semantic equivalence.

An explicitly requested intermediate adds another root. It shares an invocation
already needed downstream exactly when this full invocation identity is equal.

## Durable supplied-content acceptance

Durable submission, before planning execution begins, requires every explicitly
supplied `ContentId` to be present, readable, authorized, and atomically
protected for the accepted task while applicable upload-grace claims are
removed and the task is created in the same transaction.
Planning may therefore treat durable input binding membership as stable for the
active/recoverable task lifetime.

The MVP does not support delayed missing-root acceptance or client streaming of
new binding members into an already-running task.

## Recovery

Durable recovery creates a fresh attempt and replans the immutable request
against a compatible runtime.

Existing `CompletedBinding` cache state may satisfy planned outputs according to
the request cache policy. Active scheduler state, live iterators and incomplete
binding prefixes are not resumed as execution checkpoints in the MVP.

## Planning failures and diagnostics

Planning fails before stage execution for conditions including:

- unknown or incompatible artifact request/schema;
- missing, invalid, incompatible, or ambiguous hierarchy path;
- ambiguous unscoped requested-output occurrence;
- unresolved or ambiguous required producer;
- unsatisfied required input or semantic setting;
- artifact/cardinality/publication incompatibility;
- dependency cycle;
- conflicting stage registration or output capability; and
- nondeterministic resolver output for equivalent frozen inputs.

Failures identify the original scoped request, exact schema when resolved,
consumer/producer IDs, processing-order constraint and dependency path where
applicable. Candidate lists and graph paths are deterministically ordered.
Diagnostics may explain alternatives that were rejected, but must not expose
provider handles, cache leases or other process-local state.

## Invariants

1. Caller requests are versionless and hierarchy-qualified; planning resolves
   exact schemas and producers.
2. Processing order affects producer selection only.
3. The planner predicts binding provenance, not future `ContentId` values.
4. Produced provenance records the actual selected producer.
5. Output set and effective settings contribute to provenance.
6. Cache state never silently changes the semantic resolved output set.
7. Plural member count remains execution-time data.
8. Supplied-but-unused inputs do not create stage work.
9. Producer ambiguity and cycles are reported deterministically rather than
   resolved by traversal order.
10. Effective hierarchy participates in invocation identity and recovery.
11. Supplied binding selection cuts off upstream traversal for the satisfied
    occurrence.
