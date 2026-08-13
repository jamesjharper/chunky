# Runtime Builder

## Purpose
`ChunkyRuntimeBuilder` is the surface for bootstrapping a `ChunkyRuntime` instance with a stage manifest, configured runtime services and runtime settings.

> *Design Note:* The builder should remain a thin composition surface. It is not intended to become a general dependency-injection container, and it should not treat runtime service location as a generic mutable service locator.

## Core Concepts
`ChunkyRuntime` is the configured runtime: an immutable collection of the
stage manifest, runtime services, and runtime settings required to plan and
execute runs. It is not an individual run or a second service shape.

`ChunkyRuntimeBuilder.install(feature)` installs programmatically configured
`RuntimeFeature` values. The builder exposes three delegates to each feature
installer:

- `StageManifestBuilder` which collects artifact declarations and stage
  registrations;
- `RuntimeServicesBuilder` which configures the runtime's internal service
  slots; and
- `RuntimeSettingsBuilder` which declares runtime-setting specifications and
  setting sources.

A feature may use any combination of those delegates to install artifact
declarations, stage registrations, runtime-setting sources, required setting
specifications, and runtime services. `build()` finalizes the configuration and
returns a `ChunkyRuntime` containing an immutable `StageManifest`,
`RuntimeServices`, and `RuntimeSettings`.

## Builder
The builder is responsible for:
- accepting feature installation;
- providing installers with explicit bootstrap collaborators;
- freezing declared runtime-setting configuration and runtime-setting sources;
- freezing the underlying `StageManifestBuilder`;
- collecting known runtime service slots installed by features;
- rejecting conflicting runtime service slots;
- creating the configured runtime;
- rejecting mutation after `build()`.

*Conceptual shape:*

```python
class ChunkyRuntimeBuilder:
    def install(self, feature: RuntimeFeature) -> "ChunkyRuntimeBuilder":
        pass

    ## use_{feature_name}() -> "ChunkyRuntimeBuilder":
    # languages which mechanism like monkey patching, extension methods or traits may provide optional helper methods for a more declarative bootstrapping procedure

    def build(self) -> "ChunkyRuntime":
        pass
```


## Runtime Features
A runtime feature is the bootstrap-time installation unit for a coherent slice of runtime configuration.
Feature installation makes capabilities available for planning and execution.

> *Design Note:* It remains the planner's responsibility to decide how and if stages are configured into a task graph. *With the one exception that a integrator may elect to override the planners implementation via the `RuntimeServicesBuilder`*

```python
class RuntimeFeature(Protocol):
    feature_id: str


    def install(
        self,
        manifest_builder: StageManifestBuilder,
        service_locator: RuntimeServicesBuilder,
        env_settings: RuntimeSettingsBuilder,
    ) -> None: ...
```


### A feature may install:

__Artifact declarations:__

```python
class AsrArtifactStageFeature:
    def install(self,manifest_builder: StageManifestBuilder, service_locator: RuntimeServicesBuilder, env_settings RuntimeSettingsBuilder) -> None:
        manifest_builder.add_artifact(TIMED_WORDS_TARGET)
```

*For demonstration purposes only, a feature will likely install several artifacts and stages together*

__Stage registrations:__

```python
class AsrStageFeature:
    def install(self,manifest_builder: StageManifestBuilder, service_locator: RuntimeServicesBuilder, env_settings RuntimeSettingsBuilder) -> None:
        manifest_builder.add_stage(ASR_STAGE)
```

__Set Runtime setting:__

```python
class SetSettingsFeature:
    def __init__(self, *, provider_name: str | None = None) -> None:
        self._provider_name=provider_name

    def install(self,manifest_builder: StageManifestBuilder, service_locator: RuntimeServicesBuilder, env_settings RuntimeSettingsBuilder) -> None:
        if self._provider_name is not None:
            env_settings.set(ASR_PROVIDER_NAME_SETTING, self._provider_name)
```

*Demonstration purposes only, a feature will use this mechanic to enable inline setting configs while registering the stage which requires this setting*

__Runtime Setting Source:__

```python
class EnvironmentVariableRuntimeSettingSourceFeature:
    def install(self,manifest_builder: StageManifestBuilder, service_locator: RuntimeServicesBuilder, env_settings RuntimeSettingsBuilder) -> None:
        env_settings.install_source(EnvironmentVariableRuntimeSettingSource())
```

__Runtime services:__

```python
class SharedFileArtifactStorageFeature:
    def __init__(self, root: str | Path, *, config: FileCacheProviderConfig | None = None) -> None:
        self._root = Path(root)
        self.config=config

    def install(self, manifest_builder: StageManifestBuilder, service_locator: "RuntimeServicesBuilder", env_settings: RuntimeSettingsBuilder) -> None:
        representations = FileRepresentationStore(
            self._root / "objects",
            config=self.config,
        )
        service_locator.set_representation_store(representations)
        service_locator.set_artifact_cache(
            FileCacheProvider(self._root / "cache", representations)
        )
        service_locator.set_artifact_persistence(
            FileArtifactPersistenceProvider(
                self._root / "artifacts",
                representations,
            )
        )
```


