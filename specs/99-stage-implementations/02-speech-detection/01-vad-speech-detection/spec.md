# VAD Speech Detection Stage Spec

## Spec Metadata

| Field | Value |
| --- | --- |
| Semantic stage name | VAD speech detection |
| Semantic stage ID | `speech_detection.vad_speech_detection` |
| Stage behavior version | `001` |
| Stage family | Speech detection |
| Spec status | Draft template example |
| Primary output | `utterance_detections/vad_stream/1` |
| Default resources | `cpu_compute_bound`, or `gpu_compute_bound` when the selected VAD model requires GPU |

This document is both the VAD speech detection spec and the recommended template shape for future stage specs. Future stages should keep the same section order unless the stage has a clear reason to add a focused companion file, such as a large acceptance-test matrix.

## Purpose

This stage runs one or more VAD detector profiles over a resolved audio source and emits raw candidate speech intervals.

The output is intentionally low-level detector evidence. It may contain overlapping detections, duplicate coverage from multiple profiles, short fragments, and long speech regions. Later stages are responsible for merging VAD evidence with diarization, applying detector-profile padding, suppressing phantom events, shaping word-like boundaries, assigning speaker roles, and planning ASR chunks.

## Non-Goals

This stage does not:

- merge overlapping VAD detections into a canonical word-like stream;
- apply `speech_onset_pad_duration_ms` or `speech_offset_pad_duration_ms` to emitted intervals;
- suppress short events using `min_speech_duration_ms`;
- run diarization or emit speaker labels;
- assign speakers, speaker roles, frames, chunks, or transcript tokens;
- materialize user-facing transcript outputs.

## Stage Registry Contract

### Possible Inputs

- `audio/prepared`
- `speech_detection/vad_settings`
- `speech_detection/vad_audio_settings`
- `speech_detection/vad_detector_profiles`

### Provided Outputs

- `utterance_detections/vad_stream/1`

### Optional Diagnostic Outputs

- `diagnostics/vad_profile_events`
- `diagnostics/vad_merged_events`
- `diagnostics/vad_settings_snapshot`
- `diagnostics/vad_run_report`

### Required Input Resolution

For `utterance_detections/vad_stream/1`, the stage requires:

- one audio artifact satisfying `speech_detection/vad_audio_settings`;
- resolved VAD settings;
- resolved VAD detector profiles;
- at least one enabled profile where `detector_type = vad`.

If source audio does not satisfy the requested VAD audio settings directly, the planner may insert or reuse an audio preparation stage that produces a compatible concrete audio instance. The VAD stage consumes the resolved audio artifact and should not perform implicit audio conversion itself.

### Default Resource Policy

Default resource selection is resolved from the enabled detector profiles:

- `gpu_compute_bound` when any selected VAD model requires GPU execution;
- otherwise `cpu_compute_bound`.

If a mixed CPU/GPU profile set is allowed by the implementation, the concrete invocation may either request both required resource classes or split execution into separate concrete invocations, provided the semantic output remains deterministic.

## Artifact Contracts

### Input: `audio/prepared`

Role: `intermediate`

The resolved audio resource selected for the VAD `audio/prepared` requirement must expose enough metadata for requirement validation:

- duration;
- sample rate;
- channel count;
- container or decoded representation;
- timing-preservation status.

The stage reads the resolved audio instance in the format required by the selected VAD adapter. The planner may satisfy this requirement by directly binding `source/audio` when the source already satisfies the VAD audio settings, or by inserting/reusing audio preparation.

### Input: `speech_detection/vad_settings`

Role: `settings`

Resolved settings:

| Setting | Type | Default | Required | Fingerprinted | Description |
| --- | --- | --- | --- | --- | --- |
| `enable` | bool | `true` | no | yes | Disables this semantic stage when `false`. |
| `detection_batch_size` | integer | `1` | no | yes | Maximum detector jobs that may run concurrently within this invocation. |

Validation:

- `detection_batch_size >= 1`.
- If `enable = false`, this stage must not be selected to produce required VAD output unless the requested output is supplied by another producer or caller-supplied artifact.

### Input: `speech_detection/vad_audio_settings`

Role: `settings`

Resolved settings:

| Setting | Type | Default | Required | Fingerprinted | Description |
| --- | --- | --- | --- | --- | --- |
| `audio_source_selector` | string | implementation default | no | yes | Logical audio source requested for VAD. |
| `sample_rate_hz` | integer | model default | adapter-dependent | yes | Required sample rate for the VAD adapter. |
| `channels` | integer | `1` | no | yes | Required channel count for VAD. |
| `timing_preserved` | bool | `true` | no | yes | Whether audio preparation must preserve source timing. |

