# Stage Model

## Purpose

This document defines Chunky's stage model:

- what a stage implementation is;
- how stage metadata is registered;
- how a pipeline bootstrap process creates and freezes a stage manifest;
- how graph creation resolves concrete stage invocations from that manifest;
- what a stage receives when the scheduler executes it.

A **stage** is a deterministic unit of pipeline behavior that reads already-bound semantic artifacts and writes already-bound semantic artifacts.

A stage does not construct the task graph, resolve cache entries, acquire cache reservations, choose target paths, schedule work, or materialize caller-provided outputs.

## Design Decisions

This specification adopts the following separation:

1. A **stage executor** contains execution behavior only.
2. A **stage registration** contains the metadata and planning hooks for one stage.
3. A **stage manifest builder** is the mutable bootstrap-time registry.
4. A **stage manifest** is the validated, frozen registry snapshot used for planning and execution.
5. A **pipeline feature** such as ASR or diarization installs a coherent set of artifact declarations, stage registrations, defaults, and provider bindings into the builder.

Stage metadata is not discovered by inspecting attributes on the executor class. Metadata such as input capabilities, output capabilities, behavior version, resource policy, and requirement resolution belongs to the registration stored in the stage manifest.

This avoids making executable classes serve simultaneously as:

- runtime behavior;
- graph schema;
- mutable configuration;
- global registration state.

## Terminology

### Stage Executor

A stage executor is the runtime implementation that performs one resolved invocation.

Conceptually:

```text
StageExecutor
  execute(inputs, outputs, execution_context) -> None
```

The executor receives only artifacts and run-scoped execution services that have already been resolved for that invocation.

### Stage Registration

A stage registration is the complete bootstrap-time contract for one stage implementation.

Conceptually:

```text
StageRegistration
  stage_id
  display_name
  registration_version
  behavior_version
  executor_factory

  owned_artifacts[]
  input_capabilities[]
  output_capabilities[]

  resolve_requirements(planning_context)
  resolve_output_capabilities(planning_context)
  resolve_resource_policy(planning_context)

  concurrency_policy
```

A registration is data plus deterministic planning hooks. It is not mutable task state.

### Stage Manifest Builder

The stage manifest builder collects pipeline features, artifact declarations, stage registrations, provider bindings, and defaults during application bootstrap.

It is mutable only during bootstrap.

### Stage Manifest

The stage manifest is the validated and frozen result of the builder. It is immutable for the lifetime of every plan created from it.

### Planned Stage Invocation

A planned stage invocation is a per-request use of a registered stage. Graph creation may create more than one invocation of the same registered stage when effective settings, artifact constraints, or requested output subsets differ.

## Responsibility Boundaries

### Stage Executor Responsibilities

A stage executor is responsible for:

- reading its bound required and optional input artifacts;
- observing cancellation at safe boundaries;
- reporting useful stage-defined progress;
- producing every output bound for that invocation;
- writing outputs through `ArtifactWriter` instances;
- raising an error when execution cannot complete correctly.

### Stage Registration Responsibilities

A stage registration is responsible for declaring:

- stable stage identity;
- behavior version;
- authoritative artifact declarations owned by the stage or installed with its feature;
- the broad static input capability envelope;
- the broad static output capability envelope;
- deterministic requirement resolution;
- deterministic output-capability narrowing;
- deterministic resource-policy resolution;
- explicit producer selection where needed and concurrency metadata;
- the executor factory used by the scheduler.

### Pipeline Bootstrap Responsibilities

Pipeline bootstrap is responsible for:

- installing enabled feature packages;
- registering shared and stage-owned artifact declarations;
- registering stages and provider adapters;
- resolving feature dependencies;
- validating the complete registry;
- freezing the stage manifest;
- optionally emitting a serializable manifest description for diagnostics.

### Planner Responsibilities

Graph creation is responsible for:

- selecting producers from the stage manifest;
- resolving effective settings;
- calling the registration's deterministic planning hooks;
- resolving phased artifact selectors and constrained artifact instances;
- creating one or more concrete planned invocations;
- calculating expected artifact fingerprints and production pedigree;
- fixing the requested output subset for each invocation.

### Scheduler and Runner Responsibilities

Scheduling and runtime orchestration are responsible for:

- waiting for required artifacts, resources, and other cache writers;
- resolving cache hits and write reservations;
- binding `InputArtifacts` and `OutputArtifacts`;
- constructing the stage-facing `StageExecutionContext`;
- invoking the executor;
- aborting unwritten outputs on failure or cancellation;
- validating and recording produced artifacts;
- materializing caller-provided targets after semantic artifacts are available.

## Stage Executor Contract

### Python Shape

```python
from typing import Protocol


class StageExecutor(Protocol):
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: "StageExecutionContext",
    ) -> None:
        ...
```

The stage returns `None`.

Produced semantic artifacts are obtained from the bound output writers after `execute(...)` returns:

```python
await executor.execute(inputs, outputs, context)
produced_instances = outputs.produced_instances()
```