## `class RuntimeServicesBuilder`
`RuntimeServicesBuilder` is a narrow mutable collector for a pre-declared set of runtime services. It is not a general registry keyed by arbitrary strings or types. This interface can be used for providing system implementations of runtime operation such as resolving cached files, or orchestration of the planned stage graph.

```python
@dataclass(frozen=True)
class RuntimeServices:
    representation_store: RepresentationStore
    artifact_cache: ArtifactCache
    artifact_persistence: ArtifactPersistenceProvider
    artifact_bindings_provider_factory: ArtifactBindingsProviderFactory
    planner_factory: PlannerFactory
    runner_factory: RunnerFactory

    def set_representation_store(
        self,
        representation_store: RepresentationStore,
    ) -> "RuntimeServicesBuilder":
        pass

    def set_artifact_cache(self, artifact_cache: ArtifactCache,) -> "RuntimeServicesBuilder":
        pass

    def set_artifact_persistence(
        self,
        artifact_persistence: ArtifactPersistenceProvider,
    ) -> "RuntimeServicesBuilder":
        pass

    def set_artifact_bindings_provider_factory(self, factory: ArtifactBindingsProviderFactory) -> "RuntimeServicesBuilder":
        pass

    # ...

    def freeze(self) -> RuntimeServices:
        pass
```

__Expected behavior:__
- each service may be configured at most once, ensuring configured behavior is self-describing without ambiguous service assignment;
- if two features try to install the same service, installation/build fails with a hard configuration error;
- if a service is never configured by any feature, `freeze()` supplies the runtime's default implementation for that slot where a default is defined;
- the frozen `RuntimeServices` object exposes the resolved service set as an immutable runtime dependency bundle.

Representation storage, artifact cache, and durable artifact persistence are
separate service slots. Cache and persistence share physical objects only when
they are explicitly composed with the same representation-store instance and
namespace.

The builder must reject:

- two independent representation coordinators claiming the same physical
  namespace;
- cache and persistence providers configured with incompatible representation
  schemas;
- ambiguous reference or garbage-collection ownership; and
- workers configured with inconsistent shared-store identity.

When no storage feature is installed, the builder creates a runtime-scoped
workspace representation store and cache. It may also expose a local workspace
artifact provider for non-durable use. The runtime owns this private workspace
and deletes it on close after releasing active handles.

Workspace defaults do not satisfy shared durable-task requirements. Durable
artifact upload and durable task submission preflight the configured
persistence provider's restart survival, cross-worker visibility, claim,
reservation, recovery, and retention capabilities and fail before accepting
content/work when they are insufficient.

Artifact-persistence defaults define separately:

- short upload-grace TTL and allowed bounds;
- task artifact-retention TTL and allowed bounds;
- object/artifact size and capacity limits; and
- operation idempotency and reservation lease policy.

These defaults are not inferred from cache TTL, task-record retention, or
active/recovery deadlines.

This keeps service composition deterministic: features may replace a default by explicitly installing a service, but they may not silently override one another.

## `class RuntimeSettingsBuilder`
`RuntimeSettingsBuilder` is the configuration surface for managing runtime settings such as Credentials, endpoint URLs, concurrency limits, and retry/transport. These setting must not influence the semantic output of the runtime as this would undermine the artifact caching behaver. Settings which influence the semantic output, MUST be passed as a setting artifact when configuring a task.

*Conceptual shape:*
```python
class RuntimeSettingsBuilder:

    def set(self, key: str, value: object) -> "RuntimeSettingsBuilder":
        pass
        
    def install_source(self, source: RuntimeSettingSource) -> "RuntimeSettingsBuilder":
        pass

    def install_stage_settings(self, stage_id: str, settings: Iterable[RuntimeStageSetting]) -> "RuntimeSettingsBuilder":
        pass

    def freeze(self) -> RuntimeSettings:
        pass

```

__Configuring setting to Stage dependency:__
Configuring a dependency relationship between a state and RuntimeSettings is performed by proxy of calling `manifest_builder.add_stage(STAGE_REG)`. Upon finalizing `ChunkyRuntimeBuilder` is expected to call RuntimeSettingsBuilder.install_stage_settings(...) for each stage registration.

*Conceptual shape:*

```python
class ChunkyRuntimeBuilder:
    def build(self):
        manifest = self._manifest_builder.freeze()
        for registration in manifest.stages.values():
            self._env_settings.install_stage_settings(
                registration.stage_id,
                registration.runtime_settings,
            )
```

__Configuring a setting value:__
A feature may pass an explicit runtime-setting value using `RuntimeSettingsBuilder.set(key, value)`. This maybe performed before the setting is registered.

