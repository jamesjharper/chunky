# Artifact Model
An **artifact** is any typed value that enters, moves through, or leaves the pipeline. Artifacts include:

- source inputs, such as audio files;
- settings, such as speaker detection settings;
- intermediate results, such as utterance event streams;
- final outputs, such as transcripts;
- diagnostic outputs, such as logs, metrics, or inspection CSVs;
- target output requests, such as transcript or embedding outputs.

The artifact model gives the pipeline a stable way to reason about identity, compatibility, reuse, and materialization across stages.

## Artifact Identity
Artifact IDs are stable semantic identifiers. An artifact ID describes **what the artifact means**, not where it is stored or how it was produced.

Artifact IDs must not encode:
- temporary file names;
- physical storage locations;
- implementation language or binding;
- stage invocation IDs;
- task IDs;
- incidental output filenames.

Artifact IDs may include a namespace when it improves semantic clarity. Artifacts that represent settings private to one stage may include implementation-specific naming, but those details should still describe the setting's meaning rather than its storage format.

### Internal semantic identity
`ArtifactKey` remains the internal semantic value object for declaration-level artifact identity.

```python
@dataclass(frozen=True, order=True)
class ArtifactKey:
    domain: str
    content_kind: str
    phase: int | None = None

    @property
    def family(self) -> tuple[str, str]:
        return self.domain, self.content_kind

    def without_phase(self) -> "ArtifactKey":
        return ArtifactKey(self.domain, self.content_kind)
```

The `ArtifactKey` describes two related semantic identities:

- family or non-phased declarations are unique by `{domain}/{content_kind}`;
- concrete phased intermediate declarations are unique by `{domain}/{content_kind}/{phase}`.

`ArtifactKey` is not itself a URI string. URI strings are boundary serializations parsed into structured locator objects and then into the internal semantic fields used by the planner, manifest, cache, and bindings.

### Artifact locator family
Chunky uses a URI-compatible locator family at public and persistence boundaries.

The locator family has three schemes:

| Scheme | Structured type | Meaning |
| --- | --- | --- |
| `a-ref` | `ArtifactRefLocator` | Declaration-level semantic artifact reference. May use unresolved phase token `any`. |
| `a-instance` | `ArtifactInstanceLocator` | Concrete artifact instance reference. Uses the final artifact fingerprint as discriminator. |
| `a-task` | `TaskArtifactLocator` | Task-scoped requested-output binding reference. Uses the task ID as discriminator and resolves through task metadata. |

All three schemes share the same canonical path spine:

```text
/{projection_context}/{domain}/{content_kind}/{phase}
```

The canonical no-projection token is `global`. Projection context is an ergonomic presentation/context field, not part of semantic identity. For example, a settings artifact owned by `audio_preprocessing` may be presented under `speech_processing`, but the underlying artifact identity remains `audio_preprocessing/audio_settings`.

V1 intentionally omits `content_kind_path`. Only `projection_context`, `domain`, `content_kind`, `phase`, and the scheme-specific discriminator are in scope.

### Canonical and short `a-ref` forms
The structured `ArtifactRefLocator` still has a canonical URI serialization used for generated strings, persisted metadata, and logs that need an unambiguous full locator:

```text
a-ref:/global/{domain}/{content_kind}/{phase-or-any}
```

Documentation and caller-facing examples should prefer the accepted short `a-ref` input form:

```text
{domain}/{content_kind}
{domain}/{content_kind}/{phase-or-any}
{projection_context}/{domain}/{content_kind}
{projection_context}/{domain}/{content_kind}/{phase-or-any}
```

Examples:

```text
source/audio
speech_detection/vad_settings
utterance_detections/utterance_stream/201
speech_processing/audio_preprocessing/audio_settings
```

The `a-ref:/` scheme prefix is optional when parsing semantic artifact references. Omitted projection context canonicalizes to `global`; omitted `a-ref` phase canonicalizes to `any`.

Concrete locator schemes are not optional and remain strict:

```text
a-instance:/global/{domain}/{content_kind}/{phase-or-none}/fingerprint/{fingerprint}
a-task:/global/{domain}/{content_kind}/{phase-or-none}/task/{task_id}
```

Examples:

```text
a-instance:/global/source/audio/none/fingerprint/b3d9...
a-instance:/global/utterance_detections/utterance_stream/201/fingerprint/9af1...

a-task:/global/render/webvtt_transcript/none/task/task-123
a-task:/global/utterance_detections/utterance_stream/201/task/task-123
```

Phase tokens have scheme-specific rules:

- `a-ref` may use `any` to mean unresolved declaration-level phase selection;
- `a-ref` may also use a numeric phase selector;
- `a-instance` and `a-task` must use a concrete numeric phase or `none`;
- `none` means the resolved concrete artifact has no phase;
- `a-instance` and `a-task` must never use or infer `any`.

### Structured locator objects
Parsing is scheme-specific and yields dedicated types. Do not introduce one catch-all locator with optional fields for every scheme.

```python
@dataclass(frozen=True)
class ArtifactRefLocator:
    artifact_key: ArtifactKey
    projection_context: str = "global"

    @classmethod
    def from_a_ref(cls, value: str) -> "ArtifactRefLocator": ...

    def to_a_ref(self, *, projected: bool = False) -> str: ...


@dataclass(frozen=True)
class ArtifactInstanceLocator:
    artifact_key: ArtifactKey
    fingerprint: str
    projection_context: str = "global"

    @classmethod
    def from_a_instance(cls, value: str) -> "ArtifactInstanceLocator": ...

    def to_a_instance(self, *, projected: bool = False) -> str: ...


@dataclass(frozen=True)
class TaskArtifactLocator:
    artifact_key: ArtifactKey
    task_id: str
    projection_context: str = "global"

    @classmethod
    def from_a_task(cls, value: str) -> "TaskArtifactLocator": ...

    def to_a_task(self, *, projected: bool = False) -> str: ...
```


For `a-ref`, `artifact_key.phase is None` serializes as `any`; a numeric phase serializes as that concrete selector. For `a-instance` and `a-task`, `artifact_key.phase is None` serializes as `none`; numeric phases serialize as concrete resolved phases.

Locator objects store structured fields only. They do not preserve the original serialized string and they do not store a cached canonical string. Canonical strings are generated lazily at CLI, API, logging, metadata, and persistence boundaries.

The default plain string form of every locator is its canonical serialization. Projected/presentation serialization must be requested explicitly.

### Equality and hashing
Semantic equality and hashing for `a-ref` ignore `projection_context` and use only the canonical artifact identity fields.

```text
audio_preprocessing/audio_settings
speech_processing/audio_preprocessing/audio_settings
```

These are the same semantic artifact reference. Projection context must not affect planning semantics, cache lookup, fingerprint payloads, map keys, or producer selection.

`a-instance` equality includes the semantic artifact identity and final artifact fingerprint. `a-task` equality includes the semantic artifact identity and task ID. Their projection context is also presentation-only.

### Artifact references
The model distinguishes declaration-level semantic identity, concrete instance identity, and task-scoped requested-output identity.

A semantic artifact reference identifies what a run needs or can produce. It is represented externally as `a-ref` and internally by `ArtifactKey` plus any planning selector state.

A concrete artifact instance reference identifies one deterministic artifact instance:

```python
@dataclass(frozen=True)
class ArtifactInstanceLocator:
    artifact_key: ArtifactKey
    fingerprint: str
    projection_context: str = "global"
```

The fingerprint is always paired with the semantic artifact identity. A bare fingerprint is not a complete public artifact reference because it does not carry the declaration role, schema, phase, or validation context.

`a-instance` uses the final artifact fingerprint produced by the supplied-artifact or stage-produced artifact fingerprint builders. It is the concrete identity used internally for planned and produced artifacts. Planning may mint `a-instance` locators before execution when the final fingerprint is already deterministic.

Externally exposed `a-instance` locators are limited to durably retrievable artifacts. Non-cacheable or not-yet-durable artifact instances may still have internal `a-instance` identities, but those locators must not be returned as retrievable public references.

`a-task` identifies a task-scoped requested-output binding, not a file path or materialized object. Every requested output receives a stable `a-task` locator during planning finalization. The `a-task` may resolve to the corresponding internal `a-instance` once final planning has determined it, but content retrieval remains gated on durable commit and retrievability state.

## Artifact Roles
Every artifact has exactly one role:

```python
class ArtifactRole(str, Enum):
    SOURCE = "source"
    SETTINGS = "settings"
    INTERMEDIATE = "intermediate"
    TARGET = "target"
    DIAGNOSTIC = "diagnostic"
```

