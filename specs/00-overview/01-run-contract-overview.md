# Chunky Run Contract — Overview

## Purpose

This specification defines Chunky's public run contract and the high-level architecture used to satisfy a run request.

The contract lets callers request one or more semantic artifacts from one or more supplied artifacts without manually selecting every pipeline step. A configured pipeline plans the required graph, resolves only relevant default settings, coordinates cache reuse safely, executes ready stage invocations, and materializes the requested targets.

The contract is language-neutral. Python is the first implementation target, but the same request, artifact, stage, cache, event, and scheduling model should be expressible from Rust and other future bindings.

## Design Goals

The run contract must:

- describe requested work as a data model, not Python-only syntax;
- support direct awaited execution and detached in-process execution;
- infer required upstream work from requested outputs;
- support multiple outputs from one shared dependency graph;
- support ordered materialization fan-out for one artifact;
- allow target-less requested outputs when durable cache publication can satisfy later retrieval;
- treat source inputs, settings, intermediates, targets, and diagnostics as artifacts;
- support explicit cached-root references for upload-first and saved-task workflows;
- return concrete cached-instance references for durably published requested outputs;
- resolve default settings lazily, only when planning determines they are relevant;
- make cache reuse deterministic, explainable, and safe;
- expose per-artifact cache-use intent without exposing backend-specific knobs;
- support resource-aware scheduling for CPU, GPU, memory, and unconstrained work;
- preserve enough diagnostics to debug request validation, planning, cache reuse, execution, and materialization.


## Pipeline Configuration

A run is executed by a configured `ChunkyPipeline`. The pipeline is created by installing explicit features into a builder and then freezing the resulting manifest and runtime service configuration.

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(FileArtifactCacheFeature(cache_root="./cache"))
    .install(DefaultFingerprintProviderFeature())
    .install(DefaultPlannerFeature())
    .install(DefaultRunnerFeature())
    .install(SpeechPipelineFeature())
    .build()
)
```

### Feature
A feature is a self-contained installer. Most features install artifact declarations and stage registrations. Specialist features may install runtime services such as the cache, fingerprint provider, planner factory, runner factory, or bindings-provider factory.

```python
class WebVttTopologyExampleFeature:
    feature_id = "example.webvtt-topology"
    configuration_fingerprint = "webvtt-topology-example-v1"

    def install(self, manifest_builder: "StageManifestBuilder", _dependency_builder: "DependencyBuilder") -> None:
        manifest_builder.add_artifact(SOURCE_AUDIO)
        manifest_builder.add_artifact(AUDIO_SETTINGS)
        manifest_builder.add_artifact(SPEECH_AUDIO_SETTINGS)
        manifest_builder.add_artifact(MUSIC_AUDIO_SETTINGS)
        manifest_builder.add_stage(PREPARE_AUDIO_STAGE)
        manifest_builder.add_stage(TRANSCRIBE_STAGE)
        manifest_builder.add_stage(DIARIZE_STAGE)
        manifest_builder.add_stage(DETECT_MUSIC_STAGE)
        manifest_builder.add_stage(ALIGN_STAGE)
        manifest_builder.add_stage(RENDER_VTT_STAGE)
```

Feature composition rules are intentionally simple:

- required features must be installed explicitly;
- features do not declare explicit dependencies on other features;
- the system does not auto-install missing features;
- duplicate feature installation is a hard error by default;
- conflicting core runtime service slots are hard errors;
- the builder is single-use after `build()` succeeds.

The frozen `StageManifest` is a strong validation boundary. Static topology errors, such as unknown artifact declarations, invalid phase usage, or duplicate exact-output producers, are rejected before any run begins. Request-specific missing producer paths remain planner failures.

## Public API Shape

Language bindings may expose idiomatic syntax, but all bindings compile down to the same conceptual `RunRequest` model.

### Direct Run

`run(...)` executes a request and awaits completion. On failure, it raises an exception that carries structured diagnostics and any partial outcomes known at the time of failure.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript": [TargetVttArtifact.to_file_path("output.vtt")],
        },
    )
)
```

### Detached In-Process Run

`run_away(...)` starts the same execution core in the current process and returns a handle for cancellation, progress inspection, and awaited completion.

```python
handle = pipeline.run_away(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript": [TargetVttArtifact.to_file_path("output.vtt")],
        },
    )
)

print(f"Started task {handle.task_id}")
result = await handle.awaitable()
```

The handle is non-durable. If the process exits, the handle and progress state are gone. Re-running an equivalent request may still skip completed work through cache hits.

### Speaker-Labeled Output

