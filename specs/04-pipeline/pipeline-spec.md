# Pipeline Model
A **pipeline** is the configured runtime facade that turns a frozen stage manifest, an artifact cache, and a run request into one concrete task execution.

The pipeline model gives the system a stable way to separate:

- bootstrap-time feature installation;
- immutable manifest creation;
- request normalization and supplied-artifact binding;
- request-specific planning;
- runtime execution and materialization.

The pipeline is not itself a stage, planner, cache, or scheduler. It composes those pieces behind one run-oriented API.

## Core Pipeline Concepts
This specification adopts the following concepts:

1. A **pipeline builder** collects pipeline features before the pipeline is frozen.
2. A **configured pipeline** owns one baseline immutable `StageManifest` and configured service dependencies.
3. A **run request** declares caller-supplied inputs, requested outputs, and optional run policy.
4. A **task execution** normalizes the request, binds supplied artifacts, applies default settings, plans a task graph, and runs it.
5. A **run handle** is the asynchronous control surface for detached execution, cancellation, and progress inspection.

## Pipeline Builder
The builder is the bootstrap-time mutable facade for pipeline configuration.

Its job is to collect installed features and freeze the resulting configuration. It should remain a thin composition surface, not a full dependency-injection container.

Current Python shape:

```python
class ChunkyPipelineBuilder:
    def __init__(self, *, artifact_cache: ArtifactCache) -> None:
        self._artifact_cache = artifact_cache
        self._manifest_builder = StageManifestBuilder()

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        self._manifest_builder.install_feature(feature)
        return self

    # Extensions methods 

    def build(self) -> "ChunkyPipeline":
        manifest = self._manifest_builder.freeze()
        return ChunkyPipeline(
            stage_manifest=manifest,
            artifact_cache=self._artifact_cache,
            # ... other configured runtime collaborators
        )
```

The builder is responsible for:
- accepting feature installation;
- collecting lightweight runtime configuration installed by features;
- freezing the underlying `StageManifestBuilder`;
- creating the configured runtime pipeline.

The builder must not be used after `build()` has emitted the frozen manifest.

## Configured Pipeline
A configured pipeline owns:

- the baseline frozen `StageManifest`;
- the configured services for the artifact bindings provider, fingerprint provider, cache, planner, and runner.

```python
class ChunkyPipeline:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        artifact_cache: ArtifactCache,
        fingerprint_provider: FingerprintProvider | None = None,
        artifact_bindings_provider_factory: ArtifactBindingsProviderFactory | None = None,
        planner_factory: PlannerFactory | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        ...
```

The baseline manifest is immutable. Every task planned by the pipeline starts from that manifest.

The cache, fingerprinting service, and runtime factories may be installed through features or supplied directly. They are part of the configured pipeline state, even though they are not part of the stage manifest itself.

`ChunkyPipeline.builder(...)` is the preferred public bootstrap seam:

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(SummaryExampleFeature())
    .install(FileBasedArtifactCacheFeature())
    .build()
)
```

More ergonomically, depending on language capabilities this can be expressed via externally added method signatures which internally map to equivalent `.install(Feature())` statements.

```python
pipeline = (
    ChunkyPipeline.builder()
    .use_summary_example_stage()
    .use_file_based_artifact_cache()
    .build()
)
```

## Pipeline Features
A pipeline feature is the primary bootstrap-time installation unit for any coherent slice of pipeline configuration.

A feature may install:

- artifact declarations;
- one or more stage registrations;
- defaults;
- provider bindings;
- a `FingerprintProvider` or equivalent fingerprinting service;
- Services such as artifact-cache, planner, runner, or bindings-provider factories;
- other runtime services needed by the configured pipeline;
- prerequisite declaration-only shared features.

A feature may configure semantic pipeline behavior, runtime wiring, or both.

Feature installation makes capabilities available to planning and execution. It does not force those stages into every task graph.

### Feature-As-Configuration
The recommended model is that pipeline configuration concerns are all expressed as installable features.

For example, all of the following may be installed as features:

- a artifact cache; For example a file based or a s3 based or network share
- a specific `FingerprintProvider`;
- a specific planner factory;
- a specific runner factory;
- a bindings-provider factory;
- a provider-backed ASR or diarization package;
- a diagnostics or telemetry package.

Conceptually:

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(FileCacheFeature(cache_root))
    .install(DefaultFingerprintProviderFeature())
    .install(DefaultBindingsFeature())
    .install(DefaultPlannerFeature())
    .install(DefaultRunnerFeature())
    .install(SummaryExampleFeature())
    .build()
)
```

