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
- treat source inputs, settings, intermediates, targets, and diagnostics as artifacts;
- support explicit cached-root references for upload-first and saved-task workflows;
- resolve default settings lazily, only when planning determines they are relevant;
- make cache reuse deterministic, explainable, and safe;
- expose per-artifact cache-use intent without exposing backend-specific knobs;
- support resource-aware scheduling for CPU, GPU, memory, and unconstrained work;
- preserve enough diagnostics to debug request validation, planning, cache reuse, execution, and materialization.

## Non-Goals

The first version does not need to support:

- durable cross-process background jobs;
- resumable persisted run handles;
- distributed scheduling;
- per-output await;
- per-run manifest or registry overrides;
- implicit feature dependency installation;
- schema compatibility negotiation beyond exact matching;
- rich constraint expressions beyond exact equality;
- reachability-aware cache garbage collection.

Restart recovery is expected to come from normal semantic cache reuse when the same work is submitted again. It is not a job-resume guarantee.

A future persisted-task layer may store task configuration records for later re-submission. Such records are expected to contain task metadata and configuration, not live scheduler state or persisted run status. Restart-time recovery means reconstructing an equivalent request against a compatible planning-visible pipeline configuration, then rediscovering completed work through the artifact cache. If the saved task configuration no longer matches the available pipeline configuration, the task may remain stored but cannot start until a matching configuration is available.

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

A feature is a self-contained installer. Most features install artifact declarations and stage registrations. Specialist features may install runtime services such as the cache, fingerprint provider, planner factory, runner factory, or bindings-provider factory.

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
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
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
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
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
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
            "speaker_detection//diarization_settings": SourceSettingsArtifact.from_object({
                "detect_min_speakers": 2,
                "detect_max_speakers": 10,
            }),
            "render//webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path(
                "speaker-labelled-output.vtt"
            ),
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
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
            "speech_detection//vad_detector_profiles": SourceSettingsArtifact.from_yaml_file(
                "./vad_detector_profiles.yaml"
            ),
            "speaker_detection//diarization_settings": SourceSettingsArtifact.from_yaml_file(
                "./diarization_settings.yaml"
            ),
            "render//webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
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
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
            "render//webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
            "speaker_detection//speaker_embeddings": TargetJsonArtifact.to_file_path(
                "speaker-embeddings.json"
            ),
        },
    )
)
```

### Output Fan-Out

One semantic artifact may be materialized to an ordered list of caller targets in the same run.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render//webvtt_transcript": [
                TargetVttArtifact.to_file_path("output.vtt"),
                InMemoryTargetArtifact.capture(),
            ],
        },
    )
)
```

Target order is preserved. If one target write fails, the run fails and reports which target writes succeeded or failed. Successful target side effects are not rolled back, and a materialization failure does not invalidate a committed semantic cache entry.

### Per-Artifact Cache Policy

Cache backend configuration belongs to installed features. Per-run cache behavior is expressed through `RunRequest.cache_policy`.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source//audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render//webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
        },
        cache_policy={
            "audio//prepared": ArtifactCachePolicy(
                ignore_existing_cache=True,
            ),
            "render//webvtt_transcript": ArtifactCachePolicy(
                do_not_cache=True,
            ),
        },
    )
)
```

Cache policy targets exact semantic artifact IDs. Unknown artifact IDs fail validation. Known but unreachable policy targets are ignored with diagnostics.

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

Direct cache IO returns or accepts a concrete cached-instance reference of the form `artifact_id + fingerprint`. When such a reference is later used as a provided root, planning treats it like the same artifact supplied normally, apart from provenance diagnostics.

## Run Request Model

A `RunRequest` is the canonical language-neutral request shape.

```text
RunRequest
  inputs: map<SemanticArtifactId, SourceHandle | CachedArtifactReference>
  outputs: map<SemanticArtifactId, TargetHandle | ordered-list<TargetHandle>>
  run_policy?: PlanningRunPolicy
  cache_policy?: map<SemanticArtifactId, ArtifactCachePolicy>
```

Required fields:

- `inputs`: caller-supplied source artifacts, settings artifacts, explicitly supplied intermediate artifacts, or explicit cached-root references for those same semantic artifacts;
- `outputs`: requested semantic artifact IDs and their caller materialization targets.

Optional fields:

- `run_policy`: planning or execution-strategy policy;
- `cache_policy`: exact per-artifact cache-use intent.

`run_policy` must not suppress requested outputs or weaken materialization obligations.

## Artifact Addressing and References

Request fields use `ArtifactKey` values or their string form:

```text
DOMAIN//KIND
OVERRIDE_DOMAIN//DOMAIN//KIND
```

Language bindings may expose nested mappings that normalize to the same keys:

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

- nested keys must be strings;
- direct `ArtifactKey` entries map directly to values, not nested mappings;
- normalized paths must resolve to two or three path components;
- duplicate artifact IDs are rejected;
- structural validation errors across `inputs`, `outputs`, and `cache_policy` should be accumulated and reported together.

The public model distinguishes two related address kinds:

- a **semantic artifact ID** says what artifact is needed, produced, cached, or materialized;
- a **concrete cached-instance reference** says which published artifact instance should be rebound as a provided root.

The concrete reference shape is:

```text
CachedArtifactReference
  artifact_id: SemanticArtifactId
  fingerprint: ArtifactFingerprint
```

Plain semantic artifact IDs remain the default for normal planning, outputs, and cache policy. Concrete cached-instance references are explicit pinning mechanisms used by saved-task and upload-first workflows. The fingerprint is not an untyped global identifier: it must be paired with the semantic artifact ID and validated against the manifest declaration and cache metadata before binding.

The textual representation should remain compatible with a future URI form, but this overview does not standardize the final URI syntax.

## Settings and Defaults

Settings are normal artifacts. They are read by stages through normal input bindings and participate in produced-artifact fingerprints like any other effective input.

Caller-supplied settings are bound before planning. Declaration defaults are resolved lazily: the planner owns relevance discovery and requests only the defaults needed by the request-specific graph.

A resolved default behaves exactly like a supplied graph root after it is bound. Default-source providers must be deterministic and local to the setting declaration plus optional caller override; they must not inspect broader request shape, planner state, cache state, wall-clock time, or resource availability.

A cached settings root referenced by `artifact_id + fingerprint` may be bound and read before normal graph planning proceeds, so planning hooks see the same settings value they would have seen from an ordinary supplied settings source.

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
38. A concrete cached-instance reference is `artifact_id + fingerprint`, not a bare fingerprint.
39. Plain semantic artifact IDs remain the default public addressing form.
40. Cached settings roots may be early-bound for planning when explicitly referenced as provided roots.
