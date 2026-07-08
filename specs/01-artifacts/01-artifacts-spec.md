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

Artifact IDs may include a namespace when it improves semantic clarity. Artifact that represent settings that are private to a single stage may include implementation-specific naming, but those details should still describe the setting’s meaning rather than its storage format.

### Identity format
```text
{domain}//{content_kind}{#phase}
```

__Examples:__
```text
source//audio
speech_detection//vad_settings
utterance_detections//utterance_stream#201
render//webvtt_transcript
```

### Artifact Key
The manifest identity of a declaration is:

```python
class ArtifactKey:
    domain: str
    content_kind: str
    phase: int | None = None
```

The `ArtifactKey` describes two distinct identity types:
- Family, or non-phased declarations are unique by `{DOMAIN}//{CONTENT_KIND}`;
- Phased intermediate declarations are unique by `{DOMAIN}//{CONTENT_KIND}{#OPTIONAL_PHASE}`.

__Example Implementation:__
```python
@dataclass(frozen=True, order=True)
class ArtifactKey:
    domain: str
    content_kind: str
    phase: int | None = None

    @classmethod
    def parse(cls, value: str) -> "ArtifactKey":
        try:
            domain, remainder = value.split("//", 1)
        except ValueError as exc:
            raise ValueError(f"artifact key must be DOMAIN//CONTENT_KIND[#PHASE]: {value!r}") from exc

        if not domain or not remainder:
            raise ValueError(f"artifact key contains an empty component: {value!r}")

        if "#" in remainder:
            content_kind, phase_text = remainder.rsplit("#", 1)
            if not content_kind:
                raise ValueError(f"artifact content kind is empty: {value!r}")
            return cls(domain, content_kind, int(phase_text))

        return cls(domain, remainder)

    @property
    def family(self) -> tuple[str, str]:
        return self.domain, self.content_kind

    def without_phase(self) -> "ArtifactKey":
        return ArtifactKey(self.domain, self.content_kind)

    def __str__(self) -> str:
        suffix = "" if self.phase is None else f"#{self.phase}"
        return f"{self.domain}//{self.content_kind}{suffix}"

```

#### Optional Phased Declarations
Intermediate artifact families may opt into phases. Source, settings, target, and diagnostic declarations must remain unphased. An intermediate family opts into phase behavior by publishing one or more concrete phase declarations.

Phased declarations let one intermediate artifact family describe multiple named representations of the same logical result. Each phase names a distinct handoff point in the pipeline, such as a fast draft, a normalized form, or a fully finalized value, while still keeping those variants grouped under one semantic family instead of inventing unrelated artifact types.

Their purpose is to make pipeline progression explicit. A stage can declare that it produces or consumes a specific phase, the planner can reason about which transformation steps are still required, and downstream stages can request either an exact intermediate representation or simply the completed value that satisfies the graph. This keeps multi-step derivations legible, avoids overloading one declaration with incompatible storage/schema rules, and allows each phase to carry the contract that best matches its role in the workflow.

For a phased family:

- an output capability references a concrete phase declaration;
- an input requirement uses the optional phase on its referenced key as a selector;
- a phase-less requirement asks for the completed value selected by the evaluated graph;
- terminal remapping is performed by the planner, not by the stage.

Different phases in one family may use different:

- schemas;
- codecs;
- serialized extensions;
- cacheability policies.

Families that opt into phases are interpreted through a phase catalog. The catalog indexes all concrete intermediate declarations by family and sorts them by ascending phase number. That indexed view defines the family-level semantics used by validation and graph construction rather than leaving each stage to infer phase behavior ad hoc.

For catalog purposes:

- a concrete artifact key is known only when that exact declaration exists;
- a phase-less selector for a phased family is always a known family selector, even though it is not itself a concrete declaration;
- a numbered selector is known when the family has at least one declared phase whose number does not exceed the selector;
- declared phases are the only concrete members of a phased family; source, settings, target, and diagnostic families never participate in this lookup model.

The stage manifest must reject phase usage on a non-intermediate declaration.

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

Source artifacts are always fingerprinted. They are only copied into or retained in durable artifact storage when enabled via the artifacts behavior at task initialization. Source retention supports resume after service restart or loss of access to the original external input, but at the cost of retaining potentially large media files.

__Example:__
```text
source//audio
```
#### IO
Source artifacts must provide a source handle for reading content.