Features install known pipeline configuration records into the builder; the builder freezes those records into the configured pipeline. The builder should not grow into a full-fledged service locator, arbitrary object graph builder, or reflective DI framework.

## Run Request
A run request is the caller-facing execution contract.

Current Python shape:

```python
class RunRequest:
    inputs: Mapping[
        ArtifactKey | str,
        ArtifactSourceHandle[Any] | Mapping[str, ArtifactSourceHandle[Any]],
    ]
    outputs: Mapping[
        ArtifactKey | str,
        ArtifactTargetHandle[Any] | Mapping[str, ArtifactTargetHandle[Any]],
    ]
    run_policy: PlanningRunPolicy | None = None,
    cache_policy: CachePolicy | None = None
```

The request contains three things:

- `inputs`: caller-supplied source or settings handles;
- `outputs`: caller-requested target or diagnostic handles;
- `run_policy`: optional planning-time policy input.
- `cache_policy`: optional cache policy input.

## Request Artifact Shape
Run requests may use either direct `ArtifactKey` entries or nested string mappings.

Nested mappings normalize into `ArtifactKey` values using one of these path forms:

```text
DOMAIN//KIND
OVERRIDE_DOMAIN//DOMAIN//KIND
```

Illustrative input shape from the demo:

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

Illustrative output shape from the demo:

```python
outputs = {
    "render": {
        "webvtt_transcript": AtomicFileTargetHandle(output_path),
    },
}
```

Normalization is performed before planning:

```python
def _flatten_request_artifacts(
    artifacts: Mapping[ArtifactKey | str, Any],
) -> dict[ArtifactKey, Any]:
    flattened: dict[ArtifactKey, Any] = {}
    for domain_or_key, value in artifacts.items():
        _flatten_request_value(flattened, (), domain_or_key, value)
    return flattened
```

The request-normalization contract is:

- nested keys must be strings;
- direct `ArtifactKey` entries must map directly to handles, not nested mappings;
- normalized paths must resolve to either two or three path components;
- duplicate artifact IDs are rejected.

## Public Run Surfaces
The configured pipeline exposes two run-oriented APIs.

### `run(...)`
`run(...)` executes a task and awaits the final result directly.

Current Python shape:

```python
async def run(
    self,
    request: RunRequest,
    *,
    cancellation: CancellationSource | None = None,
) -> TaskRunResult:
    task_id = f"task-{uuid4().hex[:12]}"
    return await self._execute(
        request,
        task_id=task_id,
        cancellation=cancellation or CancellationSource(),
        progress_tracker=ensure_progress_tracker(task_id, None),
    )
```

### `run_away(...)`
`run_away(...)` starts the task in the background and returns a run handle with:

- the generated `task_id`;
- cancellation access;
- progress streaming and inspection;
- awaitable completion.

```python
def run_away(self, request: RunRequest) -> RunHandle[TaskRunResult]:
    task_id = f"task-{uuid4().hex[:12]}"
    cancellation = CancellationSource()
    dispatcher = ProgressDispatcher()
    tracker = RunProgressTracker(task_id, dispatcher)
    ...
```

`run(...)` and `run_away(...)` share the same execution core. They differ only in how they expose lifecycle control and progress to the caller.

## Execution Lifecycle
The pipeline must expose one execution seam that turns an accepted `RunRequest` plus run-scoped control surfaces into one concrete task execution.

Conceptually the execution lifecycle is:

1. Normalize request inputs and outputs into concrete `ArtifactKey` maps.
2. Derive any run-scoped runtime collaborators needed for cache access, artifact binding, fingerprinting, planning, and execution.
3. Bind caller-supplied inputs into supplied artifact instances.
4. Distinguish explicitly supplied settings from defaults and bind any declared defaults that were not supplied explicitly.
5. Plan the task graph from the frozen manifest, supplied instances, requested outputs, and run policy.
6. Register planned invocations with the progress tracker or equivalent run-observation surface.
7. Execute the plan through the configured runner.