__Configuring a setting source:__
A feature may register a `RuntimeSettingSource` by calling `RuntimeSettingsBuilder.install_source(source)`. Custom `RuntimeSettingSource` can be used to enable reading settings from a file, secretes provider or environment variables.

Sources are frozen into the configured runtime and are consulted later when the run resolves settings for the active stages. Source lookup happens only for active settings that are still unset. Declared values and defaults are applied before sources are consulted, so a source does not override an explicit value or a declared default.


*Conceptual shape:*

```python
class CustomRuntimeSettingSource(RuntimeSettingSource):
    def resolve(self, spec: RuntimeSettingSpec) -> object: ...
```

*where*

```python
@dataclass(frozen=True)
class RuntimeSettingSpec:
    key: str
    parser: RuntimeSettingParser | None = None
    default: object = _MISSING
    env_var: str | None = None
    description: str = ""
```

*Installed by*

```python
class CustomRuntimeSettingLocatorFeature:
    def install(self, _manifest_builder, _service_locator, env_settings) -> None:
        env_settings.install_source(CustomRuntimeSettingSource())
```


### Expected behavior:
Required runtime settings are validated after planning determines the concrete graph shape and therefore which stage registrations are actually active for the run, at which point an error is raised before task execution begins. This ensures the runtime fails fast rather than discovering the missing setting deep into execution.

This keeps operational configuration validation aligned with the true execution graph: missing credentials for an unplanned provider stage do not block unrelated runs, while missing credentials for a planned stage still fail before that stage executes.

## `class StageManifestBuilder`

`StageManifestBuilder` is the configuration surface for registering available stages for performing data transformations within the runtime. Stages are statically registered capabilities which are dynamically composed to meet a `LocalRunRequest`'s requirements.

Stages are installed by calling `manifest_builder.add_stage(registration)` from a feature's `install(...)` method.

`StageManifestBuilder.add_stage(...)` is intentionally narrow:
- it rejects duplicate `stage_id` values;
- it automatically installs every declaration in `registration.owned_artifacts`;
- artifact conflicts are still validated through `add_artifact(...)`;
- it stores the `StageRegistration` exactly as the planner and runner will later see it.

```python
class WebVttTopologyExampleFeature:
    def install(self, manifest_builder, service_locator, env_settings) -> None:
        del service_locator, env_settings
        manifest_builder.add_stage(PREPARE_AUDIO_STAGE)
        manifest_builder.add_stage(build_transcribe_stage())
        manifest_builder.add_stage(DIARIZE_STAGE)
        manifest_builder.add_stage(DETECT_MUSIC_STAGE)
        manifest_builder.add_stage(ALIGN_STAGE)
        manifest_builder.add_stage(RENDER_VTT_STAGE)
```

### What `class StageRegistration` Contributes

*Conceptual shape:*

```python
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

    runtime_settings: tuple[RuntimeStageSetting, ...] = ()
    concurrency_policy: StageConcurrencyPolicy | None = None
```

The important builder-time implications are:
- __`stage_id`__ is the stable identity used for manifest registration and runtime-setting scoping;
- __`display_name`__ is presentation metadata;
- __`registration_version`__ and __`behavior_version`__ are part of the stage's durable registration contract and manifest identity;
- __`executor_factory`__ is the runtime constructor the runner will use for invocations;
- __`owned_artifacts`__ are the artifacts declared by stage [[../02-artifacts/01-artifacts-spec#Artifact Declarations|Artifact Declarations]]
- __`output_capabilities`__ defines a static set of output capabilities this stage *could* produce if requested
- __`input_capabilities`__ defines a static set of input requirements this stage *could* require depending on a specific set of requested outputs
- __`resolve_output_capabilities()`__ is a function to be used to assess a stage's *actual* `output_capabilities` within the context of a specific `LocalRunRequest`
- __`resolve_requirements()`__ is a function to be used to assess a stage's *actual* `input_requirements` within the context of a specific `LocalRunRequest`
- __`resolve_resource_policy()`__ is a function to be used to assess a stage's *actual* `resource_policy` within the context of a specific `LocalRunRequest`
- __`runtime_settings`__ lets a stage declare a set of runtime-setting specs that it depends on to be successfully invoked. Runtime settings are resolved and validate after planning and before execution of the task plan
- __`concurrency_policy`__ provides optional execution constraints beyond the default scheduler behavior.

> Notes for future self and refactoring agents. 
> 1: concurrency_policy and resolve_resource_policy should probably be role into one, 
> 2: owned_artifacts could probably be refactored out of the StageRegistration and bubbled up to the feature installer.
> 3: should `input_capabilities` be renamed to input_requirements? 


## Configured Runtime

A configured runtime owns:

- one baseline immutable `StageManifest`;
- configured runtime services for cache, artifact bindings, planning, running, diagnostics, and related orchestration;
- frozen runtime settings used by runtime/provider plumbing.

Conceptual shape:

```python
class ChunkyRuntime:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        runtime_services: RuntimeServices,
        runtime_settings: RuntimeSettings,
    ) -> None: ...
```
