# Artifact Model

## Purpose
This document defines how Chunky identifies, validates, fingerprints, serializes, and materializes artifacts.

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

Artifact IDs may include a namespace when it improves semantic clarity. Settings that are private to a single stage may include implementation-specific naming, but those details should still describe the setting’s meaning rather than its storage format.

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
Only intermediate artifact families may opt into phases. Source, settings, target, and diagnostic declarations must remain unphased. An intermediate family opts into phase behavior by publishing one or more concrete phase declarations.

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


__For phased families:__

- a numbered output declaration is a concrete phase;
- a numbered input reference is a maximum phase selector; the concrete phase is resolved while building the stage graph.
- a phase-less artifact ID represents the completed family selected by the evaluated graph.

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

## Artifact Handle
An `ArtifactHandle` is an execution-time binding for one concrete artifact instance. The execution planner or runner creates an artifact handle once after the artifact fingerprint is known and the required source or target handles have been resolved.

Supplied sources are fingerprinted before their handle is created. Stage-produced outputs receive their fingerprint from the planner-provided production pedigree before cache or publication targets are bound.

```python
@dataclass(frozen=True)
class ArtifactHandle(Generic[T]):
    declaration: ArtifactDeclaration[T]
    fingerprint: str
    source: ArtifactSourceHandle[T] | None = None
    targets: tuple[ArtifactTargetHandle[T], ...] = ()
```

The structure represents both input and output bindings:

- `declaration.artifact_id` represents the fully resolved artifact ID. Any phase selection or terminal remapping is completed before the handle is created;
- an input binding has one resolved `source` and normally no `targets`;
- a cache-hit output has a cache-reserved `source` and no `targets`, allowing the producing stage to be skipped;
- an output that must be produced has one or more pre-resolved `targets` and normally an in-memory `source` that becomes readable after the write commits;
- a materialization binding may reuse an existing semantic `source` and add one caller-provided target.

A handle is immutable after bootstrap. Runtime state, such as a pending write or held reservation, belongs to the source and target handle instances referenced by it.

An `ArtifactInstance` is the available semantic artifact associated with one resolved handle:

```python
@dataclass(frozen=True)
class ArtifactInstance(Generic[T]):
    handle: ArtifactHandle[T]

    @property
    def fingerprint(self) -> str:
        return self.handle.fingerprint
```

## Artifact Source Handle
An `ArtifactSourceHandle` represents one already-selected source for a concrete artifact value. Examples include:

- a caller-supplied file;
- a caller-supplied in-memory object;
- an in-memory value produced earlier in the same task;
- a cache entry with an active read reservation.

```python
class ArtifactSourceHandle(Protocol[T]):
    kind: str

    async def get_value(self, codec: Codec[T]) -> T: ...
    async def get_content_fingerprint(self, codec: Codec[T]) -> str: ...
    async def close(self) -> None: ...
```

### `get_value(codec)`
Returns the typed artifact value. The handle decides whether the codec is needed:

- a file or cache source normally reads bytes and calls `codec.deserialize`;
- an in-memory source returns the existing object directly without serialization;
- a specialized source may decode the value using provider-specific behavior.

`ArtifactReader` delegates to this method. It does not inspect the concrete handle type.

### `get_content_fingerprint(codec)`
Returns the supplied-content fingerprint used when constructing the final fingerprint of a caller-supplied source, setting, or intermediate artifact.

For stage-produced and cache-resolved artifacts, this returns an empty value because their final fingerprint is derived from pedigree rather than by hashing the serialized output again.

### `close()`
Releases resources owned by the source handle. For a cache source, this releases its read reservation. For supplied files and in-memory values it may be a no-op.

Source-handle lifetime is owned by the runner or task executor, not by an individual `ArtifactReader`. A task-level cache read reservation can therefore remain active while several downstream stages consume the same artifact.

The example routes raw content digests through the same hashing seam via `stable_hash_bytes(raw)`.

__Example provided source handles:__

```python
class FileSource(Generic[T]):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def get_value(self, codec: Codec[T]) -> T:
        raw = await asyncio.to_thread(self.path.read_bytes)
        return await codec.deserialize(raw)

    async def get_content_fingerprint(self, _codec: Codec[T]) -> str:
        raw = await asyncio.to_thread(self.path.read_bytes)
        return stable_hash_bytes(raw)

    async def close(self) -> None:
        return None
```

