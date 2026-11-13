# Artifact Storage Boundaries

## Purpose

This document explains the boundary between representation storage, execution
cache, and durable artifact persistence.

These are separate logical contracts even when one feature, process, database,
or filesystem implements all three. Their detailed behavior is authoritative
in:

- [Representation Store](03-representation-store.md);
- [Cache Management](02-cache-management.md); and
- [Durable Artifact Persistence](04-artifact-persistence.md).

## Design Summary

Chunky separates physical bytes from the semantic reasons those bytes are
retained:

```text
Execution Cache ---------+
                         |
                         +----> Representation Store
                         |
Durable Persistence -----+
```

- The **representation store** owns immutable canonical byte objects.
- The **execution cache** owns reusable computation records and cache
  coordination.
- **Durable artifact persistence** owns caller-visible durable instances,
  task-output bindings, and retention claims.

Cache and durable records may refer to the same representation object. Their
identities and lifecycles remain independent.

## Responsibility Boundaries

### Representation Store

The representation store is the physical content layer. It answers:

> Where are these exact canonical bytes, and can they be read safely?

It owns:

- transactional preparation, commit, abort, and physical deletion;
- exact byte length and representation digest validation;
- immutable byte-object lookup;
- physical read and write reservations;
- reference accounting for semantic records;
- range reads and storage-native transfer capabilities;
- corruption state and physical recovery; and
- garbage collection of unreferenced byte objects.

It does not understand:

- artifact declarations;
- semantic artifact fingerprints;
- cache hit, miss, refresh, or bypass policy;
- `a-instance` or `a-task` locators;
- durable artifact claims; or
- why a semantic record wants the bytes retained.

### Execution Cache

The cache is the reusable-computation coordination layer. It answers:

> Has this semantic computation already produced a valid reusable artifact?

It owns:

- lookup by expected semantic artifact fingerprint;
- validation of cached artifact and representation metadata;
- cache hit, write-reservation, and wait outcomes;
- exclusive producer coordination for one expected fingerprint;
- cache read reservations;
- cache TTL, eviction eligibility, and recovery of cache records; and
- diagnostic metadata explaining cache decisions.

A committed cache entry contains semantic metadata and a reference to one
committed representation object. The cache does not own the object's physical
bytes or delete them directly.

Cache content is recomputable. A cache entry may be evicted whenever cache
policy permits, even if another semantic record continues to retain the same
representation object.

### Durable Artifact Persistence

Durable artifact persistence is the caller-visible retention layer. It answers:

> Is this concrete artifact instance durably available, and which task output
> does it satisfy?

It owns:

- immutable durable `a-instance` records;
- successful `a-task -> a-instance` bindings;
- upload-grace, active-task, terminal-task, and administrative claims;
- durable availability and retention;
- publication and claim transactions;
- durable artifact read reservations; and
- durable-record recovery and garbage collection.

A durable artifact record contains semantic metadata and a reference to one
committed representation object. Durable availability never depends on whether
an execution-cache entry exists.

## Identity Boundaries

The three layers use related but different identities.

### Representation identity

A representation object is identified by its exact canonical byte sequence:

```text
representation object key =
  digest namespace + byte digest + exact byte length
```

It deliberately excludes artifact declaration, codec, provenance, and semantic
fingerprint. Different semantic records may share one representation object
when their canonical bytes are identical.

### Cache identity

A cache entry is keyed by the final semantic artifact fingerprint. For a
stage-produced artifact, that identity may include:

- resolved artifact identity and codec;
- producing stage behavior;
- ordered input and settings fingerprints; and
- other declared behavior-affecting versions.

The cache key describes why the artifact has its value, not merely which bytes
encode that value.

### Durable artifact identity

An `a-instance` identifies one concrete semantic artifact instance. Its durable
record associates:

- the resolved artifact identity;
- semantic fingerprint;
- representation descriptor; and
- referenced representation object.

The locator remains independent of physical path, bucket, host, or provider.

### Consequences

Two semantic artifact fingerprints may legitimately reference one
representation object when separate computations produce identical canonical
bytes.

