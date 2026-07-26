# Fingerprints

## Purpose

Fingerprints identify concrete artifact instances for cache lookup, reuse, provenance, and execution-plan validation.

## Core hash contract
All final fingerprint builders use the same primitive:

```rust
stable_hash(payload) = blake3(serde_json::to_vec(payload))
```

__Expected Behavior:__
- Structured payloads are serialized with `serde_json::to_vec`.
- Raw bytes are hashed with BLAKE3.
- The returned fingerprint is the full lowercase hex digest string.

## Content fingerprint
Every concrete artifact source handle exposes:

```python
class ArtifactSourceHandle(Protocol[T]):
    async def get_value(self, codec: Codec[T]) -> T: ...
    async def get_content_fingerprint(self, codec: Codec[T]) -> str: ...
```

`get_content_fingerprint(codec)` returns the content contribution for a supplied source handle. 

The handle owns the definition of semantic equality. For example:

- A caller-supplied file handle may hash file bytes.
- A settings handle may hash canonical JSON for the resolved value.
- An intermediate in-memory handle returns `""` because produced artifacts derive identity from pedigree rather than re-hashing output bytes.
- A cache-backed source handle returns `""` because the expected final fingerprint is already the cache key that was validated before the handle was created.

*If provider versioning or canonicalization rules affect the content digest, that versioning must already be represented in the returned content fingerprint or in the structured payload that consumes it.*

## Planning Fingerprints

### Manifest fingerprint

__Use:__
- Identifies the effective manifest shape for a runtime configuration.
- Invalidates planning-derived cache state when manifest structure or manifest version changes.

__API:__
`build_manifest_fingerprint(description, manifest_version)` 

```text
manifest fingerprint = hash(
    manifest description + manifest_version
)
```

### Stage fingerprint
__Use:__
- Identifies the semantic behavior of a stage independent of a specific run.
- Serves as the stage component of a stage pedigree fingerprint.

__API:__
`build_stage_fingerprint(stage_id, behavior_version)`

```text
stage fingerprint = hash(
    stage.stage_id,
    stage.behavior_version
)
```

### Setting value fingerprint
__Use:__
- Identifies one resolved setting artifact value.
- Allows setting changes to propagate into pedigree and produced artifact invalidation.

__API:__
`build_setting_value_fingerprint(key, value)` hashes the setting artifact ID together with the resolved setting value.

```text
setting value fingerprint = hash(
    artifact_id,
    value
)
```

### Stage pedigree fingerprint
__Use:__
- Identifies one planned stage invocation's semantic production context.
- Captures the exact upstream inputs and effective settings that should invalidate downstream produced artifacts when they change.

__API:__
`build_stage_pedigree_fingerprint(stage_fingerprint, input_fingerprints, setting_fingerprints)` hashes the deterministic production pedigree for one planned stage invocation.

```text
pedigree fingerprint = hash(
    stage fingerprint,
    input fingerprints by artifact id,
    aggregate settings fingerprint
)
```

__Expected Behavior:__
- Input aggregation must be keyed and order-stable, such as sorting by canonical input artifact ID in ascending lexicographic order before hashing.
- The pedigree fingerprint is planner-owned. Artifact orchestration consumes it; it does not reconstruct it from produced values.


## Artifact Inventory fingerprints

Fingerprint payloads serialize artifact identity using the canonical unprojected `a-ref` form. Projection context must never be included in a fingerprint payload.


There are two key types of final artifact fingerprints:

- __Supplied artifact fingerprints__ for artifacts that enter the run already available from a caller, settings provider, cache source, or other pre-existing source handle.
- __Produced artifact fingerprints__ for artifacts materialized by a planned stage invocation.

Both types become concrete artifact instance identities. The difference is where their semantic identity comes from:

- Supplied fingerprints derive identity from artifact identity plus content fingerprint.
- Produced fingerprints derive identity from artifact identity plus planner-owned production pedigree.

### Settings Aggregate fingerprint

__Use:__
- Identifies the complete effective settings set seen by a planned stage invocation.
- Serves as the settings component of a stage pedigree fingerprint.

__API:__
`build_settings_fingerprint(fingerprints)` hashes the ordered list of per-setting fingerprints.

```text
settings fingerprint = hash([
    setting_fp_1,
    setting_fp_2,
    ...
])
```

__Behavior:__
- If the list is empty, the aggregate settings fingerprint is `""`.
- Ordering must be stable and deterministic, such as sorting by canonical artifact ID in ascending lexicographic order before hashing.

### Supplied Artifact fingerprint
__Use:__
- Produces the final fingerprint for an artifact that enters the run already supplied.
- Provides a root fingerprint that downstream stage pedigree calculations consume.

__API:__
`build_supplied_artifact_fingerprint(declaration, source_handle)` hashes artifact identity plus the source handle's content fingerprint.