This keeps the execution contract independent of whether the invocation has one output or many outputs.

### Why Execution Does Not Return `StageResult`

The artifact orchestration contract already makes each output writer authoritative for:

- whether the output was written;
- its resolved declaration;
- its expected fingerprint;
- its semantic publication source;
- its prepared and committed targets.

Returning a second list of produced artifacts would create two competing sources of truth.

A stage may return ordinary provider-specific values internally, but its public `execute(...)` contract returns no artifact result.

### Input Artifacts

`InputArtifacts` contains readers for the concrete artifacts selected by graph creation and bound by the runner.

Required inputs are always present when execution starts.

Optional inputs appear only when the evaluated graph resolved and bound them. The collection should support explicit optional inspection:

```python
class InputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactReader[object]: ...
    def get_optional_artifact(
        self,
        selector: ArtifactSelector,
    ) -> ArtifactReader[object] | None: ...
    def contains(self, selector: ArtifactSelector) -> bool: ...
```

A stage must not infer optional-input availability by reading planner state or querying the cache.

### Output Artifacts

`OutputArtifacts` contains writers only for the output subset fixed for this invocation.

The collection should support output inspection for stages with conditional or diagnostic outputs:

```python
class OutputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactWriter[object]: ...
    def get_optional_artifact(
        self,
        selector: ArtifactSelector,
    ) -> ArtifactWriter[object] | None: ...
    def contains(self, selector: ArtifactSelector) -> bool: ...
    def produced_instances(self) -> tuple[ArtifactInstance[object], ...]: ...
```

Every writer present in `OutputArtifacts` is a planned output and must be written exactly once before successful return.

An executor may not create its own semantic output writer. Cache and publication targets must already be bound by the runner.

### Settings

Settings remain normal settings artifacts and are read through `InputArtifacts`.

They are not duplicated in `StageExecutionContext`.

```python
settings = await inputs.get_artifact(ASR_SETTINGS).read()
```

This preserves one artifact-based dependency model for source values, settings, intermediate values, targets, and diagnostics.

### Stage Execution Context

The rich scheduler invocation record and the stage-facing execution context are different objects.

The scheduler may internally track fingerprints, cache policy, resource leases, producer metadata, and dependency links. Stage code does not need access to most of that information.

The initial stage-facing context should be intentionally small:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageExecutionContext:
    task_id: str
    invocation_id: str
    stage_id: str
    cancellation: CancellationToken
    progress: StageProgressReporter
    logger: StageLogger
    scratch_workspace: Path | None = None
```

Fields have the following meaning:

- `task_id`: correlation identity for the accepted run request;
- `invocation_id`: identity of this concrete planned invocation;
- `stage_id`: stable stage identity from the registration;
- `cancellation`: cooperative task cancellation token;
- `progress`: invocation-scoped progress reporter;
- `logger`: structured task- and invocation-scoped logger;
- `scratch_workspace`: optional invocation-owned temporary workspace.

The runner owns scratch-workspace creation and cleanup. Files written there are implementation details unless the stage writes a declared diagnostic artifact through `OutputArtifacts`.

The context must not expose APIs that allow a stage to:

- change graph shape;
- acquire cache reservations;
- select a different producer;
- write caller materialization targets directly;
- mutate the stage or artifact manifest;
- release scheduler resource leases.

### Provider and Model Dependencies

Provider clients, model pools, and implementation-specific services should normally be injected into the executor by its factory:

```python
class AsrExecutor:
    def __init__(self, provider: AsrProvider) -> None:
        self._provider = provider

    async def execute(self, inputs, outputs, context) -> None:
        ...
```

They should not be looked up through a generic mutable service locator in `StageExecutionContext`.

Credential values are runtime provider configuration. They must not be copied into the serializable manifest description or semantic fingerprints unless the corresponding artifact contract explicitly requires that behavior.

### Executor Lifetime and Concurrency

The default contract is that `executor_factory` creates an invocation-safe executor.

Implementations may choose one of these strategies:

- create a new lightweight executor for each invocation;
- create executors that share an injected thread-safe provider or model pool;
- explicitly register a concurrency-safe singleton executor.

A mutable executor instance must not be shared across concurrent invocations unless its registration explicitly declares that behavior safe.

### Failure and Cancellation

A stage reports failure by raising an exception. The runner associates that error with the invocation and stage registration.

A stage should check cancellation:

- before starting expensive provider work;
- between independent chunks, frames, profiles, or batches;
- before preparing outputs;
- after long provider calls return.

```python
context.cancellation.raise_if_cancelled()
```

Cache waits and scheduler resource waits happen before `execute(...)` and are cancellation-aware scheduler operations. Cancelling a task waiting for another task's cache writer cancels only the local wait; it does not cancel or modify the other task's reservation.

If execution fails or is cancelled, the runner aborts every bound writer that was not committed.

## Stage Registration

### Registration Model

A stage registration contains the data needed to validate the pipeline and plan concrete invocations.

Illustrative Python shape:

```python
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

