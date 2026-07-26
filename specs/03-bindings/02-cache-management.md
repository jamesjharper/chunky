# Cache Management

## Purpose

This document defines how Chunky stores, resolves, reserves, publishes, reuses, and removes cached semantic artifacts.

The cache is shared coordination state across concurrently running tasks. A task may know the artifact fingerprints it needs, but the cache is responsible for ensuring that:

- a completed artifact is validated before it is reused;
- an artifact being read cannot be removed by garbage collection;
- only one producer owns the right to write a fingerprint at a time;
- incomplete writes never become cache hits;
- tasks waiting for another producer can be cancelled without cancelling that producer;
- cache hits, misses, waits, commits, failures, and recovery are explainable.

Cache behavior is exposed to artifact orchestration through resolved source and target handles. Stages, `ArtifactReader`, and `ArtifactWriter` do not acquire cache locks or inspect cache storage directly.

## Design Boundaries

Cache management is responsible for:

- looking up cache entries by expected artifact fingerprint;
- validating entry metadata against the expected artifact declaration;
- acquiring shared read reservations for valid hits;
- acquiring exclusive write reservations for missing or deliberately refreshed entries;
- returning an explicit wait result when another task owns the write reservation;
- publishing prepared cache values atomically, including explicit artifact ingestion;
- releasing reservations after commit, abort, source close, cancellation, or recovery;
- preventing garbage collection from racing active readers and writers;
- detecting and recovering stale reservations and incomplete entries;
- exposing enough events and metadata to explain cache decisions.

Cache management is not responsible for:

- constructing the task graph;
- choosing artifact producers;
- resolving stage input requirements or output capabilities;
- calculating graph shape or phase selection;
- deciding when a stage has enough resources to run;
- waiting internally on behalf of the scheduler;
- materializing semantic artifacts to caller-provided output paths;
- calculating the production pedigree from the graph;
- deciding whether a run request should use a source handle or cached-root reference as a graph root.

The cache receives an already-resolved artifact declaration and expected fingerprint. Graph creation owns the calculation of production pedigree. Artifact orchestration combines that pedigree with the resolved artifact declaration to calculate the final artifact fingerprint used by the cache.

## Cache Identity

A cache entry is addressed by the final semantic fingerprint of one concrete artifact instance.

```text
CacheEntryKey = ArtifactFingerprint
```

The fingerprint is the primary lookup key, but a matching path or database key is not sufficient to establish a hit. The cache must also validate the entry metadata against the expected artifact declaration and codec.

Task IDs, invocation IDs, file paths, temporary paths, and reservation IDs are operational metadata. They must not be included in semantic cache identity.

A caller-provided materialization path is not part of the cache key. The same semantic target artifact may be reused and materialized to different caller destinations without rerunning its producing stage.

A public cached-instance reference is an `a-instance` locator. It pairs the semantic artifact identity with the final artifact fingerprint:

```python
@dataclass(frozen=True)
class ArtifactInstanceLocator:
    artifact_key: ArtifactKey
    fingerprint: str
    projection_context: str = "global"
```

The cache may physically index primarily by fingerprint, but public rebinding must validate both the fingerprint and the expected artifact declaration metadata. Fingerprints are not treated as untyped global object IDs. The same `a-instance` shape is used for upload deduplication, saved-task cached roots, and later retrieval of target-less or otherwise durably published requested outputs.

## Cache Entry Model

A committed cache entry contains:

1. the serialized artifact data;
2. authoritative metadata describing the semantic artifact and its serialized form;
3. optional operational metadata used for retention, diagnostics, and recovery.

Conceptually:

```python
@dataclass(frozen=True)
class CacheEntryMetadata:
    fingerprint: str

    artifact_id: ArtifactKey
    artifact_role: ArtifactRole
    artifact_version: int

    codec_id: str
    codec_version: int
    mime_type: str
    extension: str

    created_at: datetime
    last_used_at: datetime
    producing_task_id: str | None = None
    producing_invocation_id: str | None = None
```

The exact storage representation is backend-specific. A filesystem cache may use a data file plus a metadata file. A database or object-store cache may store the same fields in one record or through an immutable object plus a completion pointer.

### Required validation metadata

The following fields must match the expected artifact binding before an entry can be returned as a hit:

- artifact fingerprint;
- resolved artifact ID, including any selected phase or terminal remapping;
- artifact role;
- artifact declaration version;
- codec ID;
- codec version;
- MIME type;
- serialized extension or equivalent format identity.

The executable file-cache example writes metadata with this shape:

```python
metadata = {
    "artifact_id": str(declaration.artifact_id),
    "role": declaration.role.value,
    "version": declaration.version,
    "codec_id": codec.codec_id,
    "codec_version": codec.codec_version,
    "mime_type": codec.mime_type,
    "extension": codec.extension,
    "fingerprint": fingerprint,
}
```

A data object with missing, unreadable, or mismatched metadata is not a cache hit even when its storage name matches the requested fingerprint.

### Operational metadata

A production cache should additionally record enough operational information to support diagnostics and policy, such as:

- creation time;
- last successful use time;
- serialized size;
- producing task and invocation, when known;
- backend generation or object version;
- quarantine reason, when invalid;
- last validation or recovery time.

Operational metadata does not change semantic identity. Updating `last_used_at` must not alter the artifact fingerprint or require rewriting the semantic data.

### Entry states

A backend may represent entry state explicitly or infer it from storage layout. The observable states are:

```text
absent
preparing
committed
invalid
quarantined
deleting
```

Only `committed` entries may be returned as hits.

`preparing` data belongs to an active write reservation and must not be visible through normal lookup. `invalid`, `quarantined`, and `deleting` entries must not be returned as hits.

The file-cache example treats the final metadata file as the completion marker. Data may be staged or even moved into its final data location first, but the entry is not considered complete until the matching metadata is atomically published.

## Direct Cache I/O

Publishing caller-supplied content is explicit. Local request binding does not
publish a source merely because it is selected as an input. Direct publication
and durable request preparation use the ordinary reservation, validation,
prepare, commit, abort, and waiter-notification rules; they do not create a
weaker entry class.

The direct artifact-cache IO interface is a public convenience over the same semantics:

```python
class ArtifactCacheIO(Protocol):
    async def publish(
        self,
        declaration: ArtifactDeclaration[T],
        source: ArtifactSourceHandle[T],
        *,
    ) -> ArtifactInstanceLocator: ...

    async def open(
        self,
        locator: ArtifactInstanceLocator,
    ) -> ArtifactInstance[T]: ...
```

`publish` computes the supplied-artifact fingerprint, commits the source under
that fingerprint, and returns its structured `a-instance` locator. It uses the
ordinary artifact I/O contract; no separate streaming or bounded-memory
guarantee is required. Providers may optimize transfer internally, but transfer
capabilities and source-consumption semantics are outside this contract. `open`
validates semantic identity and fingerprint,
acquires a normal read reservation, and returns a cache-backed source handle.
Neither operation exposes backend paths, object-store keys, reservations, or
raw lock records.

A cached root opened by locator changes availability, not graph semantics. Once opened, it behaves like an ordinary supplied root. Cached settings roots may be opened and read before graph planning so settings-dependent planning hooks receive their values.

This direct cache IO surface is also the foundation for later requested-output retrieval. Results and output-availability events should expose an externally retrievable `a-instance` locator only after the cache entry is committed and validated. A future existence preflight may check whether a concrete `a-instance` already exists before uploading content, but that optimization is outside this version.

## Task-instance relations

The cache stores a TTL-bound, untyped relation:

```text
(task_id, task_a_ref) -> (resolved_canonical_a_ref, fingerprint)
```

`task_a_ref` is the stable requested output reference embedded in `a-task` and
may contain `any`. The value uses the fully resolved final-graph identity,
including projection, domain, content kind, and concrete phase. The resulting
`a-instance` is reconstructed from that identity and fingerprint.

A relation is written only for a committed readable entry, whether obtained as
a hit or newly produced. It does not pin the entry and has a task-history TTL
independent of entry lifetime. Entry expiry can therefore make an old `a-task`
unresolvable.

For one task-facing artifact reference, at most one effective mapping exists.
Conflicting mappings fail rather than overwrite one another. A recovery attempt
may replace an incompatible stale mapping only while the task remains
nonterminal; terminal mappings are immutable until their history expires.

Local tasks may use the same relation, but resolution lasts only as long as the
provider's task history and entry. The default workspace provider does not make
these locators survive pipeline shutdown.

## Request cache modes

The request layer supplies one of four produced-artifact modes. Cache resolution
implements them as follows:

- `DEFAULT`: accept a valid shared hit; reserve and publish on a miss;
- `REFRESH`: ignore shared hits; reserve and publish replacement work;
- `READ_ONLY`: accept a valid hit; return a non-publishing miss otherwise; and
- `BYPASS`: neither look up nor publish a shared entry.

On recovery, `REFRESH` may reuse an exact committed instance already related to
the same durable task. It still ignores compatible shared entries from other
tasks. `BYPASS` work has no committed instance to recover.

A resolved TTL is a best-effort retention request. A successful cache read or
new output commit refreshes the entry's expiry from that event. The TTL never
shortens a task retention claim or overrides provider limits.

## Cache Resolution Contract

Artifact orchestration asks the cache to resolve one expected artifact fingerprint.

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

`resolve` returns exactly one of three outcomes:

- `CacheHit`: a valid committed entry exists and the returned source handle already owns a read reservation;
- `CacheWriteReservation`: the caller owns the exclusive right to prepare and commit that fingerprint through the returned target handle;
- `CacheWait`: another producer owns the write reservation and the caller must not start a duplicate producer.

The result contains the reservation-owning handle. A separate lock acquisition step must not be required after `resolve` returns.

### Normal resolution algorithm

For `force_write=False`, the cache performs the following logical operation atomically:

```text
1. locate the entry by expected fingerprint
2. if a committed entry exists:
     validate all required metadata
     acquire a shared read reservation
     return CacheHit(reserved_source_handle)
3. otherwise, if an active write reservation exists:
     return CacheWait(owner, notification_handle)
4. otherwise:
     acquire the exclusive write reservation
     return CacheWriteReservation(reserved_target_handle)
```

An invalid or mismatched entry is treated as unavailable. The backend may quarantine it before attempting to acquire the write reservation.

### Force-write resolution

`force_write=True` deliberately ignores an existing completed entry, but it does not bypass write exclusivity.

```text
1. ignore an existing completed hit for selection purposes
2. if an active write reservation exists, return CacheWait
3. otherwise acquire the exclusive write reservation
4. return CacheWriteReservation
```

The demonstrated artifact runtime uses force-write only after a stage output set resolves to a partial cache hit. The stage is rerun and its complete cacheable output set is refreshed together.