If planning fails after artifacts have already been bound, the pipeline must close or release any run-scoped bound resources it already owns before surfacing the error.

## Supplied Inputs and Default Settings
Caller-supplied request inputs become bound `ArtifactInstance` values before planning.

Current Python seam:

```python
async def _bind_supplied_inputs(
    self,
    artifact_bindings_provider: ArtifactBindingsProvider,
    input_sources: Mapping[ArtifactKey, ArtifactSourceHandle[Any]],
) -> tuple[dict[ArtifactKey, ArtifactInstance[Any]], set[ArtifactKey]]:
    supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}
    user_supplied_settings: set[ArtifactKey] = set()

    for artifact_id, source in input_sources.items():
        declaration = self.stage_manifest.find_artifact(artifact_id)
        if declaration.role is ArtifactRole.SETTINGS:
            user_supplied_settings.add(artifact_id)
        effective_source = await self._resolve_settings_source(
            declaration,
            source,
        )

        supplied[artifact_id] = await artifact_bindings_provider.bind_supplied(
            declaration,
            effective_source,
        )
    return supplied, user_supplied_settings
```

Default settings are treated as graph-root supplied values:

```python
async def _bind_default_settings(
    self,
    artifact_bindings_provider: ArtifactBindingsProvider,
    supplied: dict[ArtifactKey, ArtifactInstance[Any]],
) -> dict[ArtifactKey, ArtifactInstance[Any]]:
    with_defaults = dict(supplied)
    for artifact_id, declaration in self.stage_manifest.artifacts.items():
        if (
            declaration.role is ArtifactRole.SETTINGS
            and declaration.default_value_source_provider is not None
            and artifact_id not in with_defaults
        ):
            effective_source = await self._resolve_settings_source(
                declaration,
                None,
            )
            with_defaults[artifact_id] = await artifact_bindings_provider.bind_supplied(
                declaration,
                effective_source,
            )
    return with_defaults
```

This means:

- settings remain artifact values, not special planner-side metadata;
- explicitly supplied settings can be distinguished from defaults;
- declared defaults participate in planning exactly like caller roots once resolved.

## Settings Source Resolution
Settings declarations may resolve values through a default-source provider.

Current Python seam:

```python
async def _resolve_settings_source(
    self,
    declaration: ArtifactDeclaration[Any],
    source: ArtifactSourceHandle[Any] | None,
) -> ArtifactSourceHandle[Any]:
    if (
        declaration.role is not ArtifactRole.SETTINGS
        or declaration.default_value_source_provider is None
    ):
        if source is None:
            raise ValueError(
                f"missing source for artifact without defaults: {declaration.artifact_id}"
            )
        return source
    return await declaration.default_value_source_provider.resolve_source(
        source,
        declaration=declaration,
    )
```

This keeps defaulting behavior with the artifact declaration rather than hard-coding it into individual stages.

## Planner and Runner Construction
The pipeline builds planner- and runner-facing collaborators from the baseline manifest plus a task-scoped cache view.

Current Python defaults:

```python
@staticmethod
def _default_artifact_bindings_provider_factory(
    cache: ArtifactCache,
    fingerprint_provider: FingerprintProvider,
) -> ArtifactBindingsProvider:
    return ArtifactBindingsProvider(
        cache,
        fingerprint_provider=fingerprint_provider,
    )

@staticmethod
def _default_planner_factory(
    stage_manifest: StageManifest,
    fingerprint_provider: FingerprintProvider,
) -> TaskRunPlanner:
    return TaskRunPlanner(
        stage_manifest,
        fingerprint_provider,
    )

@staticmethod
def _default_runner_factory(
    stage_manifest: StageManifest,
    artifact_bindings_provider: ArtifactBindingsProvider,
) -> ExampleTaskRunner:
    return ExampleTaskRunner(
        manifest=stage_manifest,
        artifact_bindings_provider=artifact_bindings_provider,
    )
```

