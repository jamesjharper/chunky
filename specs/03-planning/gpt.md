# Chunky Run Contract - Graph Creation

## Purpose

This document defines how a `RunRequest` is resolved into a request-specific graph of stage invocations.

Graph creation is responsible for deciding **what work exists**. It starts from a frozen `StageManifest`, applies one request, resolves supplied roots, producer paths, stage requirements, settings propagation, optional paths, and invocation deduplication, then emits a stable request graph that later execution code can fingerprint, cache-check, schedule, and materialize.

Graph creation is not responsible for:

- running stages;
- reading or writing cache entries;
- acquiring cache read or write reservations;
- materializing caller targets;
- changing graph shape after runtime cache state is known.

Runtime cache lookup and cache wait behavior belong to the runner, artifact binding layer, cache coordinator, and scheduler.

## 1. Plan Phases

A run moves through four conceptual graph/plan phases.

### 1.1 Stage Manifest Plan

The stage manifest plan is the static candidate graph derived from the configured `StageManifest`.

It records every configured stage, every declared input capability, every declared output capability, and every artifact declaration known to the configured pipeline.

It is independent of any single `RunRequest` and changes only when the configured stage manifest changes.

The stage manifest plan validates structural facts that are independent of a concrete request, including:

- stage IDs are unique;
- artifact declarations are valid;
- stage inputs and outputs reference declared artifacts;
- producer indexes can be built;
- phase behavior is valid for declared artifact roles;
- manifest-level cycles are rejected where they are detectable from static declarations.

### 1.2 Proposed Request Scope

The proposed request scope is not yet a concrete invocation graph. It is the immutable input context used to expand one request.

In the current implementation this is represented by `TaskRequestContext`.

```python
@dataclass(frozen=True)
class TaskRequestContext:
    manifest: StageManifest
    manifest_graph: StageManifestGraph
    requested_output_ids: tuple[ArtifactKey, ...]
    supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]]
    policy: PlanningRunPolicy
    request_settings: PlannerRequestSettings
```

The request context records:

- the frozen stage manifest;
- the frozen manifest graph;
- requested output artifact IDs;
- supplied source, settings, and intermediate artifact instances;
- run policy visible during planning;
- request-scoped settings already read from supplied/default settings artifacts.

At this phase the planner has not yet decided:

- which producer path satisfies each requested artifact;
- which optional paths are valid;
- which logical stages become concrete invocations;
- which invocations share work;
- which cache entries exist.

Supplied artifacts are recorded as eligible graph roots. If the caller supplies an intermediate artifact, it wins over a producer path by default unless an explicit recompute/force-producer policy is added later.

### 1.3 Evaluated Request Graph

The evaluated request graph is the request-specific dependency graph produced by walking backward from the requested outputs.

In the current implementation this is represented by `TaskRequestGraph`.

```python
@dataclass(frozen=True)
class TaskRequestGraph:
    manifest_id: str
    invocations: tuple[PlannedInvocation, ...]
    dependencies_by_invocation: dict[str, tuple[str, ...]]
    dependents_by_invocation: dict[str, tuple[str, ...]]
    requested_output_bindings: tuple[RequestedOutputBinding, ...]
```

The evaluated request graph contains concrete planned stage invocations and their artifact bindings, but it is still before volatile runtime cache decisions. A cache hit may later skip an invocation, but it must not remove the invocation from this graph.

This phase resolves:

- supplied artifact roots;
- inherited settings roots;
- producer selection;
- phase selector resolution;
- terminal remapping for phase-less final intermediate requirements;
- optional requirement viability;
- ambiguous producer viability;
- invocation expansion;
- invocation deduplication;
- requested output subset per invocation;
- required and optional input bindings;
- dependency and dependent indexes.

### 1.4 Task Execution Graph / Execution Plan

The task execution graph is derived from the evaluated request graph.

This phase may calculate final expected fingerprints, prepare execution-facing artifact records, and create the immutable work contract used by the scheduler. Runtime cache lookup and cache reservation should still be applied at the last responsible moment before an invocation is admitted or skipped.