Force-write does not create a new semantic identity. The replacement is written under the same expected fingerprint and therefore is valid only for deterministic cacheable artifacts. An implementation must never allow force-write to create two concurrent writers for the same fingerprint.

An older committed generation may already have active readers when force-write begins. The backend must either:

- keep the older immutable generation readable until those source handles close; or
- delay replacement until the older generation has no readers.

It must not replace bytes underneath an active cache source handle.

### Atomicity of `resolve`

Lookup, metadata validation, and reservation acquisition are one atomic cache operation from the perspective of concurrent tasks.

The implementation may use:

- one process-local mutex;
- database transactions;
- filesystem locks;
- lock records or leases;
- a dedicated cache service.

The mechanism is backend-specific, but the caller must never observe these races:

- a hit is returned and then removed before its read reservation exists;
- two callers both receive write reservations for the same fingerprint;
- a writer begins after a miss while another writer already owns the fingerprint;
- garbage collection deletes an entry between validation and read-reservation acquisition.

## Read Reservations

A read reservation protects a committed cache entry while a task may still read it.

Read reservations are shared. Any number of tasks or invocations may hold read reservations for the same committed fingerprint at the same time.

A production read reservation should record:

```text
reservation_id
fingerprint
owner_task_id
owner_invocation_id?
created_at
last_renewed_at?
```

The reservation ID is operational identity. The cache entry remains keyed by artifact fingerprint.

### Cache source handle

A cache hit returns an `ArtifactSourceHandle` that already owns the read reservation:

```python
class CacheSourceHandle(ArtifactSourceHandle[T]):
    async def get_value(self, codec: Codec[T]) -> T: ...

    async def get_content_fingerprint(self, codec: Codec[T]) -> str:
        # The final semantic fingerprint was validated before this handle existed.
        return ""

    async def close(self) -> None:
        # Idempotently release the read reservation.
        ...
```

The source handle may deserialize data lazily. Its reservation must already be active even when the first read has not happened yet.

`ArtifactReader` does not acquire or release the reservation. It only delegates to the bound source handle and may memoize the typed value for the invocation.

### Read-reservation lifetime

The runner or task executor owns source-handle lifetime.

A cache source may be shared by several downstream invocations in the same task. Closing an individual `ArtifactReader` must not release a task-level reservation while another consumer still depends on the source.

The source handle is closed when the owning task or scoped artifact binding no longer needs it, including after:

- successful completion;
- task failure;
- task cancellation;
- materialization completion;
- replacement of a provisional cache-resolution pass;
- recovery of a task known not to be running.

`close()` must be idempotent.

### Garbage-collection protection

An entry with one or more active read reservations is not eligible for deletion.

The cache SHOULD update `last_used_at` and refresh expiry when a cached entry is
successfully consumed. Acquiring a reservation without consuming the entry does
not refresh it. The update must not weaken reservation protection or make
semantic metadata inconsistent.

### Reservation inspection

The cache manager should expose internal administrative lookup sufficient for cleanup, recovery, and diagnostics. Read reservations should be discoverable by:

- reservation ID;
- artifact fingerprint;
- owner task ID;
- owner invocation or stage ID, when recorded;
- task plus invocation or stage.

These indexes do not change reservation semantics. A read reservation is a distinct operational record associated with one fingerprint; it is not semantically keyed by stage or task identity.

## Write Reservations

A write reservation protects a fingerprint that is being produced.

Write reservations are exclusive. At most one live reservation may exist for a fingerprint.

A production write reservation should record:

```text
fingerprint
owner_task_id
owner_invocation_id?
created_at
last_renewed_at?
state
```

The reservation is acquired before the producing stage is admitted. The returned target handle owns it until `commit()` or `abort()` reaches a terminal state.

Write reservations should be inspectable by fingerprint and owner task, invocation, or stage when those fields are available. This supports progress reporting, stale-owner recovery, and identification of the task returned in `CacheWait.owner_task_id`.

### Cache target handle

A cache write reservation returns an `ArtifactTargetHandle` with this lifecycle:

```python
class ArtifactTargetHandle(Protocol[T]):
    async def prepare(
        self,
        value: T,
        codec: Codec[T],
        fingerprint: str,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def abort(self) -> None: ...
```

State model:

```text
reserved -> prepared -> committed
    |           |
    +-----------+-> aborted
```

The reservation remains active throughout `reserved` and `prepared`.

### Prepare

`prepare(value, codec, fingerprint)` serializes or stages the artifact without making it visible as a committed hit.

A file-backed cache should write to temporary paths. A database cache may write an uncommitted row or a generation not yet referenced by the committed entry record.

Preparation should stage both:

- serialized artifact data;
- authoritative metadata for the expected declaration, codec, and fingerprint.

If preparation fails, the caller aborts the target and the entry remains unavailable.

### Commit

`commit()` publishes the prepared entry atomically where possible and releases the write reservation.

The cache must not report a hit before the complete entry is visible. In the file-cache example:

```text
1. atomically move the prepared data into its final data path
2. atomically move the prepared metadata into its final metadata path
3. treat metadata publication as the completion marker
4. release the write reservation
5. wake waiting tasks
```

A production backend should avoid exposing mixed data and metadata generations during replacement. This may be implemented using immutable generation directories, transactional records, or an atomic completion pointer.

`commit()` must be idempotent after successful commit.

### Abort

