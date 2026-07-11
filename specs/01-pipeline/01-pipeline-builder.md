# Pipeline Builder

## Purpose

A configured pipeline turns a frozen `StageManifest`, configured runtime services, environment settings, and a `RunRequest` into one concrete task execution. The builder is the mutable bootstrap surface used to install pipeline features before the pipeline is frozen.

The builder should remain a thin composition surface. It desired that it is not a general dependency-injection container and it is does not overload its self a runtime settings service locator, but this may be revisited in the future. 

## Core Concepts

1. A `ChunkyPipelineBuilder` collects explicitly installed pipeline features before the pipeline configuration is frozen.
2. A configured `ChunkyPipeline` owns one baseline immutable `StageManifest`, configured runtime services, and frozen environment settings.
3. Feature installation may add declarations, stages, default producers, known runtime service slots, or required environment settings.

## Builder Shape

Conceptual Python shape:

```python
class ChunkyPipelineBuilder:
    def __init__(self) -> None:
        self._manifest_builder = StageManifestBuilder()
        self._runtime_services = PipelineRuntimeServicesBuilder()
        self._env_settings = PipelineRuntimeSettingsBuilder()
        self._installed_feature_ids: set[str] = set()
        self._built = False

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        self._assert_not_built()
        if feature.feature_id in self._installed_feature_ids:
            raise RuntimeError(f"duplicate pipeline feature: {feature.feature_id}")
        self._installed_feature_ids.add(feature.feature_id)
        feature.install(
            self._manifest_builder,
            self._runtime_services,
            self._env_settings,
        )
        self._manifest_builder.record_feature(
            feature_id=feature.feature_id,
            configuration_fingerprint=feature.configuration_fingerprint,
        )
        return self

    def build(self) -> "ChunkyPipeline":
        manifest = self._manifest_builder.freeze()
        for registration in manifest.stages.values():
            self._env_settings.install_stage_settings(
                registration.stage_id,
                registration.runtime_settings,
            )
        runtime_settings = self._env_settings.freeze()
        runtime_services = self._runtime_services.freeze(...defaults...)
        self._built = True
        return ChunkyPipeline(
            stage_manifest=manifest,
            runtime_services=runtime_services,
            runtime_settings=runtime_settings,
        )
```

The builder is responsible for:

- accepting feature installation;
- providing installers with explicit bootstrap collaborators;
- freezing declared runtime-setting configuration and sources;
- freezing the underlying `StageManifestBuilder`;
- collecting known runtime service slots installed by features;
- rejecting conflicting runtime service slots;
- creating the configured runtime pipeline;
- rejecting mutation after `build()`.

The public configuration path is feature installation. Runtime collaborators should be installed by features that receive the `PipelineRuntimeServicesBuilder` service locator.

## Pipeline Features

A pipeline feature is the bootstrap-time installation unit for a coherent slice of pipeline configuration.

```python
class PipelineFeature(Protocol):
    feature_id: str

    @property
    def configuration_fingerprint(self) -> str: ...

    def install(
        self,
        manifest_builder: StageManifestBuilder,
        service_locator: PipelineRuntimeServicesBuilder,
        env_settings: PipelineRuntimeSettingsBuilder,
    ) -> None: ...
```

A feature may install:

- artifact declarations;
- stage registrations;
- default producer selection;
- provider-backed executors or executor factories;
- an artifact cache;
- a bindings-provider factory;
- a planner factory;
- a runner factory;
- diagnostics or telemetry runtime services;
- required environment settings;
- prerequisite declaration-only shared features.

Feature installation makes capabilities available to planning and execution. It remains the planners responsibility as to how and if those stages are configured into a task graph.


### Provider Configuration and Required Environment Settings

Provider credentials, endpoint URLs, concurrency limits, and retry/transport tuning are operational configuration. They may be passed directly to the feature, installed as explicit runtime-setting values, or resolved from runtime-setting sources such as environment variables.