The execution graph may record current runtime disposition, such as:

- waiting for inputs;
- waiting for another task's cache writer;
- ready to run;
- skipped because all requested outputs are already supplied or cache-reserved.

The scheduler may update runtime state in an execution manifest, but it must not change:

- graph shape;
- producer selection;
- artifact identity;
- phase remapping;
- requested output subset;
- input bindings.

## 2. Implementation Model



### 2.1 Planner Services

#### `ExecutionPlanner`

`ExecutionPlanner` is an orchestration facade. It should stay small.

It performs these steps:

1. Validate that at least one requested output exists in the manifest.
2. Build or reuse the manifest graph.
3. Read supplied/default settings into `PlannerRequestSettings`.
4. Build a `TaskRequestContext`.
5. Build a `TaskRequestGraph`.
6. Build a task execution graph from the request graph.

It should not directly contain artifact-resolution, producer-selection, settings-propagation, or cache-coordination logic.

#### `PlannerSettingsResolver`

`PlannerSettingsResolver` is a service. It resolves the effective settings visible to a stage invocation.

It is intentionally separate from `PlannerRequestSettings`, which is only request-scoped settings data.

Settings precedence is:

```text
default settings
< inherited path-specific settings
< user-configured settings
< path-pushed inherited settings
```

This means path-pushed inherited settings override user configuration. User configuration overrides defaults.

#### `ProducerSelector`

`ProducerSelector` owns root and producer choice. It should answer:

- whether a supplied artifact can satisfy a requirement;
- which stage producer should satisfy an unsupplied requirement;
- how phased requirements resolve to concrete declared outputs;
- whether a producer selection is ambiguous.

It does not expand candidate paths. Candidate-path validation belongs to speculative graph branches.

### 2.2 Planner Work Items

#### `ArtifactNeed`

`ArtifactNeed` is the planner work item used during backward traversal.

It is not the same thing as a stage's static `ArtifactRequirement`. It wraps the requirement with the consumer/request context needed to record a selected binding.

```python
@dataclass(frozen=True)
class ArtifactNeed:
    requirement: ArtifactRequirement
    inherited_settings: tuple[tuple[ArtifactKey, object], ...] = ()
    consumer_invocation_key: str | None = None
    requested_output_id: ArtifactKey | None = None
```

An `ArtifactNeed` may represent:

- a requested output terminal, when `requested_output_id` is present;
- an invocation input, when `consumer_invocation_key` is present.

The preferred name is `ArtifactNeed`. Older names such as `PendingArtifactRequirement` are compatibility aliases only.

#### `GraphWorkQueue`

`GraphWorkQueue` owns the traversal queues used by `StageManifestGraphWalker`.

It contains:

- unmet artifact needs to resolve;
- invocation keys whose stage requirements still need to be resolved;
- a set used to avoid queueing the same unresolved invocation more than once.

Each queued artifact need is wrapped in a `GraphWorkItem`.

```python
@dataclass(frozen=True)
class GraphWorkItem:
    need: ArtifactNeed
    force_required: bool = False
```

`force_required` is queue behavior, not part of `ArtifactNeed`. It is used when an optional path is being probed speculatively and should temporarily fail like a required path if it cannot resolve.

The preferred name is `GraphWorkQueue`. Older names such as `GraphWalkDestinationQueue` are compatibility aliases only.

### 2.3 Graph Build State

#### `GraphBuildState`

`GraphBuildState` is the mutable graph-discovery state for one expansion pass.

It owns:

- draft invocations;
- requested output bindings;
- producer invocation lookup by resolved artifact instance ID;
- per-stage invocation sequence allocation.

```python
@dataclass
class GraphBuildState:
    invocations_by_key: dict[str, InvocationDraft]
    requested_output_bindings: dict[ArtifactKey, RequestedOutputBinding]
    producer_invocation_by_instance_id: dict[str, str]
    next_invocation_sequence_by_stage: dict[str, int]
```

This replaces the older conceptual split between:

- `PlanningExpansionState`;
- `TaskRequestGraphDraft`;
- `InvocationKeyRegistry`.