`abort()` removes staged data and metadata, releases the write reservation, and wakes waiters.

Abort is required when:

- the stage fails;
- task cancellation is observed before commit;
- the stage does not write a requested output;
- another semantic target fails during preparation;
- a multi-output resolution pass is abandoned because another output must wait;
- a partial cache hit causes the full cacheable output set to be reacquired for force-write;
- recovery determines that the writer is stale.

`abort()` must be idempotent.

### Reservation release

Only the reservation-owning cache target handle, or conservative recovery acting on a stale owner, releases a live write reservation.

`ArtifactWriter` invokes target `commit()` or `abort()`, but it does not need to know that a target is cache-backed.

## Cache Waits

A cache wait means another task currently owns the write reservation for the expected fingerprint.

```python
@dataclass(frozen=True)
class CacheWait:
    fingerprint: str
    owner_task_id: str | None
    wait_handle: CacheWaitHandle
```

The wait handle is a notification mechanism only. It does not own:

- a read reservation;
- a write reservation;
- an artifact source;
- the result of the active writer.

The cache must not block inside `resolve`. Waiting belongs to the runner or scheduler because that layer owns cancellation, progress state, fairness, and retry.

A cache wait blocks only the invocation and downstream path that require the missing artifact. Unrelated ready invocations in the same task, and eligible work from other tasks, may continue according to normal scheduling policy.

### Wait completion

The wait handle becomes ready after the active write reservation is released, regardless of whether the writer:

- committed successfully;
- aborted;
- failed;
- was cancelled;
- was removed by recovery.

Wait completion does not mean that a valid cache entry now exists.

After every wait, the caller must invoke `resolve` again. The next result may be:

- `CacheHit`, when the prior writer committed;
- `CacheWriteReservation`, when the prior writer abandoned the output and this task wins the retry;
- another `CacheWait`, when a different task acquired the reservation first.

### Cancellation-aware waits

Cache waits must use the task cancellation seam:

```python
await execution_context.wait_for(cache_wait.wait_handle.wait())
execution_context.raise_if_cancelled()
```

Cancelling this task-local wait must not cancel, abort, or otherwise affect the task that owns the active write reservation.

When the waiting task is cancelled:

- it stops awaiting the notification;
- its stage invocation becomes cancelled;
- it releases any provisional bindings it acquired in the same resolution pass;
- it does not acquire a replacement write reservation;
- the active producer continues under its own task policy.

### Waiter notification

When a writer commits, aborts, or is recovered, all waiters must become eligible to retry.

A process-local cache may use an event. A multi-process cache may use a notification channel, lock release, lease observation, or bounded polling. Notifications may be coalesced because retry always re-runs authoritative resolution.

The cache does not need to select one successor writer. If many waiters retry after an abort, atomic `resolve` ensures that one receives the write reservation and the others receive `CacheWait`.

## Stage Output-Set Binding

A stage invocation is admitted using its complete planned output subset. Cache resolution remains atomic per artifact fingerprint, while artifact orchestration applies stage-level policy across the resulting set.

The executable artifact runtime partitions the planned outputs into:

- cacheable outputs;
- non-cacheable outputs.

It then repeatedly resolves the cacheable outputs until the output set is in a coherent state.

### Bootstrap result per cacheable output

Artifact orchestration translates the three cache outcomes into stage-facing bootstrap data:

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

A writer is constructed only after the cache has returned a reserved target handle. A cache hit is exposed only through the reserved source handle returned by the cache.

### Any output is waiting

When any planned cacheable output returns `CacheWait`:

```text
1. abort every write reservation acquired during this output-set pass
2. close every provisional cache-hit source acquired during this pass
3. wait for all returned wait handles using task cancellation
4. retry resolution for the complete cacheable output set
```

The runner must not retain one output's write reservation while waiting for another output. Releasing provisional writers avoids lock-order deadlocks between stages that produce overlapping output sets.

The executable mock does not implement durable read reservations, so closing provisional hit sources is not observable there. A production implementation must still close them before discarding the pass.

### All cacheable outputs are hits

When every cacheable planned output is available:

- the producing stage is skipped when there are no non-cacheable outputs to produce;
- the reserved cache sources become the available semantic artifacts;
- requested caller materializations may proceed from those sources;
- normal cache-hit and stage-skipped progress is emitted.

Missing incidental outputs do not force a rerun. Only the planned requested output subset participates in skip decisions.

### All cacheable outputs are reserved for write

When every cacheable output has an exclusive write reservation:

- the stage receives one writer per output;
- each cacheable writer includes the reserved cache target;
- an in-memory publication target is also included when downstream stages in the same task need the value;
- the stage is admitted only after the complete set has been bound.

### Partial cache hit

A partial cache hit occurs when some cacheable outputs are committed hits and others are reserved for production, with no active waits.

The demonstrated policy is:

```text
1. abort newly acquired writers
2. close provisional cache-hit sources
3. resolve every cacheable output again with force_write=True
4. rerun the stage for its complete cacheable output set
```

This avoids merging related cacheable outputs from different executions of the same stage invocation. It also provides deterministic recovery from an earlier stage failure that committed only part of its output set.

Force-write remains exclusive per fingerprint. If another writer appears during retry, the normal cache-wait path applies.

Cache commit is still per artifact, not one transaction across the whole stage output set. Outputs committed before a later stage failure remain valid entries. A future partial-hit resolution refreshes the complete set according to this policy.