One semantic artifact fingerprint must never resolve to conflicting valid
bytes. That is an integrity, nondeterminism, canonicalization, or fingerprinting
failure rather than a replacement opportunity.

## Lifetime Boundaries

Each layer protects a different resource:

- a **cache reservation** protects a cache entry and coordinates semantic
  producers;
- a **durable claim** protects a durable semantic artifact record;
- a **semantic reference** protects the representation object used by a cache
  or durable record; and
- an **operation reservation** protects an active physical read, preparation,
  or commit.

Cache TTL and durable claim expiry do not directly delete physical content.
They make their respective semantic records eligible for removal.

Removing a semantic record atomically detaches only that record's representation
reference. The representation store may delete the physical object only after
all semantic references, operation reservations, and recoverable work are gone.

For example:

```text
cache entry ----------+
                      +----> representation object
durable record -------+
```

Evicting the cache entry removes the cache reference. The object remains
because the durable record still references it. When the durable claim later
expires, durable persistence may remove its record and detach the final
reference. Only then may representation-store garbage collection delete the
physical object.

## Read and Write Coordination

### Cache write

A cache miss follows this logical sequence:

```text
1. Cache acquires the exclusive producer reservation for a fingerprint.
2. Output bytes are prepared and committed through the representation store.
3. Cache atomically publishes its semantic entry and object reference.
4. Cache releases the producer reservation.
```

The cache reservation prevents duplicate semantic production. The
representation-store reservation protects physical staging and commit.

### Cache read

A cache hit follows this logical sequence:

```text
1. Cache validates the semantic entry.
2. Cache acquires its entry read reservation.
3. Representation store opens the referenced object with an object reservation.
4. The returned source handle owns both protections until close.
```

Neither semantic eviction nor physical deletion may race the active reader.

### Durable publication

Durable publication follows this logical sequence:

```text
1. Canonical bytes are prepared or an existing trusted object is selected.
2. The representation object is committed or reused.
3. The durable record, object reference, and initial claim become visible
   atomically.
```

When cache and durable persistence share a representation store, trusted cache
promotion can attach another reference to the existing object without
re-encoding or copying its bytes.

## Illustrative Object Contracts

The following Python-like stubs illustrate the minimum shape of each provider
boundary. They are examples, not authoritative binding syntax. Implementations
may combine operations into backend transactions or expose more specific
result types while preserving the ownership and atomicity described here.

Names such as `ArtifactDeclaration`, `ArtifactTransferSource`,
`ArtifactSourceHandle`, and locator types refer to the contracts defined by the
artifact and binding specifications.

### Shared representation objects and handles

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class RepresentationObject:
    object_key: str
    byte_length: int
    byte_digest: str


@dataclass(frozen=True)
class RepresentationReference:
    reference_id: str
    object_key: str
    owner_kind: str
    owner_id: str


class ReservedRepresentationSource(Protocol):
    object: RepresentationObject

    def open_stream(
        self,
        byte_range: tuple[int, int] | None = None,
    ) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...
```

`ReservedRepresentationSource.close()` releases the physical read reservation.
It is idempotent. Possession of the source does not authorize deletion or
destructive movement of its backing content.

### `RepresentationStore`

```python
class PreparedRepresentation(Protocol):
    transaction_id: str

    async def commit(self) -> RepresentationObject: ...
    async def abort(self) -> None: ...


class RepresentationStore(Protocol):
    async def prepare_from(
        self,
        source: ArtifactTransferSource,
    ) -> PreparedRepresentation: ...

    async def open(
        self,
        object_key: str,
    ) -> ReservedRepresentationSource: ...

    async def attach_reference(
        self,
        object_key: str,
        *,
        owner_kind: str,
        owner_id: str,
    ) -> RepresentationReference: ...

    async def detach_reference(
        self,
        reference: RepresentationReference,
    ) -> None: ...

    def capabilities(self) -> RepresentationStoreCapabilities: ...
