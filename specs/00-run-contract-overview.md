# Chunky Run Contract — Overview

## Purpose

This specification defines Chunky's public run contract and the high-level internal architecture used to satisfy a run request.

The contract allows callers to request one or more outputs from one or more inputs without manually selecting every pipeline step. The engine resolves the required stages, reuses cached artifacts when valid, schedules work safely across constrained resources (compute), and materializes the requested targets.

The contract is language-neutral. Python is the first implementation target, but the same request, artifact, stage, cache, event, and scheduling model should be expressible from Rust or other future bindings.

## Design Goals

The run contract must:

- describe requested work as a data model, not as Python-only syntax;
- support synchronous and asynchronous execution;
- infer required upstream work from requested outputs;
- support multiple outputs from one shared dependency graph;
- treat source inputs, settings, intermediate outputs, final outputs, and diagnostics as artifacts;
- make cache reuse deterministic, explainable, and safe;
- support resource-aware scheduling for CPU, GPU, memory, and unconstrained work;
- expose progress through one typed event stream;
- preserve enough diagnostics to debug planning, cache reuse, execution, and materialization.

## Non-Goals

The first version does not need to support:
- durable cross-process background jobs;
- distributed scheduling;
- remote cache backends;
- per-output await;
- schema compatibility negotiation beyond exact matching;
- rich constraint expressions beyond exact equality;
- reachability-aware cache garbage collection.

## Public API Shape

Language bindings may expose idiomatic syntax, but all bindings must compile down to the same conceptual `RunRequest` model.

### Synchronous Run

```python
from chunky import chunky_run
from chunky.artifacts import SourceAudioArtifact, TargetVttArtifact

result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("output.vtt"),
    },
)
```

### Speaker-Labeled Output

Requesting a speaker-labeled transcript implies speaker detection. The caller may provide explicit speaker detection settings, but the planner may synthesize safe defaults when settings are omitted.

```python
from chunky import chunky_run
from chunky.artifacts import SourceAudioArtifact, TargetVttArtifact
from chunky.settings import SourceSettingsArtifact

result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        "speaker_detection::diarization_settings": SourceSettingsArtifact.from_object({
            "detect_min_speakers": 2,
            "detect_max_speakers": 10,
        }),
        "render::webvtt_settings": SourceSettingsArtifact.from_object({
            "include_speakers": True,
        }),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("speaker-labelled-output.vtt"),
    },
)
```

### Explicit VAD And Diarization Profiles

VAD and diarization profile settings are supplied as separate profile lists. `speech_detection::vad_detector_profiles` configures one or more VAD passes, while `speech_detection::diarization_detector_profiles` configures one or more diarization passes. Speaker-count hints and diarization runtime options remain in `speaker_detection::diarization_settings`.

> __Note to integrators and agents:__ Documentation and tests will use `pyannote/speaker-diarization-community-1` for diarization examples and documentation. Avoid `pyannote/speaker-diarization-3.1` in unless the behavior of that older model is specifically under test. This model performs significantly differently to "speaker-diarization-community-1". It clusters speakers much less aggressively / effectively causing significantly more speaker labels and speaker turns to be generated. Incorrect speaker turns can negatively impact chunk planning unless the are exclude from the planing stage.

```python
from chunky import chunky_run
from chunky.artifacts import SourceAudioArtifact, TargetVttArtifact
from chunky.settings import SourceSettingsArtifact

result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        "speech_detection::vad_settings": SourceSettingsArtifact.from_object({
            "detection_batch_size": 3,
        }),
        "speech_detection::vad_detector_profiles": SourceSettingsArtifact.from_object([
            {
                "detector_id": "aggressive",
                "detector_model": "silero-vad",
                "confidence": 1.00,
                "enabled": True,
                "speech_onset_pad_duration_ms": 20,
                "speech_offset_pad_duration_ms": 30,
                "min_speech_duration_ms": 35,
                "vad_threshold": 0.60,
                "vad_neg_threshold": 0.45,
                "vad_max_speech_duration_s": 30,
                "vad_min_silence_at_max_speech_ms": 80,
                "vad_min_silence_duration_ms": 80,
            },
            {
                "detector_id": "normal",
                "detector_model": "silero-vad",
                "confidence": 0.75,
                "enabled": True,
                "speech_onset_pad_duration_ms": 60,
                "speech_offset_pad_duration_ms": 90,
                "min_speech_duration_ms": 90,
                "vad_threshold": 0.50,
                "vad_neg_threshold": 0.35,
                "vad_max_speech_duration_s": 30,
                "vad_min_silence_at_max_speech_ms": 80,
                "vad_min_silence_duration_ms": 100,
            },
            {
                "detector_id": "permissive",
                "detector_model": "silero-vad",
                "confidence": 0.45,
                "enabled": True,
                "speech_onset_pad_duration_ms": 100,
                "speech_offset_pad_duration_ms": 140,
                "min_speech_duration_ms": 120,
                "vad_threshold": 0.42,
                "vad_neg_threshold": 0.27,
                "vad_max_speech_duration_s": 30,
                "vad_min_silence_at_max_speech_ms": 80,
                "vad_min_silence_duration_ms": 120,
            },
        ]),
        "speaker_detection::diarization_pynnote_settings": SourceSettingsArtifact.from_object({
            "detect_min_speakers": 2,
            "detect_max_speakers": 10,
            "diarization_device": "cuda",
            "segmentation_batch_size": 128,
            "embedding_batch_size": 128,
        }),
        "speech_detection::diarization_detector_profiles": SourceSettingsArtifact.from_object([
            {
                "detector_id": "diarization-standard",
                "detector_model": "pyannote/speaker-diarization-community-1",
                "confidence": 0.35,
                "speech_onset_pad_duration_ms": 80,
                "speech_offset_pad_duration_ms": 120,
                "min_speech_duration_ms": 150,
            },
        ]),
        "render::webvtt_settings": SourceSettingsArtifact.from_object({
            "include_speakers": True,
        }),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("speaker-labelled-output.vtt"),
    },
)
```