### Mixed cacheable and non-cacheable outputs

Non-cacheable outputs do not participate in cache lookup or write reservations. They still receive final semantic fingerprints and in-memory publication writers.

Stage admission follows these cases:

```text
all cacheable outputs hit + no non-cacheable outputs
    -> skip the stage

all cacheable outputs hit + non-cacheable outputs exist
    -> run the stage for the non-cacheable output bindings
       and merge them with the available cache hits

all cacheable outputs reserved + any non-cacheable outputs
    -> run the stage for all bound writers

no cacheable outputs
    -> run using non-cacheable publication writers only
```

The stage-facing `OutputArtifacts` collection contains only outputs that this execution must write. Cache-hit outputs are already available instances and are merged after execution.

### Binding loop example

The source implementation has the following shape:

```python
while True:
    bindings = await bind_cacheable_stage_outputs(
        cacheable_outputs,
        pedigree_fingerprint,
        force_cache_overwrite=force_cache_overwrite,
    )
    force_cache_overwrite = False

    if bindings.has_waits:
        await close_provisional_hits(bindings.available)
        await abort_writers(bindings.writers)
        await wait_cancellably(bindings.waits)
        continue

    if bindings.has_partial_cache_hit:
        await close_provisional_hits(bindings.available)
        await abort_writers(bindings.writers)
        force_cache_overwrite = True
        continue

    non_cacheable_writers = await bind_non_cacheable_outputs(...)

    if bindings.all_available and not non_cacheable_writers:
        return bindings.available, ()

    if bindings.all_reserved_for_write:
        return (), bindings.writers + non_cacheable_writers

    if bindings.all_available:
        return bindings.available, non_cacheable_writers
```

The exact adapter types may vary, but the cleanup and retry semantics are required.

## Cache-backed Artifact Handles

Cache reservations are encapsulated by the concrete source and target handles returned from `resolve`.

### Cache-hit artifact handle

A hit is bound as an available artifact instance:

```python
ArtifactInstance(
    ArtifactHandle(
        declaration=declaration,
        fingerprint=fingerprint,
        source=resolution.source_handle,
    )
)
```

The producing stage does not receive a writer for this output.

### Cache-miss artifact handle

A cacheable output that must be produced is bound to two semantic targets:

```python
publication = IntermediateArtifactHandle[T]()

ArtifactHandle(
    declaration=declaration,
    fingerprint=fingerprint,
    source=publication,
    targets=(
        resolution.target_handle,  # durable cache target
        publication,               # same-process source after commit
    ),
)
```

Target order is significant:

1. the durable cache target commits first;
2. the in-memory publication target commits last.

Downstream stages therefore cannot observe the produced value before durable cache publication succeeds.

### Prepare-all-before-commit

`ArtifactWriter` prepares every bound semantic target before committing any target:

```python
prepared = []
try:
    for target in handle.targets:
        await target.prepare(value, codec, fingerprint)
        prepared.append(target)

    for target in handle.targets:
        await target.commit()
except BaseException:
    for target in reversed(prepared):
        await target.abort()
    raise
```

This prevents one target from becoming visible when another target could not even stage the same semantic value.

Once commit begins, cross-target rollback is not always possible. The binding order is chosen so durable semantic publication succeeds before same-process availability.

## Semantic Commit and Caller Materialization

The semantic cache entry and caller-provided output target are separate lifecycles.

A producing stage writes only semantic targets:

- durable cache target, when cacheable;
- in-memory publication target, when required for this task.

Caller file, stream, object, or diagnostic sinks are materialized after the semantic artifact is available.

```python
reader = ArtifactReader(semantic_instance.handle)
value = await reader.read()

materialization_handle = ArtifactHandle(
    declaration=semantic_instance.handle.declaration,
    fingerprint=semantic_instance.fingerprint,
    source=semantic_instance.handle.source,
    targets=(caller_target,),
)
await ArtifactWriter(materialization_handle).write(value)
```

This separation has several consequences:

- a cache hit can be materialized to a new destination without rerunning the stage;
- a newly produced semantic artifact is cached before downstream use or caller materialization;
- failure to materialize a caller target does not invalidate a valid semantic cache entry;
- a subsequent task may reuse the semantic cache entry and retry only materialization;
- caller target paths do not influence semantic fingerprints.

## Failure and Cancellation

### Stage failure

When a stage fails before writing all bound outputs, the runner calls `abort_if_unwritten()` for every output writer.

This releases write reservations and removes prepared data for outputs that have not committed.

Artifacts already committed before the failure remain valid. Cache management does not roll them back merely because another output or branch failed later.

### Task cancellation

When task cancellation is accepted:

- no new cache-backed stage outputs should be admitted;
- cache waits stop through cancellation-aware waiting;
- unwritten output writers are aborted;
- write reservations are released through target abort;
- task-owned cache source handles are closed;
- committed semantic entries remain valid;
- outputs returned after cancellation are ignored unless they were already committed through their bound writer.

A task must not be finalized while an abandoned worker can still commit through a task-owned cache target. Provider adapters that support safe abandonment must revoke or isolate publication rights before task cleanup is considered complete.

### Cache prepare or commit failure

If cache preparation fails:

- no entry becomes visible;
- the cache target is aborted;
- the write reservation is released;
- waiters are notified and retry resolution.

If commit fails before the completion marker is published:

- the entry is not a hit;
- temporary or partial data is removed or quarantined;
- the write reservation is released;
- waiters retry and may acquire replacement production rights.