```python
class SummarySettingsSource:
    def __init__(self, value: dict[str, object]):
        self.value = value

    async def get_value(
        self,
        _codec: Codec[dict[str, object]],
    ) -> dict[str, object]:
        return self.value

    async def get_content_fingerprint(
        self,
        codec: Codec[dict[str, object]],
    ) -> str:
        raw = await codec.serialize(self.value)
        return stable_hash_bytes(raw)

    async def close(self) -> None:
        return None
```

__Example cache source handles:__

```python
class CacheFileSourceHandle(Generic[T]):
    """Example cache-hit source with simplified reservation behavior."""

    def __init__(self, data_path: Path):
        self._data_path = data_path
        self._closed = False

    async def get_value(self, codec: Codec[T]) -> T:
        if self._closed:
            raise RuntimeError("cache read handle is closed")
        raw = await asyncio.to_thread(self._data_path.read_bytes)
        return await codec.deserialize(raw)

    async def get_content_fingerprint(self, _codec: Codec[T]) -> str:
        # Cache entries already have their final semantic fingerprint.
        return ""

    async def close(self) -> None:
        self._closed = True
```

A production cache source handle should be created only after the cache coordinator has validated the cache entry and acquired its read reservation, and `close()` should release that reservation. The executable example deliberately mocks this part: it validates cache metadata but only records local closed state and does not implement a durable read reservation.

## Artifact Target Handle

An `ArtifactTargetHandle` represents one already-selected and, when necessary, already-reserved destination for an artifact value. Examples include:

- an in-memory publication handle for direct stage-to-stage passing;
- an exclusively reserved cache entry;
- a caller-provided file or object sink;
- a diagnostic or streaming sink.

A single `set_value` method is not sufficient for cache-safe orchestration because a write reservation must also be released when a stage fails before producing its output. The target contract should therefore expose a small lifecycle:

```python
class ArtifactTargetHandle(Protocol[T]):
    kind: str

    async def prepare(
        self,
        value: T,
        codec: Codec[T],
        fingerprint: str,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def abort(self) -> None: ...
```

A target follows this state model:

```text
reserved -> prepared -> committed
    |           |
    +-----------+-> aborted
```

### `prepare(value, codec, fingerprint)`

Stages the value without making it visible as a completed artifact. A file-backed target should serialize into a temporary path. A cache target keeps its exclusive write reservation while preparing.

### `commit()`

Atomically publishes the prepared value where possible and releases any write reservation. A cache entry must not become visible as a hit before this operation succeeds.

### `abort()`

Discards temporary output and releases any held write reservation. The runner calls this when:

- the stage fails or is cancelled;
- the stage does not produce a requested output;
- another output target fails during preparation.

`commit()` and `abort()` should be idempotent after the target reaches a terminal state.


__Example supplied target handles:__

```python
class AtomicFileTargetHandle(Generic[T]):

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._temporary_path: Path | None = None
        self._committed = False
        self._aborted = False

    async def prepare(
        self,
        value: T,
        codec: Codec[T],
        _fingerprint: str,
    ) -> None:
        if self._committed or self._aborted:
            raise RuntimeError("target is already terminal")
        if self._temporary_path is not None:
            raise RuntimeError("target is already prepared")

        await asyncio.to_thread(
            self.path.parent.mkdir,
            parents=True,
            exist_ok=True,
        )
        raw = await codec.serialize(value)
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        await asyncio.to_thread(temporary.write_bytes, raw)
        self._temporary_path = temporary

    async def commit(self) -> None:
        if self._committed or self._aborted:
            return
        if self._temporary_path is None:
            raise RuntimeError("target has not been prepared")

        await asyncio.to_thread(os.replace, self._temporary_path, self.path)
        self._temporary_path = None
        self._committed = True

    async def abort(self) -> None:
        if self._committed or self._aborted:
            return
        if self._temporary_path is not None:
            await asyncio.to_thread(
                self._temporary_path.unlink,
                missing_ok=True,
            )
        self._temporary_path = None
        self._aborted = True
```

__Example in-memory target/source handle:__

