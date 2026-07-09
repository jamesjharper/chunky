# Pipeline Model

A **pipeline** is the configured runtime facade that turns a frozen `StageManifest`, configured runtime services, and a `RunRequest` into one concrete task execution.

The pipeline model separates:

- bootstrap-time feature installation;
- immutable manifest creation and validation;
- request normalization and supplied-artifact binding;
- request-specific planning;
- runtime execution, cache coordination, and materialization.

The pipeline is not itself a stage, planner, cache, scheduler, or dependency-injection container. It composes those pieces behind one run-oriented API.

## Core Pipeline Concepts

1. A **pipeline builder** collects explicitly installed pipeline features before the pipeline is frozen.
2. A **configured pipeline** owns one baseline immutable `StageManifest` and configured runtime services.
3. A **run request** declares caller-supplied inputs, requested outputs, optional run policy, and optional per-artifact cache policy.
4. A **task execution** normalizes the request, binds supplied artifacts, lets planning resolve relevant defaults, builds a task graph, executes it, and materializes requested targets.
5. A **run handle** is the in-process asynchronous control surface for detached execution, cancellation, progress inspection, and completion.

## Pipeline Builder

The builder is the bootstrap-time mutable facade for pipeline configuration.

Its job is to collect explicitly installed features and freeze the resulting configuration. It should remain a thin composition surface, not a full dependency-injection container, reflective service locator, or arbitrary object graph builder.

Conceptual Python shape:

```python
class ChunkyPipelineBuilder:
    def __init__(self) -> None:
        self._manifest_builder = StageManifestBuilder()
        self._runtime_services = PipelineRuntimeServicesBuilder()
        self._built = False
        self._installed_feature_ids: set[str] = set()

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        if self._built:
            raise RuntimeError("pipeline builder cannot be used after build()")
        if feature.feature_id in self._installed_feature_ids:
            raise ValueError(f"duplicate pipeline feature: {feature.feature_id}")

        self._installed_feature_ids.add(feature.feature_id)
        feature.install(
            manifest_builder=self._manifest_builder,
            runtime_services=self._runtime_services,
        )
        return self

    def build(self) -> "ChunkyPipeline":
        if self._built:
            raise RuntimeError("pipeline builder cannot be built more than once")
        self._built = True

        manifest = self._manifest_builder.freeze()
        runtime_services = self._runtime_services.freeze()
        return ChunkyPipeline(
            stage_manifest=manifest,
            runtime_services=runtime_services,
        )
```

The builder is responsible for:

- accepting explicit feature installation;
- rejecting duplicate feature installation by default;
- rejecting conflicting core runtime service slots;
- collecting runtime services installed by features;
- freezing and validating the underlying `StageManifestBuilder`;
- creating the configured runtime pipeline;
- hard-failing on any mutation or second `build()` after the first successful build.

The public configuration path is feature installation. Direct constructor or builder injection of runtime collaborators is not a peer public configuration model; it may exist only as a narrow implementation, test, or migration escape hatch.

## Pipeline Features

A **pipeline feature** is the bootstrap-time installation unit for a coherent slice of pipeline configuration.

Most features install only artifact declarations and stage registrations. A smaller number of specialist features may install core runtime services such as the artifact cache, fingerprint provider, planner factory, runner factory, or bindings-provider factory.

A feature may install:

- artifact declarations;
- stage registrations;
- default-source providers attached to artifact declarations;
- provider bindings or executor factories;
- fingerprinting, cache, planner, runner, or binding-provider services;
- diagnostics, telemetry, or runtime support services.

Features are self-contained installers. They contribute fully constructed registrations, declarations, or service instances; they do not emit abstract fragments to be assembled later by a general dependency-injection framework.

Feature installation makes capabilities available to planning and execution. It does not force those stages into every task graph.

### Feature Composition Rules

Feature composition is intentionally simple:

- required features must be installed explicitly by the caller;
- features do not declare explicit dependencies on other features;
- the system does not auto-install missing features;
- installing the same feature twice is a hard error by default;
- two specialist features installing the same core service slot is a hard error;
- missing artifact declarations are surfaced during manifest creation;
- missing request-specific producer paths are surfaced during planning.