### Stage-Specific Audio Processing

Audio-consuming stages declare their own audio settings. When two stages need different preprocessing, the planner resolves separate `audio::prepared` requirements and reuses only the prepared audio instances with matching constraints and transforms. This example gives diarization a timing-preserving, noise-suppressed signal while ASR gets a loudness-normalized signal.

```python
from chunky import chunky_run
from chunky.artifacts import SourceAudioArtifact, TargetVttArtifact
from chunky.settings import SourceSettingsArtifact

result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        "speaker_detection::diarization_settings": SourceSettingsArtifact.from_object({
            "detect_min_speakers": 2,
            "detect_max_speakers": 10,
            "diarization_model": "pyannote/speaker-diarization-community-1",
            "diarization_device": "cuda",
        }),
        "speaker_detection::diarization_audio_settings": SourceSettingsArtifact.from_object({
            "noise_suppression": True,
            "noise_suppression_engine": "rnnoise",
            "noise_reduction_db": 8,
            "normalization": "off",
        }),
        "asr::settings": SourceSettingsArtifact.from_object({
            "asr_model": "whisper-large-v3",
            "language": "en",
        }),
        "asr::audio_settings": SourceSettingsArtifact.from_object({
            "noise_suppression": False,
            "normalization": "loudness",
            "target_loudness_lufs": -18,
            "true_peak_limit_dbfs": -1,
            "loudness_range_target_lu": 11,
        }),
        "render::webvtt_settings": SourceSettingsArtifact.from_object({
            "include_speakers": True,
        }),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("speaker-labelled-output.vtt"),
    },
)
```

### Setting from file
Which are custom configured and seldom changing can be loaded from file

```python
from chunky import chunky_run
from chunky.artifacts import SourceAudioArtifact, TargetVttArtifact
from chunky.settings import SourceSettingsArtifact

result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        "speech_detection::vad_settings": SourceSettingsArtifact.from_object({
            "detection_batch_size": 3,
        }),
        "speech_detection::vad_detector_profiles": SourceSettingsArtifact.from_yaml_file("./vad_detector_profiles.yaml"),
        "speaker_detection::diarization_settings": SourceSettingsArtifact.from_yaml_file("./diarization_settings.yaml"),
        "speech_detection::diarization_detector_profiles": SourceSettingsArtifact.from_yaml_file("./diarization_detector_profiles.yaml"),
        "render::webvtt_settings": SourceSettingsArtifact.from_object({
            "include_speakers": True,
        }),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("speaker-labelled-output.vtt"),
    },
)
```

### Multiple Outputs
Multiple outputs are resolved into one shared dependency graph. Shared upstream artifacts must be produced once and reused.

```python
result = chunky_run(
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        "speaker_detection::diarization_settings": SourceSettingsArtifact.from_object({
            "detect_min_speakers": 2,
            "detect_max_speakers": 10,
        }),
        "render::webvtt_settings": SourceSettingsArtifact.from_object({
            "include_speakers": True,
        }),
    },
    outputs={
        "render::webvtt_transcript": TargetVttArtifact.to_file_path("speaker-labelled-output.vtt"),
        "speaker_detection::speaker_embeddings": TargetJsonArtifact.to_file_path("speaker-embeddings.json"),
    },
)
```

### Artifact Cache

When enabled, the artifact cache stores reusable semantic artifacts. Cache keys are based on artifact identity, schema, fingerprints, resolved settings, and producer behavior. Target output paths are materialization metadata and must not be part of semantic cache keys.

```python
result = chunky_run(
    artifact_cache=FileArtifactCache(
        folder_path="./intermediate",
        delete_artifacts_older_than_seconds=259200,
    ),
    inputs={
        "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
    },
    outputs={
        "transcript": TargetVttArtifact.to_file_path("output.vtt"),
    },
)
```

### Asynchronous Run
`chunky_run` and `chunky_run_away` use the same planner, scheduler, cache, event model, and result model.