StageExecutorFactory = Callable[[], StageExecutor]
StageRequirementResolver = Callable[
    ["StagePlanningContext"],
    "ResolvedStageRequirements",
]
StageOutputResolver = Callable[
    ["StagePlanningContext"],
    tuple[ArtifactCapability, ...],
]
StageResourceResolver = Callable[
    ["StagePlanningContext"],
    "ResourcePolicy",
]


@dataclass(frozen=True)
class StageRegistration:
    stage_id: str
    display_name: str
    registration_version: str
    behavior_version: str
    executor_factory: StageExecutorFactory

    owned_artifacts: tuple[ArtifactDeclaration[Any], ...]
    input_capabilities: tuple[ArtifactCapability, ...]
    output_capabilities: tuple[ArtifactCapability, ...]

    resolve_requirements: StageRequirementResolver
    resolve_output_capabilities: StageOutputResolver
    resolve_resource_policy: StageResourceResolver

    concurrency_policy: "StageConcurrencyPolicy | None" = None
```

The exact binding syntax may differ by language. The conceptual separation must remain the same.

### Stage Identity

`stage_id` is a stable semantic identifier for the registered behavior, for example:

```text
audio.prepare
speech_detection.vad
speaker_detection.diarize
utterance.merge
asr.transcribe
transcript.compose
render.webvtt
```

The stage ID is used for:

- producer selection;
- planning and run-manifest provenance;
- scheduling policy;
- progress correlation;
- diagnostics;
- explicit producer preferences.

It is not an artifact ID and must not be included in caller target paths.

### Display Name

`display_name` is a human-readable name used in progress and diagnostics. It is not part of semantic identity.

### Registration Version

`registration_version` identifies the planning and registration contract itself. It must change when capability declarations, planning-hook behavior, producer metadata, or other manifest-visible semantics change even if the bytes produced by execution would not change.

The registration version contributes to the stage manifest identity. It does not automatically contribute to artifact fingerprints.

### Behavior Version

`behavior_version` identifies behavior that can affect produced semantic artifacts.

It must change when the same inputs and effective settings may produce semantically different output because of a change such as:

- model or provider version;
- decoding algorithm;
- output semantics;
- default behavior;
- a bug fix that changes emitted content;
- a provider adapter change that affects results.

The behavior version is registration metadata, not an instance method on the executor.

### Owned Artifact Declarations

A registration may install declarations authoritatively owned by the stage or its feature package.

A stage or feature normally owns:

- intermediate artifacts it produces;
- target artifacts whose semantic shape it defines;
- diagnostic artifacts whose shape it defines;
- stage-specific settings artifacts;
- source artifacts defined by its subsystem.

A stage does not redeclare intermediate artifacts that it only consumes.

Shared source or settings declarations may be installed once by a shared feature package and referenced by several stage registrations.

### Static Input Capabilities

`input_capabilities` is the broad static envelope of artifacts the stage may consume in some valid invocation.

Capabilities are required before request-specific planning because the manifest candidate graph and consumer index are built from them.

A resolved requirement must be a member of the registration's input capability envelope.

### Static Output Capabilities

`output_capabilities` is the broad static envelope of artifacts the stage may produce in some valid invocation.

Every output capability must reference an authoritative artifact declaration.

A planned output and an incidental output must both be declared in this envelope before they may be validated, recorded, or cached.

### Requirement Resolution

Requirement resolution converts the broad input capability envelope into the concrete requirements for one request-specific invocation.

```python
@dataclass(frozen=True)
class ResolvedStageRequirements:
    inputs: tuple[ArtifactRequirement, ...]
```

`ArtifactRequirement` is owned by the Artifact Model. It expresses requiredness, phased matching, and any canonical instance constraints needed by graph creation.

The resolver receives a read-only planning context:

```python
@dataclass(frozen=True)
class StagePlanningContext:
    stage_id: str
    requested_outputs: frozenset[ArtifactKey]
    resolved_settings: ResolvedSettingsView
    run_policy: PlanningRunPolicy
```

The context contains only deterministic planning inputs. It does not expose cache state, live scheduler state, mutable task state, current resource availability, or active cache reservations.

Before creating this context, graph creation resolves the settings declarations in the registration's static settings capability envelope. User-supplied settings and declaration defaults can therefore be inspected before the resolver decides which settings artifacts and provider-specific inputs are concrete requirements. A missing settings artifact with no default remains absent from `ResolvedSettingsView`; inspecting it through `require(...)` is a planning error.

A simple resolver may ignore the context:

```python
def resolve_audio_analysis_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(ArtifactRequirement(SOURCE_AUDIO.artifact_id),),
    )