This keeps failures located on the real semantic surfaces: invalid manifest topology or unsatisfied request-specific graph reachability.

## Manifest Creation and Validation

`build()` is the bootstrap boundary at which the manifest is frozen.

`StageManifest` creation is a strong validation boundary. It must reject structurally invalid stage and artifact topology before any run can begin. Static validation should catch as much as possible without solving every future request.

Manifest creation must validate at least:

- unique stage IDs;
- unique compatible artifact declarations;
- stage capability references to known artifact declarations or valid artifact families;
- invalid role or phase usage;
- duplicate exact-output producers unless ambiguity has already been removed before freezing;
- resolver outputs that are statically outside a stage's advertised capability envelope, where such validation is possible.

Request-dependent producer-path failures remain planner errors. Static structural impossibilities should not be deferred to execution.

### Manifest Identity Boundary

Only semantic, planning-visible configuration contributes to manifest identity.

Runtime mechanics such as cache backend choice, runner implementation, task IDs, file paths, lock strategies, and deployment wiring remain configured pipeline state outside manifest identity unless they can change graph shape, artifact identity, or semantic outputs.

## Configured Pipeline

A configured pipeline owns:

- one baseline immutable `StageManifest`;
- configured runtime services for cache, fingerprinting, artifact bindings, planning, running, diagnostics, and related orchestration.

Conceptual shape:

```python
class ChunkyPipeline:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        runtime_services: PipelineRuntimeServices,
    ) -> None:
        self.stage_manifest = stage_manifest
        self._runtime_services = runtime_services
```

Every task planned by the pipeline starts from the baseline manifest. The baseline manifest is never mutated during a run.

Per-run manifest overrides are out of scope for this public contract. If such a mechanism is ever introduced, it must be specified as derived immutable manifest creation, not mutation of the baseline manifest.

## Run Request

A `RunRequest` is the caller-facing execution contract.

```python
@dataclass(frozen=True)
class RunRequest:
    inputs: Mapping[ArtifactKey | str, ArtifactSourceHandle[Any] | Mapping[str, Any]]
    outputs: Mapping[
        ArtifactKey | str,
        ArtifactTargetHandle[Any]
        | Sequence[ArtifactTargetHandle[Any]]
        | Mapping[str, Any],
    ]
    run_policy: PlanningRunPolicy | None = None
    cache_policy: Mapping[
        ArtifactKey | str,
        PerArtifactCachePolicy | Mapping[str, Any],
    ] | None = None
```

The request contains:

- `inputs`: caller-supplied source, settings, or explicitly supplied intermediate handles;
- `outputs`: caller-requested materialization targets for semantic artifacts;
- `run_policy`: optional planning and execution-strategy policy;
- `cache_policy`: optional per-artifact cache-use intent.

`run_policy` may influence planning, producer selection, scheduling, or execution strategy. It must not suppress requested outputs or weaken materialization obligations.

## Request Artifact Addressing

`inputs`, `outputs`, and `cache_policy` use the same artifact-address normalization model.

Callers may use direct `ArtifactKey` entries or nested string mappings. Nested mappings normalize into concrete artifact IDs using these path forms:

```text
DOMAIN//KIND
OVERRIDE_DOMAIN//DOMAIN//KIND
```

Normalization rules:

- nested keys must be strings;
- direct `ArtifactKey` entries must map directly to values, not nested mappings;
- normalized paths must resolve to two or three path components;
- duplicate artifact IDs are rejected;
- structural validation errors across inputs, outputs, and cache policy should be accumulated and reported together.

Example input shape:

```python
inputs = {
    "source": {
        "audio": FileSource(audio_path),
    },
    "settings": {
        "asr": InMemorySource({"model": "placeholder-asr"}),
        "language": InMemorySource({"language": "en"}),
    },
    "speech_processing": {
        "audio_preprocessing": {
            "audio_settings": InMemorySource({"sample_rate_hz": 16000}),
        },
    },
}
```

Example output shape:

```python
outputs = {
    "render": {
        "webvtt_transcript": AtomicFileTargetHandle(output_path),
    },
}
```

## Output Requests and Materialization

Callers may request materialization for any semantic artifact, including intermediates, provided the artifact is known to the manifest and materializable to the requested target handle.