```python
from chunky import chunky_run_away

async def main() -> None:
    handle = chunky_run_away(
        inputs={
            "source::audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "transcript": TargetVttArtifact.to_file_path("output.vtt"),
        },
    )

    print(f"Started task {handle.task_id}")
    result = await handle.awaitable()
```

Language-neutral meaning:

```text
chunky_run(request) == await chunky_run_away(request).awaitable()
```

## Run Request

A run request is the canonical language-neutral data model.

```text
RunRequest
  inputs: map<ArtifactId, Artifact>
  outputs: map<ArtifactId, TargetArtifact>
  artifact_cache?: ArtifactCacheConfig
  execution_policy?: ExecutionPolicy
  diagnostics?: DiagnosticsConfig
  handlers?: HandlerConfig
  registry_overrides?: RegistryPatch
```

Required fields:

- `inputs`: source artefacts, settings artefacts, or explicitly supplied intermediate artifacts.
- `outputs`: requested semantic output artifact IDs and their target sinks.

Optional fields:

- `artifact_cache`: cache configuration.
- `execution_policy`: failure, cancellation, and scheduling behavior.
- `diagnostics`: manifest, logs, and debug artifact behavior.
- `handlers`: typed event subscribers.
- `registry_overrides`: per-run stage or artifact overrides.

## Minimal Stage/Artifact Contract
A stage declares semantic inputs and outputs, and emits the artifact declarations it owns. The runner passes artifact bindings that can satisfy the same semantic artifact from memory, a supplied file, or cache.

```python
class Stage(Protocol):
    stage_id: str
    behavior_version: str
    artifact_declarations: list[ArtifactDeclaration]
    inputs: list[ArtifactRequirement]
    outputs: list[str]

    def run(self, context: StageInvocationContext) -> None: ...
```

A binding is the physical transport abstraction. Within one process, an upstream stage may pass a typed object directly to a downstream stage without a serialize/deserialize round trip. The same produced artifact is still serialized through its codec and committed to the cache for recoverability and later reuse. After restart or cache hit, the binding deserializes the cached bytes back into the typed value.

Codecs include both `mime_type` and `extension`. The extension is the canonical file-extension hint for cache files and target materialization; it is not part of the stage's semantic dependency contract.


## Internal Architecture Overview

Chunky resolves each `RunRequest` into a task graph.

The graph is built from registered stages and artifacts:

- A **stage** is a deterministic unit of work that consumes artifacts and produces artifacts.
- An **artifact** is any value that enters, moves through, or leaves the pipeline.
- A **stage registry** declares which stages exist, what artifacts they may consume, what artifacts they may produce, and what resource constraints they require.
- A **planner** starts from the requested outputs and walks backwards through the registry until every required input is either supplied by the caller, produced by another stage, or synthesized as a safe default setting. The planner should produce a **unweighted directed acyclic graph** and fail if it encounters a graph cycle, multiple paths, or a graph which can not be build from the provided inputs.
- A **task plan** is the per task graph which the planner build to be run by the scheduler. The task plan is built as per the needs of the desired output artefacts, the settings provided to each stage. 
- A **scheduler** executes ready _task plan_ invocations when their input artifacts are available and their resource constraints allow them to run.
- A **cache** may satisfy an artifact before the producing stage runs. Cache hits skip compute work but still produce normal artifact availability events.
- A **materializer** writes requested semantic outputs to target sinks such as files.

The engine should treat the graph as one shared DAG per request. A multi-output request should not duplicate upstream work when outputs share dependencies.

Conceptual flow:

```text
RunRequest
  -> resolve requested outputs
  -> synthesize/default missing settings when allowed
  -> build shared artifact dependency DAG
  -> prue graph for artifacts with cache hits and lock cache entries to prevent Time of check, time of use viloations
  -> bind supplied inputs and cahe hits
  -> schedule stage invocations
  -> validate produced artifacts
  -> materialize requested targets
  -> return RunResult
```

---

## Learned Facts To Actualize Later

These facts were learned while consolidating `03-stage-implementations/02-expected_stages.md` and should be reconciled in this document during its own design pass.

1. Public examples should use canonical artifact IDs such as `render::webvtt_transcript`, `render::speech_document`, `source::audio`, and settings artifact IDs.
2. Render outputs are target artifacts; renderer settings remain normal settings artifacts.
3. The planner should build a shared DAG from artifact requirements, not direct stage dependencies. Stages may dynamically resolve required inputs and output capabilities from resolved settings plus read-only deterministic context.
4. Cache state should not shape the graph. Cache hits should affect execution skipping only.
5. Audio preparation should be represented through generic `audio::prepared` logical requirements and canonical concrete instances, with possible direct binding to `source::audio`.
6. The conceptual flow should avoid saying the graph is pruned by cache hits during planning. Cache lookup occurs during execution immediately before a stage would run.
7. Supplied artifacts are graph roots by default and win over producer paths unless an explicit recompute/force-producer override is requested.
8. Pre-ASR event terminology should use "utterance" rather than "word" unless referring to actual ASR transcript words/tokens.
