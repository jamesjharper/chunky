# Chunky Run Contract - Graph Creation

## Purpose

This document defines how a `RunRequest` is resolved into a graph of stage invocations and how that graph is scheduled.

## 1. What is the Plan
The plan is a directed acyclic graph (DAG). In its final form, nodes are planned stage invocations and edges are artifacts required for the specific run contract given

The graph exists to:
- Dynamically connect ASR operations based on the requested inputs, outputs, and settings.
- Optimize hardware usage by allowing independent stages to run concurrently when their resource requirements allow it.
- Support cache-aware execution, including pause, resume, cancel, recovery, and stateful multi-pass or multi-request flows.
- Avoid unnecessary computation by reusing supplied artifacts and cache hits where the requested graph shape permits it.

The plan goes through several stages before it is finalized:

__1: Stage manifest plan:__
This graph is based on all inputs and outputs supported by the configured stages in the stage manifest. It only changes when the `stage manifest` changes, which is not expected to happen often.

__2: Proposed Plan:__
This is a copy of the stage manifest plan with unused outputs culled from nodes. Nodes with all outputs culled are removed unless they produce intermediate outputs needed elsewhere.

__3: The evaluated Plan:__
This is the proposed plan after it has been walked to find the required paths for producing the desired targets. At this stage, unused nodes are culled and ambiguous nodes are expanded.

__4: The execution Plan:__
This is the evaluated plan after cache hits and already supplied inputs have been considered. It records which stages need to run, which stages can be skipped, and the expected fingerprint values for produced artifacts.

## 2. How Each Plan Is Built

### 2.1 Stage Manifest Plan

The planner starts from the configured stage manifest and builds a static candidate graph. This graph contains every stage that may be used for the current runtime configuration, along with every artifact relationship those stages declare.

In this graph:

- 1. Stage nodes declare the artifacts they can consume and produce.
- 2. Artifact edges represent sources, settings, intermediate values, diagnostics, and targets.
- 3. Source artifact nodes represent possible provided input artifacts, such as `source::audio`.
- 4. Target artifact nodes represent possible requested output artifacts, such as `render::webvtt_transcript`.

The stage manifest plan is independent of any single `RunRequest`. It changes only when the configured stage manifest changes.

While building the graph an error is raised when a graph cycle is detected.


### 2.1.1 Declaration ownership and codecs
The stage manifest plan is built from declarations emitted by stages and shared artifact packages. A stage emits declarations for source, settings, target, and diagnostic artifacts that are unique to itself or its subsystem, and for intermediate artifacts that it produces. A consumer of an intermediate artifact references the declaration; it does not redeclare it.

For phased intermediate families, the producer of a phase emits the declaration for that phase. Multiple producers may emit declarations for the same phased family only when their owned phase numbers or phase ranges do not overlap. The manifest builder validates this before graph creation.

Artifact declarations include a codec. The codec supplies the MIME type, preferred file extension, and serde behavior used at cache/materialization boundaries. Graph creation treats this as artifact metadata; it should not force a stage to require file-on-disk access.


<!-- example-implementation:start -->
### Example implementation: manifest collection and graph-time fingerprints

This stripped-down manifest builder collects declarations emitted by stages. Non-phased declarations must be unique unless they are identical shared declarations. Phased declarations may share a family only when their owned phase ranges do not overlap.