__Example as input file:__
```python
inputs={
  "source//audio": AudioSource.from_file_path("input.wav"),
}
cache_behavior={
  "source//audio": {
    is_cached: True/False
  },
}
```

### Settings role
Settings artifacts are resolved configuration values.

Settings resolution order is:

```text
1. declaration defaults
2. user-configured settings
3. path-pushed inherited settings
```

The fully resolved effective settings value is fingerprinted. A defaulted settings value is treated as a supplied graph-root value, even when the caller did not explicitly provide it.

__Examples:__
```text
speech_detection//vad_settings
speech_detection//vad_detector_profiles
speaker_detection//diarization_settings
speech_detection//diarization_detector_profiles
```

#### IO
__Example as input object:__
```python
inputs={
  "speaker_detection//diarization_settings": DiarizationSettings.from_object({
    "detect_min_speakers": 2,
    "detect_max_speakers": 10,
  }),
  "render//webvtt_settings": WebVttSettings.from_object({
        "include_speakers": True,
  })
}
```

__Example as input file:__
```python
inputs={
  "speaker_detection//diarization_settings": DiarizationSettings.from_json_file("./diarization_settings.json")
}
```

### Intermediate role
Intermediate artifacts are produced by one stage and consumed by another.

Intermediate artifacts are cached by default. Their availability controls dependency readiness and downstream data flow. Stage-produced intermediates are not fingerprinted from their content values. Their fingerprint is derived from the operations and artifact fingerprints that produced them. If an intermediate artifact is explicitly supplied as part of a task configuration, it is fingerprinted as a supplied input instead.


__Examples:__
```text
utterance_detections//vad_stream
utterance_detections//diarization_stream
utterance_detections//utterance_stream#1
speaker_detection//speaker_role_token_stream#100
chunk_plan//chunk_segments#1
```

#### IO
Intermediate artifacts can optionally provide a target or source handle.

__Example as output:__
```
outputs={
  "speaker_detection//speaker_role_token_stream": SpeakerRoleTokenStreamTargetArtifact.to_file_path("speaker_role_token_stream.csv"),
},
```
__Example as input:__
```
inputs={
  "speaker_detection//speaker_role_token_stream": SpeakerRoleTokenStreamSourceArtifact.from_file_path("speaker_role_token_stream.csv"),
},
```


### Target role
Target artifacts are requested final outputs.

Target artifacts are write only from the perspective of the producing stage. Their semantic values may be reused when the requested target fingerprint matches a previously produced target.

__Examples:__
```text
target//embeddings
target//transcript
```

#### IO
Targets require a target handle to be configured as part of the task configuration.

__Example as output:__
```
outputs={
  "render//webvtt_transcript": WebVttTargetArtifact.to_file_path("output.vtt"),
},
```

### Diagnostic role
Diagnostic artifacts are requested inspection outputs.

Non-deterministic diagnostic artifacts are write only from the perspective of the producing stage. They cannot be reused by subsequent tasks, but can be requested by the task configuration.

They can also be queried outside of the task flow by a given task id. 

__Examples:__
```text
{stage}//logs
{stage}//performance
performance//all_stages
speaker_detection//detect_stats
```

#### IO
Diagnostics require a target handle to be configured as part of the task configuration to be written.
The stage declaration should expose the expected diagnostic output format.

__Examples:__
```
outputs={
  "speaker_detection//diagnostics_detect_stats": DiagnosticLogArtifact.to_file_path("status.log"),
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
            artifact_id=ArtifactKey.parse("transcript//token_text"),
            role=ArtifactRole.INTERMEDIATE,
            version=2,
            codec=TokenTextCodec(),
        ),
        ArtifactDeclaration(
            artifact_id=ArtifactKey.parse("transcript//token_boundaries#100"),
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
            artifact_id=ArtifactKey.parse("transcript//token_text"),
        ),
        ArtifactCapability(
            artifact_id=ArtifactKey.parse("transcript//token_boundaries#100"),
        ),
    ]

    input_capabilities = {
        ArtifactCapability(
            artifact_id=ArtifactKey.parse("audio//prepared"),
        ),
        ArtifactCapability(
            artifact_id=ArtifactKey.parse("transcript//token_boundaries#99"),
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
{domain}//{content_kind}#{phase}
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
                artifact_id=ArtifactKey.parse("transcript//token_boundaries#99"),
            )
        ]
```