### Source role 
Source artifacts are externally supplied inputs.

Source artifacts are always fingerprinted. When a cacheable source artifact is supplied as a graph root, it is published into durable artifact cache by default during binding unless the request's exact per-artifact cache policy opts out with `do_not_cache`. Source publication supports saved-task re-submission and later reuse when the original external input is unavailable.

__Example:__
```text
source/audio
```
#### IO
Source artifacts must provide a source handle for reading content.

__Example as input file:__
```python
inputs={
  "source/audio": AudioSource.from_file_path("input.wav"),
}
cache_policy={
  "source/audio": ArtifactCachePolicy(do_not_cache=True),
}
```

### Settings role
Settings artifacts are resolved configuration values.

Settings are normal artifacts. A caller-supplied settings artifact takes precedence over the declaration default for that artifact. Declaration defaults are resolved lazily only after planning determines that the setting is relevant. Path-pushed or inherited settings are planner-owned effective-settings overlays, not a separate hidden configuration channel.

The fully resolved effective settings value is fingerprinted. A defaulted settings value is treated as a supplied graph-root value after it is resolved, even when the caller did not explicitly provide it. Cacheable supplied or defaulted settings roots are published to cache by default unless the exact artifact policy opts out with `do_not_cache`.

A cached settings root may be provided as an `a-instance` locator. The pipeline may bind and read that cached settings value before graph planning so settings-dependent planning hooks see the same value they would see from an ordinary supplied settings source.

__Examples:__
```text
speech_detection/vad_settings
speech_detection/vad_detector_profiles
speaker_detection/diarization_settings
speech_detection/diarization_detector_profiles
```

#### IO
__Example as input object:__
```python
inputs={
  "speaker_detection/diarization_settings": DiarizationSettings.from_object({
    "detect_min_speakers": 2,
    "detect_max_speakers": 10,
  }),
  "render/webvtt_settings": WebVttSettings.from_object({
        "include_speakers": True,
  })
}
```

__Example as input file:__
```python
inputs={
  "speaker_detection/diarization_settings": DiarizationSettings.from_json_file("./diarization_settings.json")
}
```

### Intermediate role
Intermediate artifacts are produced by one stage and consumed by another.

Intermediate artifacts are cached by default when their declaration is cacheable. Their availability controls dependency readiness and downstream data flow. Stage-produced intermediates are not fingerprinted from their content values. Their fingerprint is derived from the operations and artifact fingerprints that produced them. If an intermediate artifact is explicitly supplied as part of a task configuration, it is fingerprinted as a supplied input instead and published to cache by default unless the exact artifact policy opts out.


__Examples:__
```text
utterance_detections/vad_stream
utterance_detections/diarization_stream
utterance_detections/utterance_stream/1
speaker_detection/speaker_role_token_stream/100
chunk_plan/chunk_segments/1
```

#### IO
Intermediate artifacts can optionally provide a target or source handle.

__Example as output:__
```
outputs={
  "speaker_detection/speaker_role_token_stream": SpeakerRoleTokenStreamTargetArtifact.to_file_path("speaker_role_token_stream.csv"),
},
```
__Example as input:__
```
inputs={
  "speaker_detection/speaker_role_token_stream": SpeakerRoleTokenStreamSourceArtifact.from_file_path("speaker_role_token_stream.csv"),
},
```


### Target role
Target artifacts are requested final outputs.

Target artifacts are write only from the perspective of the producing stage. Their semantic values may be reused when the requested target fingerprint matches a previously produced target.

__Examples:__
```text
target/embeddings
target/transcript
```

#### IO
Targets normally have one or more caller materialization handles in the task configuration. A requested output may instead have an empty target list when the caller wants durable semantic availability and later cache retrieval. That target-less form is valid only when the artifact is cacheable for the request and is not opted out through `cache_policy.do_not_cache`.

__Example as output:__
```
outputs={
  "render/webvtt_transcript/any": WebVttTargetArtifact.to_file_path("output.vtt"),
},
```

__Example as target-less output:__
```
outputs={
  "render/webvtt_transcript/any": [],
},
```

### Diagnostic role
Diagnostic artifacts are requested inspection outputs.

Non-deterministic diagnostic artifacts are write only from the perspective of the producing stage. They cannot be reused by subsequent tasks, but can be requested by the task configuration.