```

A settings-dependent resolver may choose required and optional inputs:

```python
def resolve_webvtt_requirements(
    context: StagePlanningContext,
) -> ResolvedStageRequirements:
    settings = context.resolved_settings.require(WEBVTT_SETTINGS)
    requirements = [
        ArtifactRequirement(TOKEN_STREAM.artifact_id),
        ArtifactRequirement(WEBVTT_SETTINGS.artifact_id),
    ]

    if settings.include_speakers:
        requirements.append(
            ArtifactRequirement(
                SPEAKER_ROLE_CHANGE_STREAM.artifact_id,
                required=True,
            )
        )

    return ResolvedStageRequirements(tuple(requirements))
```

An optional input is declared with `required=False`. The planner attempts to bind it when a valid supplied artifact or producer path exists, but absence does not invalidate the invocation.

The executor discovers which optional inputs were actually bound through `InputArtifacts.get_optional_artifact(...)` or `contains(...)`.

### Output-Capability Resolution

A registration may narrow its broad output capability envelope for the current settings and requested mode:

```python
def resolve_asr_output_capabilities(
    context: StagePlanningContext,
) -> tuple[ArtifactCapability, ...]:
    settings = context.resolved_settings.require(ASR_SETTINGS)
    outputs = [
        ArtifactCapability(TOKEN_TEXT_001.artifact_id),
        ArtifactCapability(TOKEN_CONFIDENCE_001.artifact_id),
    ]

    if settings.emit_token_boundaries:
        outputs.append(
            ArtifactCapability(TOKEN_BOUNDARIES_001.artifact_id)
        )

    return tuple(outputs)
```

The result must be a subset of the statically registered output capabilities. If a producer candidate cannot expose the output currently being resolved, the planner rejects that candidate for the current path and either evaluates another registered producer or reports an unsatisfied output.

Output-capability resolution describes what the stage can validly produce under the resolved settings. It does not decide which of those outputs the invocation is asked to produce. Graph creation fixes the requested output subset from downstream requirements and explicitly requested diagnostics or targets.

### Resource-Policy Resolution

The registration resolves resource requirements during graph creation:

```python
def resolve_asr_resources(
    context: StagePlanningContext,
) -> ResourcePolicy:
    settings = context.resolved_settings.require(ASR_SETTINGS)
    if settings.provider == "remote_api":
        return ResourcePolicy(classes=frozenset({"unbound"}))
    return ResourcePolicy(
        classes=frozenset({
            "gpu_compute_bound",
            "gpu_memory_bound",
        })
    )
```

The resolved resource policy affects scheduler admission only. It must not change after the execution plan is fixed.

### Producer Uniqueness and Concurrency

Exact artifact outputs must have at most one registered producer in a frozen
manifest. If two stages claim the same output artifact key, manifest validation
must fail unless the ambiguity has already been removed before planning, for
example by disabling or replacing one stage in a derived manifest.

`concurrency_policy` may declare limits such as:

- maximum concurrent invocations of this registration;
- provider-specific concurrency keys;
- device affinity;
- whether an executor singleton is concurrency-safe.

These values affect selection or scheduling but do not create direct stage-to-stage dependencies.

## Pipeline Bootstrap and Manifest Creation

### Bootstrap API

A configured pipeline is created before a run request is planned.

A typical public API may look like:

```python
pipeline = (
    ChunkyPipeline.builder()
    .enable_audio_preparation()
    .enable_vad()
    .enable_asr(provider=WhisperAsrProvider(...))
    .enable_diarization(provider=PyannoteDiarizationProvider(...))
    .enable_transcript_composition()
    .enable_webvtt()
    .build()
)

result = await pipeline.run(request)
```

A convenience mutable facade may also support the shorter shape:

```python
pipeline = ChunkyPipeline()
pipeline.enable_asr()
pipeline.enable_diarization()
pipeline.freeze()
```

Both forms compile to the same bootstrap model. Convenience methods are thin feature-installation adapters rather than special registry logic:

```python
class ChunkyPipelineBuilder:
    def enable_asr(
        self,
        *,
        provider: AsrProvider,
    ) -> "ChunkyPipelineBuilder":
        self._manifest_builder.install_feature(AsrFeature(provider))
        return self

    def enable_diarization(
        self,
        *,
        provider: DiarizationProvider,
    ) -> "ChunkyPipelineBuilder":
        self._manifest_builder.install_feature(
            DiarizationFeature(provider)
        )
        return self

    def build(self) -> "ChunkyPipeline":
        manifest = self._manifest_builder.freeze()
        return ChunkyPipeline(
            stage_manifest=manifest,
            runtime_services=self._runtime_services.freeze(),
        )