```python
class FingerprintProvider:
    async def build_supplied_artifact_fingerprint(
        self,
        declaration: ArtifactDeclaration[T],
        source_handle: ArtifactSourceHandle[T],
    ) -> str:
        content_fingerprint = await source_handle.get_content_fingerprint(
            declaration.codec
        )
        return stable_hash(
            {
                "artifact": {
                    "artifact_id": str(declaration.artifact_id),
                    "role": declaration.role.value,
                    "version": declaration.version,
                },
                "content": content_fingerprint,
            }
        )
```

__Behavior:__
- It is used for caller-supplied source artifacts and for supplied settings artifacts.
- It can also be used for supplied intermediate roots when a run starts from an already available intermediate.

__Complete payload shape:__

```text
supplied artifact fingerprint =
  stable_hash({
    "artifact": {
      "artifact_id": "<artifact-id>",
      "role": "<artifact-role>",
      "version": "<artifact-version>"
    },
    "content": "<content-fingerprint>"
  })
```

```text
where:

content-fingerprint =
  source_handle.get_content_fingerprint(declaration.codec)
```

```text
full supplied calculation =
  stable_hash({
    "artifact": {
      "artifact_id": declaration.artifact_id,
      "role": declaration.role,
      "version": declaration.version
    },
    "content": stable_hash({ semantic source content })
      or provider-defined equivalent digest
      or ""
  })
```

The nested `content` field is not always literally `stable_hash({ ... })`; it is whatever stable semantic digest the source handle defines. The examples above show the full enclosing payload shape.

### Concrete instance locator fingerprint shape

A concrete artifact instance locator is the pair of resolved artifact identity and final fingerprint, serialized as `a-instance`:

```text
a-instance:/global/{domain}/{content_kind}/{phase-or-none}/fingerprint/{fingerprint}
```

The `fingerprint` is the final artifact fingerprint produced by either the supplied-artifact or stage-produced fingerprint builder. It is not recalculated from cache bytes when the locator is used. Rebinding validates that the cache entry metadata matches the paired artifact identity, declaration version, role, codec metadata, and fingerprint.

For cacheable provided roots, the locator returned by binding-time publication or direct cache IO uses the supplied-artifact fingerprint. For stage-produced artifacts, it uses the stage-produced artifact fingerprint. The same locator shape works for both because the fingerprint already commits to the correct lineage source. Projection context is not part of fingerprint payloads.

### Stage-produced artifact fingerprint builder

__Use:__
- Produces the final fingerprint for a stage-produced artifact instance.
- Acts as the produced artifact's cache identity and as the upstream fingerprint consumed by downstream stages.
- Recursively carries the full upstream lineage of the run, because each input fingerprint may itself be either a supplied artifact fingerprint or another produced artifact fingerprint.

__API:__
`build_intermediate_artifact_fingerprint(declaration, pedigree_fingerprint)` hashes artifact identity, codec identity, and production pedigree.

```python
class FingerprintProvider:
    async def build_intermediate_artifact_fingerprint(
        self,
        declaration: ArtifactDeclaration[T],
        pedigree_fingerprint: str,
    ) -> str:
        return stable_hash(
            {
                "artifact": {
                    "artifact_id": str(declaration.artifact_id),
                    "role": declaration.role.value,
                    "version": declaration.version,
                    "codec": {
                        "codec_id": declaration.codec.codec_id,
                        "codec_version": declaration.codec.codec_version,
                        "mime_type": declaration.codec.mime_type,
                        "extension": declaration.codec.extension,
                    },
                },
                "pedigree": pedigree_fingerprint,
            }
        )
```

__Behavior:__

- This builder is used for stage-produced artifacts, including cacheable and non-cacheable outputs.
- Despite the historical method name, it is not limited to intermediate artifacts.
- Produced fingerprints do include codec identity.
- Produced fingerprints do not re-read or re-hash the produced output bytes.
- The `inputs` payload is recursive: each input fingerprint is already a final artifact fingerprint, and that fingerprint may have been derived from either a supplied artifact or another stage-produced artifact.
- Because downstream stages consume final upstream artifact fingerprints, lineage propagates transitively through the graph without requiring the runtime to re-expand prior pedigree objects.

__Complete payload shape:__

```text
produced artifact fingerprint =
  stable_hash({
    "artifact": {
      "artifact_id": "<artifact-id>",
      "role": "<artifact-role>",
      "version": "<artifact-version>",
      "codec": {
        "codec_id": "<codec-id>",
        "codec_version": "<codec-version>",
        "mime_type": "<mime-type>",
        "extension": "<extension>"
      }
    },
    "pedigree": stable_hash({
      "stage": stable_hash({
        "stage": {
          "stage_id": "<stage-id>",
          "behavior_version": "<behavior-version>"
        }
      }),
      "inputs": {
        "<input-artifact-id-1>": "<input-fingerprint-1>",
        "<input-artifact-id-2>": "<input-fingerprint-2>"
      },
      "settings": stable_hash([
        "<setting-fingerprint-1>",
        "<setting-fingerprint-2>"
      ])
    })
  })
```

