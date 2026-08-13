# Bindings

Bindings are the runtime objects that connect the planner's semantic artifact graph to concrete stage execution. A binding answers the question "what handle should this stage read from or write to for this artifact right now?" without forcing stage code to know whether the artifact came from a supplied source, durable persistence, a cache hit, an in-memory intermediate value, or a caller-provided terminal target.

Bindings are needed for two reasons:

1. stages require a uniform `execute(inputs, outputs)` contract even though artifact lifetimes and storage locations differ;
2. cache and durable lookup, fingerprinting, reservation ownership, in-memory publication, and final output materialization must happen before or after stage execution, not inside stage logic.

Storage location does not change the stage-facing contract. A durable source is
adapted to the same typed `ArtifactReader` used for local and cache-backed
sources. Representation transfer is an additional capability used by the
runtime outside stage code; it does not expose persistence APIs to stages.

The supporting storage contracts are:

- [Cache Management](02-cache-management.md);
- [Representation Store](03-representation-store.md); and
- [Artifact Persistence](04-artifact-persistence.md).

## Stage-facing input/output collections

Stages receive uniform `execute(inputs, outputs)` bindings. `InputArtifacts` and `OutputArtifacts` are keyed collections over already-resolved readers and writers:

```python
ArtifactSelector = ArtifactDeclaration[Any] | ArtifactKey | ArtifactRefLocator | str

class InputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactReader[Any]: ...


class OutputArtifacts:
    def get_artifact(self, selector: ArtifactSelector) -> ArtifactWriter[Any]: ...
```

The selector may be a declaration, a fully resolved `ArtifactKey`, an `ArtifactRefLocator`, or a string such as `"utterance_detections/vad_stream"`. These collections do not perform cache lookup, phase selection, or reservation acquisition. They expose bindings that were already resolved by the planner, runner, and `ArtifactBindingsProvider`.

The runner is also responsible for resolving the set of semantic artifacts actually produced by the stage invocation. A stage may write one or many bound outputs during execution, and the runner derives the produced intermediate resources from the bound writers after `execute(inputs, outputs)` returns rather than from a stage return value. If a declared bound output is required but never written, output resolution should fail.

## How bindings are created

This document distinguishes three binding categories:

1. supplied bindings: graph-root artifacts provided by the caller or pinned to a cached instance;
2. intermediate bindings: stage inputs and stage outputs used during task execution;
3. output materialization bindings: caller-requested terminal outputs written after the semantic artifact instance is already known.

### 1. Supplied bindings

A supplied graph root is bound by fingerprinting the concrete source content and constructing an available instance. `ArtifactBindingsProvider.bind_supplied` performs this binding:

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

The result is an `ArtifactInstance` whose source handle is the caller-supplied source. No cache lookup is required to select the root because the caller has already supplied the concrete artifact content.

Binding a local supplied root does not publish it to cache or durable
persistence. Durable upload is a separate per-artifact I/O operation. A local
binding may use private task-scoped workspace storage without creating a public
cache instance.

A supplied root may instead be an `ArtifactInstanceLocator` parsed from
`a-instance`. That locator pairs semantic artifact identity with the expected
final fingerprint. When resolving through cache, binding validates the manifest
declaration and cache metadata, acquires a read reservation, and returns an
`ArtifactInstance` backed by the reserved cache source.

For a durable locator, binding performs the equivalent validation and
reservation through artifact persistence and adapts the returned transfer
source to the declaration's typed reader. Localizing the representation into
cache is an optional optimization, never a prerequisite for stage binding.

Cached settings locators may be rebound and read before graph planning so the planner's settings view contains the same value it would have received from a normal supplied settings handle.

### 2. Intermediate bindings

Intermediate bindings cover both stage inputs and stage outputs. They allow stages to read and write semantic artifacts without knowing whether those artifacts are backed by cache, memory, or another runtime-specific handle.

#### Stage input bindings

The planner or runner selects an already-available `ArtifactInstance` and injects its source into a reader:

```python
input_handle = selected_artifact_instance.handle
reader = ArtifactReader(input_handle)
```

The concrete source may be supplied, cached, or in memory. The runner exposes the bound readers to the stage through `InputArtifacts`, and the stage uses the same contract in every case:

```python
audio = await inputs.get_artifact(ArtifactKey.from_a_ref("source/audio")).read()
```

#### Stage output bindings

`ArtifactBindingsProvider.bind_stage_outputs` resolves all planned outputs for a stage before the stage is allowed to execute. It separates stage outputs into cacheable and non-cacheable declarations and binds each category differently.

At a high level:

- cacheable outputs may resolve to an already-available cache hit, a reserved writer for a cache miss, or a wait on another task's active writer;
- non-cacheable outputs always resolve to publication-only writers backed by an `IntermediateArtifactHandle`;
- the returned result tells the runner whether the stage can be skipped, must wait, or must execute with writers.

`bind_stage_outputs` uses these internal implementations:

- `_bind_cacheable_stage_outputs` runs one cacheable-output bootstrap pass and returns `StageOutputBindings` split into available instances, reserved writers, and wait states;
- `_bind_non_cacheable_stage_outputs` builds publication-only writers for non-cacheable outputs, using intermediate fingerprints and an `IntermediateArtifactHandle` as both source and target.

##### Cached stage output behavior

Each cacheable output is bound through `_bind_cacheable_output`. The helper derives the output fingerprint from the declaration and the planned pedigree fingerprint, then asks the cache to atomically resolve that fingerprint.

#### Cache resolution contract
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