```

`build()` is the point at which the runtime stage manifest is emitted and frozen. The resulting `ChunkyPipeline` keeps that manifest as part of its immutable planning configuration and passes it to every task planner created by `run(...)` or `run_away(...)`.

`enable_asr()` means that ASR stages and their contracts are available to the planner. It does not mean that every run executes ASR. A stage is planned only when a requested output requires one of its producer paths.

Per-run settings such as `enable: false` may exclude an installed stage from producer selection for that request. Installation and request-specific enablement are separate concepts.

### Pipeline Features

A feature installer owns a coherent registration unit.

For example, enabling ASR may install:

- ASR settings declarations;
- ASR audio settings declarations;
- transcript output declarations owned by base ASR;
- the ASR stage registration;
- an ASR provider adapter or provider binding;
- default settings providers;
- dependencies on shared audio and chunk-plan declarations.

Illustrative shape:

```python
class AsrFeature:
    def __init__(self, provider: AsrProvider) -> None:
        self._provider = provider

    def install(self, builder: StageManifestBuilder) -> None:
        builder.install_feature(AudioArtifactsFeature())
        builder.install_feature(ChunkPlanArtifactsFeature())

        provider = self._provider
        builder.add_stage(
            StageRegistration(
                stage_id="asr.transcribe",
                display_name="Transcribe audio",
                registration_version="1",
                behavior_version=(
                    f"asr-v1+{provider.semantic_behavior_version}"
                ),
                executor_factory=lambda: AsrExecutor(provider),
                owned_artifacts=(
                    ASR_SETTINGS,
                    ASR_AUDIO_SETTINGS,
                    TOKEN_TEXT_001,
                    TOKEN_BOUNDARIES_001,
                    TOKEN_CONFIDENCE_001,
                ),
                input_capabilities=(
                    ArtifactCapability(CHUNK_SEGMENTS.artifact_id),
                    ArtifactCapability(AUDIO_PREPARED.artifact_id),
                    ArtifactCapability(ASR_SETTINGS.artifact_id),
                    ArtifactCapability(ASR_AUDIO_SETTINGS.artifact_id),
                ),
                output_capabilities=(
                    ArtifactCapability(TOKEN_TEXT_001.artifact_id),
                    ArtifactCapability(TOKEN_BOUNDARIES_001.artifact_id),
                    ArtifactCapability(TOKEN_CONFIDENCE_001.artifact_id),
                ),
                resolve_requirements=resolve_asr_requirements,
                resolve_output_capabilities=resolve_asr_outputs,
                resolve_resource_policy=resolve_asr_resources,
            )
        )
```

The feature installer is bootstrap code. It does not run once per task.

### Feature Dependencies

Feature installation should be idempotent by stable feature ID.

A feature may install shared prerequisite features, or the builder may require dependencies to be explicitly enabled. The initial recommended behavior is:

- a feature may install declaration-only shared prerequisites automatically;
- a feature must not silently install an alternative semantic producer that materially changes pipeline behavior;
- duplicate installation of the same feature configuration is a no-op;
- conflicting installation of the same feature ID is an error.

For example, ASR may ensure that shared audio artifact declarations are installed, but it should not silently choose and install a particular audio-preparation provider when more than one implementation is configured.

### Builder Shape

Illustrative builder API:

```python
class StageManifestBuilder:
    def install_feature(self, feature: PipelineFeature) -> None: ...
    def add_artifact(self, declaration: ArtifactDeclaration[object]) -> None: ...
    def add_stage(self, registration: StageRegistration) -> None: ...
    def set_default_producer(
        self,
        artifact_id: ArtifactKey,
        stage_id: str,
    ) -> None: ...
    def validate(self) -> None: ...
    def freeze(self) -> "StageManifest": ...
```

The builder must not be used after `freeze()`. `add_stage(...)` also contributes the registration's `owned_artifacts`; `add_artifact(...)` is primarily for shared declarations that are not owned by one stage registration.

### Manifest Build Steps

Freezing the manifest performs at least these steps:

1. Collect all installed feature records.
2. Collect shared and stage-owned artifact declarations.
3. Reject conflicting declarations for the same artifact key.
4. Collect all stage registrations.
5. Validate that stage IDs are unique.
6. Validate that every registration has non-empty registration and behavior versions.
7. Validate that every input capability references a known artifact declaration or artifact family allowed by the Artifact Model.
8. Validate that every output capability references an authoritative artifact declaration.
9. Validate that every owned artifact is declared consistently with its role and phase behavior.
10. Validate that every registration supplies its required planning hooks.
11. Install planning-time guards that reject requirement or output resolver results outside the registration's static capability envelopes.
12. Build producer indexes from output capabilities.
13. Build consumer indexes from input capabilities.
14. Validate configured default producers and reject duplicate exact-output producers.
15. Build the static candidate artifact relationship graph.
16. Reject impossible static cycles where they can be proven from declarations.
17. Calculate a deterministic manifest identity.
18. Freeze all maps, registrations, and indexes.

Request-specific cycles or ambiguities that depend on dynamic requirement resolution are detected during graph creation.

### Frozen Manifest Shape

Conceptually:

```text
StageManifest
  manifest_id
  manifest_version
  features: map<FeatureId, FeatureRecord>
  stages: map<StageId, StageRegistration>
  artifacts: map<ArtifactKey, ArtifactDeclaration>
  producers: map<ArtifactFamilyOrKey, tuple<StageProducer, ...>>
  consumers: map<ArtifactFamilyOrKey, tuple<StageConsumer, ...>>
  default_producers: map<ArtifactFamilyOrKey, StageId>
