# Building a Run Request

## Purpose

This document defines the caller-facing `RunRequest` model and the rules for constructing one correctly.

It focuses on:

- semantic artifact addressing;
- caller-supplied roots and cached-root rebinding;
- requested outputs and materialization targets;
- per-artifact cache policy;
- direct artifact cache I/O used by upload-first and saved-task workflows.

The task-control surface that executes a request is specified separately in [03-run-surface.md](./03-run-surface.md).

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

`run_policy` may influence planning, producer selection, scheduling, or execution strategy. It must not suppress requested outputs or weaken materialization obligations.

`RunRequest` does not carry runtime settings. Provider credentials, runtime concurrency caps, and similar operational values belong to the configured pipeline rather than request semantics.

## Inputs

Caller-supplied request inputs and explicit `a-instance` cached-root locators become bound artifact instances before planning.

Inputs may represent:

- source artifacts;
- settings artifacts;
- explicitly supplied intermediate artifacts;
- concrete cached instances for any of the above.

During binding, the pipeline validates the artifact declaration, opens any referenced cache entry, and acquires the required reservations before planning proceeds.

If the bound root is a settings artifact, the pipeline may read it before normal graph planning so settings-dependent planning hooks observe the same value they would have seen from an ordinary supplied settings source.

## Artifact Addressing

Public request fields use semantic artifact reference strings. The `a-ref:/` prefix is accepted but optional for declaration-level references, so examples use short paths such as `source/audio`.

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

The public model distinguishes three related address kinds:

- an `a-ref` says what artifact is needed, produced, cached, or materialized;
- an `a-instance` says which concrete artifact instance is meant, paired with the final artifact fingerprint;
- an `a-task` says which task-scoped requested-output binding is meant.

Plain semantic artifact reference strings remain the default public surface for planning, outputs, and cache policy. Concrete `a-instance` locators are explicit pinning mechanisms used by saved-task and upload-first workflows.

Bindings and internal helpers may still use structured locator objects such as `ArtifactRefLocator`, but support for those objects as direct caller-facing `RunRequest` map keys is not required by this contract.

## Request Normalization

Language bindings may expose nested mappings that normalize to the same semantic `a-ref` values.

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
- direct `ArtifactKey` entries map directly to values, not nested mappings;
- normalized paths must resolve to two or three nested path components before conversion to `a-ref`;
- omitted projection context canonicalizes to `global`;
- omitted `a-ref` phase canonicalizes to `any`;
- `a-instance` and `a-task` never infer `any`;
- duplicate artifact IDs are rejected after projection context is stripped;
- structural validation errors across `inputs`, `outputs`, and `cache_policy` should be accumulated and reported together.

## Outputs and Materialization

Callers may request materialization for any semantic artifact known to the manifest and materializable to the requested target handle.

One artifact may be materialized to multiple caller targets in the same run:

```python
outputs = {
    "render/webvtt_transcript": [
        TargetVttArtifact.to_file_path("output.vtt"),
        InMemoryTargetArtifact(),
    ],
}
```

Fan-out rules:

- a single target handle is normalized as a one-item target list;
- multiple target handles are represented as an ordered list under one artifact key;
- list order is semantically meaningful and must be preserved for materialization, diagnostics, and cancellation reporting;
- caller target handles are not passed to producing stage executors;
- caller target paths or sinks are not part of semantic artifact identity or cache keys.

If one target materialization fails, the run fails as a whole while reporting which targets succeeded and which failed. Successful target writes are left in place; rollback is not promised.

## Target-Less Requested Outputs

A requested output may have an empty target list. This is an explicit request for semantic availability and later retrieval, not an omitted output.

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript": [],
        },
    )
)
```

Target-less outputs are valid only when the requested artifact is durably retrievable after the run.

Validation rules are intentionally strict:

- the artifact must be known to the manifest;
- the output declaration must be cacheable for this request;
- `cache_policy[artifact_id].do_not_cache=True` is a validation error;
- every requested output receives an `a-task` locator during planning finalization;
- an externally retrievable `a-instance` locator is emitted only after durable cache commit.

Clients should store returned locators rather than reconstructing them from graph metadata.

## Cache Policy

`cache_policy` is an explicit per-run, per-artifact policy surface. It expresses semantic cache-use intent, not backend-specific operational knobs.

Conceptual shape:

```python
@dataclass(frozen=True)
class ArtifactCachePolicy:
    ignore_existing_cache: bool = False
    do_not_cache: bool = False
    desired_ttl: timedelta | None = None