```python
class IntermediateArtifactHandle(Generic[T]):

    def __init__(self) -> None:
        self._pending: T | object = _UNSET
        self._value: T | object = _UNSET
        self._committed = False
        self._aborted = False

    async def prepare(
        self,
        value: T,
        _codec: Codec[T],
        _fingerprint: str,
    ) -> None:
        if self._committed or self._aborted:
            raise RuntimeError("publication handle is already terminal")
        if self._pending is not _UNSET:
            raise RuntimeError("publication handle is already prepared")
        self._pending = value

    async def commit(self) -> None:
        if self._committed or self._aborted:
            return
        if self._pending is _UNSET:
            raise RuntimeError("publication handle has not been prepared")
        self._value = self._pending
        self._pending = _UNSET
        self._committed = True

    async def abort(self) -> None:
        if self._committed or self._aborted:
            return
        self._pending = _UNSET
        self._aborted = True

    async def get_value(self, _codec: Codec[T]) -> T:
        if not self._committed:
            raise RuntimeError("artifact is not committed")
        return self._value  # type: ignore[return-value]

    async def get_content_fingerprint(
        self,
        _codec: Codec[T]
    ) -> str:
        return ""

    async def close(self) -> None:
        return None
```

## Artifact Target/Source Handles

The `IntermediateArtifactHandle` shown above implements both source and target contracts. It is prepared as an output target, becomes readable only after commit, and is then used as the source for downstream stages.

## Artifact Orchestration
Artifact orchestration has two distinct responsibilities:

1. **binding resolution**, performed by the planner, runner, or cache coordinator;
2. **typed I/O**, performed by `InputArtifacts`, `OutputArtifacts`, `ArtifactReader`, and `ArtifactWriter` over already-resolved handles.

### Cache resolution contract
The cache coordinator atomically resolves an expected artifact fingerprint into one of three results:

```python
@dataclass(frozen=True)
class CacheHit(Generic[T]):
    source_handle: ArtifactSourceHandle[T]


@dataclass(frozen=True)
class CacheWriteReservation(Generic[T]):
    target_handle: ArtifactTargetHandle[T]


class CacheWaitHandle(Protocol):
    async def wait(self) -> None: ...


@dataclass(frozen=True)
class CacheWait:
    fingerprint: str
    owner_task_id: str | None
    wait_handle: CacheWaitHandle


class ArtifactCache(Protocol):
    async def resolve(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
        *,
        force_write: bool = False,
    ) -> CacheHit[T] | CacheWriteReservation[T] | CacheWait: ...
```

A `CacheHit` contains a source handle that already owns a read reservation. A `CacheWriteReservation` contains a target handle that already owns the exclusive write reservation for that fingerprint.

The resolution operation must be atomic with respect to lookup, metadata validation, and reservation acquisition:

```text
lookup + validate + acquire read reservation
or
confirm miss + acquire exclusive write reservation
```

When another task is already writing the fingerprint, `resolve` returns an explicit cache-wait result. After the active writer commits or aborts, the waiting path re-checks the entry.

`resolve(..., force_write=True)` uses the same exclusive reservation behavior but deliberately ignores an existing completed cache entry. The stage-output binding helper uses it only when a partial cache hit requires the example to rerun the stage and refresh its complete cacheable output set together.

The output-bootstrap adapter must preserve all three cache outcomes. It must not convert `CacheWait` into an exception or construct a writer without a reservation:

```python
@dataclass(frozen=True)
class CacheableOutputAvailable(Generic[T]):
    instance: ArtifactInstance[T]


@dataclass(frozen=True)
class CacheableOutputProduce(Generic[T]):
    writer: ArtifactWriter[T]


@dataclass(frozen=True)
class CacheableOutputWaiting:
    wait: CacheWait
```

Waiting and retry are runner or scheduler responsibilities. `CacheWait.wait_handle` becomes ready after the active writer commits or aborts. The runner then calls output bootstrap again and receives either `CacheableOutputAvailable` or `CacheableOutputProduce`. The wait handle is a notification mechanism only; it does not carry an artifact source or write reservation.

### Reader

`ArtifactReader` is bootstrapped with a complete `ArtifactHandle` containing its source.

```python
class ArtifactReader(Generic[T]):
    def __init__(self, handle: ArtifactHandle[T]):
        if handle.source is None:
            raise ValueError("input artifact has no bound source")
        self.handle = handle
        self._value: T | object = _UNSET

    async def read(self) -> T:
        if self._value is _UNSET:
            assert self.handle.source is not None
            self._value = await self.handle.source.get_value(
                self.handle.declaration.codec
            )
        return cast(T, self._value)
```

The reader may memoize the typed object for the invocation. It does not own the lifetime of the underlying source reservation; the task executor closes the source handle when the task no longer needs it.

### Stage-facing input/output collections

Stages receive uniform `execute(inputs, outputs)` bindings. `InputArtifacts` and `OutputArtifacts` are keyed collections over already-resolved readers and writers:

```python
ArtifactSelector = ArtifactDeclaration[Any] | ArtifactKey | str


class InputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactReader[Any]: ...


class OutputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactWriter[Any]: ...
    def produced_instances(self) -> tuple[ArtifactInstance[Any], ...]: ...
```

The selector may be a declaration, a fully resolved `ArtifactKey`, or a string such as `"utterance_detections//vad_stream"`. These collections do not perform cache lookup, phase selection, or reservation acquisition; they only expose the already-bound stage inputs and outputs through a stable stage method signature.

`OutputArtifacts` is also responsible for resolving the set of semantic artifacts actually produced by the stage invocation. A stage may write one or many bound outputs during execution, and the runner derives the produced intermediate resources from the bound writers after `execute(inputs, outputs)` returns rather than from a stage return value. If a declared bound output is required but never written, output resolution should fail.

### Writer

`ArtifactWriter` is bootstrapped with a complete `ArtifactHandle` containing all required targets. 

```python
class ArtifactWriter(Generic[T]):
    def __init__(self, handle: ArtifactHandle[T]):
        if not handle.targets:
            raise ValueError("output artifact has no bound targets")
        self.handle = handle
        self.written = False

    async def write(self, value: T) -> ArtifactInstance[T]:
        if self.written:
            raise RuntimeError("artifact may be written only once")

        prepared: list[ArtifactTargetHandle[T]] = []
        try:
            for target in self.handle.targets:
                await target.prepare(
                    value,
                    self.handle.declaration.codec,
                    self.handle.fingerprint,
                )
                prepared.append(target)

            for target in self.handle.targets:
                await target.commit()
        except BaseException:
            for target in reversed(prepared):
                await target.abort()
            raise

        self.written = True
        return ArtifactInstance(self.handle)

    async def abort_if_unwritten(self) -> None:
        if not self.written:
            for target in reversed(self.handle.targets):
                await target.abort()
```

For a cacheable artifact that must be produced, the runner binds two semantic output targets:

1.  the reserved durable cache target;
2.  an in-memory publication target, when downstream stages need the value during the current task.

Caller-provided file, object, or stream targets are not included in the producing stage's `ArtifactWriter`. They are materialization targets and are written separately after the semantic artifact has become available.

All semantic targets are prepared before any target is committed. The durable cache target is committed first. The in-memory publication target is committed last so that downstream stages cannot observe the value before the durable cache entry has been successfully published.

If the durable cache commit fails:

-   the in-memory target is aborted and remains unreadable;
-   the cache write reservation is released;
-   the artifact is not marked available;
-   caller-provided materialization targets are not attempted.

If the semantic artifact commits successfully but a later caller-provided materialization fails, the semantic cache entry remains valid. A subsequent run may reuse the cached artifact and retry materialization without rerunning the producing stage.

### Bootstrap flow for a supplied artifact

A supplied graph root is bound by fingerprinting the concrete source content and constructing an available instance. In the executable example this is an `ArtifactBindingsProvider` method:

```python
class ArtifactBindingsProvider:
    async def bind_supplied(
        self,
        declaration: ArtifactDeclaration[T],
        source: ArtifactSourceHandle[T],
    ) -> ArtifactInstance[T]:
        fingerprint = await (
            self._fingerprint_provider.build_supplied_artifact_fingerprint(
                declaration,
                source,
            )
        )
        return ArtifactInstance(
            ArtifactHandle(
                declaration=declaration,
                fingerprint=fingerprint,
                source=source,
            )
        )
```

### Bootstrap flow for a cacheable stage output
The executable example keeps the single-output binding as a private `ArtifactBindingsProvider` helper because stages are admitted using their complete planned output set. The cache still owns lookup and reservation policy; the provider helper only translates cache outcomes into stage-facing bindings:

```python
class ArtifactBindingsProvider:
    async def _bind_cacheable_output(
        self,
        declaration: ArtifactDeclaration[T],
        pedigree_fingerprint: str,
        *,
        force_cache_overwrite: bool = False,
    ) -> CacheableOutputBootstrap[T]:
        publication = IntermediateArtifactHandle[T]()
        fingerprint = await (
            self._fingerprint_provider.build_intermediate_artifact_fingerprint(
                declaration,
                pedigree_fingerprint,
            )
        )

        resolution = await self._cache.resolve(
            declaration,
            fingerprint,
            force_write=force_cache_overwrite,
        )

        if isinstance(resolution, CacheHit):
            return CacheableOutputAvailable(
                ArtifactInstance(
                    ArtifactHandle(
                        declaration=declaration,
                        fingerprint=fingerprint,
                        source=resolution.source_handle,
                    )
                )
            )

        if isinstance(resolution, CacheWriteReservation):
            handle = ArtifactHandle(
                declaration=declaration,
                fingerprint=fingerprint,
                source=publication,
                targets=(
                    # Commit the durable semantic representation first.
                    resolution.target_handle,

                    # Publish to downstream stages only after cache commit.
                    publication,
                ),
            )
            return CacheableOutputProduce(ArtifactWriter(handle))

        if isinstance(resolution, CacheWait):
            # Do not construct a writer or start the producing stage.
            return CacheableOutputWaiting(resolution)

        raise TypeError(f"unsupported cache resolution: {resolution!r}")
```

### Binding a stage output set

The executable example resolves all planned outputs for a stage before admitting it:

- cacheable outputs use `_bind_cacheable_output`;
- non-cacheable outputs receive an in-memory publication writer only;
- if any output is waiting on another writer, newly acquired write reservations are aborted, all wait handles are awaited, and the full output set is resolved again;

- if all cacheable outputs are available and there are no non-cacheable outputs, the stage is skipped;
- if only part of the cacheable output set is available, the example reruns the stage and uses `resolve(..., force_write=True)` to overwrite every cacheable output in that set;
- caller-provided targets remain separate and are materialized from the merged available and newly produced instances.

This is the stage-level policy demonstrated by `bind_stage_outputs`. Cache resolution itself remains atomic per artifact fingerprint.

A runner handles the single-output bootstrap result as follows:

```python
available, writers, waited_for_writer = await artifact_bindings_provider.bind_stage_outputs(
    list(invocation.output_declarations),
    pedigree_fingerprint,
)

if not writers:
    # Every planned output was already available from cache.
    await artifact_bindings_provider.materialize_provided_outputs(
        available,
        invocation.provided_outputs,
    )
    produced_instances = available
else:
    outputs = OutputArtifacts.from_writers(writers)
    await invocation.stage.execute(inputs, outputs)

    produced_instances = artifact_bindings_provider.merge_stage_output_instances(
        available,
        outputs.produced_instances(),
    )
    await artifact_bindings_provider.materialize_provided_outputs(
        produced_instances,
        invocation.provided_outputs,
    )
```

The result after a cache wait is not assumed to be a hit. If the previous writer aborted, retry may acquire the replacement write reservation and return a writer.

The writer prepares every target before committing any target. Because targets are committed in binding order, the durable cache target is committed before the in-memory publication target.

After the stage returns, the runner resolves the produced semantic instances from the bound outputs:

```python
outputs = OutputArtifacts.from_writers(writers)
await stage.execute(inputs, outputs)
produced_instances = outputs.produced_instances()
```

This keeps the stage execution contract independent from the number of outputs a stage chooses to write.

After `semantic_instance.handle.source` is available, each caller-provided output is materialized independently:

```python
reader = ArtifactReader(semantic_instance.handle)
value = await reader.read()

for caller_target in caller_targets:
    materialization_handle = ArtifactHandle(
        declaration=declaration,
        fingerprint=fingerprint,
        source=semantic_instance.handle.source,
        targets=(caller_target,),
    )

    await ArtifactWriter(materialization_handle).write(value)
```

This materialization flow is the same whether the semantic source came from:

-   a newly produced in-memory value;
-   a cache hit;
-   another already-resolved semantic source.

A task may treat failure to materialize a requested target as a task failure, but that failure does not invalidate or remove an otherwise valid semantic cache entry.

### Bootstrap flow for a stage input
The planner or runner selects an already-available `ArtifactInstance` and injects its source into a reader:

```python
input_handle = selected_artifact_instance.handle
reader = ArtifactReader(input_handle)
```

The concrete source may be supplied, cached, or in memory. The runner exposes the bound readers to the stage through `InputArtifacts`, and the stage uses the same contract in every case:

```python
audio = await inputs.get_artifact(ArtifactKey.parse("source//audio")).read()
```

### Materialization flow

Materialization reuses the semantic artifact source and a caller-provided target handle:

```python
reader = ArtifactReader(semantic_instance.handle)
value = await reader.read()

materialization_handle = ArtifactHandle(
    declaration=semantic_instance.handle.declaration,
    fingerprint=semantic_instance.fingerprint,
    source=semantic_instance.handle.source,
    targets=(caller_target_handle,),
)
await ArtifactWriter(materialization_handle).write(value)
```