```text
recursive input rule:

for each entry in "inputs":
  "<input-artifact-id>": "<final upstream artifact fingerprint>"

where each final upstream artifact fingerprint is exactly one of:

  supplied artifact fingerprint =
    stable_hash({
      "artifact": { ... },
      "content": ...
    })

  produced artifact fingerprint =
    stable_hash({
      "artifact": { ... },
      "pedigree": stable_hash({
        "stage": ...,
        "inputs": { ... },
        "settings": ...
      })
    })
```

```text
full produced calculation =
  stable_hash({
    "artifact": {
      "artifact_id": declaration.artifact_id,
      "role": declaration.role,
      "version": declaration.version,
      "codec": {
        "codec_id": declaration.codec.codec_id,
        "codec_version": declaration.codec.codec_version,
        "mime_type": declaration.codec.mime_type,
        "extension": declaration.codec.extension
      }
    },
    "pedigree": stable_hash({
      "stage": stable_hash({
        "stage": {
          "stage_id": stage.stage_id,
          "behavior_version": stage.behavior_version
        }
      }),
      "inputs": {
        "<canonical-input-id>": "<final-input-artifact-fingerprint>",
        "...": "..."
      },
      "settings": stable_hash([
        "<setting fingerprint sorted by canonical setting artifact id>",
        "..."
      ])
    })
  })
```

The nested `stage`, `inputs`, and `settings` values shown above are the planner-owned pieces that together form the pedigree fingerprint consumed by the final produced artifact fingerprint.

The important recursive property is:

- If an input was supplied, its entry in `inputs` is a supplied artifact fingerprint.
- If an input was produced upstream, its entry in `inputs` is that upstream produced artifact fingerprint.
- Therefore each produced artifact fingerprint implicitly commits to the entire transitive fingerprint lineage of the run up to that point.

## End-to-end fingerprint flow

After the graph shape is known, the planner can derive the expected fingerprint for each artifact edge by walking forward from available inputs.

```text
  source/audio ──────────── source_audio_fp
         │
         ▼
 ┌───────────────┐
 │ prepare_audio │ ───────── audio/settings ── audio_settings_fp
 └───────────────┘
         │
         ▼
  audio/prepared ────────── audio_prepared_fp
         │
         ▼
   ┌────────────┐
   │ transcribe │ ────────── asr/settings ─── asr_settings_fp
   └────────────┘
         │
         ▼
 transcript/token_text ──── transcript_token_text_fp
```

```text
source_audio_fp =
  hash(
    source/audio identity,
    source/audio content fingerprint
  )

audio_settings_fp =
  hash(
    audio/settings identity,
    audio/settings content fingerprint
  )

prepare_audio_stage_fp =
  hash(
    prepare_audio stage_id,
    prepare_audio behavior_version
  )

prepare_audio_pedigree_fp =
  hash(
    prepare_audio_stage_fp,
    { "source/audio": source_audio_fp },
    audio_settings_fp
  )

audio_prepared_fp =
  hash(
    audio/prepared identity,
    audio/prepared codec identity,
    prepare_audio_pedigree_fp
  )

transcribe_stage_fp =
  hash(
    transcribe stage_id,
    transcribe behavior_version
  )

transcribe_pedigree_fp =
  hash(
    transcribe_stage_fp,
    { "audio/prepared": audio_prepared_fp },
    asr_settings_fp
  )

transcript_token_text_fp =
  hash(
    transcript/token_text identity,
    transcript/token_text codec identity,
    transcribe_pedigree_fp
  )
```

This means a produced artifact fingerprint changes when:

- Upstream supplied or produced fingerprints change.
- Effective settings change.
- The producing stage's `behavior_version` changes.
- The artifact declaration identity changes.
- The produced artifact's codec identity changes.

This also means a produced artifact fingerprint does not change merely because the final target sink changes. Materialization location is not part of the fingerprint payload.

## Cache and execution implications
The planner computes expected fingerprints ahead of execution. Runtime bindings and writers must agree with those planned values.

__Current behavior:__

- Cache-backed source handles do not contribute a second content hash; they return `""` and rely on the already validated cache key.
- Intermediate in-memory handles also return `""`; produced identity comes from pedigree.
- Runtime validates that resolved cache instances and output writers match the `expected_fingerprint` recorded in the execution plan.
- A sink change alone must not change semantic fingerprint identity for the same requested artifact.
- Publishing a provided root to cache does not create a new fingerprint; it stores the supplied-artifact fingerprint already assigned to that root.
- Direct cache IO must use the same final fingerprint builders as run-time binding and execution.
- A cached-root reference must pair the final fingerprint with the semantic artifact ID; a bare fingerprint is not enough public type information.

## Scope boundary

This document specifies fingerprint payload composition and runtime expectations, including the final fingerprints carried by cached-instance references.

The planner algorithm that discovers which stages, inputs, and settings participate in a given pedigree is specified in the planning docs. Once that selection is made, this document defines how the selected pieces are hashed.