If the cache target commits successfully but in-memory publication later fails, the durable cache entry remains valid. The current task may recover the value by resolving the cache again or may fail according to runner policy.

### Cache read failure

A cache source may fail while reading or deserializing data even after metadata validation, for example because of external corruption.

The runner should:

```text
1. close the source handle and release its read reservation
2. report the cache-read or artifact-validation failure
3. mark or quarantine the entry as invalid when the backend supports it
4. re-resolve or fail according to task policy
```

An entry that cannot be read and validated must not continue to be returned as a hit.

## Atomicity and Concurrency Invariants

Cache behavior must preserve these invariants:

1. A cache hit is not exposed until a read reservation is active.
2. A cache miss is not assigned to a producer until its write reservation is active.
3. At most one live write reservation exists per fingerprint.
4. Read reservations may be shared by multiple tasks.
5. An entry with any active read reservation is not garbage-collected.
6. An entry with an active write reservation is not garbage-collected or replaced by another writer.
7. Preparing data is not visible as a committed hit.
8. Metadata validation occurs before hit exposure.
9. Write reservation release makes waiters eligible to retry, regardless of commit or abort.
10. Wait completion never substitutes for authoritative re-resolution.
11. A task does not hold newly acquired output write reservations while waiting for another output fingerprint.
12. Cache locks are not held while stage code, provider work, or caller materialization runs.
13. The cache never depends on traversal order to determine semantic identity.

## Garbage Collection

Garbage collection may remove only committed or invalid entries that have:

- no active read reservations;
- no active write reservation;
- no backend-specific pin or retention hold;
- no concurrent deletion or recovery operation.

### Collection policy

Eligible entries may be prioritized by policy such as:

- explicit expiration time;
- least recently used;
- total storage pressure;
- maximum entry count;
- serialized size;
- artifact role;
- artifact or stage behavior versions no longer produced by the current manifest;
- diagnostic retention shorter than semantic artifact retention.

A cacheable artifact does not become eligible merely because its producing task completed. Once committed, it is globally reusable until retention policy removes it.

### Collection atomicity

Eligibility check and deletion claim must be atomic with respect to `resolve` and reservation acquisition.

Conceptually:

```text
1. select candidate
2. atomically verify no read/write reservations
3. mark candidate deleting or acquire deletion lease
4. remove data and metadata
5. remove deletion record
```

A new resolver must not acquire a source from an entry already claimed for deletion. It may wait for deletion to finish or treat the entry as absent and compete for a write reservation afterward.

### Orphan cleanup

Garbage collection or recovery should also remove:

- abandoned temporary data files;
- abandoned temporary metadata files;
- unreferenced immutable generations;
- data without a valid completion marker;
- metadata without matching data;
- invalid entries whose quarantine retention has expired.

These objects are not normal committed cache entries and do not require semantic LRU treatment.

## Recovery

Recovery is required when reservation or entry state may outlive the task that created it.

A process-local cache can use the task manager's known active tasks. A durable or multi-process cache should use leases, heartbeats, owner-process identity, or another liveness mechanism.

Recovery must be conservative. Uncertain data does not become a cache hit.

### Stale read reservations

A read reservation is stale when its owner is known not to be running and no supported recovery path can resume that owner.

Recovery releases stale read reservations so otherwise eligible entries can be collected.

It must not release a reservation solely because it is old when the owning task may still be valid. Age may identify candidates for liveness verification, not prove abandonment by itself.

### Stale write reservations

For a stale write reservation, recovery inspects the entry state:

```text
valid committed data + matching completion metadata
    -> retain the committed entry
       release the stale reservation
       notify waiters

prepared or partial data without a valid completion marker
    -> discard or quarantine partial data
       release the stale reservation
       notify waiters

ambiguous or mismatched data
    -> quarantine or delete
       never expose as a hit
       release the stale reservation
       notify waiters
```

A backend may finalize a completed artifact only when it can prove that the same atomic commit protocol had completed. It must not infer completeness from the presence of a data file alone.

### Wait recovery

Wait handles are notification mechanisms, not durable ownership records. Recovery acts on the underlying reservation.

After a stale write reservation is released, waiting tasks are notified or become eligible to detect the change. Each waiting task re-runs `resolve` and does not assume whether the recovered writer committed or aborted.

### Startup scan

A durable filesystem backend should inspect at least:

- reservation records or leases;
- temporary data and metadata;
- final metadata without data;
- final data without valid metadata;
- mismatched metadata;
- obsolete immutable generations.

The scan may be incremental, but invalid objects must remain unavailable until inspected.

## Events and Diagnostics

Cache coordination should emit internal events or diagnostic records sufficient to explain why a task reused, waited, produced, refreshed, or failed.

Recommended event types:

```text
artifact_cache_lookup_started
artifact_cache_hit
artifact_cache_miss
artifact_cache_entry_invalid
artifact_cache_read_reserved
artifact_cache_read_released
artifact_cache_write_reserved
artifact_cache_wait_started
artifact_cache_wait_finished
artifact_cache_prepare_started
artifact_cache_prepared
artifact_cache_committed
artifact_cache_write_aborted
artifact_cache_write_released
artifact_cache_entry_deleted
artifact_cache_recovery_action
```

Recommended fields:

```text
task_id
invocation_id?
stage_id?
artifact_id
fingerprint
cache_backend
reservation_type?
reservation_owner_task_id?
state
reason
timestamp
```