Validation:

- `sample_rate_hz > 0`, when specified.
- `channels >= 1`.
- `timing_preserved` should normally be `true`; if an adapter allows non-preserved timing, the output must still map timestamps back to the original source timeline before emission.

### Input: `speech_detection/vad_detector_profiles`

Role: `settings`

Each profile contains:

| Field | Type | Default | Required | Fingerprinted | Description |
| --- | --- | --- | --- | --- | --- |
| `detector_id` | string | none | yes | yes | Stable profile identifier unique within this settings artifact. |
| `detector_type` | enum | none | yes | yes | Must be `vad` for this stage. Non-VAD profiles are ignored by this stage. |
| `detector_model` | string | implementation default | no | yes | VAD model or adapter identifier. |
| `confidence` | number | none | yes | yes | Finite confidence band assigned to events from this profile. |
| `enabled` | bool | `true` | no | yes | Whether this profile should run. |
| `speech_onset_pad_duration_ms` | integer | `100` | no | yes | Boundary shaping value used by downstream stages, not applied here. |
| `speech_offset_pad_duration_ms` | integer | `100` | no | yes | Boundary shaping value used by downstream stages, not applied here. |
| `min_speech_duration_ms` | integer | `250` | no | yes | Downstream culling value, not applied here. |
| `vad_threshold` | number | `0.5` | adapter-dependent | yes | Speech-entry threshold. |
| `vad_neg_threshold` | number | `vad_threshold - 0.15` | no | yes | Speech-exit or negative threshold. |
| `vad_max_speech_duration_s` | number | `30` | no | yes | Adapter max speech duration. |
| `vad_min_silence_at_max_speech_ms` | integer | `80` | no | yes | Silence used to split at max speech duration. |
| `vad_min_silence_duration_ms` | integer | `100` | no | yes | Silence needed before ending a speech chunk. |

Validation:

- `detector_id` values must be unique after filtering to enabled VAD profiles.
- `detector_type` must be `vad` for profiles executed by this stage.
- `0 <= confidence <= 1`.
- `0 <= vad_neg_threshold <= vad_threshold <= 1`, when both are specified.
- durations must be non-negative.
- at least one enabled VAD profile must remain after filtering.

The profile fields `speech_onset_pad_duration_ms`, `speech_offset_pad_duration_ms`, and `min_speech_duration_ms` are included in the profile artifact because downstream stages need them, but this stage must not use them to reshape raw detector output.

### Output: `utterance_detections/vad_stream/1`

Role: `intermediate`

The output is a flat ordered stream of raw VAD detections.