```

The representation-store implementation:

- consumes the transfer source when `prepare_from(...)` succeeds;
- owns staging until `commit()` or `abort()`;
- derives and validates the representation object key;
- returns only immutable committed objects from `open(...)`;
- prevents object deletion while references or reservations exist; and
- does not accept artifact fingerprints, cache policy, `a-instance`, or
  `a-task` as object identity.

`attach_reference(...)` and semantic-record publication must form one logical
transaction. A provider backed by one database may implement that transaction
directly. Independently backed providers require an explicit coordinator and
recovery protocol; callers must not emulate reference attachment by sharing a
path or object-store key.

### `CacheProvider`

```python
@dataclass(frozen=True)
class CacheHit:
    source_handle: ArtifactSourceHandle


@dataclass(frozen=True)
class CacheWriteReservation:
    target_handle: ArtifactTransferTarget


class CacheWaitHandle(Protocol):
    async def wait(self) -> None: ...


@dataclass(frozen=True)
class CacheWait:
    fingerprint: str
    owner_task_id: str | None
    wait_handle: CacheWaitHandle


CacheResolution = CacheHit | CacheWriteReservation | CacheWait


class CacheProvider(Protocol):
    async def resolve(
        self,
        declaration: ArtifactDeclaration,
        fingerprint: str,
        *,
        force_write: bool = False,
    ) -> CacheResolution: ...

    async def evict(
        self,
        declaration: ArtifactDeclaration,
        fingerprint: str,
    ) -> bool: ...

    async def recover(self) -> None: ...
```

The cache implementation:

- receives an already resolved declaration and expected semantic fingerprint;
- atomically returns a reserved hit, an exclusive writer, or a wait result;
- validates cache metadata before returning a hit;
- uses the configured representation store behind its source and target
  handles;
- publishes a cache record and representation reference atomically when the
  target commits; and
- detaches only its own representation reference when an entry is evicted.

The returned handles contain reservation ownership:

- closing a hit source releases both cache-entry and physical-object read
  protection;
- committing a write target publishes the prepared representation and cache
  entry;
- aborting a write target removes staging and releases the cache writer
  reservation; and
- completing a wait never implies a hit—the caller resolves again.

The cache provider does not expose representation paths and does not publish
durable records or claims.

### `ArtifactPersistenceProvider`

```python
@dataclass(frozen=True)
class ClaimRequest:
    kind: str
    desired_ttl: timedelta | None
    owner_id: str


@dataclass(frozen=True)
class DurableArtifactRecord:
    artifact_instance: ArtifactInstanceLocator
    resolved_artifact: ArtifactKey
    semantic_fingerprint: str
    representation: RepresentationDescriptor
    representation_object: RepresentationObject


@dataclass(frozen=True)
class DurablePublication:
    record: DurableArtifactRecord
    claim_id: str
    retained_until: datetime | None


@dataclass(frozen=True)
class PendingArtifact:
    task_artifact: TaskArtifactLocator


@dataclass(frozen=True)
class AvailableArtifact:
    record: DurableArtifactRecord
    retained_until: datetime | None


@dataclass(frozen=True)
class UnavailableArtifact:
    reason: str
    resolved_instance: ArtifactInstanceLocator | None = None


ArtifactAvailability = (
    PendingArtifact
    | AvailableArtifact
    | UnavailableArtifact
)


class ArtifactPersistenceProvider(Protocol):
    async def publish(
        self,
        declaration: ArtifactDeclaration,
        source: ArtifactTransferSource,
        *,
        initial_claim: ClaimRequest,
        idempotency_key: str | None = None,
    ) -> DurablePublication: ...

    async def promote(
        self,
        source: ArtifactSourceHandle,
        *,
        initial_claim: ClaimRequest,
    ) -> DurablePublication: ...

    async def inspect(
        self,
        locator: ArtifactInstanceLocator | TaskArtifactLocator,
    ) -> ArtifactAvailability: ...

    async def open(
        self,
        locator: ArtifactInstanceLocator | TaskArtifactLocator,
        *,
        byte_range: tuple[int, int] | None = None,
        wait: bool = False,
    ) -> ArtifactSourceHandle: ...

    async def bind_task_output(
        self,
        task_artifact: TaskArtifactLocator,
        artifact_instance: ArtifactInstanceLocator,
        *,
        claim: ClaimRequest,
    ) -> None: ...

    async def renew_claim(
        self,
        claim_id: str,
        desired_ttl: timedelta,
    ) -> datetime: ...

    async def release_claim(self, claim_id: str) -> None: ...

    def capabilities(self) -> ArtifactPersistenceCapabilities: ...