```

Directive semantics are orthogonal:

- `ignore_existing_cache` affects read and reuse for this run only;
- `do_not_cache` affects write and publish-to-cache behavior for this run only;
- `desired_ttl` applies only when creating a new cache entry and never mutates an existing entry's TTL.

Validation rules:

- unknown artifact IDs are request validation errors;
- known but unreachable artifacts are ignored for the run and surfaced as diagnostics;
- policy may target inputs, requested outputs, or intermediates that are known to the manifest;
- `do_not_cache=True` is invalid when the same artifact is requested with an empty target list;
- the original TTL of an existing cache entry persists even if a later request names a different desired TTL.

Cache policy also controls provided-root publication. Any caller-provided root whose declaration is cacheable is published to cache by default during binding. If `do_not_cache=True` is set for that exact artifact, the root remains usable for the current run but is not published as a durable cache entry.

This rule applies equally to supplied sources, supplied settings, and explicitly supplied intermediates.

## Direct Artifact Cache I/O

Chunky also exposes a dedicated artifact-cache I/O surface separate from `run(...)` and `run_away(...)`. It lets callers publish artifact content into cache and retrieve published instances without constructing a run request solely for cache population or inspection.

The interface is semantic and handle-based: callers use artifact declarations or semantic artifact IDs plus source or target handles.

Direct cache writes must use the same fingerprinting, metadata validation, reservation, and atomic publish rules as run-time cache publication. A directly published artifact is therefore a normal cache entry.

Cache I/O accepts an `a-ref` locator and returns an `a-instance` locator:

- the `a-ref` identifies which semantic artifact declaration is being published;
- the returned `a-instance` identifies the concrete committed cache instance, including its final content fingerprint.

This surface is the common foundation for upload-first web-service flows, duplicate-upload detection, cached-root rebinding, and later output retrieval.

### Publishing through `ArtifactCacheIO`

```python
audio_instance = await pipeline.cache_io.publish(
    "source/audio",
    SourceAudioArtifact.from_file_path("input.wav"),
)

assert audio_instance.artifact_id == ArtifactKey.from_a_ref("source/audio")
print(audio_instance.fingerprint)
```

The cache layer is intended to compute the same supplied-artifact fingerprint that normal root binding would compute, reserve the cache entry, materialize the supplied artifact into the cache handle, validate metadata, and return a typed cache reference. Implementations must avoid value-buffering large artifacts; if the current handle/cache contracts cannot materialize the source into the cache safely, this method should be left as an explicit stub.

### Using a Cached Artifact in a Run Request

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": audio_instance,
            "render/webvtt_settings": SourceSettingsArtifact.from_object({
                "include_speakers": True,
            }),
        },
        outputs={
            "render/webvtt_transcript": [
                TargetVttArtifact.to_file_path("output-from-cached-audio.vtt"),
            ],
        },
    )
)
```

The normalized input key still declares the semantic artifact being supplied. The `a-instance` value pins which concrete committed cache instance should be used. The locator is valid only when its semantic artifact identity matches the normalized input key and the cache contains committed metadata compatible with the manifest declaration.

## Settings and Defaults

Settings are normal artifacts. They participate in artifact fingerprints like any other effective input.

Caller-supplied settings are bound before planning. Declaration defaults are resolved lazily: the planner owns relevance discovery and requests only the defaults needed by the request-specific graph.

A resolved default behaves exactly like a supplied graph root after it is bound. Default-source providers must be deterministic and local to the setting declaration plus optional caller override; they must not inspect broader request shape, planner state, cache state, wall-clock time, or resource availability.