Those older names may remain as migration aliases, but they should not be treated as separate planner concepts.

`GraphBuildState` must support clone/replace behavior because optional-path validation and ambiguous-producer validation use speculative branches. A speculative branch receives a cloned state. If its candidate path succeeds and is selected, the branch commits by replacing the parent state with the branch state. If it fails or loses selection, it discards the cloned state.

#### `InvocationIdentity`

`InvocationIdentity` is the deduplication key for draft invocations.

It contains:

- stage ID;
- inherited settings carried to the invocation;
- effective settings fingerprint.

Two paths that reach the same stage with the same invocation identity should share one `InvocationDraft`. If a later path asks the same invocation to produce an additional output, the invocation's requested output subset expands and its requirements must be refreshed.

### 2.4 Invocation Models

#### `InvocationDraft`

`InvocationDraft` is the mutable stage-invocation model used while graph discovery is still in progress.

```python
@dataclass
class InvocationDraft:
    key: str
    registration: ManifestStageRegistration
    inherited_settings: tuple[tuple[ArtifactKey, object], ...]
    effective_settings_fingerprint: str
    output_bindings: dict[ArtifactKey, PlannedOutputBinding]
    input_bindings: dict[InputRequirementKey, PlannedInputBinding]
    resource_policy: ResourcePolicy | None
    resolved_settings: ResolvedSettingsView
    input_requirements_resolved_for_outputs: frozenset[tuple[ArtifactKey, ArtifactKey]]
```

It is mutable because traversal may discover that an existing invocation must produce more outputs than originally requested. When the requested output signature changes, the planner must re-run the stage's requirement and output-capability hooks for that invocation.

#### `PlannedInvocation`

`PlannedInvocation` is the immutable evaluated-plan invocation emitted into `TaskRequestGraph`.

```python
@dataclass(frozen=True)
class PlannedInvocation:
    invocation_id: str
    registration: ManifestStageRegistration
    required_input_bindings: tuple[PlannedInputBinding, ...]
    optional_input_bindings: tuple[PlannedInputBinding, ...]
    requested_output_bindings: tuple[PlannedOutputBinding, ...]
    effective_settings_fingerprint: str
    resolved_resource_policy: ResourcePolicy
    pedigree_fingerprint: str | None = None
    resolved_settings: ResolvedSettingsView = field(default_factory=ResolvedSettingsView)
```

`InvocationDraft` and `PlannedInvocation` must remain separate. The draft is mutable discovery state. The planned invocation is a stable evaluated-graph record.

Older names such as `PlannedInvocationDraft` and `PlannedStageInvocation` are compatibility aliases only.

### 2.5 Artifact Resolution and Bindings

#### `ArtifactResolution`

`ArtifactResolution` is the answer to an `ArtifactNeed`.

It is intentionally narrower than final input/output bindings.

```python
@dataclass(frozen=True)
class ArtifactResolution:
    resolved_artifact_id: ArtifactKey
    resolved_instance_id: str
    producer_invocation_key: str | None = None
```

A resolution can be:

- supplied by the request/defaults/inherited settings;
- produced by a planned invocation.

If `producer_invocation_key` is present, the producing invocation must be queued for requirement expansion.

Older names such as `ResolvedArtifactBinding` are compatibility aliases only.

#### `MissingOptionalArtifact`

`MissingOptionalArtifact` represents an optional artifact that could not be supplied and could not be produced through a valid path.

It is not a successful binding. The planner records no input binding for it, and it must not block the consuming invocation.

Required artifacts must not resolve to `MissingOptionalArtifact`; they either resolve to an `ArtifactResolution` or fail planning.

#### `PlannedInputBinding`

A `PlannedInputBinding` records how one invocation input requirement was satisfied.

It records:

- the original selector from the requirement;
- the resolved artifact ID;
- the resolved artifact instance ID;
- whether the input is required;
- the phase-match behavior;
- pushed settings associated with that requirement;
- the producer invocation key, when the input is produced by another planned invocation.

#### `PlannedOutputBinding`

