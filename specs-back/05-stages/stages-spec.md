# Stage Model
A **stage** is a deterministic unit of runtime behavior that reads already-bound semantic artifacts and writes already-bound semantic artifacts.

This specification defines the stage-facing contract: what a stage registration must declare, what an executor may do at runtime, and which boundaries must remain owned by runtime planning and orchestration.

Runtime bootstrap, manifest ownership, run requests, planning lifecycle, and
runner orchestration are defined across the
[Runtime specifications](../01-runtime/00-design-principles.md). This document
intentionally does not restate those runtime-level mechanics except where a
stage contract depends on them.

## Core Stage Concepts
This specification adopts the following concepts:

1. A **stage registration** is the immutable bootstrap-time contract for one stage implementation.
2. A **stage executor** contains execution behavior only.
3. A **stage invocation** is one concrete, request-specific use of a registered stage after planning has resolved its inputs, output subset, settings, and resource policy.

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
- explicit producer-selection metadata where needed;
- the executor factory used by the scheduler.

### Runtime-Owned Responsibilities

The following remain runtime concerns and are specified across the
[Runtime specifications](../01-runtime/00-design-principles.md):

- feature installation and manifest freezing;
- producer selection and graph creation;
- supplied-artifact binding and default-setting binding;
- fingerprint-provider injection and fingerprint calculation used by planning and bindings;
- cache resolution, reservation, and cache waits;
- scheduler admission and concurrency enforcement;
- output materialization into caller-provided targets.

A stage must not re-implement or bypass those responsibilities.

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

### Input Artifacts

`InputArtifacts` contains readers for the concrete artifacts selected by planning and bound by the runner.

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

Runtime-only configuration such as API keys, provider transport tuning, and scheduler concurrency caps is not a settings artifact. Those values belong to explicit feature/provider configuration and should reach stage code only through injected provider or executor dependencies.

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
- read a generic runtime-settings map or secret store;
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

When a provider requires operational configuration such as an API key, the executor factory should capture a fully configured provider client from explicit feature/provider configuration:

```python
class OpenAiAsrFeature:
    def __init__(self, *, api_key: str, concurrency_limit: int = 4) -> None:
        self._provider = OpenAiAsrProvider(
            api_key=api_key,
            concurrency_limit=concurrency_limit,
        )

    def install(self, manifest_builder, service_locator, env_settings) -> None:
        del service_locator, env_settings
        manifest_builder.add_stage(
            build_openai_asr_stage_registration(
                executor_factory=lambda: AsrExecutor(self._provider)
            )
        )
```

The corresponding runtime setup is explicit:

```python
runtime = (
    ChunkyRuntime.builder()
    .install(ArtifactCacheFeature(cache))
    .install(OpenAiAsrFeature(api_key=env["OPENAI_API_KEY"]))
    .build()
)
```

The API key is operational runtime configuration. It must not be modeled as a semantic artifact input unless the artifact contract explicitly intends credential identity to affect semantic output identity.

`FingerprintProvider` is also a runtime-level injectable dependency, but it belongs on planning and artifact-binding collaborators rather than on stage executors. Stages consume already-bound artifacts and resolved planning results; they do not calculate canonical artifact fingerprints themselves.

Credential values are runtime provider configuration. They must not be copied into the serializable manifest description or semantic fingerprints unless the corresponding artifact contract explicitly requires that behavior.

Likewise, runtime concurrency limits, retry budgets, and transport timeouts must not be treated as stage settings unless changing them is explicitly intended to change semantic output behavior.

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

Cache waits and scheduler resource waits happen before `execute(...)` and are cancellation-aware runtime operations. Cancelling a task waiting for another task's cache writer cancels only the local wait; it does not cancel or modify the other task's reservation.

## Stage Registration

### Registration Model

A stage registration contains the data needed to validate the runtime and plan concrete invocations.

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

Capabilities are required before request-specific planning because producer and consumer candidate graphs are built from them.

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

Before creating this context, planning resolves the settings declarations in the registration's static settings capability envelope. User-supplied settings and declaration defaults can therefore be inspected before the resolver decides which settings artifacts and provider-specific inputs are concrete requirements. A missing settings artifact with no default remains absent from `ResolvedSettingsView`; inspecting it through `require(...)` is a planning error.

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

Output-capability resolution describes what the stage can validly produce under the resolved settings. It does not decide which of those outputs the invocation is asked to produce. Planning fixes the requested output subset from downstream requirements and explicitly requested diagnostics or targets.

### Resource-Policy Resolution

The registration resolves resource requirements during planning:

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

Exact artifact outputs must have at most one registered producer in a frozen manifest. If two stages claim the same output artifact key, manifest validation must fail unless the ambiguity has already been removed before planning, for example by disabling or replacing one stage in a derived manifest.

`concurrency_policy` may declare limits such as:

- maximum concurrent invocations of this registration;
- provider-specific concurrency keys;
- device affinity;
- whether an executor singleton is concurrency-safe.

These values affect selection or scheduling but do not create direct stage-to-stage dependencies.

### Stage Invocation

A planned stage invocation is a per-request use of a registered stage. Planning may create more than one invocation of the same registered stage when effective settings, artifact constraints, or requested output subsets differ.

Common reasons include:

- overridden settings that change the effective settings fingerprint;
- different constrained artifact instances, such as two different prepared-audio variants;
- different requested output subsets, such as one path requiring diagnostics and another requiring only the main output;
- different producer selections in a derived manifest.