The pipeline does not embed planning or scheduling logic directly. It wires together the planning and runtime components for one request, including the configured fingerprinting service used to derive stable settings and artifact fingerprints.

Those runtime collaborators may come from default builder configuration or from installed configuration features.

## Task-Scoped Cache Views
The pipeline may derive a task-specific cache facade before binding or planning.

Current Python seam:

```python
def _cache_for_task(self, task_id: str) -> ArtifactCache:
    for_task = getattr(self._artifact_cache, "for_task", None)
    if callable(for_task):
        return for_task(task_id)
    return self._artifact_cache
```

This allows cache implementations to provide task-scoped ownership or bookkeeping without changing the public pipeline API.

## Progress Registration
The pipeline registers planned invocations with the run progress tracker after planning succeeds and before the runner begins execution.

Current Python seam:

```python
@staticmethod
def _register_plan_progress(
    progress_tracker: RunProgressTracker,
    plan: Any,
) -> None:
    with progress_tracker.batch_update():
        for invocation in plan.invocations:
            progress_tracker.register_stage(
                invocation.invocation_id,
                invocation.registration.stage_id,
                invocation.registration.display_name,
            )
```

The pipeline is responsible for surfacing plan structure to progress reporting. Individual stages still report invocation-local execution progress through their stage context.

## Responsibility Boundaries

### Pipeline Responsibilities
The pipeline is responsible for:

- owning the baseline frozen manifest and runtime factories;
- accepting run requests;
- normalizing request artifact mappings;
- binding caller-supplied inputs and default settings;
- constructing task-scoped planner and runner instances;
- initiating planning and execution;
- connecting task execution to cancellation and progress surfaces.

### Pipeline Non-Responsibilities
The pipeline does not:

- mutate the frozen baseline manifest during a run;
- behave as a general dependency-injection container;
- decide stage-internal execution behavior;
- perform stage-local cache reads or writes directly;
- replace planner graph logic with ad hoc stage chaining;
- materialize semantic outputs by bypassing the runner;
- pass caller output targets directly into stage executors.

### Planner Responsibilities
The planner is responsible for:

- selecting producer paths from the manifest;
- resolving settings- and requirement-driven graph structure;
- computing the concrete plan for the request.

### Runner Responsibilities
The runner is responsible for:

- cache-resolution and reservation behavior;
- binding stage inputs and outputs;
- invoking executors;
- publishing produced artifacts;
- materializing requested caller targets.

## Demo Flow
The current Python demo illustrates the pipeline end to end:

1. `create_demo_fixture(...)` creates a file-backed cache and builds a pipeline by installing `SummaryExampleFeature()`.
2. `build_request(...)` constructs a `RunRequest` with nested input mappings and one `render//webvtt_transcript` output target.
3. The first run uses `run_away(...)` to demonstrate detached execution and progress tracking.
4. The second run uses `run(...)` to demonstrate the direct awaitable API and cache reuse.

## Recommended Invariants
1. A configured pipeline owns one baseline immutable `StageManifest`.
2. `build()` is the bootstrap boundary at which the manifest is frozen.
3. `PipelineFeature` is the primary installation mechanism for coherent pipeline configuration, including stage packages and runtime wiring.
4. A `RunRequest` contains only caller-supplied inputs, requested outputs, and optional planning policy.
5. Request artifact mappings normalize deterministically into concrete `ArtifactKey` values before planning.
6. Duplicate request artifact IDs are rejected during normalization.
7. Supplied inputs are bound into artifact instances before graph planning begins.
8. Default settings are treated as supplied graph roots when the caller did not provide them explicitly.
9. Settings remain normal artifact inputs and are not duplicated into a pipeline-specific side channel.
10. Runtime collaborators such as caches and factories may be installed through features rather than bespoke builder APIs.
11. `run(...)` and `run_away(...)` share one execution core and differ only in lifecycle exposure.
12. The pipeline must close already-bound supplied instances if planning fails before runner handoff.
13. Any future per-run manifest override must derive a new immutable manifest snapshot and must not mutate the baseline manifest.
14. `ChunkyPipelineBuilder` remains a pipeline-specific configuration surface, not a full-fledged DI container.