```python
class ManifestError(ValueError):
    pass


class StageManifest:
    """Collects declarations emitted by stages and validates ownership."""

    def __init__(self, stages: list[Stage]):
        self.stages = stages
        self.declarations = self._collect_declarations(stages)
        self._validate_requirements_and_outputs(stages)

    @staticmethod
    def _same_non_phase_declaration(a: ArtifactDeclaration, b: ArtifactDeclaration) -> bool:
        return (
            a.artifact_id == b.artifact_id
            and a.role == b.role
            and a.version == b.version
            and a.codec.mime_type == b.codec.mime_type
            and a.codec.extension == b.codec.extension
            and a.cacheable == b.cacheable
            and a.phase_owner is None
            and b.phase_owner is None
        )

    def _collect_declarations(self, stages: list[Stage]) -> dict[str, ArtifactDeclaration]:
        declarations: dict[str, ArtifactDeclaration] = {}
        phase_owners: list[tuple[str, ArtifactDeclaration]] = []

        for stage in stages:
            for declaration in stage.artifact_declarations:
                if declaration.phase_owner is not None:
                    for owner_stage_id, existing in phase_owners:
                        if declaration.phase_owner.overlaps(existing.phase_owner):
                            raise ManifestError(
                                "overlapping phased artifact declaration: "
                                f"{declaration.phase_owner.family_id} "
                                f"{declaration.phase_owner.phase_min}-{declaration.phase_owner.phase_max} "
                                f"owned by {stage.stage_id} overlaps "
                                f"{existing.phase_owner.phase_min}-{existing.phase_owner.phase_max} "
                                f"owned by {owner_stage_id}"
                            )
                    phase_owners.append((stage.stage_id, declaration))
                    declarations[declaration.artifact_id] = declaration
                    continue

                existing = declarations.get(declaration.artifact_id)
                if existing is not None and not self._same_non_phase_declaration(existing, declaration):
                    raise ManifestError(f"duplicate artifact declaration: {declaration.artifact_id}")
                declarations[declaration.artifact_id] = declaration

        return declarations

    def _validate_requirements_and_outputs(self, stages: list[Stage]) -> None:
        for stage in stages:
            for requirement in stage.inputs:
                if requirement.artifact_id not in self.declarations:
                    raise ManifestError(
                        f"stage {stage.stage_id} requires undeclared artifact: {requirement.artifact_id}"
                    )
            for output_id in stage.outputs:
                if output_id not in self.declarations:
                    raise ManifestError(f"stage {stage.stage_id} produces undeclared artifact: {output_id}")
```

Fingerprints include declaration identity and `Codec.extension`, so a change in serialized representation invalidates stale cache entries. Produced fingerprints are based on producer behavior and input fingerprints, not by rereading the output bytes.

```python
def stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return blake3(raw).hexdigest()[:16]


def supplied_fingerprint(declaration: ArtifactDeclaration, supplied: SuppliedArtifact) -> str:
    if supplied.value is not None:
        content_bytes = declaration.codec.serialize(supplied.value)
    elif supplied.file_path is not None:
        content_bytes = supplied.file_path.read_bytes()
    else:
        raise ValueError("supplied artifact must have value or file_path")

    payload = {
        "kind": "supplied",
        "artifact_id": declaration.artifact_id,
        "role": declaration.role.value,
        "version": declaration.version,
        "codec": {
            "mime_type": declaration.codec.mime_type,
            "extension": declaration.codec.extension,
        },
        "content_blake3": blake3(content_bytes).hexdigest(),
    }
    return stable_json_hash(payload)


def produced_fingerprint(
    declaration: ArtifactDeclaration,
    stage: Stage,
    input_instances: dict[str, ArtifactInstance],
) -> str:
    payload = {
        "kind": "produced",
        "artifact_id": declaration.artifact_id,
        "role": declaration.role.value,
        "version": declaration.version,
        "codec": {
            "mime_type": declaration.codec.mime_type,
            "extension": declaration.codec.extension,
        },
        "producer": {
            "stage_id": stage.stage_id,
            "behavior_version": stage.behavior_version,
        },
        "inputs": {k: input_instances[k].fingerprint for k in sorted(input_instances)},
    }
    return stable_json_hash(payload)
```
<!-- example-implementation:end -->


### 2.2 Proposed Plan

The proposed plan is created by applying a specific `RunRequest` to the stage manifest plan.

For example:

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

In this request, `source::audio` is a supplied source artifact and `render::webvtt_transcript` is a requested target artifact.

To build the proposed plan, the planner removes target nodes that are not configured for the request. Only supplied inputs, requested outputs, and configured settings, whether explicit or inferred from defaults, remain eligible for the planned graph.

Unused outputs are culled from stage nodes. Nodes with all outputs culled are removed unless they produce intermediate outputs needed elsewhere.

Supplied artifacts win over producer paths by default. If an intermediate artifact is supplied, the planner uses that value rather than planning a producer path for it.

### 2.3 Evaluated Plan

The evaluated plan is created by walking the proposed plan to discover the concrete stage invocations needed to produce the requested targets.

During this walk, unused nodes are culled and ambiguous producer choices are expanded into concrete stage invocations.

For each remaining target artifact, the planner discovers producer paths backward through the graph.