```

The runtime manifest contains executable factories and resolver callables.

### Serializable Manifest Description

Executable factories and callables are language-binding details and are not directly serializable as a portable data model.

For diagnostics, tooling, and cross-language comparison, the runtime manifest should be able to emit a serializable description:

```text
StageManifestDescription
  manifest_id
  manifest_version
  features[]
  stages[]
    stage_id
    display_name
    registration_version
    behavior_version
    input_capability_ids[]
    output_capability_ids[]
    owned_artifact_ids[]
    concurrency_policy
  artifacts[]
  default_producers[]
```

The description omits executable objects, credentials, mutable provider instances, and process-local addresses. A feature record may include a stable feature ID and non-secret configuration fingerprint so that materially different bootstrap configurations produce different manifest identities.

The same configured declarations and registration metadata should produce the same `manifest_id`. The identity must not depend on map insertion order or object memory addresses.

### Baseline and Per-Run Overrides

A pipeline owns one baseline frozen stage manifest.

A `RunRequest.registry_overrides` value, when supported, creates a derived manifest snapshot before graph creation. It must not mutate the baseline manifest or affect already planned tasks.

A derived manifest receives its own deterministic identity.

Typical overrides include:

- replacing a registered producer with a test or application-specific implementation;
- disabling an installed stage for one run;
- changing an explicit producer preference;
- removing or replacing a producer so the derived manifest remains unambiguous.

Runtime cache state is not a registry override.

## Planner Interaction

### Candidate Graph

The planner uses static capabilities from the frozen manifest to build or reuse a candidate artifact relationship graph.

Stages depend on artifact requirements, not directly on other stage IDs.

### Request-Specific Resolution

For a specific request, graph creation:

1. starts from requested target and diagnostic artifact keys;
2. finds candidate producers from the manifest;
3. selects a producer according to explicit preference, default producer, phase rules, or strict ambiguity failure;
4. resolves effective settings for that stage path;
5. constructs a read-only `StagePlanningContext`;
6. calls requirement resolution;
7. calls output-capability resolution;
8. resolves required and optional artifact paths;
9. resolves the resource policy;
10. creates a concrete planned invocation.

The planning hooks are deterministic and side-effect free. They must not:

- read cache availability;
- inspect current GPU or CPU load;
- contact a remote model provider;
- mutate the builder or manifest;
- allocate task-owned cache or resource leases;
- depend on wall-clock time or random values.

### Planned Invocation Shape

Conceptually:

```text
PlannedStageInvocation
  invocation_id
  registration_id / stage_id
  registration_version
  behavior_version
  executor_factory_binding

  required_input_bindings[]
  optional_input_bindings[]
  requested_output_bindings[]

  effective_settings_fingerprint
  expected_output_fingerprints[]
  resolved_resource_policy
  resolved_concurrency_policy
```

The planned invocation contains a specific requested output subset. The executor receives writers for exactly that subset.

### Invocation Sharing and Duplication

Two graph paths may share one invocation when all identity-affecting values are equivalent, including:

- stage registration;
- registration and behavior versions;
- effective settings fingerprint;
- constrained input requirements;
- selected input artifact fingerprints;
- requested output subset, or a safely shareable superset fixed by planning.

The planner creates separate invocations when these values differ.

This is required, for example, when ASR and diarization require different concrete `audio//prepared` instances.

### Supplied Artifacts and Cache State

Supplied artifacts may terminate producer path discovery according to the graph-creation contract.

Cache state does not affect requirement resolution or graph shape. Cache resolution occurs after the evaluated graph and expected fingerprints are fixed.

## Scheduler and Runner Interaction

### Runtime Sequence

For a planned invocation, the runner performs the following high-level sequence:

```text
1. wait until required input artifacts are available
2. wait for scheduler resource admission
3. resolve the complete planned output set against the cache
4. if another task owns a required output write reservation:
     wait cancellation-safely, then resolve the complete output set again
5. if all planned outputs are available:
     mark the invocation skipped and publish those instances
6. otherwise:
     bind InputArtifacts from selected ArtifactInstances
     bind OutputArtifacts from reserved semantic output targets
     construct StageExecutionContext
     create or obtain an invocation-safe executor
     call execute(inputs, outputs, context)
     verify every bound output was written
     publish produced instances
7. materialize requested caller targets from available semantic instances
8. release task-owned handles and scheduler leases at the appropriate lifetime boundary
```

### Illustrative Runner Seam

