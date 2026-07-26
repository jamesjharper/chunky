# Artifact IO

## Artifact Handles
An `ArtifactHandle` is an execution-time binding for one concrete artifact instance. The execution planner or runner creates an artifact handle once after the artifact fingerprint is known and the required source or target handles have been resolved.

Stage-produced outputs receive their fingerprint from the planner-provided production pedigree before cache or publication targets are bound.

```python
@dataclass(frozen=True)
class ArtifactHandle(Generic[T]):
    declaration: ArtifactDeclaration[T]
    fingerprint: str
    source: ArtifactSourceHandle[T] | None = None
    targets: tuple[ArtifactTargetHandle[T], ...] = ()

    @property
    def instance_locator(self) -> ArtifactInstanceLocator:
        return ArtifactInstanceLocator(
            artifact_key=self.declaration.artifact_id,
            fingerprint=self.fingerprint,
        )
```

The structure represents both input and output bindings:

- `declaration.artifact_id` represents the fully resolved artifact ID. Any phase selection or terminal remapping is completed before the handle is created;
- `fingerprint` is the final artifact fingerprint and, together with the resolved artifact ID, derives the concrete `a-instance` locator for this handle;
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

    @property
    def locator(self) -> ArtifactInstanceLocator:
        return self.handle.instance_locator
```

## Artifact Source Handle
An `ArtifactSourceHandle` represents one already-selected source for a concrete artifact value. Examples include:

- a caller-supplied file;
- a caller-supplied in-memory object;
- an in-memory value produced earlier in the same task;
- a cache entry with an active read reservation, including one opened from a concrete cached-instance reference.

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
`get_content_fingerprint(codec)` returns the content contribution for a supplied source handle. It does not return the final artifact fingerprint.

A source handle should represent one stable logical snapshot, and a separately
reported content fingerprint should describe the same content returned to
execution. This is a soft integrity expectation: implementations should use
inexpensive consistency checks where practical, but need not spool or reread
every source solely to prove snapshot stability.

The method remains valid for every source-handle type:

- a caller-supplied file, object, or stream returns a stable digest of its semantic content;
- a synthesized settings handle returns a digest of the resolved effective settings value;
- a stage-produced in-memory handle returns an empty string because its identity comes from production pedigree;
- a cache-backed source handle returns an empty string because the expected final fingerprint is already the cache key validated before the handle was created.

The handle decides what content equality means. It may use the declaration codec, but it is not required to hash the codec's raw serialized bytes. For example:

- a JSON settings handle may hash canonical serialized JSON;
- an audio handle may hash decoded canonical PCM plus relevant sample metadata;
- a raw file handle may hash file bytes when byte identity is the desired contract;
- a derived handle returns `""` without reading or serializing its value.

If a canonicalization algorithm or provider version affects the returned digest, that version must be represented in the returned content fingerprint or final structured payload.

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

## Concrete Artifact Instance Locators

A concrete artifact instance locator is not a source handle by itself. It is a structured pointer to one deterministic artifact instance and serializes as `a-instance`:

```python
@dataclass(frozen=True)
class ArtifactInstanceLocator:
    artifact_key: ArtifactKey
    fingerprint: str
    projection_context: str = "global"
```

Before a stage or planner can use an externally supplied `a-instance`, artifact binding must validate the locator against the manifest declaration and cache metadata, acquire a cache read reservation, and turn it into an `ArtifactInstance` whose source is a cache-backed `ArtifactSourceHandle`.

For settings artifacts, this binding may happen before normal graph planning so settings values can participate in deterministic requirement and output-capability resolution. For other root types, the rebound cache source behaves like any other supplied artifact root.

Internally, every handle can derive an `a-instance` locator from its resolved declaration and final fingerprint, including non-cacheable in-memory instances. Externally exposed `a-instance` locators are limited to durably retrievable artifacts. A non-cacheable handle may have a valid internal locator while still being ineligible for public retrieval.

The locator answers *which concrete artifact instance* is meant. It does not encode whether that instance has been committed, is durably retrievable, or is currently readable by the caller. Those are execution and retrieval state fields.

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


## Artifact Reader Writers

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

> Special Note: memoization might need revisiting, languages like rust will require this to become a mutable method which might be cumbersome, might be needed to read object into memory in the planner and pass to stage 

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

Explicit artifact publication and canonicalization use the same target lifecycle
when committing supplied content into cache. Live execution does not publish a
supplied source merely because it was selected as a graph root.

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

For targetless requested outputs there are no caller materialization targets.
The runner reports availability only after the semantic artifact has committed,
and exposes an `a-instance` only for that real readable cache entry. The
provider's lifetime determines whether a live result is durable; canonical
tasks require the stronger persisted-task retention contract.



## Recommended Invariants

1. `ArtifactHandle.declaration.artifact_id` is the resolved semantic artifact ID.
2. Concrete cached-instance references must be rebound into cache source handles before use.
3. A cache source handle owns an active read reservation from binding until close.
4. Explicit cache publication uses the same prepare/commit/abort target
   lifecycle as stage-produced cache publication.
5. Settings roots opened from cache may be read before planning; other opened roots behave like supplied graph roots after binding.
