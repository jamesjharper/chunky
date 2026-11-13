# Stage Model

## Purpose

A stage is a deterministic runtime unit that consumes already-bound artifact
bindings and writes already-bound artifact bindings.

This specification defines stage registration, capability/requirement metadata,
streaming I/O, cancellation, resource requirements and the boundary between
stage code and runtime orchestration.

## Core concepts

1. A **stage registration** is the immutable bootstrap-time contract for one
   producer implementation.
2. A **stage executor** contains execution behavior only.
3. A **stage invocation** is one concrete request-specific execution after
   planning fixes inputs, outputs, settings, cache behavior and resources.

## Stable producer identity

Each registration has one stable human-readable URL-safe producer ID:

```text
producer_id: [a-z0-9]+(?:[._-][a-z0-9]+)*
```

Examples:

```text
audio.prepare
asr.transcribe
punctuation.repair
render.webvtt
```

`display_name` is presentation metadata and may change without changing
producer identity.

`behavior_version` must change when equal semantic production context may
legitimately produce a different binding.

`registration_version` identifies manifest/planning contract changes and may
change independently from behavior version.

## Stage registration

Conceptually:

```text
StageRegistration
  producer_id
  display_name
  registration_version
  behavior_version
  executor_factory

  owned_artifacts
  input_capabilities
  output_capabilities

  resolve_requirements
  resolve_output_capabilities
  resolve_resource_policy

  runtime_settings
```

A stage may expose at most one output capability for a given exact artifact
schema. This makes producer capability identity unambiguous internally while
allowing the capability to run in multiple scoped invocations.

## Artifact capability and requirement

Cardinality and streaming capability belong to stage edges, not artifact
declarations.

```text
ArtifactCardinality
  ONE
  ONE_OR_MORE

ArtifactRequirement
  artifact: ArtifactRequest/family
  scope_transition: INHERIT | EXTEND(binding_scope_segment)
  cardinality
  optional
  max_processing_order?

ArtifactOutputCapability
  artifact: exact schema/family installed by the manifest
  binding_scope_segment?
  default_terminal_occurrence: bool
  cardinality
  optional
  processing_order?
  incremental_supported: bool
  default_publication_mode
```

### Processing order

`processing_order` orders processing positions for alternative producers of the
same artifact family. It is not artifact identity.

For a requirement with `max_processing_order=N`, planning chooses the highest
applicable candidate at or below N and then uses normal producer-selection rules
for ties.

A requirement with no maximum asks for the terminal/highest applicable
producer. There is no general-purpose exact processing-order dependency.

### Binding-scope declarations

Binding-scope segments are stable manifest identifiers matching
`[a-z][a-z0-9_]*` and owned by stage edges,
independent of exact producer IDs. An output capability may advertise the
segment at which its semantic occurrence is published and whether it is the
unique default/terminal occurrence for an unscoped caller request. An input
requirement declares either:

- `INHERIT`: keep the current occurrence scope; or
- `EXTEND(segment)`: append one declared upstream segment to the current scope
  before resolving the requirement.

Because canonical text is downstream-to-upstream, repeated backward traversal
appends scope segments before the final two artifact-family segments. The
manifest validates segment grammar, uniqueness in the owning edge namespace,
reachable propagation, and that each unscoped family has at most one applicable
default/terminal occurrence.

For successive same-family production, the later capability must consume an
earlier occurrence through an explicit `INHERIT`/`EXTEND` requirement and
publish a distinct declared occurrence. Completed content is never overwritten;
the later invocation publishes a new binding. Cyclic scope transitions and an
output that can consume itself without moving to an earlier production
position are invalid.

Exact producer constraints are not part of the MVP caller request. Producer
identity remains stable internal planning/provenance data.

## Requirement and output resolution

Request-specific planning may resolve requirements and output subsets using a
read-only planning context.

Settings are available as a final effective-settings view because defaults and
caller overrides may affect requirement/output resolution.

Planning callbacks must be deterministic and must not inspect:

- live cache state;
- current resource availability;
- task timing races;
- mutable scheduler state; or
- provider reservations.

## Executor contract

```python
class StageExecutor(Protocol):
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        ...
```

The executor returns `None`. Output members are committed through binding
writers while execution runs.

## Input artifacts

```text
InputArtifacts
  get_artifact(selector) -> ArtifactBindingReader
  contains(selector) -> bool
```

`get_artifact(...)` always returns an ordered binding reader. Optional absence is
represented by an empty closed binding rather than `None`.

Examples:

```python
# Singular convenience
settings = await inputs.get_artifact(ASR_SETTINGS).one()
config = await settings.read()

# Plural/live processing
async for member in inputs.get_artifact(SOURCE_AUDIO):
    context.cancellation.raise_if_cancelled()
    audio = await member.read()
    await process(audio)
```

Required cardinality has already been resolved/bound before invocation starts.
A live reader may wait for future committed upstream members.

## Output artifacts

```text
OutputArtifacts
  get_artifact(selector) -> ArtifactBindingWriter
  contains(selector) -> bool
```

A stage emits members with:

```python
await outputs.get_artifact(AUDIO_CHUNK).write_next(chunk)
```

The runtime assigns contiguous member ordinals. The executor never supplies
sequence indexes.

For `ONE`, a second `write_next(...)` fails immediately and causes stage
failure.

The executor does not explicitly close output bindings. Normal return requests
runtime finalization; the runtime validates minimum cardinality and closes each
binding successfully. An exception, cancellation or final cardinality failure
leaves open bindings incomplete.

Already committed members remain valid.

## Incremental publication

Incremental publication is independent of cardinality.

A capability declares whether its output can expose committed members before
stage completion. Stage configuration supplies the default mode; `RunPolicy`
may request `DEFAULT`, `AT_COMPLETION` or `INCREMENTAL`.

`INCREMENTAL` is only valid for a capable output. An incompatible forced mode is
an execution compatibility failure, not a reason to select another semantic
producer.

Once `write_next(...)` exposes a member incrementally, that member is final and
cannot be revised based on later inputs.

## Streaming consumers

A streaming consumer is one long-lived stage invocation, not automatic
per-member fan-out.

```text
producer binding opens
  -> committed member makes consumer dependency-ready
  -> scheduler resource admission
  -> consumer execute() starts
  -> async iterator yields committed members until close/failure
```

A consumer becomes dependency-ready when:

- at least one consumable member is committed; or
- an optional binding successfully closes empty.

Resource admission still applies normally. Incremental readiness never bypasses
GPU/RAM/CPU/concurrency gating.

For the MVP, resources acquired for an invocation remain held until that
invocation ends, even while it awaits the next live member.

## Cancellation

`StageExecutionContext` includes a cooperative cancellation token:

```python
context.cancellation.raise_if_cancelled()
```

Stages should check it:

- before expensive provider work;
- between independent chunks/batches;
- before publishing outputs; and
- after long provider calls.

Blocking artifact operations also observe the same cancellation context. A
stage waiting for the next live member wakes promptly when cancellation wins.

Upstream terminal conditions remain distinguishable:

- successful close -> normal iterator EOF;
- upstream failure -> committed prefix then failure;
- upstream cancellation -> committed prefix then cancellation condition.

Cancelling one downstream consumer does not inherently cancel a shared upstream
producer. Scheduler demand ownership decides whether that producer is still
needed.

## Resource policy

Each stage declares/resolves its resource requirements through the scheduler's
normal resource policy. Streaming changes dependency readiness only; it does not
change resource accounting semantics.

Stage code must not directly acquire/release scheduler leases or alter run
policy.

## Runtime-owned responsibilities

Stages do not:

- construct the graph;
- choose producers;
- resolve exact `ArtifactSchemaId` values;
- calculate `ContentId` values or binding provenance;
- acquire cache reservations;
- publish CompletedBinding cache records;
- manage durable retention claims;
- materialize caller targets directly; or
- mutate the manifest.

Provider/model dependencies should be injected by the executor factory rather
than looked up through a generic mutable service locator.

## Partial cache hits

The resolved output set is part of stage production provenance. Therefore a
partial cache hit does not remove hit outputs from `OutputArtifacts`.

A hit output may be bound to a verification writer while missing outputs use
normal publication writers. The executor sees the same complete output set that
planning used for provenance.

## Failure

A stage reports failure by raising through the binding's normal mechanism. The
runner associates the failure with the invocation and task.

A failed invocation:

- stops further successful binding closure;
- preserves already committed member content;
- propagates failure to live downstream iterators;
- does not publish a reusable CompletedBinding for incomplete outputs; and
- participates in normal fail-fast task stopping.

## Invariants

1. Stage dependencies are artifact-based, not producer-coupled by default.
2. Processing order is ordering metadata only.
3. Cardinality belongs to stage edges.
4. Every input/output uses the ordered binding abstraction.
5. Runtime owns member ordinals and binding closure.
6. Incrementally published members are irrevocably final.
7. Resource gating remains authoritative for streaming stages.
8. Partial cache hits never change the resolved output set presented to the
   executor.