```python
async def run_invocation(
    invocation: PlannedStageInvocation,
    cancellation: CancellationToken,
) -> tuple[ArtifactInstance[object], ...]:
    input_instances = await resolve_available_inputs(invocation)

    while True:
        cancellation.raise_if_cancelled()
        resolution = await bind_stage_outputs(invocation)

        if isinstance(resolution, StageCacheWait):
            await cancellation.wait_for(
                resolution.cache_wait.wait_handle.wait()
            )
            continue

        break

    if not resolution.writers:
        return resolution.available

    inputs = InputArtifacts.from_instances(input_instances)
    outputs = OutputArtifacts.from_writers(resolution.writers)
    context = build_stage_execution_context(invocation, cancellation)
    executor = invocation.executor_factory()

    try:
        await executor.execute(inputs, outputs, context)
        produced = outputs.produced_instances()
    except BaseException:
        await abort_writers(resolution.writers)
        raise

    return merge_instances(resolution.available, produced)
```

This pseudocode shows the responsibility seam only. The production scheduler additionally records state changes, progress snapshots, resource leases, errors, and cleanup ownership.

### Cache Wait Cancellation

A cache wait is not passed to the stage executor.

It is a scheduler-level blocked condition because no stage output writer can be constructed until the existing cache writer commits or aborts.

The scheduler waits through the task cancellation token:

```python
await cancellation.wait_for(cache_wait.wait_handle.wait())
```

Cancelling this wait stops only this task's wait operation. It does not release, abort, or otherwise modify the reservation owned by the other task.

### Output Validation

After successful execution:

- every bound output writer must have been written exactly once;
- every produced artifact must match its resolved declaration;
- every expected output fingerprint remains the planner-provided fingerprint;
- undeclared scratch files are not semantic artifacts;
- unrequested outputs are ignored unless they were emitted through separately bound declared incidental-output writers.

The initial recommended runner binds only the planned output subset. Incidental output support may bind additional explicitly declared writers, but graph correctness and cache-skip decisions must depend only on the planned requested subset.

### Materialization

Caller-provided file, object, stream, or sink targets are not passed to the producing stage.

After the semantic artifact is available from a newly produced value or cache hit, the runner materializes it through the artifact orchestration contract.

A materialization failure may fail the requested output without invalidating an already committed semantic cache artifact.

## Python Example

This example moves metadata off the executor classes and into explicit registrations.

### Artifact Declarations

```python
from typing import Any

AudioAnalysis = dict[str, Any]
SummarySettings = dict[str, object]
SummaryDocument = dict[str, Any]

SOURCE_AUDIO = ArtifactDeclaration[bytes](
    artifact_id=ArtifactKey.parse("source//audio"),
    role=ArtifactRole.SOURCE,
    version=1,
    codec=BytesCodec(),
    cacheable=False,
)

SUMMARY_SETTINGS = ArtifactDeclaration[SummarySettings](
    artifact_id=ArtifactKey.parse("render//summary_settings"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SummarySettings](),
    cacheable=False,
)

AUDIO_ANALYSIS = ArtifactDeclaration[AudioAnalysis](
    artifact_id=ArtifactKey.parse("analysis//audio_summary#100"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[AudioAnalysis](),
)

SUMMARY_DOCUMENT = ArtifactDeclaration[SummaryDocument](
    artifact_id=ArtifactKey.parse("render//summary_document"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[SummaryDocument](),
)
```

### Executors

```python
class AudioAnalysisExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        context.cancellation.raise_if_cancelled()

        raw = await inputs.get_artifact(SOURCE_AUDIO).read()
        value: AudioAnalysis = {
            "byte_count": len(raw),
            "first_8_hex": raw[:8].hex(),
            "energy_hint": sum(raw) % 100,
        }

        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(AUDIO_ANALYSIS).write(value)


class SummaryExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        analysis = await inputs.get_artifact(AUDIO_ANALYSIS).read()
        settings = await inputs.get_artifact(SUMMARY_SETTINGS).read()

        title = settings.get("title", "Untitled")
        value: SummaryDocument = {
            "title": str(title),
            "summary": (
                f"Audio has {analysis['byte_count']} bytes; "
                f"energy hint is {analysis['energy_hint']}."
            ),
            "analysis": analysis,
        }

        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(SUMMARY_DOCUMENT).write(value)
```

Neither executor exposes stage IDs, capabilities, resource metadata, or planning hooks.

### Planning Hooks

```python
def analysis_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(ArtifactRequirement(SOURCE_AUDIO.artifact_id),),
    )


def summary_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(AUDIO_ANALYSIS.artifact_id),
            ArtifactRequirement(SUMMARY_SETTINGS.artifact_id),
        ),
    )


def fixed_outputs(*capabilities: ArtifactCapability):
    def resolve(
        _context: StagePlanningContext,
    ) -> tuple[ArtifactCapability, ...]:
        return capabilities

    return resolve


def unbound_resources(
    _context: StagePlanningContext,
) -> ResourcePolicy:
    return ResourcePolicy(classes=frozenset({"unbound"}))


def cpu_resources(
    _context: StagePlanningContext,
) -> ResourcePolicy:
    return ResourcePolicy(
        classes=frozenset({"cpu_compute_bound"})
    )
```

### Registrations