Schema:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detector_id` | string | yes | Profile that produced this detection. |
| `detector_type` | enum | yes | Always `vad`. |
| `confidence` | number | yes | Confidence value copied from the producing profile. |
| `raw_start` | number | yes | Raw start time in seconds on the original source timeline. |
| `raw_end` | number | yes | Raw end time in seconds on the original source timeline. |

Output invariants:

- `raw_start >= 0`.
- `raw_end > raw_start`.
- `raw_end <= source_audio_duration`, allowing only the implementation's documented timestamp precision tolerance.
- rows are sorted by `raw_start`, then `raw_end`, then `detector_id`;
- rows from different profiles may overlap;
- rows from the same profile may overlap only if the underlying adapter emits overlapping intervals and the adapter behavior version documents that choice.

## Behavior

### 1. Resolve Enabled VAD Profiles

Filter detector profiles:

```text
profile.enabled == true
and profile.detector_type == "vad"
```

If no enabled VAD profiles remain, planning or execution must fail with `vad_no_enabled_profiles` unless another producer or supplied artifact satisfies the requested output.

### 2. Resolve Audio Requirements

The stage consumes the concrete audio instance selected by the planner for `speech_detection/vad_audio_settings`.

The stage must treat source audio as immutable. Any conversion, resampling, channel mixing, enhancement, or timing-preserving preparation belongs to the audio preparation stage and must appear as an explicit artifact in the run graph.

### 3. Run Each Profile Independently

Each enabled profile runs over the full resolved audio instance.

Concurrency is bounded by:

```text
speech_detection/vad_settings/any.detection_batch_size
```

Profile execution order must not affect final output ordering or artifact fingerprints.

### 4. Adapter Mapping

When using a Silero-style VAD adapter, map profile fields as:

```text
threshold = profile.vad_threshold
max_speech_duration_s = profile.vad_max_speech_duration_s
min_silence_duration_ms = profile.vad_min_silence_duration_ms
speech_pad_ms = 0
neg_threshold = profile.vad_neg_threshold
```

Do not pass these profile fields to Silero at this stage:

```text
profile.speech_onset_pad_duration_ms
profile.speech_offset_pad_duration_ms
profile.min_speech_duration_ms
```

Those fields shape canonical utterance events later. Keeping them out of the raw detector pass preserves a clean separation between detector evidence and downstream interpretation.

If an adapter has its own unavoidable minimum-speech behavior, the adapter and stage behavior version must document it because it affects cacheable output semantics.

### 5. Normalize Adapter Output

For each adapter interval, emit one VAD row:

```text
{
  detector_id: profile.detector_id,
  detector_type: "vad",
  confidence: profile.confidence,
  raw_start: interval.start_on_source_timeline,
  raw_end: interval.end_on_source_timeline
}
```

Rows with non-finite timestamps or non-positive duration must fail validation unless the adapter explicitly classifies them as warnings and drops them before emission. Dropped adapter rows should be counted in diagnostics.

### 6. Concatenate and Order Rows

Concatenate rows from every enabled profile, then sort by:

1. `raw_start` ascending;
2. `raw_end` ascending;
3. `detector_id` ascending.

The stage must not merge rows across profiles.

## Fingerprinting and Cache Behavior

The output fingerprint for `utterance_detections/vad_stream/1` is stage-derived and must include:

- artifact ID and schema version;
- producing semantic stage ID;
- stage behavior version;
- resolved audio artifact fingerprint;
- resolved `speech_detection/vad_settings` fingerprint;
- resolved `speech_detection/vad_audio_settings` fingerprint;
- resolved `speech_detection/vad_detector_profiles` fingerprint;
- relevant VAD adapter provider IDs and provider versions.

Changing any enabled profile setting must invalidate the VAD output. Changing only a disabled profile should not invalidate the output if the resolved settings fingerprint supports pruning disabled profiles before fingerprinting. If the settings artifact fingerprints the full supplied profile list, disabled-profile changes will invalidate the output; either behavior is acceptable if it is documented and deterministic.

## Progress Events

The stage should emit typed task events using the canonical event stream.

Recommended event sequence:

```text
stage_state_changed: waiting_for_artifacts
stage_state_changed: waiting_for_compute
stage_state_changed: running
stage_step_progress: resolving_profiles
stage_step_progress: running_profile {detector_id}
stage_step_progress: normalizing_profile_events {detector_id}
artifact_available: utterance_detections/vad_stream/1
stage_state_changed: completed
```

On failure:

```text
stage_error
artifact_failed: utterance_detections/vad_stream/1
stage_state_changed: failed
```

Per-profile progress percentages are optional. If emitted, they must be best-effort diagnostics and must not affect output fingerprints.

## Diagnostics and Workspace Materialization

Diagnostic artifacts are optional semantic artifacts, not required user outputs.

Recommended diagnostic artifacts:

- `diagnostics/vad_profile_events`: one inspection table per enabled detector profile, preserving adapter output before global concatenation;
- `diagnostics/vad_merged_events`: the final ordered VAD stream in a human-inspectable format;
- `diagnostics/vad_settings_snapshot`: resolved settings and profiles used by this invocation;
- `diagnostics/vad_run_report`: counts, warnings, adapter versions, dropped-row counts, and timing metadata.

When a workspace exists and diagnostics are enabled, suggested materialized filenames are:

```text
vad/profile_events/{detector_id}.csv
vad/vad_stream.csv
vad/settings_snapshot.yaml
vad/run_report.json
```

These filenames are workspace layout suggestions only. They must not be used as artifact IDs or cache keys.

## Failure Codes

Recommended stable failure codes:

| Code | Meaning |
| --- | --- |
| `vad_disabled` | The stage was selected even though `enable = false`. |
| `vad_no_enabled_profiles` | No enabled VAD detector profiles were available. |
| `vad_invalid_detector_profile` | A selected profile failed validation. |
| `vad_audio_requirement_unsatisfied` | No concrete audio artifact satisfies VAD audio constraints. |
| `vad_adapter_unavailable` | The requested VAD model or adapter is not installed or cannot be loaded. |
| `vad_adapter_failed` | The adapter failed while processing audio. |
| `vad_invalid_adapter_interval` | The adapter produced an invalid interval that could not be normalized. |
| `vad_output_validation_failed` | The emitted artifact failed schema or invariant validation. |

## Acceptance Tests

### Contract Tests

1. Given one enabled VAD profile and valid audio, the stage emits `utterance_detections/vad_stream/1` with `detector_type = vad` on every row.
2. Given multiple enabled VAD profiles, the output contains rows from all profiles and preserves each row's `detector_id`.
3. Given overlapping intervals from different profiles, the output keeps the overlaps unchanged.
4. Given unsorted adapter output, the final artifact is sorted by `raw_start`, `raw_end`, and `detector_id`.
5. Given a disabled profile, no row is emitted with that profile's `detector_id`.
6. Given only disabled or non-VAD profiles, the stage fails with `vad_no_enabled_profiles`.
7. Given `speech_onset_pad_duration_ms`, `speech_offset_pad_duration_ms`, or `min_speech_duration_ms`, the emitted `raw_start` and `raw_end` are not changed by those fields.

### Validation Tests

1. A profile with duplicate `detector_id` after filtering fails validation.
2. A profile with `confidence < 0` or `confidence > 1` fails validation.
3. A profile with `vad_neg_threshold > vad_threshold` fails validation.
4. An adapter interval where `raw_end <= raw_start` is dropped with a diagnostic warning or fails with `vad_invalid_adapter_interval`, according to adapter policy.
5. An emitted row beyond source audio duration fails output validation unless within the documented timestamp tolerance.

### Fingerprint Tests

1. Changing the source audio fingerprint changes the VAD stream fingerprint.
2. Changing an enabled profile threshold changes the VAD stream fingerprint.
3. Changing `detection_batch_size` changes the invocation fingerprint only if the implementation treats concurrency as behavior-affecting; otherwise it must be excluded or normalized consistently.
4. Reordering profile declarations without changing their resolved identities and values does not change output rows or fingerprints.

### Diagnostics Tests

1. With diagnostics enabled and a workspace present, profile-level events and merged events are materialized and listed in the run manifest.
2. With diagnostics disabled, required semantic output is still produced and no diagnostic artifact is required.
3. Adapter warnings are represented in `diagnostics/vad_run_report` when that artifact is requested or diagnostics are enabled.

## Example Resolved Settings

```yaml
speech_detection/vad_settings:
  enable: true
  detection_batch_size: 2