The public progress model may collapse these into higher-level stage states. In particular, an invocation waiting on another task's cache writer is represented as blocked with reason `waiting_for_other_task` and may include the blocking task ID.

Cache event handlers must not execute while holding cache resolution locks. Slow or failing diagnostics must not block cache state transitions.

## Fingerprints

Fingerprints identify concrete semantic artifact instances for cache lookup, reuse, provenance, and invalidation.

The implementation distinguishes:

- supplied artifact fingerprints, derived from declared identity plus supplied semantic content;
- stage-produced artifact fingerprints, derived from declared identity and codec plus production pedigree.

The produced artifact value is not serialized and hashed again merely to create its cache key.

### Stable structured hashing

Fingerprint payloads must use a deterministic encoding.

The executable example uses canonical JSON and BLAKE3:

```python
def stable_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return blake3(raw).hexdigest()
```

Changing the hash algorithm or canonical payload format must be versioned or otherwise separated so values calculated under incompatible rules are not confused.

Structured payloads should be preferred over ambiguous string concatenation. Every list or map included in a fingerprint must have explicit stable ordering.

### Supplied artifact fingerprints

A supplied source, settings artifact, or explicitly supplied intermediate artifact starts as a graph root.

Its final fingerprint is:

```text
supplied_artifact_fingerprint = hash(
    artifact identity,
    supplied semantic content fingerprint,
)
```

The source implementation builds it as:

```python
async def build_supplied_artifact_fingerprint(
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

`get_content_fingerprint(codec)` returns the content contribution, not the final artifact fingerprint.

The source handle defines semantic content equality. Examples:

- raw file bytes, when byte identity is the contract;
- canonical decoded PCM plus relevant audio metadata;
- canonical JSON for settings;
- normalized table rows for a supplied intermediate artifact.

If the content canonicalization algorithm changes, its version must affect the content contribution or the final payload.

### Resolved settings fingerprints

Settings use the supplied-artifact builder after defaults, user values, and path-pushed inherited overrides have been resolved.

```text
resolved settings = defaults + user settings + pushed inherited settings
```

The canonical resolved value is fingerprinted. Therefore, changing a default that changes the effective value also changes the settings fingerprint even when the caller did not explicitly supply that field.

Non-semantic secrets or credentials must be excluded or replaced with a stable semantic provider identity when they do not affect produced content. For example, an authentication token should not invalidate a diarization result merely because the token text changed.

### Building production pedigree

After graph shape, concrete requirements, settings propagation, phase selection, and terminal remapping are resolved, graph creation walks forward from available artifact edges.

For each stage invocation, it calculates a production pedigree from:

- the stage's stable ID;
- the stage behavior version;
- the fingerprints of required non-settings input edges;
- the fingerprints of resolved settings artifacts that affect production;
- provider, model, or implementation versions that affect output but are not already represented by stage behavior version or settings;
- any other declared behavior dependency required for deterministic invalidation.

A concrete structured form is:

```python
stage_pedigree_fingerprint = stable_hash(
    {
        "producer": {
            "stage_id": invocation.stage_id,
            "behavior_version": invocation.behavior_version,
        },
        "inputs": [
            {
                "binding": binding.stable_input_role,
                "artifact_id": str(binding.artifact_id),
                "fingerprint": binding.fingerprint,
            }
            for binding in sorted_required_data_inputs
        ],
        "settings": [
            {
                "artifact_id": str(setting.artifact_id),
                "fingerprint": setting.fingerprint,
            }
            for setting in sorted_effective_settings
        ],
        "providers": sorted_provider_versions,
    }
)
```

Settings may instead be represented in one unified required-input collection. They must not be omitted or accidentally counted twice. Declaration ownership does not affect fingerprint participation: borrowed or shared settings consumed by the stage participate like any other effective setting.

The artifact example contains a deliberately incomplete mock:

```python
def mock_pedigree_fingerprint(
    stage_id: str,
    input_fingerprints: dict[str, str],
) -> str:
    return stable_hash(
        {
            "mock_stage": stage_id,
            "inputs": dict(sorted(input_fingerprints.items())),
        }
    )