```python
AUDIO_ANALYSIS_STAGE = StageRegistration(
    stage_id="analysis.audio",
    display_name="Analyse audio",
    registration_version="1",
    behavior_version="1",
    executor_factory=AudioAnalysisExecutor,
    owned_artifacts=(AUDIO_ANALYSIS,),
    input_capabilities=(
        ArtifactCapability(SOURCE_AUDIO.artifact_id),
    ),
    output_capabilities=(
        ArtifactCapability(AUDIO_ANALYSIS.artifact_id),
    ),
    resolve_requirements=analysis_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(AUDIO_ANALYSIS.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)

SUMMARY_STAGE = StageRegistration(
    stage_id="render.summary",
    display_name="Render summary",
    registration_version="1",
    behavior_version="1",
    executor_factory=SummaryExecutor,
    owned_artifacts=(SUMMARY_SETTINGS, SUMMARY_DOCUMENT),
    input_capabilities=(
        ArtifactCapability(AUDIO_ANALYSIS.artifact_id),
        ArtifactCapability(SUMMARY_SETTINGS.artifact_id),
    ),
    output_capabilities=(
        ArtifactCapability(SUMMARY_DOCUMENT.artifact_id),
    ),
    resolve_requirements=summary_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(SUMMARY_DOCUMENT.artifact_id),
    ),
    resolve_resource_policy=unbound_resources,
)
```

`SOURCE_AUDIO` may instead be owned by a shared core-audio feature. The important rule is that it has one authoritative declaration in the completed manifest.

### Feature Installer and Bootstrap

```python
class SummaryExampleFeature:
    feature_id = "example.summary"

    def install(self, builder: StageManifestBuilder) -> None:
        builder.add_artifact(SOURCE_AUDIO)
        builder.add_stage(AUDIO_ANALYSIS_STAGE)
        builder.add_stage(SUMMARY_STAGE)


pipeline = (
    ChunkyPipeline.builder()
    .install(SummaryExampleFeature())
    .build()
)
```

The frozen manifest now contains the complete planning and execution registration for the two stages. A run request decides whether either stage is actually planned.

## Language-Neutral Contract

Language bindings may use classes, traits, records, function pointers, dependency-injection containers, or generated registration data.

Every binding must preserve these concepts:

```text
bootstrap feature
  -> artifact declarations
  -> immutable stage registration metadata
  -> deterministic planning hooks
  -> executable factory binding
  -> validated frozen manifest
  -> request-specific planned invocations
  -> bound stage execution
```

A Rust implementation might store an `Arc<dyn StageFactory>` and `Arc<dyn RequirementResolver>` inside the runtime manifest while emitting the same portable manifest description used by Python.

## Recommended Invariants

1. Stage executor classes do not act as the authoritative metadata registry.
2. The stage manifest is fully frozen before graph creation begins.
3. Stage IDs are unique within one manifest, and every registration has explicit registration and behavior versions.
4. Artifact declarations have one authoritative compatible definition per artifact key.
5. Every resolved input requirement belongs to the registration's static input capability envelope.
6. Every resolved output capability belongs to the registration's static output capability envelope.
7. Planning hooks are deterministic, side-effect free, and independent of cache and scheduler state.
8. Stages depend on artifact requirements, not upstream stage IDs.
9. Settings are normal artifact inputs and are not duplicated in the execution context.
10. The executor receives already-bound readers and writers and never performs cache lookup or reservation acquisition.
11. The executor writes every bound planned output exactly once or fails.
12. Produced instances are derived from committed bound writers, not a competing stage return value.
13. Cache waits and resource waits are scheduler concerns and occur outside stage execution.
14. Cancellation of a local cache wait never modifies another task's writer reservation.
15. Caller materialization targets are handled after semantic artifact availability and are not passed to the producing stage.
16. The runtime manifest may contain executable language-specific bindings, while its emitted diagnostic description remains portable and deterministic.
17. Enabling a pipeline feature makes stages available; it does not force them into every task graph.
18. Per-run registry overrides create a derived frozen manifest and never mutate the baseline manifest.

## Required Follow-On Alignment

Adopting this stage model requires small corresponding updates in adjacent specifications and examples:

1. The Artifact Model's `InputArtifacts` and `OutputArtifacts` examples should add `contains(...)` and optional lookup methods.
2. Scheduling should distinguish its rich internal planned-invocation record from the narrower stage-facing `StageExecutionContext` defined here.
3. Graph creation should refer to planning hooks stored in `StageRegistration`, rather than methods discovered on a stage executor class.
4. The run contract should describe the pipeline's baseline `StageManifest` as an input to planning and clarify that `registry_overrides` derive a new immutable snapshot.
5. Existing examples that put `input_capabilities`, `output_capabilities`, `stage_id`, or `input_requirements(...)` directly on stage classes should move those values into explicit registrations.
6. The Artifact Model should finish the concrete representation of constrained requirements used for generic artifacts such as `audio//prepared`; the stage registration contract assumes those constraints are carried by `ArtifactRequirement` or an associated graph-owned requirement object.