One artifact may be materialized to multiple caller targets in the same run:

```python
outputs = {
    "render//webvtt_transcript": [
        AtomicFileTargetHandle("output.vtt"),
        InMemoryTargetHandle(),
    ],
}
```

Fan-out rules:

- a single target handle is normalized as a one-item target list;
- multiple target handles are represented as an ordered list under one artifact key;
- list order is semantically meaningful and must be preserved for materialization, diagnostics, and cancellation reporting;
- caller target handles are not passed to producing stage executors;
- caller target paths or sinks are not part of semantic artifact identity or cache keys.

If one target materialization fails, the run fails as a whole while reporting which targets succeeded and which failed. Successful target writes are left in place; rollback is not promised. If cancellation arrives during materialization, the runner should stop remaining target writes as soon as practical.

## Cache Policy

`cache_policy` is an explicit per-run, per-artifact policy surface. It expresses semantic cache-use intent, not backend-specific operational knobs.

A cache policy entry targets one exact concrete artifact ID. Wildcards, prefixes, domain-wide policy, and family-wide expansion are out of scope.

Conceptual shape:

```python
@dataclass(frozen=True)
class PerArtifactCachePolicy:
    ignore_existing_cache: bool = False
    do_not_cache: bool = False
    desired_ttl: timedelta | None = None
```

Directive semantics are orthogonal:

- `ignore_existing_cache` affects read/reuse for this run only;
- `do_not_cache` affects write or publish-to-cache behavior for this run only;
- `desired_ttl` applies only when creating a new cache entry and never mutates an existing entry's TTL.

Validation rules:

- unknown artifact IDs are request validation errors;
- known but unreachable artifacts are ignored for the run and surfaced as diagnostics;
- policy may target inputs, requested outputs, or intermediates that are known to the manifest;
- the original TTL of an existing cache entry persists even if a later request names a different desired TTL.

## Public Run Surfaces

The configured pipeline exposes two run-oriented APIs.

### `run(...)`

`run(...)` executes a task and awaits completion directly.

```python
async def run(
    self,
    request: RunRequest,
    *,
    cancellation: CancellationSource | None = None,
) -> TaskRunResult:
    ...
```

On success, it returns a completed `TaskRunResult`. On failure, it raises a structured exception carrying diagnostics and any successful partial outcomes that are available.

### `run_away(...)`

`run_away(...)` starts the task in the current process and returns a run handle:

```python
def run_away(self, request: RunRequest) -> RunHandle[TaskRunResult]:
    ...
```

The handle exposes:

- generated `task_id`;
- cancellation access;
- progress inspection and streaming;
- awaitable typed terminal completion.

`run_away(...)` is strictly in-process and non-durable. It does not promise resumable job state across process restarts. Re-running a semantically identical job after restart may skip completed work through normal cache reuse, but that is an expected consequence of cache behavior, not a separate resumability guarantee.

`run(...)` and `run_away(...)` share one execution core. They differ in lifecycle exposure and failure-surface ergonomics: direct `run(...)` raises on failure, while detached completion resolves to a typed terminal result.

## Execution Lifecycle

The pipeline exposes one execution seam that turns an accepted `RunRequest` plus run-scoped control surfaces into one concrete task execution.

Conceptual lifecycle:

1. Normalize and structurally validate request inputs, outputs, and cache policy.
2. Derive task-scoped runtime collaborators, including cache view, bindings provider, planner, runner, cancellation, and progress surfaces.
3. Bind caller-supplied inputs into supplied artifact instances.
4. Provide planning with supplied instances, requested outputs, run policy, cache policy, and an explicit settings-default resolver interface.
5. Let the planner discover relevant settings and request default resolution on demand.
6. Plan the task graph from the frozen manifest and request-specific context.
7. Register planned invocations with the progress tracker after planning succeeds.
8. Execute the plan through the configured runner.
9. Materialize requested caller targets from available semantic artifact instances.
10. Return success, or surface structured failure diagnostics and partial outcomes.

If planning fails after supplied artifacts have already been bound, the pipeline must close every bound supplied instance it holds for that run before surfacing the error.

## Supplied Inputs