```
 source::audio
      │
      ├───────────────────────────────────────────────────────────────┐
      │                                                               │
      ├─ settings::audio_settings(1)                                  ├─ settings::audio_settings(2)
      │                                                               │
      ▼                                                               ▼
 ┌───────────────┐                                            ┌───────────────┐
 │ prepare_audio │                                            │ prepare_audio │
 └───────────────┘                                            └───────────────┘
      │                                                               │
      ▼                                                               ▼ 
 audio::prepared(1)                                             audio::prepared(2)
      │                                                               │
      ├───────────────────────────────┐                               │
      │                               │                               │
      ▼                               ▼                               ▼
 ┌────────────┐                 ┌─────────────┐                 ┌──────────────┐
 │ transcribe │                 │ diarize     │                 │ detect_music │
 └────────────┘                 └─────────────┘                 └──────────────┘
      │                               │                               │
      ├─ settings::asr                ├─ settings::diarization        ▼
      ├─ settings::language           │                       audio::music_spans
      │                               ▼                               │
      ▼                       speaker::segments                       │
 transcript::token_text               │                               │
      │                               │                               │
      ├───────────────────────────────┘                               │
      │                                                               │
      ├─ settings::alignment                                          │
      │                                                               │
      ▼                                                               │
 ┌────────────┐                                                       │
 │ align      │                                                       │
 └────────────┘                                                       │
      │                                                               │
      ▼                                                               │
 transcript::timed_words                                              │
      │                                                               │
      └───────────────────────────────┬───────────────────────────────┘
                                      │
                                      ├─ settings::vtt
                                      ├─ settings::render
                                      │
                                      ▼
                               ┌────────────┐
                               │ render_vtt │
                               └────────────┘
                                      │
                                      ▼
                          render::webvtt_transcript
```

Path discovery starts at the requested output edge or edges and walks toward source edges. At each stage node, the path fans out across the artifacts that the stage requires. If two paths reach the same stage invocation under the same effective settings, only one path needs to continue.

A path terminates when it reaches:
- A supplied input artifact.
- A stage that has no required input artifacts.

An error is raised when
- a required input can not be resolved

During path discovery, each planned stage invocation receives concrete instances for its settings artifacts. From those settings, the stage may resolve:

- Which possible inputs are required for this request.
- Which possible inputs are optional.
- Which declared outputs are relevant to the request.

__Required vs Optional artifacts__
- __Required__ artifacts participate in path discovery and must resolve to either a supplied artifact or a producer path.
- __Optional__ artifacts are considered during path discovery, but they do not make the graph invalid if no valid producer or supplied artifact can satisfy them.

Planned stage invocations must record the explicit subset of outputs requested from that invocation. Cache-hit skipping should require only those planned outputs to be available.

#### 2.3.1 Phased Intermediate Artifacts
Some intermediate artifact families opt into numbered processing phases:

```text
{domain}::{content_kind}::{phase}
```
The phase number is a sparse processing phase, not a schema version. Phase numbers are meaningful only within their own artifact family.

For phased families:

- A numbered input requirement is a less-than-or-equal selector by default. For example, `transcript::token_text::200` resolves to the highest enabled and available phase in the same family less than or equal to `200`.
- A numbered output declaration is an exact concrete phase, such as `transcript::token_text::201`.
- A phase-less intermediate requirement asks for the completed version of that artifact family, such as `transcript::token_text`.

The planner is responsible for terminal remapping. If a selected terminal producer emits a concrete phase-numbered output that satisfies a phase-less requirement, the result is stored under the phase-less final artifact ID.

When a concrete phase-numbered output is remapped to a phase-less final artifact ID:

- Store only the phase-less final artifact ID.
- Do not create an additional cache alias under the concrete phase-numbered ID.
- Record the declared concrete output phase in the run manifest for provenance.

Phase behavior applies only to artifact families that explicitly declare it. Source, settings, target, diagnostic, and non-phased intermediate artifacts do not receive numeric phase selector behavior from their textual ID shape alone.

#### 2.3.2 Settings Propagation

Some upstream planning decisions need to push settings downstream. A common example is an audio-consuming stage declaring the exact prepared audio it needs, such as sample rate, channel count, or custom audio enhancement processing.