speech_detection/vad_audio_settings:
  audio_source_selector: default
  sample_rate_hz: 16000
  channels: 1
  timing_preserved: true

speech_detection/vad_detector_profiles:
  - detector_id: aggressive
    detector_type: vad
    detector_model: silero_vad
    confidence: 1.0
    enabled: true
    speech_onset_pad_duration_ms: 20
    speech_offset_pad_duration_ms: 30
    min_speech_duration_ms: 35
    vad_threshold: 0.6
    vad_neg_threshold: 0.45
    vad_max_speech_duration_s: 30
    vad_min_silence_at_max_speech_ms: 80
    vad_min_silence_duration_ms: 100
  - detector_id: normal
    detector_type: vad
    detector_model: silero_vad
    confidence: 0.75
    enabled: true
    speech_onset_pad_duration_ms: 60
    speech_offset_pad_duration_ms: 90
    min_speech_duration_ms: 90
    vad_threshold: 0.5
    vad_neg_threshold: 0.35
    vad_max_speech_duration_s: 30
    vad_min_silence_at_max_speech_ms: 80
    vad_min_silence_duration_ms: 100
```

## Example Output

```csv
detector_id,detector_type,confidence,raw_start,raw_end
aggressive,vad,1.0,0.420,0.810
normal,vad,0.75,0.390,0.850
normal,vad,0.75,1.230,1.700
aggressive,vad,1.0,1.260,1.620
```

## Open Questions

- Should disabled profiles be pruned before settings fingerprinting, or should the full supplied profile artifact participate in the fingerprint?
- Should `detection_batch_size` participate in semantic output fingerprints, or should it be treated as scheduling-only metadata?
- What timestamp precision tolerance should validators use for adapter outputs at source audio boundaries?
- Should the first implementation allow mixed CPU/GPU VAD profiles in one semantic invocation, or split them into separate concrete invocations?

---

## Learned Facts To Actualize Later

These facts were learned while consolidating `../02-expected_stages.md` and should be reconciled in this document during its own design pass.

1. VAD should consume a logical `audio/prepared` requirement satisfying `speech_detection/vad_audio_settings`, not `audio/source` directly. The planner may bind `source/audio` directly if it satisfies the requirement.
2. The `audio/prepared` input artifact contract describes the resolved audio resource selected for the VAD audio requirement.
3. VAD output remains low-level detector evidence, but downstream canonicalization terminology should use "utterance event stream" rather than "word-like stream."
4. Audio preparation should use canonical `audio/prepared` requirements distinguished by constraints and fingerprints, not consumer-specific ready IDs.
5. Diagnostics remain ordinary declared artifacts. If VAD emits diagnostics that were not requested by the graph, they are incidental outputs.