Requesting a speaker-labeled transcript may cause planning to select speaker-detection stages. The caller may supply explicit settings, but omitted settings are resolved only when planning discovers that a relevant stage or graph branch needs them.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
            "speaker_detection/diarization_settings": SourceSettingsArtifact.from_object({
                "detect_min_speakers": 2,
                "detect_max_speakers": 10,
            }),
            "render/webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render/webvtt_transcript": [TargetVttArtifact.to_file_path(
                "speaker-labelled-output.vtt"
            )],
        },
    )
)
```

### Settings From File

Custom settings that change rarely can be supplied through source handles backed by files.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
            "speech_detection/vad_detector_profiles": SourceSettingsArtifact.from_yaml_file(
                "./vad_detector_profiles.yaml"
            ),
            "speaker_detection/diarization_settings": SourceSettingsArtifact.from_yaml_file(
                "./diarization_settings.yaml"
            ),
            "render/webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render/webvtt_transcript": [TargetVttArtifact.to_file_path("output.vtt")],
        },
    )
)
```

### Multiple Outputs
Multiple requested outputs are resolved into one shared dependency graph. Shared upstream artifacts are produced once and reused.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
            "render/webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render/webvtt_transcript": [TargetVttArtifact.to_file_path("output.vtt")],
            "speaker_detection/speaker_embeddings": [TargetJsonArtifact.to_file_path(
                "speaker-embeddings.json"
            )],
        },
    )
)
```

### Output Fan-Out

One semantic artifact may be materialized to an ordered list of caller targets in the same run.

```python

output = InMemoryTargetArtifact()
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript/any": [
                TargetVttArtifact.to_file_path("output.vtt"),
                output,
            ],
        },
    )
)
```

Target order is preserved. If one target write fails, the run fails and reports which target writes succeeded or failed. Successful target side effects are not rolled back, and a materialization failure does not invalidate a committed semantic cache entry.

### Target-Less Requested Outputs

A requested output may name zero caller materialization targets. This means: make the semantic artifact available for this run, publish it durably, and return enough metadata for later retrieval.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript/any": [],
        },
    )
)

webvtt_output = result.outputs[ArtifactKey.from_a_ref("render/webvtt_transcript")]
print(webvtt_output.instance_locator)
print(webvtt_output.task_locator)
```

Target-less outputs are valid only when the requested artifact is durably retrievable after the run. In practice, the artifact must be cacheable for that request. 

*A target-less output combined with `cache_policy[artifact_id].do_not_cache=True` is a validation error because it asks the run to promise later retrieval while forbidding durable publication.*

Every requested output receives a stable `a-instance` locator at planning finalization. 

When a requested output becomes durably available, terminal results and progress/event payloads may also include the externally retrievable `a-instance` locator. The `a-task` is available once the final plan exists; the public `a-instance` is emitted only after the cache entry has committed and is retrievable.

### Per-Artifact Cache Policy

Cache backend configuration belongs to installed features. Per-run cache behavior is expressed through `RunRequest.cache_policy`.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript/any": TargetVttArtifact.to_file_path("output.vtt"),
        },
        cache_policy={
            "audio/prepared": ArtifactCachePolicy(
                ignore_existing_cache=True,
            ),
            "render/webvtt_transcript": ArtifactCachePolicy(
                do_not_cache=True,
            ),
        },
    )
)
```

Cache policy targets exact semantic artifact IDs. Unknown artifact IDs fail validation. Known but unreachable policy targets are ignored with diagnostics. `do_not_cache=True` is a validation error for a target-less requested output.

The policy directives are orthogonal:

- `ignore_existing_cache` affects read/reuse for the current run;
- `do_not_cache` affects write/publish-to-cache for the current run;
- `desired_ttl` applies only when creating a new cache entry and does not mutate an existing entry's TTL.

### Supplied Root Cache Publication

A caller-provided graph root whose declaration is cacheable is published into the artifact cache by default during binding. This applies uniformly to supplied source artifacts, supplied settings artifacts, and explicitly supplied intermediate artifacts.

This publication does not change graph shape or supplied-root precedence. The supplied artifact remains the root selected for the current run; cache publication only makes the same semantic instance durable for later reuse, saved-task re-submission, upload-first workflows, or materialization retry.

`cache_policy[artifact_id].do_not_cache` opts out for that exact root. The root can still be used in the current run, but a future persisted task that depends on an uncached provided root is not restartable because its durable root set is incomplete.

### Direct Artifact Cache IO

Chunky also reserves a dedicated artifact-cache IO surface separate from `run(...)` and `run_away(...)`. It lets callers publish artifact content into cache and retrieve published instances without constructing a run request solely for cache population or inspection.

The interface is semantic and handle-based: callers use artifact declarations or artifact IDs plus source or target handles. It must not expose backend-native cache paths, object keys, or lock records. Direct cache writes must use the same fingerprinting, metadata validation, reservation, and atomic publish rules as run-time cache publication. A directly published artifact is therefore a normal cache entry, not a weaker side channel.

Direct cache IO returns or accepts a concrete `a-instance` locator. When such a locator is later used as a provided root, planning treats it like the same artifact supplied normally, apart from provenance diagnostics. This direct cache IO surface is the common foundation for upload-first web-service flows, duplicate-upload detection, cached-root rebinding, and later output retrieval. A future artifact-existence preflight may check an `a-instance` before a large upload, but that optimization is not required by this version.

#### Publishing an artifact through `ArtifactCacheIO`
A caller may publish a root before creating a run request. The cache IO surface computes the same supplied-artifact fingerprint that normal root binding would compute, reserves the cache entry, writes through the declaration codec, validates metadata, and returns a typed cache reference.

```python
audio_instance = await pipeline.cache_io.publish(
    "source/audio",
    SourceAudioArtifact.from_file_path("input.wav"),
)