```python
class OpenAiAsrFeature:
    feature_id = "providers.openai.asr"
    configuration_fingerprint = "openai-asr-v1"

    def install(self, manifest_builder, service_locator, env_settings) -> None:
        env_settings.require(
            RuntimeSettingSpec.string(
                "providers.openai.api_key",
                suggested_env_var="OPENAI_API_KEY",
            ),
            stage_ids=("asr.transcribe",),
        )
        env_settings.declare(
            RuntimeSettingSpec.integer(
                "providers.openai.concurrency_limit",
                default=4,
                suggested_env_var="OPENAI_CONCURRENCY_LIMIT",
            ),
            stage_ids=("asr.transcribe",),
        )
        manifest_builder.add_stage(
            build_transcribe_stage(
                executor_factory=TranscribeExecutor,
            )
        )
```

The builder freezes runtime-setting declarations, explicit values, and configured sources during `build()`, but it does not fail solely because a stage-scoped required setting is absent. Required runtime settings are validated only after planning determines the concrete graph shape and therefore which stage registrations are actually active for the run.

This keeps operational configuration validation aligned with the true execution graph: missing credentials for an unplanned provider stage do not block unrelated runs, while missing credentials for a planned stage still fail before that stage executes.

## Runtime Service Slots

`PipelineRuntimeServicesBuilder` is a narrow mutable collector for a predeclared set of runtime service slots. It is not a general registry keyed by arbitrary strings or types.

Conceptual slots:

```python
@dataclass(frozen=True)
class PipelineRuntimeServices:
    artifact_cache: ArtifactCache
    artifact_bindings_provider_factory: ArtifactBindingsProviderFactory
    planner_factory: PlannerFactory
    runner_factory: RunnerFactory
```

Expected behavior:

- each service slot may be configured at most once;
- if two features try to install the same service slot, installation/build fails with a hard configuration error;
- if a slot is never configured by any feature, `freeze()` supplies the runtime's default implementation for that slot where a default is defined;
- the artifact cache has no useful universal default and must be configured;
- the frozen `PipelineRuntimeServices` object exposes the resolved service set as an immutable runtime dependency bundle.

This keeps service composition deterministic: features may replace a default by explicitly installing a service, but they may not silently override one another.

### Artifact Cache Feature

The artifact cache is a runtime service and should be installed through a feature:

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(FileCacheFeature("./cache"))
    .install(WebVttTopologyFeature(api_key=env["ASR_API_KEY"]))
    .build()
)
```

The cache feature calls `service_locator.set_artifact_cache(cache)` during installation. If no feature installs an artifact cache, `ChunkyPipelineBuilder.build()` fails when runtime services are frozen.

## Runtime Settings

This public builder contract does not expose `require_runtime_setting(...)`, `get_runtime_setting(...)`, or typed variants of those methods.

Runtime-setting declarations, explicit values, and runtime-setting sources are available only to feature installers via `PipelineRuntimeSettingsBuilder`. Installers may register required stage-scoped settings, declare optional settings with defaults, install setting sources, or seed explicit values. Stage executors consume the resolved `PipelineRuntimeSettings` for the concrete planned graph at execution time.

Missing required runtime settings are reported when the pipeline resolves settings for the planned stage set of a concrete run, not during `ChunkyPipelineBuilder.build()`.

Values that affect graph shape, producer selection, semantic artifact contents, artifact fingerprints, or requested-output meaning must be modeled as normal semantic setting artifacts or other planning-visible configuration.

Per-run runtime-setting overrides are out of scope for this contract.

## Configured Pipeline

A configured pipeline owns:

- one baseline immutable `StageManifest`;
- configured runtime services for cache, artifact bindings, planning, running, diagnostics, and related orchestration;
- frozen environment settings used by runtime/provider plumbing.

Conceptual shape:

```python
class ChunkyPipeline:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        runtime_services: PipelineRuntimeServices,
        runtime_settings: PipelineRuntimeSettings,
    ) -> None: ...
```