A `PlannedOutputBinding` records one output explicitly requested from one planned invocation.

It records:

- the declared output artifact ID;
- the resolved output artifact ID;
- the resolved artifact instance ID;
- the expected fingerprint, when already calculated.

When `declared_artifact_id != resolved_artifact_id`, the output was terminal-remapped. This is expected for phase-less final intermediate requirements satisfied by a concrete phase-numbered output.

#### `RequestedOutputBinding`

A `RequestedOutputBinding` maps a user-requested output artifact ID to the artifact instance selected to satisfy it.

## 3. Evaluated Graph Expansion

### 3.1 Fixed-Point Traversal

`StageManifestGraphWalker` expands the request graph as a fixed-point traversal.

Traversal starts with a `GraphWorkQueue` created from the requested outputs. Each requested output becomes an `ArtifactNeed` whose requirement is built from the requested artifact ID.

The walker repeatedly performs two operations:

1. Resolve all pending artifact needs.
2. Resolve queued invocation requirements when no artifact needs are currently pending.

Resolving an artifact need may discover a producing invocation. When that happens, the producing invocation key is queued for requirement resolution.

Resolving an invocation may discover more artifact needs. Those needs are appended to the work queue.

Traversal ends when both queues are empty.

### 3.2 Artifact Need Resolution Order

`ArtifactRequirementResolver` resolves each `ArtifactNeed` in this order:

1. Resolve from inherited settings when the need is for a settings artifact and exactly one inherited setting can satisfy it.
2. Resolve from supplied artifacts when the request has a supplied source, setting, or intermediate artifact that satisfies the requirement and phase-match mode.
3. If the need is optional and its declaration is a source artifact, return `MissingOptionalArtifact`.
4. If the need is optional, probe it speculatively as required. If the speculative path succeeds, commit it. If it fails with no producer path, return `MissingOptionalArtifact`.
5. Resolve a producer path for required needs, or for optional needs that are being force-probed.

This order preserves the rule that supplied artifacts win over producer paths by default.

### 3.3 Inherited Settings Artifacts

A settings artifact requirement may be satisfied from inherited path-specific settings.

The resolver matches inherited settings by comparing the requirement selector with inherited keys after removing override qualifiers.

If no inherited setting matches, normal supplied/producer resolution continues.

If more than one inherited setting can satisfy the same settings requirement, planning fails because the planner cannot deterministically choose one.

If an inherited setting is selected, the corresponding supplied/default settings artifact instance must exist in `TaskRequestContext.supplied_instances`.

### 3.4 Supplied Artifacts

`ProducerSelector.select_supplied(...)` checks whether a supplied artifact instance can satisfy the requested artifact selector and phase-match mode.

For phased intermediate families, a concrete supplied phase may satisfy a bounded numeric requirement according to the requirement's phase-match mode. A phase-less supplied intermediate satisfies the completed-family requirement directly.

A supplied artifact becomes an `ArtifactResolution` with no producer invocation key.

### 3.5 Producer Selection

When no inherited or supplied root satisfies a need, the planner selects a producer.

Producer selection should:

1. Ask the manifest for producers that can satisfy the artifact selector and phase-match mode.
2. Fail with `ProducerPathUnavailable` if there are no candidates.
3. Prefer the highest matching phase for phased families.
4. Apply an explicit/default producer preference when configured.
5. If exactly one candidate remains, select it.
6. If more than one equally ranked candidate remains, raise `AmbiguousProducerSelection`.

A selected producer is represented by `ProducerSelection`:

```python
@dataclass(frozen=True)
class ProducerSelection:
    registration: ManifestStageRegistration
    declared_output_id: ArtifactKey
    resolved_output_id: ArtifactKey
```

`declared_output_id` is the output declared by the stage. `resolved_output_id` is the semantic artifact ID under which this graph will store and bind the output. These differ when terminal remapping occurs.

### 3.6 Optional Path Probing

Optional artifacts are allowed to be absent, but if a valid path exists, the planner may include that path.

To avoid mutating the real graph with a path that later proves invalid, optional resolution uses `SpeculativeGraphBranch`:

1. Clone the parent `GraphBuildState`.
2. Expand the optional need with `force_required=True`.
3. If expansion fails with `ProducerPathUnavailable`, discard the branch and return `MissingOptionalArtifact`.
4. If expansion succeeds, read the resolved input binding from the branch state.
5. Commit the branch into the parent state.

Optional source artifacts do not need speculative traversal. If an optional source is not supplied, it resolves directly to `MissingOptionalArtifact`.

### 3.7 Ambiguous Producer Probing

When producer selection is ambiguous, the planner probes each candidate in an isolated `SpeculativeGraphBranch`.

For each candidate:

1. Clone the parent `GraphBuildState`.
2. Create or reuse the candidate producer invocation inside the branch.
3. Expand the branch from that producer.
4. If the path fails with `ProducerPathUnavailable`, record the candidate as invalid and discard the branch.
5. If the path succeeds, keep the branch as a valid candidate.

After all candidates are probed:

- If exactly one candidate is valid, commit that candidate's branch.
- If no candidates are valid, fail with `ProducerPathUnavailable`.
- If multiple candidates are valid, fail with `AmbiguousProducerSelection` enriched with the probe result.

The planner must not partially commit losing candidate branches.

## 4. Invocation Expansion and Requirement Resolution

### 4.1 Produced Artifact Instance Identity

When a producer is selected, the planner builds a `ProducedArtifact` from the selected stage planning context.

The produced artifact records:

- producer selection;
- resolved semantic artifact ID;
- resolved artifact instance ID;
- effective settings fingerprint.

The current implementation uses a deterministic instance ID of:

```text
{artifact_id}@{effective_settings_fingerprint[:16]}
```

When the effective settings fingerprint is empty, the instance ID is simply the artifact ID.

This instance ID is a planner/runtime binding identity. It is not the final semantic cache fingerprint.

### 4.2 Invocation Deduplication

Before creating a new invocation, `GraphBuildState` checks whether the same resolved artifact instance already has a producer invocation.

If so, the existing invocation is reused.

If not, `GraphBuildState.find_or_create_invocation(...)` reuses an existing invocation with the same `InvocationIdentity` or allocates a new draft invocation key.

Draft invocation keys use per-stage sequencing:

```text
{stage_id}@{sequence}
```

Final planned invocation IDs are assigned during graph finalization using topological order:

```text
{stage_id}:{sequence:03d}
```

### 4.3 Requested Output Expansion

The same invocation may be reached by multiple downstream needs.

When another path asks an existing invocation to produce an additional output, the planner records the additional `PlannedOutputBinding` on the same `InvocationDraft`.

The invocation tracks the requested-output signature for which its inputs were last resolved. If the signature changes, `InvocationRequirementResolver` must re-resolve that invocation's requirements.

This prevents stale input bindings after output relevance changes.

### 4.4 Stage Hook Resolution

`InvocationRequirementResolver` resolves stage-specific planning hooks for each queued invocation.

For the invocation's current requested output subset, it builds a `StagePlanningContext` with:

- stage ID;
- requested declared outputs;
- effective settings;
- inherited settings;
- run policy.

The resolver then asks the stage registration for:

- validated output capabilities;
- validated input requirements;
- validated resource policy.

The planner must fail if the stage cannot expose one of the outputs currently requested from the invocation.

After the hooks are resolved, the invocation draft records:

- resolved settings;
- resolved resource policy;
- current requested-output signature as resolved.

Then previous input bindings are cleared and rediscovered from the new requirement set.

### 4.5 Input Requirement Work Items

Each stage input requirement becomes an `ArtifactNeed` with `consumer_invocation_key` set to the current invocation draft key.

The need carries inherited settings calculated for that specific input edge.

When a need resolves successfully, `GraphBuildState.record_invocation_input_binding(...)` records a `PlannedInputBinding` on the consumer invocation.

### 4.6 Settings Propagation Through Inputs

Settings propagation is path-specific.

When calculating inherited settings for an input requirement:

- If the input requirement itself is a settings artifact, the planner may carry all inherited settings so that the settings requirement can be satisfied directly from inherited values.
- If the input requirement is not a settings artifact, the planner passes through inherited settings that were not consumed by the current stage.
- For every artifact listed in `requirement.pushed_settings`, the planner resolves the setting from the merged effective settings view and adds it to the inherited settings passed to that input.

The merged effective settings view is:

```text
defaults + inherited + user
```

with user settings winning over inherited settings.

## 5. Phased Intermediate Artifacts

Some intermediate artifact families opt into numbered processing phases:

```text
{domain}::{content_kind}::{phase}
```

The phase number is a sparse processing phase, not a schema version. Phase numbers are meaningful only within their own artifact family.

For phased families:

- a numbered input requirement is a selector;
- an `AT_MOST` selector resolves to the highest enabled concrete phase in the same family that is less than or equal to the requested phase;
- an `EXACT` selector resolves only the exact concrete phase;
- a numbered output declaration is an exact concrete phase;
- a phase-less intermediate requirement asks for the completed value of the family selected by the evaluated graph.

The planner is responsible for terminal remapping. If a selected terminal producer emits a concrete phase-numbered output that satisfies a phase-less requirement, the binding is stored under the phase-less final artifact ID.

When terminal remapping occurs:

- `PlannedOutputBinding.declared_artifact_id` records the stage-declared concrete output;
- `PlannedOutputBinding.resolved_artifact_id` records the phase-less final artifact ID;
- `terminal_remapped` is true;
- no additional cache alias should be created under the concrete phase ID;
- run/diagnostic manifests should record the declared concrete output for provenance.

Phase behavior applies only to artifact families that explicitly declare it. Source, settings, target, diagnostic, and non-phased intermediate artifacts must not receive phase behavior from textual shape alone.

## 6. Fingerprints

Graph creation establishes enough structure to calculate stable fingerprints, but final semantic fingerprints may be assigned by the task execution graph builder after the evaluated request graph is stable.

### 6.1 Supplied Artifact Fingerprints

Supplied source, settings, and intermediate artifacts are graph roots.

Their fingerprints are derived from:

- artifact identity;
- declaration/schema version;
- role;
- content fingerprint from the supplied source handle.

Settings artifacts use the same supplied-artifact fingerprinting rule after defaults, user values, and path-pushed inherited overrides have been resolved.

### 6.2 Invocation Pedigree Fingerprints

A stage invocation pedigree fingerprint should be calculated from:

- stage ID;
- stage behavior version;
- stable ordered required input bindings and fingerprints;
- effective settings fingerprints;
- behavior-affecting provider/model versions not already represented by settings or behavior version.

Input aggregation must be order-stable. It should not depend on traversal order, insertion order, concurrent completion order, or object memory address.

### 6.3 Output Fingerprints

Each requested output receives its own fingerprint derived from:

- resolved artifact identity;
- artifact declaration/schema version;
- codec identity;
- the producing invocation pedigree fingerprint.

One invocation may produce multiple requested outputs. They share the same invocation pedigree but receive distinct output fingerprints because each output contributes its own resolved artifact identity and declaration metadata.

### 6.4 Instance IDs vs Fingerprints

The planner's `instance_id(...)` helper produces request-graph binding identities.

These IDs are useful for deduplicating producer invocations and connecting inputs to outputs during graph discovery.

They must not be confused with final semantic artifact fingerprints used for cache lookup. Final fingerprints must include declaration identity, codec identity, producer behavior, input fingerprints, settings fingerprints, and terminal-remapping decisions.

## 7. Cache and Execution

The evaluated request graph must be built without consulting cache availability.

Runtime cache behavior applies later to the already-planned requested output subset of each invocation.

For each planned invocation, cache skipping may occur only when all outputs explicitly requested from that invocation are available as supplied artifacts or cache-reserved hits. Incidental outputs are not part of the skip decision.

When a cache hit is selected, it must be reserved before downstream work can consume it.

A cache lookup should atomically perform:

```text
1. lookup by expected fingerprint
2. validate metadata against expected artifact identity/schema/codec
3. acquire a read reservation
4. expose a cache source handle
```