Caller-supplied request inputs become bound `ArtifactInstance` values before planning.

```python
async def bind_supplied_inputs(
    artifact_bindings_provider: ArtifactBindingsProvider,
    input_sources: Mapping[ArtifactKey, ArtifactSourceHandle[Any]],
) -> dict[ArtifactKey, ArtifactInstance[Any]]:
    supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}

    for artifact_id, source in input_sources.items():
        declaration = stage_manifest.find_artifact(artifact_id)
        effective_source = await resolve_supplied_settings_source_if_needed(
            declaration,
            source,
        )
        supplied[artifact_id] = await artifact_bindings_provider.bind_supplied(
            declaration,
            effective_source,
        )

    return supplied
```

Supplied settings are still normal artifact roots. They are fingerprinted from their resolved effective value and participate in planning and produced-artifact pedigree like any other effective setting.

## Lazy Default Settings Resolution

Default settings are treated semantically as supplied graph-root values, but they should be resolved only when planning determines the setting is relevant to the request-specific graph.

The planner owns relevance discovery. The pipeline owns binding mechanics once a setting has been deemed relevant.

Conceptual seam:

```python
class SettingsDefaultResolver(Protocol):
    async def resolve_default_setting(
        self,
        artifact_id: ArtifactKey,
    ) -> ArtifactInstance[Any]: ...
```

Planner behavior:

- inspect settings only through an explicit resolved-settings view or resolver interface;
- request defaults on demand for settings relevant to a candidate invocation or graph branch;
- treat resolved defaults exactly like supplied graph roots after resolution;
- include effective settings fingerprints in stage pedigree when the setting affects production.

Default-source providers must remain deterministic and local to the setting declaration plus optional caller-supplied override. They must not inspect broader request shape, planner state, cache state, wall-clock time, resource availability, or other hidden context.

Conceptual provider shape:

```python
class DefaultValueSourceProvider(Protocol[T]):
    async def resolve_source(
        self,
        source: ArtifactSourceHandle[T] | None,
        *,
        declaration: ArtifactDeclaration[T],
    ) -> ArtifactSourceHandle[T]: ...
```

This keeps defaulting behavior attached to the artifact declaration while avoiding eager binding of irrelevant settings.

## Planner and Runner Construction

The pipeline builds planner- and runner-facing collaborators from the baseline manifest plus task-scoped runtime views.

Conceptual defaults:

```python
class PipelineRuntimeServices:
    artifact_cache: ArtifactCache
    fingerprint_provider: FingerprintProvider
    artifact_bindings_provider_factory: ArtifactBindingsProviderFactory
    planner_factory: PlannerFactory
    runner_factory: RunnerFactory
```

The pipeline does not embed planning, scheduling, cache-resolution, or materialization logic directly. It wires together the configured collaborators for one request.

Runtime collaborators may be installed by features. Their runtime identity remains configured pipeline state unless the choice changes semantic planning-visible behavior.

## Task-Scoped Cache Views

The pipeline may derive a task-specific cache facade before binding or execution:

```python
def cache_for_task(cache: ArtifactCache, task_id: str) -> ArtifactCache:
    for_task = getattr(cache, "for_task", None)
    if callable(for_task):
        return for_task(task_id)
    return cache
```

This allows cache implementations to provide task-scoped ownership, diagnostics, or bookkeeping without changing the public pipeline API.

## Progress and Failure Reporting

The pipeline registers planned invocations after planning succeeds and before runner execution begins.

```python
def register_plan_progress(
    progress_tracker: RunProgressTracker,
    plan: TaskPlan,
) -> None:
    with progress_tracker.batch_update():
        for invocation in plan.invocations:
            progress_tracker.register_stage(
                invocation.invocation_id,
                invocation.registration.stage_id,
                invocation.registration.display_name,
            )
```

There is no pre-plan progress reporting in this contract. Request normalization, supplied-input binding, and planning may fail before any invocation progress exists.

For `run_away(...)`, pre-plan failures must still produce an observable terminal failed state with phase-specific diagnostics. For both run surfaces, failures after partial production or materialization should surface successful partial outcomes alongside the error.

## Responsibility Boundaries

### Pipeline Responsibilities