```

The mock demonstrates stable input ordering. The production pedigree additionally includes behavior version, effective settings, provider versions where needed, and the resolved graph identity decisions listed above.

### Order-stable input aggregation

Input aggregation must not depend on traversal order, insertion order, concurrent completion order, or object memory address.

Inputs should be ordered by a stable binding identity such as:

```text
input role + resolved artifact ID + concrete binding discriminator
```

When a stage can consume more than one concrete instance of the same artifact family, the binding discriminator or canonical constraint identity must distinguish them.

Equivalent graphs with equivalent resolved bindings must calculate the same pedigree even when discovered or executed in a different order.

### Stage-produced artifact fingerprints

Every stage-produced output receives its own final fingerprint:

```text
stage_output_fingerprint = hash(
    resolved artifact identity and schema,
    codec identity,
    stage production pedigree,
)
```

The source implementation builds it as:

```python
async def build_intermediate_artifact_fingerprint(
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

Despite the example method name, this rule applies to any stage-produced semantic artifact, including intermediate and target roles. Cacheability controls whether the result is stored and coordinated through the cache; it does not change how the semantic fingerprint is derived.

### Output-specific identity

One stage invocation may produce several outputs. The outputs share the same stage pedigree but receive different final fingerprints because each declaration contributes its own:

- resolved artifact ID;
- role;
- declaration version;
- codec identity.

```text
output_A_fp = hash(output_A_identity, stage_pedigree_fp)
output_B_fp = hash(output_B_identity, stage_pedigree_fp)
```

A change to one output's schema or codec therefore invalidates that output without requiring an unrelated output declaration to use the same final fingerprint.

### Phase selection and terminal remapping

The resolved artifact ID included in the final output fingerprint is the identity under which the artifact will be stored.

For a phased intermediate family:

- a non-terminal concrete output uses its concrete phase ID;
- a terminal output satisfying a phase-less completed-family requirement uses the phase-less remapped ID;
- the run manifest records the concrete declared output phase for provenance;
- no second cache alias is created under the concrete phase when the stored result is terminal-remapped.

The producing stage behavior, inputs, and settings remain in pedigree. Terminal remapping changes artifact identity and therefore changes the final fingerprint.

### Concrete fingerprint example

Consider:

```text
  source/audio ---------------- source_audio_fp
         |
         v
  +----------------+
  | audio.prepare  | <----------- audio/settings --- audio_settings_fp
  +----------------+
         |
  audio/prepared --------------- audio_prepared_fp
         |
         v
  +----------------+
  | asr.transcribe | <----------- asr/settings ----- asr_settings_fp
  +----------------+
         |
  transcript/token_text -------- transcript_token_text_fp
```

Supplied roots:

```text
source_audio_fp = hash(
    artifact_identity(
        id=source/audio,
        role=source,
        version=source_audio_schema_version,
    ),
    content_fingerprint(canonical_audio_content),
)

audio_settings_fp = hash(
    artifact_identity(
        id=audio/settings,
        role=settings,
        version=audio_settings_schema_version,
    ),
    content_fingerprint(resolved_audio_settings),
)

asr_settings_fp = hash(
    artifact_identity(
        id=asr/settings,
        role=settings,
        version=asr_settings_schema_version,
    ),
    content_fingerprint(resolved_asr_settings),
)
```

Audio preparation pedigree and output:

```text
audio_prepare_pedigree_fp = hash(
    producer(stage_id=audio.prepare, behavior_version=audio_prepare_version),
    ordered_inputs(source/audio=source_audio_fp),
    ordered_settings(audio/settings=audio_settings_fp),
    behavior_affecting_provider_versions,
)

audio_prepared_fp = hash(
    artifact_identity_and_codec(audio/prepared),
    audio_prepare_pedigree_fp,
)
```

Transcription pedigree and output:

```text
transcribe_pedigree_fp = hash(
    producer(stage_id=asr.transcribe, behavior_version=transcribe_version),
    ordered_inputs(audio/prepared=audio_prepared_fp),
    ordered_settings(asr/settings=asr_settings_fp),
    behavior_affecting_model_and_provider_versions,
)

transcript_token_text_fp = hash(
    artifact_identity_and_codec(transcript/token_text),
    transcribe_pedigree_fp,
)
```

This means the transcript fingerprint changes when:

- source audio semantic content changes;
- audio preparation settings change;
- audio preparation behavior or provider versions change;
- ASR settings change;
- ASR behavior, model, or provider versions change;
- the resolved transcript artifact identity changes;
- the transcript schema or codec identity changes.

It does not change because:

- a task ID changes;
- an invocation ID changes;
- a cache path changes;
- a caller output path changes;
- equivalent inputs are discovered or executed in a different order.

## Recommended Invariants

1. The final artifact fingerprint is the primary cache lookup key.
2. A matching key is a hit only after exact metadata validation.
3. `resolve` atomically returns a reserved hit, an exclusive writer, or a wait.
4. Reservation ownership is encapsulated by cache-backed source and target handles.
5. Readers and writers never perform cache lookup or lock acquisition themselves.
6. A cache source releases its read reservation through idempotent `close()`.
7. A cache target releases its write reservation through idempotent `commit()` or `abort()`.
8. Prepared or partial data is never returned as a hit.
9. The durable cache target commits before same-process publication.
10. Caller materialization is separate from semantic cache commit.
11. A materialization failure does not invalidate a committed semantic entry.
12. Cache waits are cancellation-aware and never cancel the owning writer.
13. Wait completion always causes re-resolution rather than assuming a hit.
14. A stage holds no provisional write reservations while waiting for another output.
15. Partial cache hits refresh the complete cacheable output set under the demonstrated default policy.
16. Garbage collection cannot delete an entry with active read or write reservations.
17. Recovery never promotes uncertain partial data to a cache hit.
18. Supplied artifacts are fingerprinted from identity plus semantic content.
19. Stage-produced artifacts are fingerprinted from resolved identity and codec plus production pedigree.
20. Fingerprint aggregation is deterministic and order-stable.
21. Caller-supplied live roots are published only through an explicit cache-I/O
    or durable-preparation operation.
22. Direct cache IO uses artifact declarations and handles, not backend paths or raw cache keys.
23. Direct cache writes obey the same correctness and atomicity rules as run-time cache publication.
24. A cached-instance reference is an `a-instance` locator; rebinding validates both artifact identity and fingerprint.
25. Cached settings roots may be opened before planning when explicitly supplied by `a-instance` locator.
26. Externally retrievable `a-instance` locators are exposed only after durable commit.
27. Upload deduplication and future artifact-existence preflight use the same `a-instance` identity.
28. Task-instance relations are written only for committed readable entries and
    never pin those entries.