They can also be queried outside of the task flow by a given task id. 

__Examples:__
```text
{stage}/logs
{stage}/performance
performance/all_stages
speaker_detection/detect_stats
```

#### IO
Diagnostics require a target handle to be configured as part of the task configuration to be written.
The stage declaration should expose the expected diagnostic output format.

__Examples:__
```
outputs={
  "speaker_detection/diagnostics_detect_stats": DiagnosticLogArtifact.to_file_path("status.log"),
},
```

## Artifact Declarations
An artifact declaration is the semantic contract emitted by the stage or shared package that owns an artifact schema.

```python
T = TypeVar("T")

class DefaultProvider(Protocol[T]):
    def get_default(self) -> T: ...

@dataclass(frozen=True)
class ArtifactDeclaration(Generic[T]):
    artifact_id: ArtifactKey
    role: ArtifactRole
    version: int
    codec: Codec[T]
    cacheable: bool = True
    default_provider: DefaultProvider[T] | None = None

    def __post_init__(self) -> None:
        if self.artifact_id.phase is not None and self.role is not ArtifactRole.INTERMEDIATE:
            raise ValueError("only intermediate artifact declarations may use phases")
```

### Declaration ownership by stage
Stages emit the declarations they authoritatively own:

- source artifacts that are unique to that stage or subsystem;
- settings artifacts that are unique to that stage or subsystem;
- target artifacts whose semantic output shape the stage defines;
- diagnostic artifacts whose shape the stage defines;
- intermediate artifacts produced by that stage.

A stage must not emit a declaration for an intermediate artifact it only consumes. For shared source or settings artifacts, ownership should live in the stage/subsystem that defines the schema or in a shared artifact package, not in every consumer.

```python
class AsrRepairStage:
    artifact_declarations = [
        ArtifactDeclaration(
            artifact_id=ArtifactKey.from_a_ref("transcript/token_text"),
            role=ArtifactRole.INTERMEDIATE,
            version=2,
            codec=TokenTextCodec(),
        ),
        ArtifactDeclaration(
            artifact_id=ArtifactKey.from_a_ref("transcript/token_boundaries/100"),
            role=ArtifactRole.INTERMEDIATE,
            version=1,
            codec=TokenBoundariesCodec(),
            cacheable=False,
        ),
    ]
    # ...
```


### Codec
A codec defines the canonical serialized representation at cache, process, and materialization boundaries.

```python
class Codec(Protocol[T]):
    codec_id: str
    codec_version: int
    mime_type: str
    extension: str

    async def serialize(self, value: T) -> bytes: ...
    async def deserialize(self, raw: bytes) -> T: ...
```

`extension` includes the leading dot, such as `.json`, `.csv`, `.wav`, or `.vtt`. It is storage metadata, not a request that the consuming stage receive a file path.

```python
class TokenBoundariesCodec:
    codec_id = "token-boundaries-json"
    codec_version = 1
    mime_type = "application/json"
    extension = ".json"

    async def serialize(self, value: TokenBoundaries) -> bytes:
        return json.dumps(
            value.to_json_value(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def deserialize(self, raw: bytes) -> TokenBoundaries:
        return TokenBoundaries.from_json_value(json.loads(raw.decode("utf-8")))
```


## Artifact Capabilities
A stage advertizes its input and output capabilities thought the use of Artifact Capabilities. An artifact capability points at a declared artifact.

> Artifact References, let stages depend on published artifact contracts without copying declaration details. The declaration owns the artifact's type, role, version, codec, and fingerprint provider. The reference owns how this stage uses that artifact.


__Artifact Capabilities fields:__

| Field | Description |
| ----- | ----------- |
| `artifact_id` | ID of the artifact declaration being referenced. |

__Example:__
```python
@dataclass(frozen=True)
class ArtifactCapability:
    artifact_id: ArtifactKey
```

Artifact capabilities are _statically_ advertised by the stage because they must be known while performing the backward traversal that generates the task graph.

```python
class AsrRepairStage:
    output_capabilities = [
        ArtifactCapability(
            artifact_id=ArtifactKey.from_a_ref("transcript/token_text"),
        ),
        ArtifactCapability(
            artifact_id=ArtifactKey.from_a_ref("transcript/token_boundaries/100"),
        ),
    ]

    input_capabilities = {
        ArtifactCapability(
            artifact_id=ArtifactKey.from_a_ref("audio/prepared"),
        ),
        ArtifactCapability(
            artifact_id=ArtifactKey.from_a_ref("transcript/token_boundaries/99"),
        ),
    }
    # ...
```