The pipeline is responsible for:

- owning the baseline frozen manifest and configured runtime services;
- accepting run requests;
- normalizing and validating request artifact mappings;
- binding caller-supplied inputs;
- providing an on-demand default-settings resolver to planning;
- constructing task-scoped planner and runner instances;
- initiating planning and execution;
- registering plan progress after planning succeeds;
- connecting task execution to cancellation and progress surfaces;
- surfacing structured diagnostics and partial outcomes.

### Pipeline Non-Responsibilities

The pipeline does not:

- mutate the frozen baseline manifest during a run;
- behave as a dependency-injection container;
- auto-install missing features;
- decide stage-internal execution behavior;
- perform stage-local cache reads or writes directly;
- replace planner graph logic with ad hoc stage chaining;
- materialize semantic outputs by bypassing the runner;
- pass caller output targets directly into stage executors;
- provide durable cross-process background jobs.

### Planner Responsibilities

The planner is responsible for:

- selecting producer paths from the frozen manifest;
- resolving request-specific graph structure;
- resolving settings- and requirement-driven stage requirements;
- discovering which default settings are relevant;
- requesting relevant defaults through the explicit resolver interface;
- computing the concrete task plan and planned output fingerprints.

### Runner Responsibilities

The runner is responsible for:

- cache resolution, waiting, and reservation behavior;
- binding stage inputs and semantic outputs;
- invoking executors;
- publishing produced semantic artifacts;
- honoring cancellation during execution and materialization;
- materializing requested caller targets;
- reporting partial materialization success or failure.

## Demo Flow

A typical demo flow is:

1. Create and install feature packages, including stages and required runtime services such as a cache feature.
2. Call `build()` to freeze and validate the manifest and runtime service slots.
3. Construct a `RunRequest` with nested input mappings and one or more requested output target lists.
4. Use `run_away(...)` when detached in-process lifecycle control and progress inspection are needed.
5. Use `run(...)` when direct await-and-raise semantics are preferred.
6. Rerun semantically equivalent requests to observe normal cache reuse.

## Recommended Invariants

1. A configured pipeline owns one baseline immutable `StageManifest`.
2. `build()` is the bootstrap boundary at which the manifest and runtime service configuration are frozen.
3. The builder is single-use and hard-fails after the first successful `build()`.
4. `PipelineFeature` is the canonical public installation mechanism for stage packages and runtime services.
5. Features are self-contained installers, not DI fragments.
6. Duplicate feature installation is a hard error by default.
7. Conflicting core service-slot installation is a hard error.
8. The system does not auto-install feature dependencies.
9. `StageManifest` creation is a strong validation boundary for static topology errors.
10. Request artifact mappings normalize deterministically into concrete `ArtifactKey` values before planning.
11. Request structural validation aggregates deterministic shape errors.
12. A `RunRequest` contains caller-supplied inputs, requested outputs, optional run policy, and optional per-artifact cache policy.
13. `run_policy` must not suppress requested outputs.
14. `cache_policy` targets exact concrete artifact IDs only.
15. Unknown cache-policy artifact IDs fail validation; known unreachable ones produce diagnostics.
16. Supplied inputs are bound before graph planning begins.
17. Default settings are resolved lazily for relevant settings only.
18. Resolved defaults behave exactly like supplied graph roots.
19. Settings remain normal artifact inputs and are not duplicated into pipeline-specific side channels.
20. Runtime collaborators may be installed through features but remain outside manifest identity unless planning-visible semantics change.
21. `run(...)` and `run_away(...)` share one execution core.
22. `run_away(...)` is in-process and non-durable.
23. Cache reuse after restart is a consequence of cache identity, not a resumable-job guarantee.
24. The pipeline must close all bound supplied instances it holds if planning fails before runner handoff.
25. Progress registration begins after planning succeeds.
26. Pre-plan failures still surface terminal failure diagnostics for detached runs.
27. Failed runs should surface successful partial outcomes where available.
28. Caller output fan-out preserves target list order.
29. A materialization failure fails the run but does not invalidate a committed semantic artifact.
30. Successful materialization side effects are reported, not rolled back.
31. Per-run manifest overrides are out of scope for this public contract.