When an invocation needs to produce cacheable outputs, write reservation must be acquired before the invocation is admitted. If another task already owns a write reservation for a requested output fingerprint, this invocation waits for that writer and then re-resolves cache state.

Cache wait completion never implies a hit. The waiting path must re-run authoritative cache resolution.

## 8. Finalization of `TaskRequestGraph`

After `GraphBuildState` has no pending artifact needs or unresolved invocations, `TaskRequestGraphBuilder` finalizes the request graph.

Finalization performs:

1. Topological ordering of draft invocation keys using producer dependencies from input bindings.
2. Detection of request-specific cycles or unresolved dependency references.
3. Assignment of stable planned invocation IDs in topological order.
4. Conversion from `InvocationDraft` to `PlannedInvocation`.
5. Partitioning of input bindings into required and optional input binding tuples.
6. Stable ordering of requested output bindings.
7. Construction of `dependencies_by_invocation`.
8. Construction of `dependents_by_invocation`.
9. Stable ordering of requested output bindings for user-requested terminal artifacts.

A request-specific graph is invalid if no topological ordering can be produced.

## 9. Public Surface and Migration Guidance

The stable request-graph API should expose only the concepts needed by downstream graph execution and tests:

- `TaskRequestGraph`;
- `TaskRequestGraphBuilder`;
- `TaskRequestContext`;
- `PlannerRequestSettings`;
- `PlannedInvocation`;
- `PlannedInputBinding`;
- `PlannedOutputBinding`;
- `RequestedOutputBinding`;
- `ArtifactResolution`;
- `MissingOptionalArtifact`;
- `ProducerSelection`;
- `InputRequirementKey`.

Internal aliases may remain during migration, but they should not appear in new spec text or new code unless the purpose is backwards compatibility.

Deprecated compatibility names include:

| Deprecated name | Preferred name |
| --- | --- |
| `PendingArtifactRequirement` | `ArtifactNeed` |
| `PendingRequirements` | `tuple[ArtifactNeed, ...]` |
| `ResolvedArtifactBinding` | `ArtifactResolution` |
| `PlannedInvocationDraft` | `InvocationDraft` |
| `PlannedStageInvocation` | `PlannedInvocation` |
| `TaskRequestGraphDraft` | `GraphBuildState` |
| `PlanningExpansionState` | `GraphBuildState` |
| `InvocationKeyRegistry` | allocation methods on `GraphBuildState` |
| `InvocationShape` | `InvocationIdentity` |
| `GraphWalkDestinationQueue` | `GraphWorkQueue` |
| `SpeculativeStageManifestSubgraphWalker` | `SpeculativeGraphBranch` |

## 10. Invariants

Graph creation should preserve these invariants:

1. The stage manifest is immutable during request graph creation.
2. Cache availability does not change evaluated graph shape.
3. Supplied artifacts win over producer paths by default.
4. Required artifact needs must resolve to a supplied artifact or producer path, or planning fails.
5. Optional artifact needs may resolve to `MissingOptionalArtifact` without failing the graph.
6. Optional paths and ambiguous producer candidates are probed in speculative branches before they mutate the parent graph state.
7. A losing or failing speculative branch must not partially mutate the parent graph state.
8. A concrete invocation is shared when another path reaches the same stage with the same invocation identity.
9. A concrete invocation is duplicated when effective settings differ.
10. If a shared invocation gains requested outputs, its requirements and resource policy must be refreshed before finalization.
11. Inherited path-specific pushed settings override user-configured settings.
12. Settings consumed by a stage are not blindly passed upstream/downstream unless they are explicitly pushed through a requirement.
13. Phase selection and terminal remapping are planner responsibilities, not stage responsibilities.
14. A planned invocation records the explicit requested output subset.
15. Cache skipping considers only the explicit requested output subset, not incidental outputs.
16. Final invocation IDs and dependency indexes are assigned only after the graph is topologically ordered.
17. Scheduling and runtime execution must not call stage planning hooks in a way that changes graph shape.