__Example:__
```python
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

A stage may emit a settings artifact associated with an artifact edge. When that settings artifact reaches a downstream node:
- If the downstream stage declares or requires the same settings type, the inherited settings are merged into that stage's effective settings.
- If the downstream stage does not declare that settings type, the settings continue through that stage's outgoing edges.

Settings are resolved in this order:
1. Defaults.
2. User-configured settings.
3. Inherited settings pushed onto this path.

This means pushed path-specific settings override user configuration, and user configuration overrides defaults.

If the same logical stage receives conflicting effective settings, the planner must duplicate the stage node. Conflict can be detected by calculating a settings fingerprint.

__Subsequent path rules:__

- If another path pushes the same settings fingerprint into the stage, reuse the existing invocation.
- If another path pushes a unique settings fingerprint into the stage, create another invocation using the base non-inherited configuration plus the new inherited settings.
- If a path does not push inherited settings, use the base non-inherited configuration for that invocation.

### 2.4 Execution Plan

The execution plan is created by applying cache availability, supplied artifacts, disabled-stage policy, and cache reservations to the evaluated plan. It is the final immutable work contract handed to the scheduler. The scheduler records live status in the execution manifest, but it must not change graph shape, artifact identity, producer selection, or the set of outputs requested from each invocation.

Execution-plan finalization records:

1. Stage name.
2. Stage identity: `{stage name} | {stage behavior version} | {effective settings fingerprint} | {task id}`.
3. Dependent stages: the stage invocations waiting for this invocation's outputs.
4. Planned stage disposition:
   - `{waiting}`: the invocation is expected to run once its artifacts and resources are available.
   - `{waiting_for_global_cache}`: the invocation is waiting because another task is already producing one or more of the same cacheable output fingerprints.
   - `{no_operation}`: the invocation should not run because it is disabled or all requested outputs are already supplied or cache-reserved.
5. Output artifacts: every requested output artifact, its expected fingerprint, producer, cacheability, materialization target, and current plan-time availability.
6. Input artifacts: every required and optional input artifact, its selected binding, fingerprint, and availability.

Cache lookup and reservation happen after the evaluated graph shape is fixed and before the execution plan is finalized.

For each planned invocation, the planner checks only the requested output subset for that invocation. The invocation may be skipped only when all requested outputs for that invocation are cache hits or supplied artifacts.

When a cache hit is selected, execution-plan finalization must reserve it before exposing it to downstream work. Cache reservation should be atomic:

```text
1. lookup artifact by expected fingerprint
2. validate cache entry metadata against the expected artifact identity and schema
3. create a usage lock or reservation
4. mark the artifact as available_cache_reserved
5. emit artifact_cache_hit and artifact_available events
```

If lookup misses or reservation fails, the artifact remains unavailable, an `artifact_cache_miss` event should be emitted for cacheable requested outputs, and the producer invocation must run if one exists.

Cache reservations protect artifacts from garbage collection for the lifetime of the task. They should be released when the task completes, fails, or is cancelled. Recovery may release reservations owned by tasks that are no longer running.

Execution-plan finalization must also consult global cache management before allowing a cacheable producer invocation to run. If another live task already owns a write reservation for any requested output fingerprint that this invocation would produce, this task must not run a duplicate writer. The affected invocation, and any downstream invocations that require its outputs, remain waiting until the existing writer completes, fails, or releases the reservation.

If the existing writer completes successfully, the waiting task re-checks the produced artifact, creates its own usage reservation, marks the artifact `available_cache_reserved`, and may skip the duplicate producer. If the existing writer fails or releases the write reservation without producing a valid artifact, the waiting task may attempt to acquire the write reservation itself and run the producer.

Global cache coordination is defined in [Cache management](../../03-bindings/02-cache-management.md). Artifact fingerprinting is described in more detail in [Artifact caching and fingerprinting](#3-artifact-caching-and-fingerprinting). Cache-aware execution is described in [Cache and Execution](#5-cache-and-execution), and runtime orchestration is described in [Scheduling](../../07-scheduling/eventing-and-task-control-model-spec.md).

## 3. Artifact caching and fingerprinting

Fingerprint payload composition is centralized in [Fingerprint Spec](../../04-fingerprinting/01-fingerprints.md).

Planning owns only the selection side of fingerprinting:

- It determines which supplied artifacts, produced artifacts, settings, and stage invocations participate in the run.
- It computes the planner-owned `pedigree_fingerprint` from stage fingerprint, keyed input fingerprints, and aggregate settings fingerprint.
- It records the `expected_fingerprint` for each planned output binding so execution can validate cache instances and writers against the plan.

The planner still derives fingerprints by walking forward from available inputs after the graph shape is known. The centralized spec contains the current builder payloads and the updated ASCII diagrams.

## 5. Cache and Execution
The planner determines the evaluated graph shape without considering cache availability. Execution-plan finalization then applies supplied artifacts, cache hits, usage reservations, write reservations, and disabled-stage policy. A stage invocation may be skipped only if all requested outputs for that invocation are supplied or cache-reserved.

In the current model, cache decisions are driven by planned output fingerprints, not by a separately materialized "stage invocation fingerprint" object. Those expected output fingerprints are derived from:

- Stage identity and `behavior_version`.
- Effective settings fingerprints, including defaults and inherited settings.
- Supplied and produced input artifact fingerprints.
- Produced artifact identity.
- Produced artifact codec identity.

For phase-less final intermediate artifacts, the final artifact fingerprint still preserves the actual producing invocation's pedigree even when the stored artifact ID is the phase-less terminal ID.