## Artifact requirements
A stage states its input requirements thought the use of Artifact Requirements. An artifact requirement points at a declared artifact. 

__Artifact Requirements fields:__

| Field | Description |
| ----- | ----------- |
| `artifact_id` | ID of the artifact declaration being referenced. |
| `required` | Whether the stage must receive this artifact to run. |

__Example:__
```python
@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_id: ArtifactKey
    required: bool = True
```

Artifact requirements are _dynamically_ advertised by the stage through inspection of the task's `StageNodeContext`. StageNodeContext can be used to interrogate 
 - The expected outputs for this stage 
 - The resolved and inherited settings for this stage 

These inputs should be calculated during each step of the backward traversal that generates the task graph.

```python
class AsrRepairStage:
    def input_requirements(
        self,
        context: StageNodeContext,
    ) -> list[ArtifactRequirement]: ...

    # ...
```

### Phased intermediate references:
Some intermediate artifact families opt into numbered processing phases:

```text
{domain}/{content_kind}/{phase}
```

The phase number is used to discover stage processing order. Phase behavior applies only to artifact families that explicitly declare it. Specifically for ArtifactRequirement the `ArtifactRequirement::artifact_id.phase` represents the phase requirements value for this stage. Phase requirements are simple: a numbered requirement selects the closest available phase that does not exceed the requested value.

Graph creation evaluates phased references through the family catalog:

- `PhaseMatchMode.AT_MOST` is the default for numbered requirements and matches any declared phase in the same family whose phase number is less than or equal to the selector;
- `PhaseMatchMode.EXACT` suppresses fallback and requires the referenced concrete phase declaration to exist;
- when several concrete phases satisfy an `AT_MOST` selector, planning chooses the highest available phase that does not exceed the selector;
- a phase-less selector addresses the completed family result rather than a concrete phase and therefore does not use `phase_match` to choose among declarations.

Requesting phased outputs follows the same family rules:

- a concrete phased output may be requested only when that exact declaration exists;
- a phase-less output request is valid when the family is known, because the planner may terminally remap the producing phase to the completed family ID;
- a numbered output request for a not-explicitly-declared phase is valid only when the family has both a declared phase at-or-below the requested phase and a declared phase above it, which proves the request names an interior handoff rather than a nonexistent terminal.


__For phased families:__

- a numbered output declaration is a concrete phase;
- a numbered input reference is a maximum phase selector; the concrete phase is resolved while building the stage graph.
- a phase-less artifact ID represents the completed family selected by the evaluated graph.

Terminal remapping remains planner-owned:

- a stage declares the concrete phased output it materializes;
- the planner may remap that output to the phase-less family ID only when the declared output is an intermediate declaration in the same family and has a concrete phase number;
- any other remapping is invalid, including remapping across families, remapping non-intermediate artifacts, or remapping from a phase-less declaration to another key.

The focused artifact example includes an intentionally empty `StageNodeContext` stub. The complete planning context is owned by graph creation and is outside this example.

__Example:__
```python
@dataclass(frozen=True)
class StageNodeContext:
    """Stub for the read-only planning context owned by graph creation."""

    pass


class AsrRepairStage:
    def input_requirements(
        self,
        _context: StageNodeContext,
    ) -> list[ArtifactRequirement]:
        return [
            ArtifactRequirement(
                # Select the closest available phase not exceeding 99.
                artifact_id=ArtifactKey.from_a_ref("transcript/token_boundaries/99"),
            )
        ]
```


## Recommended Invariants

1. Artifact IDs describe semantic meaning, not storage location or producer instance.
2. Artifact roles are declaration-level properties and do not change when an artifact is requested as an output.
3. A concrete cached-instance reference is an `a-instance` locator, not a bare fingerprint.
4. Plain semantic artifact references remain the default request and planning address and serialize as `a-ref`.
5. Cacheable provided roots are published to cache by default unless exact artifact policy opts out.
6. Caller-supplied settings take precedence over declaration defaults.
7. Defaults are resolved lazily only for settings relevant to the request-specific graph.
8. Resolved defaults and cached settings roots behave like supplied graph roots after binding.