Conceptually:

```text
PlannedStageInvocation
  invocation_id
  registration_id / stage_id
  registration_version
  behavior_version
  executor_factory_binding

  input_bindings[]
  requested_output_bindings[]

  effective_settings_fingerprint
  expected_output_fingerprints[]
  resolved_resource_policy
  resolved_concurrency_policy
```

The planned invocation contains a specific requested output subset. The executor receives writers for exactly that subset.

Two graph paths may share one invocation when all identity-affecting values are equivalent, including:

- stage registration;
- registration and behavior versions;
- effective settings fingerprint;
- constrained input requirements;
- selected input artifact fingerprints;
- requested output subset, or a safely shareable superset fixed by planning.

The planner creates separate invocations when these values differ.

## Runtime Integration

### Bootstrap and Manifest Creation

The full bootstrap model lives in the
[Runtime Builder](../01-runtime/01-runtime-builder-surface.md). From the stage
model's perspective, bootstrap must guarantee only these stage-specific
properties before any run begins:

1. Every stage registration in the frozen manifest has a unique `stage_id`.
2. Every registration has non-empty `registration_version` and `behavior_version`.
3. Every input and output capability references a valid artifact declaration or family allowed by the Artifact Model.
4. Every owned artifact is declared consistently with its role and phase behavior.
5. Validation rejects requirement or output resolver results outside the registration's static capability envelopes.
6. Duplicate exact-output producers are rejected unless a derived manifest has already removed the ambiguity.

This stage spec does not redefine builder APIs, feature dependency rules, manifest serialization, or run-scoped manifest override mechanics.

### Planning Integration

The full planning lifecycle is defined in
[Task Planning](../06-planning/01-task-planning.md). The stage-specific
planning contract is:

- planning hooks are deterministic and side-effect free;
- requirement resolution depends only on `StagePlanningContext`;
- output-capability resolution can narrow outputs but cannot invent undeclared ones;
- resource-policy resolution influences scheduler admission only;
- supplied artifacts may satisfy requirements, but cache state does not change graph shape.

Planning hooks must not:

- read cache availability;
- inspect current GPU or CPU load;
- contact a remote model provider;
- mutate the builder or manifest;
- allocate task-owned cache or resource leases;
- depend on wall-clock time or random values.

### Execution Integration

The full runner and scheduler flow is defined in
[Run Binding and Execution](../01-runtime/99-internals/05-run-binding-and-execution.md).
The stage-facing runtime boundary is:

- the runner waits for prerequisites, resolves cache state, and binds readers and writers before `execute(...)`;
- the executor never receives caller materialization targets directly;
- cache waits and scheduler resource waits occur outside stage execution;
- after successful execution, produced artifacts are validated and published by the runner.

## Python Example

This example keeps metadata off executor classes and in explicit registrations.

### Artifact Declarations

```python
from typing import Any

AudioAnalysis = dict[str, Any]
SummarySettings = dict[str, object]
SummaryDocument = dict[str, Any]

SOURCE_AUDIO = ArtifactDeclaration[bytes](
    artifact_id=ArtifactKey.from_a_ref("source/audio"),
    role=ArtifactRole.SOURCE,
    version=1,
    codec=BytesCodec(),
    cacheable=False,
)

SUMMARY_SETTINGS = ArtifactDeclaration[SummarySettings](
    artifact_id=ArtifactKey.from_a_ref("render/summary_settings"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SummarySettings](),
    cacheable=False,
)

AUDIO_ANALYSIS = ArtifactDeclaration[AudioAnalysis](
    artifact_id=ArtifactKey.from_a_ref("analysis/audio_summary/100"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[AudioAnalysis](),
)

SUMMARY_DOCUMENT = ArtifactDeclaration[SummaryDocument](
    artifact_id=ArtifactKey.from_a_ref("render/summary_document"),
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



class AudioAnalysisExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        context.cancellation.raise_if_cancelled()

        raw = await inputs.get_artifact(SOURCE_AUDIO).next().read()
        value: AudioAnalysis = {
            "byte_count": len(raw),
            "first_8_hex": raw[:8].hex(),
            "energy_hint": sum(raw) % 100,
        }

        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(AUDIO_ANALYSIS).write(value)


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
13. Cache waits and resource waits are runtime concerns and occur outside stage execution.
14. Caller materialization targets are handled after semantic artifact availability and are not passed to the producing stage.
15. Enabling a runtime feature makes stages available; it does not force them into every task graph.

## Required Follow-On Alignment

Adopting this stage model requires small corresponding updates in adjacent specifications and examples:

1. The Artifact Model's `InputArtifacts` and `OutputArtifacts` examples should add `contains(...)` and optional lookup methods.
2. Scheduling should distinguish its rich internal planned-invocation record from the narrower stage-facing `StageExecutionContext` defined here.
3. Graph creation should refer to planning hooks stored in `StageRegistration`, rather than methods discovered on a stage executor class.
4. Existing examples that put `input_capabilities`, `output_capabilities`, `stage_id`, or `input_requirements(...)` directly on stage classes should move those values into explicit registrations.
5. The Artifact Model should finish the concrete representation of constrained requirements used for generic artifacts such as `audio/prepared`; the stage registration contract assumes those constraints are carried by `ArtifactRequirement` or an associated graph-owned requirement object.