# The locator is semantic plus concrete: it is not just a raw fingerprint.
assert audio_instance.artifact_key == ArtifactKey.from_a_ref("source/audio")
print(audio_instance)
```

The returned reference is suitable for saved task configuration, upload-first service flows, or explicit rebinding in a later run. Publishing through `ArtifactCacheIO` does not expose a cache path or backend object key.

#### Using a cached artifact in a task

A cached locator can later be supplied as a graph root in `RunRequest.inputs`. The request key still declares the semantic artifact being supplied, and the `a-instance` locator pins which committed cache instance should be used. The locator's artifact identity must match the normalized request key.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": audio_ref,
            "render/webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render/webvtt_transcript": TargetVttArtifact.to_file_path(
                "output-from-cached-audio.vtt"
            ),
        },
    )
)
```

During binding, the pipeline opens the cache entry by `a-instance`, validates the cache metadata against the declaration, and acquires a read reservation before planning proceeds. If the cached root is a settings artifact, the pipeline may read it early so settings-dependent planning hooks see the same value they would have seen from an ordinary supplied settings source.

## Run Request Model

A `RunRequest` is the canonical language-neutral request shape.

```text
RunRequest
  inputs: map<a-ref, SourceHandle | a-instance>
  outputs: map<a-ref, TargetHandle | ordered-list<TargetHandle> | empty-list>
  run_policy?: PlanningRunPolicy
  cache_policy?: map<a-ref, ArtifactCachePolicy>
```

Required fields:

- `inputs`: caller-supplied source artifacts, settings artifacts, explicitly supplied intermediate artifacts, or explicit `a-instance` cached-root locators for those same semantic artifacts;
- `outputs`: requested semantic artifact references and their caller materialization targets. The target list may be empty only for target-less outputs that can be durably retrieved later.

Optional fields:

- `run_policy`: planning or execution-strategy policy;
- `cache_policy`: exact per-artifact cache-use intent.

`run_policy` must not suppress requested outputs or weaken materialization obligations.

## Artifact Addressing and References

Public request fields use semantic artifact reference strings or structured `ArtifactRefLocator` values. The `a-ref:/` prefix is accepted but optional for declaration-level references, so documentation examples use short paths such as `source/audio`. `ArtifactKey` remains the internal semantic value object, while `ArtifactRefLocator` owns boundary parsing and serialization.

Preferred short semantic reference form:

```text
{domain}/{content_kind}/{phase-or-any}
```

Concrete instance form:

```text
a-instance:/global/{domain}/{content_kind}/{phase-or-none}/fingerprint/{fingerprint}
```

Task-scoped requested-output form:

```text
a-task:/global/{domain}/{content_kind}/{phase-or-none}/task/{task_id}
```

Language bindings may expose nested mappings that normalize to the same `a-ref` values. Three-level nested mappings use the first segment as projection context and the second segment as the actual artifact domain. Projection context is presentation-only and does not participate in equality, hashing, planning, cache lookup, or fingerprint payloads.

```python
RunRequest(
    inputs={
        "source": {
            "audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        "speech_processing": {
            "audio_preprocessing": {
                "audio_settings": SourceSettingsArtifact.from_object({
                    "sample_rate_hz": 16000,
                }),
            },
        },
    },
    outputs={
        "render": {
            "webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
        },
    },
)
```