```

The durable-persistence implementation:

- validates a fully resolved declaration and canonical representation;
- derives or validates the semantic fingerprint and concrete `a-instance`;
- commits or reuses the representation object;
- publishes the durable record, object reference, and initial claim atomically;
- resolves `a-task` separately from concrete `a-instance` availability;
- returns source handles that already own durable-record and physical-object
  read protection; and
- removes a record only after its durable claims and active reservations permit
  removal.

`publish(...)` consumes and closes its transfer source for every terminal
outcome. `promote(...)` may attach a reference to an already validated object
when both providers share a representation store; otherwise it copies through
a compatible native operation or bounded-memory stream.

The persistence provider does not perform cache hit/miss resolution and does
not treat cache TTL as a durable claim.

### Composition dependency

The cache and durable providers depend on the representation store; the
representation store does not depend on either semantic provider:

```python
class FileCacheProvider:
    def __init__(
        self,
        metadata_root: Path,
        representations: RepresentationStore,
    ) -> None: ...


class FileArtifactPersistenceProvider:
    def __init__(
        self,
        metadata_root: Path,
        representations: RepresentationStore,
    ) -> None: ...
```

This dependency direction prevents physical storage from acquiring cache or
durable policy. A combined implementation may use private shared transaction
machinery, but its public contracts must preserve the same direction and
observable behavior.

## Rationale

### Cache eviction must not define durability

Cache content is reusable and recomputable. Durable inputs and requested
outputs are caller-visible retained artifacts. Combining their lifecycle would
either make cache eviction unsafe or make ordinary cache entries unnecessarily
durable.

### Physical storage should not own semantic policy

Filesystems, object stores, SMB shares, and memory stores are concerned with
bytes, atomicity, reservations, integrity, and recovery. They should not need
to understand graph provenance, cache modes, task-output bindings, or artifact
retention policy.

### Cache and durable records should reuse bytes

Keeping semantic records separate from representation objects permits
deduplication without coupling lifecycle policy. Cache eviction can detach one
reference while durable persistence retains another.

### Coordination and storage scale differently

Cache hit/miss coordination may require database transactions or distributed
leases. Physical bytes may belong in a filesystem or object store. Separate
contracts allow each responsibility to use an appropriate backend.

### Recovery has clearer ownership

The cache recovers semantic entries and producer reservations. Durable
persistence recovers claims and task bindings. The representation store
recovers physical staging, references, corruption, and deletion. No provider
must infer another module's policy from paths or directory contents.

## Provider Composition

The separation is logical, not necessarily operational.

A local feature may compose:

```python
representations = FileRepresentationStore(root / "objects")
cache = FileCacheProvider(root / "cache", representations)
artifacts = FileArtifactPersistenceProvider(root / "artifacts", representations)
```

A distributed deployment may instead use a database-backed cache coordinator,
a durable-record service, and an object-store-backed representation provider.

Providers share physical objects only through explicit composition with the
same representation-store namespace and coordinator. Equal filesystem paths,
buckets, or endpoints do not imply safe sharing.

Runtime construction must reject ambiguous reference ownership, incompatible
metadata or digest schemas, and multiple independent coordinators claiming the
same representation namespace.

## Cross-Layer Invariants

1. Every committed cache or durable record references one committed immutable
   representation object.
2. No representation object with a semantic reference or active reservation is
   physically deleted.
3. Cache eviction never determines durable artifact availability.
4. Durable claim expiry never directly invalidates an independent cache entry.
5. A cache commit alone does not make a durable output available.
6. A physical object path or provider key is never a public artifact identity.
7. Native transfer and deduplication optimizations do not weaken validation,
   ownership, atomicity, or recovery guarantees.
8. Stages consume artifact handles without needing to know whether their bytes
   came from cache, durable persistence, a local source, or a task workspace.