No cache lookup is needed during materialization because the semantic artifact source has already been resolved.

### Reservation ownership
Reservation ownership is attached to concrete cache handles:

- a cache source handle owns a read reservation from cache resolution until `close()`;
- a cache target handle owns an exclusive write reservation from cache resolution until `commit()` or `abort()`;
- `ArtifactReader` does not acquire or release cache reservations;
- `ArtifactWriter` causes target handles to commit or abort, but it does not know that a target is a cache target;
- the runner closes task-owned source handles and aborts unwritten target handles during failure or cancellation cleanup.

This allows cache locking behavior to remain encapsulated by the cache-backed handles while keeping stage-facing readers and writers independent of cache implementation.

The focused `ExampleTaskRunner` demonstrates successful execution and cache-hit skipping, and contains the cache-wait path, but it does not implement the production task cleanup path. Its helper contracts expose `abort_if_unwritten`, `abort_writers`, and `close()` so a production runner can perform the required failure, cancellation, and source-lifetime cleanup.


### Orchestration invariants

1. Every reader is constructed with exactly one already-resolved source handle.
2. Every writer is constructed with all already-resolved target handles required for that output.
3. Cache lookup and reservation happen before reader or writer construction.
4. A cache hit is not exposed until its source handle owns a read reservation.
5. A cache miss is not assigned to a stage until its target handle owns the write reservation.
6. A cache write reservation is released only by target `commit()` or `abort()`.
7. A produced value is not exposed through the in-memory source until all required output preparation has succeeded and durable targets have committed.
8. Same-process stage-to-stage transfer uses an in-memory source and does not require serialization.
9. Cache and file boundaries use the declaration codec through their concrete handles.
10. Stage code sees semantic artifact keys and typed values, not cache paths, locks, or provider-selection logic.

## Fingerprints
Fingerprints identify concrete artifact instances for cache lookup, reuse, provenance, and invalidation.

The example uses two explicit builders because supplied and stage-produced artifacts have different available inputs at binding time:

```text
supplied artifact fingerprint = hash(
    artifact identity,
    supplied content fingerprint,
)

stage-produced artifact fingerprint = hash(
    artifact identity and codec identity,
    production pedigree,
)
```

The example's `stable_hash` uses canonical JSON plus BLAKE3.

### Content fingerprint

Every concrete artifact source handle exposes:

```python
class ArtifactSourceHandle(Protocol[T]):
    async def get_value(self, codec: Codec[T]) -> T: ...
    async def get_content_fingerprint(self, codec: Codec[T]) -> str: ...
```

`get_content_fingerprint(codec)` returns the content contribution for a supplied source handle. It does not return the final artifact fingerprint.

The method remains valid for every source-handle type:

- a caller-supplied file, object, or stream returns a stable digest of its semantic content;
- a synthesized settings handle returns a digest of the resolved effective settings value;
- a stage-produced in-memory handle returns an empty string because its identity comes from production pedigree;
- a cache-backed source handle returns an empty string because the expected final fingerprint is already the cache key validated before the handle was created.

The current example calls this method from `FingerprintProvider.build_supplied_artifact_fingerprint`. The stage-produced builder does not read or serialize the produced value.

The handle decides what content equality means. It may use the declaration codec, but it is not required to hash the codec's raw serialized bytes. For example:

- a JSON settings handle may hash canonical serialized JSON;
- an audio handle may hash decoded canonical PCM plus relevant sample metadata;
- a raw file handle may hash file bytes when byte identity is the desired contract;
- a derived handle returns `""` without reading or serializing its value.

If a canonicalization algorithm or provider version affects the returned digest, that version must be represented in the returned content fingerprint or final structured payload.

### Pedigree fingerprint

Production pedigree is the deterministic description of how an artifact edge was derived. It is calculated while walking the planned graph forward from available input edges.

The pedigree calculation itself is owned by graph creation and is intentionally not specified here. Artifact orchestration receives the completed `pedigree_fingerprint` as an input. The executable example uses `mock_pedigree_fingerprint`, which is explicitly incomplete and stands in for graph creation.

### Supplied artifact fingerprint builder

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

This builder is used for caller-supplied source, settings, or intermediate graph roots.

### Stage-produced artifact fingerprint builder

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

This builder is used for both cacheable and non-cacheable stage outputs. It does not fingerprint the produced content again. Despite its example name, it is also used for stage-produced target artifacts; the distinguishing input is production pedigree rather than artifact role.