Normalization is deterministic:

- direct string keys must parse as semantic `a-ref` references; the `a-ref:/` prefix is optional;
- nested keys must be strings;
- direct `ArtifactKey` or locator entries map directly to values, not nested mappings;
- normalized paths must resolve to two or three nested path components before conversion to `a-ref`;
- omitted projection context canonicalizes to `global`;
- omitted `a-ref` phase canonicalizes to `any`;
- `a-instance` and `a-task` never infer `any`;
- duplicate artifact IDs are rejected after projection context is stripped;
- structural validation errors across `inputs`, `outputs`, and `cache_policy` should be accumulated and reported together.

The public model distinguishes three related address kinds:

- an `a-ref` says what artifact is needed, produced, cached, or materialized;
- an `a-instance` says which concrete artifact instance is meant, paired with the final artifact fingerprint;
- an `a-task` says which task-scoped requested-output binding is meant.

Plain semantic artifact references remain the default for normal planning, outputs, and cache policy; their public string form may be short path-like syntax or canonical `a-ref:/...`. Concrete `a-instance` locators are explicit pinning mechanisms used by saved-task and upload-first workflows. The fingerprint is not an untyped global identifier: it must be paired with the semantic artifact identity and validated against the manifest declaration and cache metadata before binding.

## Settings and Defaults

Settings are normal artifacts. They are read by stages through normal input bindings and participate in produced-artifact fingerprints like any other effective input.

Caller-supplied settings are bound before planning. Declaration defaults are resolved lazily: the planner owns relevance discovery and requests only the defaults needed by the request-specific graph.

A resolved default behaves exactly like a supplied graph root after it is bound. Default-source providers must be deterministic and local to the setting declaration plus optional caller override; they must not inspect broader request shape, planner state, cache state, wall-clock time, or resource availability.

A cached settings root referenced by `a-instance` may be bound and read before normal graph planning proceeds, so planning hooks see the same settings value they would have seen from an ordinary supplied settings source.

## Minimal Stage and Artifact Contract

A stage is registered through immutable metadata and executed through a narrow executor contract. The executor is not the registry.

```python
@dataclass(frozen=True)
class StageRegistration:
    stage_id: str
    display_name: str
    registration_version: str
    behavior_version: str
    owned_artifacts: tuple[ArtifactDeclaration[Any], ...]
    input_capabilities: tuple[ArtifactCapability, ...]
    output_capabilities: tuple[ArtifactCapability, ...]
    resolve_requirements: StageRequirementResolver
    resolve_output_capabilities: StageOutputResolver
    resolve_resource_policy: StageResourceResolver
    executor_factory: StageExecutorFactory


class StageExecutor(Protocol):
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None: ...
```

The runner passes already-bound readers and writers. A stage does not perform cache lookup, acquire cache reservations, mutate the manifest, or write caller materialization targets directly.

A binding is the physical transport abstraction. Within one process, an upstream stage may pass a typed object directly to a downstream stage without a serialize/deserialize round trip. At cache and materialization boundaries, the artifact's codec defines the canonical serialized representation.

Codecs include both `mime_type` and `extension`. The extension is storage and materialization metadata; it is not part of the stage's semantic dependency contract.

## Internal Architecture Overview

Chunky resolves each `RunRequest` into one shared task graph.

The graph is built from registered stages and artifacts:

- a **stage** is deterministic pipeline behavior that consumes and produces semantic artifacts;
- an **artifact** is any typed value that enters, moves through, or leaves the pipeline;
- a **stage manifest** is the frozen, validated set of artifact declarations, stage registrations, and planning-visible feature records;
- a **planner** starts from requested outputs and walks backward through artifact requirements until every required input is caller-supplied, produced by a selected stage, optional, or resolved from a relevant default setting;
- a **task plan** is the request-specific DAG of stage invocations and expected artifact fingerprints;
- a **scheduler/runner** executes ready invocations when their inputs are available and resource constraints allow admission;
- a **cache** may satisfy an artifact immediately before the producing stage would run;
- a **materializer** writes already-available semantic artifacts to caller targets after semantic production or cache resolution succeeds.

Cache state does not shape the planned graph. Cache hits affect execution skipping only.

Conceptual flow:

```text
install features
  -> freeze and validate StageManifest
  -> accept RunRequest
  -> normalize and validate inputs, outputs, and cache_policy
  -> bind caller-supplied and cached graph roots
       -> publish cacheable provided roots unless opted out
       -> read cached settings roots needed for planning
  -> plan shared artifact dependency DAG
       -> resolve relevant default settings on demand
       -> calculate expected fingerprints
  -> register planned invocation progress
  -> execute ready invocations
       -> resolve cache hit / write reservation / wait at stage admission
       -> bind stage readers and semantic output writers
       -> skip, wait, or execute stage
  -> materialize requested caller targets in declared target order
  -> return success, or raise/report structured failure with partial outcomes
```

## Failure and Progress Semantics

Progress registration begins after planning succeeds and concrete invocations exist. There is no pre-plan progress stream in this contract.

Pre-plan failures are still surfaced clearly:

- `run(...)` raises a structured exception;
- `run_away(...)` completes its handle with a terminal failed result.

If a run fails after producing semantic artifacts or writing some caller targets, the failure surface should include successful partial outcomes where available.

A requested output receives an `a-task` locator after planning finalization. A requested-output availability event is emitted only after the semantic artifact has committed durably, and then the event/result payload may also carry the externally retrievable `a-instance` locator. Clients should store returned locators rather than reconstructing them from graph metadata.

## Recommended Invariants

1. A configured pipeline owns one baseline immutable `StageManifest`.
2. `build()` freezes and validates the manifest and runtime service configuration.
3. The builder is single-use.
4. `PipelineFeature` is the canonical public installation mechanism.
5. Features are self-contained installers, not dependency-injection fragments.
6. Duplicate feature installation and conflicting service-slot installation hard-fail.
7. Features are never auto-installed implicitly.
8. `StageManifest` creation rejects static topology errors before any run begins.
9. A `RunRequest` contains caller-supplied inputs, requested outputs, optional run policy, and optional per-artifact cache policy.
10. Request artifact mappings normalize deterministically into concrete `ArtifactKey` values.
11. Request structural validation aggregates deterministic shape errors.
12. `run_policy` must not suppress requested outputs.
13. `cache_policy` targets exact semantic artifact IDs only.
14. Unknown cache-policy artifact IDs fail validation; known unreachable ones produce diagnostics.
15. Supplied inputs are bound before graph planning begins.
16. Default settings are resolved lazily for relevant settings only.
17. Resolved defaults behave exactly like supplied graph roots.
18. Settings remain normal artifact inputs.
19. Cache state does not change graph shape.
20. Cache hits skip execution but still produce normal artifact availability.
21. Runtime mechanics such as cache backend choice remain outside manifest identity unless they change planning-visible semantics.
22. `run(...)` and `run_away(...)` share one execution core.
23. `run_away(...)` is in-process and non-durable.
24. The pipeline closes bound supplied instances it owns if planning fails before runner handoff.
25. Progress registration begins after planning succeeds.
26. Pre-plan failures still surface terminal diagnostics for detached runs.
27. Failed runs surface successful partial outcomes where available.
28. Any materializable semantic artifact may be requested as an output.
29. Caller output fan-out preserves target list order.
30. A materialization failure fails the run but does not invalidate a committed semantic artifact.
31. Successful materialization side effects are reported, not rolled back.
32. Per-run manifest overrides are out of scope for this public contract.
33. Cacheable provided roots are published to cache by default during binding.
34. `cache_policy.do_not_cache` opts out of both produced-artifact cache publication and provided-root cache publication for the exact artifact.
35. A saved task with any uncached provided root is not restartable because the durable root set is incomplete.
36. Direct artifact-cache IO is semantic and handle-based, not backend-path-based.
37. Direct cache writes obey the same fingerprinting, metadata, reservation, and atomic-publish rules as run-time cache publication.
38. A concrete cached-instance reference is an `a-instance` locator, not a bare fingerprint.
39. Plain semantic artifact references remain the default public addressing form and serialize as `a-ref`.
40. Cached settings roots may be early-bound for planning when explicitly referenced as provided roots.
41. Requested outputs may have zero caller targets only when durable cache publication satisfies later retrieval.
42. A target-less output with `cache_policy.do_not_cache=True` is a validation error.
43. Published requested-output results and events carry the requested `a-task` and, when durably retrievable, the concrete `a-instance` locator.
44. Concrete retrieval references are emitted only after durable cache commit.
45. Future persisted-task execution is a higher-level orchestration layer over the same `RunRequest` and execution core.
46. Persisted task records store normalized request/configuration and compatibility metadata, not live scheduler state.
47. A restarted persisted task keeps its stable persisted-task identity but creates a fresh live `task_id`.
48. Graceful shutdown preserves restartable durable task records and distinguishes shutdown interruption from ordinary failure or caller cancellation.