1. A `CacheHit` contains a source handle that already owns a read reservation. 
2. A `CacheWriteReservation` contains a target handle that already owns the exclusive write reservation for that fingerprint.

The resolution operation must be atomic with respect to lookup, metadata validation, and reservation acquisition:

```text
lookup + validate + acquire read reservation
or
confirm miss + acquire exclusive write reservation
```

When another task is already writing the fingerprint, `resolve` returns an explicit cache-wait result. After the active writer commits or aborts, the waiting path re-checks the entry.

`resolve(..., force_write=True)` uses the same exclusive reservation behavior but deliberately ignores an existing completed cache entry. `bind_stage_outputs` uses this only when a partial cache hit requires the provider to rerun the full cacheable output set and refresh it coherently.

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

Waiting and retry are runner responsibilities. `CacheWait.wait_handle` becomes ready after the active writer commits or aborts. The runner then calls output bootstrap again and receives either `CacheableOutputAvailable` or `CacheableOutputProduce`. The wait handle is a notification mechanism only; it does not carry an artifact source or write reservation.

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

For cached outputs, `bind_stage_outputs` applies this stage-level policy:

- if any cacheable output returns a wait, the provider releases provisional readers and writers from that pass and returns a `StageCacheWait` to the runner;
- if all cacheable outputs are already available and there are no non-cacheable outputs, the stage is skipped;
- if only part of the cacheable output set is available, the provider releases provisional readers and writers, reruns cacheable binding, and uses `resolve(..., force_write=True)` so the stage refreshes the whole cacheable output set together;
- if all cacheable outputs are reserved for write, the stage receives writers for those outputs.

##### Non-cached stage output behavior

Non-cacheable outputs do not consult the cache. `_bind_non_cacheable_stage_outputs` creates an intermediate fingerprint for each declaration and constructs an `ArtifactWriter` whose source and only target are both the `IntermediateArtifactHandle`. This means:

- the stage still writes through the same `OutputArtifacts` contract as cacheable outputs;
- the produced value is published in memory for downstream consumers in the same task;
- no shared cache reservation or cache-hit path exists for these outputs.

If a stage has both cacheable and non-cacheable outputs, `bind_stage_outputs` binds the cacheable outputs first and then appends the non-cacheable writers when the stage is allowed to run.

The runner pattern is:

- keep retrying while `bind_stage_outputs` returns `StageCacheWait`;
- skip stage execution when every planned output is already available;
- otherwise construct `OutputArtifacts` from the bound writers, execute the stage, resolve the produced instances from those writers, and merge them with any already-available cache hits.

The runner handles the stage-output binding result as follows:

```python
while True:
    bindings = await artifact_bindings_provider.bind_stage_outputs(
        list(invocation.output_declarations),
        pedigree_fingerprint,
    )
    if isinstance(bindings, StageCacheWait):
        await bindings.cache_wait.wait_handle.wait()
        continue
    break

available = bindings.available
writers = bindings.writers

if not writers:
    # Every planned output was already available from cache.
    produced_instances = available
else:
    outputs = OutputArtifacts.from_writers(writers)
    await invocation.stage.execute(inputs, outputs)

    produced_instances = artifact_bindings_provider.merge_stage_output_instances(
        available,
        resolve_produced_instances(writers),
    )
```

After the stage returns, the runner resolves the produced semantic instances from the bound outputs:

```python
outputs = OutputArtifacts.from_writers(writers)
await stage.execute(inputs, outputs)
produced_instances = resolve_produced_instances(writers)
```

This keeps the stage execution contract independent from the number of outputs a stage chooses to write. `merge_stage_output_instances` merges cache-hit `available` instances with stage-produced instances by `artifact_id`, allowing newly produced values to replace provisional cache-hit entries for the same declared output when needed.

### 3. Output materialization bindings

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

If a requested output has zero caller targets, no materialization binding is created. The runner instead records the already-available semantic instance and exposes the requested output `a-task` locator and, once durable publication has completed, the externally retrievable `a-instance` locator.

Later, the task runner materializes requested terminal outputs by calling `ArtifactBindingsProvider.materialize` for each selected `ArtifactInstance` and caller target handle. This materialization flow is the same whether the semantic source came from:

- a newly produced in-memory value;
- a cache hit;
- another already-resolved semantic source.

A task may treat failure to materialize a requested target as a task failure, but that failure does not invalidate or remove an otherwise valid semantic cache entry.

### Reservation ownership
Reservation ownership is attached to concrete cache handles:

- a cache source handle owns a read reservation from cache resolution until `close()`;
- a cache target handle owns an exclusive write reservation from cache resolution until `commit()` or `abort()`;
- `ArtifactReader` does not acquire or release cache reservations;
- `ArtifactWriter` causes target handles to commit or abort, but it does not know that a target is a cache target;
- the runner closes task-owned source handles and aborts unwritten target handles during failure or cancellation cleanup.

This allows cache locking behavior to remain encapsulated by the cache-backed handles while keeping stage-facing readers and writers independent of cache implementation.

The focused `ExampleTaskRunner` demonstrates successful execution and cache-hit skipping, and contains the cache-wait path, but it does not implement the production task cleanup path. Its helper contracts expose `abort_if_unwritten`, `abort_writers`, and `close()` so a production runner can perform the required failure, cancellation, and source-lifetime cleanup.


## Binding invariants

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
11. Binding a local supplied root does not implicitly publish it to cache.
12. A cached-root reference is validated and rebound into a reserved cache source before planning or execution consumes it.
13. Directly published cached roots behave like ordinary supplied roots once selected by request configuration.
14. Targetless requested outputs create no materialization bindings and expose
    an instance locator only for committed readable cache content.
