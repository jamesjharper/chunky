# Representation Store

## Purpose

The representation store persists immutable logical byte sequences for cache
entries and durable artifact instances. It is an internal provider boundary, not
an application-facing artifact API.

The store separates physical content ownership from semantic artifact identity:

- a representation object identifies exact canonical bytes;
- cache entries and durable artifact records attach semantic metadata and
  lifecycle references to that object; and
- cache and durable providers may share one representation store or use
  separate stores.

The artifact transfer capabilities used by this contract are defined in
[Artifact IO](../02-artifacts/02-artifact-io-surface.md).

## Boundaries

The representation store owns:

- transactional object preparation, commit, and abort;
- byte-addressed object lookup;
- exact length and digest validation;
- semantic-record references and temporary operation reservations;
- opaque native-transfer capability negotiation;
- range-capable byte sources when supported;
- object corruption state;
- orphan and stale-operation recovery; and
- physical object garbage collection.

It does not own:

- artifact declarations or semantic fingerprints;
- `a-instance` or `a-task` records;
- cache resolution, cache policy, or task-output policy;
- upload or download application surfaces; or
- HTTP, SMB, S3, or filesystem-specific caller APIs.

## Representation Object

A representation object is one finite ordered logical byte sequence:

```text
RepresentationObject
  object_key
  byte_length
  byte_digest
  state
  provider_metadata
```

Its key is derived from:

```text
digest_namespace = "blake3-256:v1"
object_key = (digest_namespace, byte_digest, byte_length)
```

The initial digest is BLAKE3-256. A future digest change uses a new namespace
and never reinterprets an existing key.

Artifact identity, codec identity, MIME type, extension, and semantic
fingerprint are not part of the representation-object key. Two semantic records
may reference the same object whenever their canonical representation bytes are
identical.

Chunk boundaries are not semantic. A provider may split, coalesce, align,
buffer, or bypass chunks according to its memory budget and backend.

## Logical and Physical Bytes

The object key describes the logical canonical bytes exposed through transfer
sources. Providers may transparently encrypt, compress, shard, sparse-allocate,
or deduplicate physical storage if they reproduce the exact logical byte
sequence.

An at-rest transformation:

- does not change logical length or digest;
- must preserve advertised range behavior;
- remains provider-private;
- may affect which native-transfer offers are compatible; and
- must not expose backend credentials or storage addresses through artifact
  handles.

If callers observe different bytes, the content is a different representation,
not an at-rest transformation.

## Object States

The logical states are:

```text
PREPARING
PREPARED
COMMITTED
CORRUPT
DELETING
```

Only `COMMITTED` objects are openable. `CORRUPT` objects retain their key,
references, and diagnostics but cannot be returned as readable content.
Physical staging without a recoverable preparation record is an orphan.

## Transactional Write

A write target follows:

```text
OPEN -> PREPARED -> COMMITTED
                  \-> ABORTED
```

`prepare_from(source)`:

- may be called successfully only once;
- validates size and capacity limits while ingesting;
- establishes logical byte length and digest;
- records enough state for crash recovery;
- never exposes partial content as committed; and
- returns only after ownership of any adopted backing resource is unambiguous.

`commit()`:

- atomically creates or reuses the immutable object;
- is create-if-absent;
- is idempotent after success or ambiguous success;
- reuses an existing object only when key, length, and validated bytes are
  compatible; and
- reports an integrity conflict rather than choosing between conflicting
  content.

`abort()`:

- is idempotent;
- disposes of target-owned staging;
- never removes a committed object;
- reports the committed outcome when commit already won; and
- releases provisional capacity and write reservations.

Providers expose a stable transaction ID so recovery can reconcile ambiguous
commit completion.

## Source Ownership and Native Transfer

Possession of a transfer source does not authorize destructive movement of its
backing content.

A source is either:

- preserve-only; or
- explicitly consumable.

Targets select the concrete operation. The public provider contract does not
force `MOVE`, `COPY`, `HARDLINK`, or a backend-specific command.

A target may attempt, in order:

- atomic adoption of an explicitly consumable resource;
- compatible reflink, block clone, provider-side copy, or metadata attachment;
- another recognized opaque native-transfer offer; or
- bounded-memory byte streaming.

Unknown offers are ignored. Local paths, bucket keys, SMB handles, signed URLs,
and credentials are not common transfer metadata.

Native operations are valid only when they preserve:

- exact logical bytes;
- representation identity;
- mutation isolation;
- source ownership semantics;
- transactional visibility; and
- failure and cancellation behavior.

In particular, a store-owned immutable inode must not be hardlinked into an
ordinary caller-writable path. Rename is valid only for an exclusive consumable
resource. When correctness cannot be established, the provider copies or
streams.

## Ownership Handoff

For consumable backing content, ownership transfers when
`prepare_from(source)` succeeds:

- before success, the source owns the backing resource;
- after success, the target owns staging;
- `commit` publishes it; and
- `abort` disposes of it.

A provider that cannot complete adoption or preserve unambiguous ownership must
fall back to a preserving operation. A one-shot byte stream may be partially
consumed on failure; this rule governs adoptable backing resources.

## Integrity

Every committed object has:

- exact logical byte length; and
- a BLAKE3-256 v1 digest.

Stream ingestion computes both incrementally. Source-provided values are
assertions and must be verified unless the source is another trusted committed
object and the selected operation guarantees exact-byte preservation.

Provider-native checksums may be retained as diagnostics. They replace the
canonical digest only when their explicitly identified algorithm and semantics
are the canonical digest contract.

Opening an object validates committed metadata, backing-object identity, and
cheap provider integrity signals. The common contract does not require a full
pre-read rehash. Providers may verify incrementally during reads, use trustworthy
native checksums, or run background scrubbing.

Detected corruption:

- fails the current read;
- marks the shared object `CORRUPT`;
- prevents opens through every cache or durable record referencing it; and
- may be repaired only by restoring the exact bytes and fully validating them.

Semantic records and references are not silently rebound to different bytes.

## References

A semantic cache or durable record holds an authoritative reference to its
representation object.

Attaching and detaching references must ensure:

- publication of a semantic record cannot succeed without its object reference;
- deleting or replacing a record releases only that record's reference;
- attaching a reference cannot race object deletion; and
- physical objects are not removed while any semantic reference exists.

The representation store, not cache or durable providers manipulating paths,
owns reference accounting. Filesystem hardlink counts and directory scans may
assist an implementation but are not the lifecycle contract.

## Operation Reservations

In-progress writes and active reads hold renewable operation reservations.
Reservations are distinct from durable artifact retention claims and cache
policy.

A read reservation:

- is acquired atomically with validation of a committed readable object;
- prevents physical deletion;
- remains active for the owning source-handle lifetime; and
- is released on close or stale-reader recovery.

A write reservation protects preparation and commit from concurrent deletion or
conflicting publication. Active long-running operations heartbeat their leases.
Lease duration and heartbeat frequency are provider configuration.

## Range Reads

Sequential full-object streaming is required. A provider may additionally
support one contiguous half-open range:

```text
[start, end)
```

The rules are:

- offsets are zero-based bytes;
- `0 <= start <= end <= byte_length`;
- omitted `end` means `byte_length`;
- invalid ranges fail before streaming; and
- the stream yields exactly `end - start` bytes.

The full-object digest remains metadata and does not claim to validate an
isolated range. Multipart range framing belongs to a transport layer using
independent reserved opens.

## Capacity and Limits

Providers enforce configured object-size and capacity limits:

- a known oversized length is rejected before reading;
- actual bytes are counted while streaming;
- unknown or dishonest-length streams abort at the limit;
- declared and actual lengths must match;
- provisional capacity is released after abort or recovery; and
- capacity failure is reported through stable provider-neutral categories.

Decoded-resource limits belong to the codec/validation layer.

## Cancellation

Streaming checks cancellation between bounded chunks and stops pulling new
bytes promptly.

An issued native operation may be physically uninterruptible. Cancellation
still:

- prevents publication unless commit already completed;
- transitions incomplete work toward abort/recovery;
- releases logical reservations when safe; and
- never reports a committed object as rolled back.

Abandoned physical staging may be removed asynchronously.

## Recovery

Recovery reconciles:

- expired read and write leases;
- ambiguous commit results;
- prepared targets without live owners;
- physical staging without records;
- records whose physical object is missing;
- unreferenced committed objects; and
- interrupted deletion.

Recovery is concurrency-safe across workers. An expired write is classified as
committed, abortable staging, or orphaned work before cleanup. Successful
reconciliation is idempotent.

## Garbage Collection

An object is eligible for logical deletion only when it has:

- no semantic references;
- no active read reservation;
- no active write/preparation reservation; and
- no recovery state that may still commit or attach a reference.

Logical deletion prevents new opens before physical deletion begins. Physical
cleanup may be delayed, but deleted content must not become readable again
without a new validated commit.

## Provider Composition

Cache and durable providers share objects only by explicit composition with the
same representation-store namespace and coordinator.

Runtime construction must reject:

- independent coordinators claiming the same physical namespace;
- ambiguous reference or garbage-collection ownership;
- incompatible metadata or digest schemas across workers; and
- accidental sharing inferred only from equal paths, buckets, or endpoints.

Each representation namespace has one authoritative reference and GC domain.

## Provider Guarantees

Every provider exposes strong logical semantics regardless of backend:

- commit is immediately resolvable by subsequent opens;
- create-if-absent and conflict detection are authoritative;
- reference attachment cannot race deletion;
- readers never observe uncommitted content; and
- directory or bucket listing is not the source of truth for committed state.

Providers also declare observable durability and visibility capabilities,
including process/host restart survival, worker sharing, recovery, and
retention support. Backend brands and replication settings remain provider
configuration.

## Conformance

Every implementation must pass shared scenarios covering:

- bounded-memory one-shot ingestion;
- preserving and consumable ownership;
- native and streaming equivalence;
- concurrent create-if-absent;
- reference attachment versus deletion;
- read reservation versus garbage collection;
- range reads;
- cancellation and crash recovery;
- stale leases;
- shared-object corruption and repair; and
- insufficient capability rejection.

## Invariants

1. A committed representation object is immutable.
2. One object key always denotes one exact logical byte sequence.
3. Chunk boundaries never affect identity.
4. No semantic record exists without an attached object reference.
5. No object with a reference or active reservation is physically deleted.
6. A native optimization never weakens streaming-path semantics.
7. Backing-store weakness never weakens the logical provider contract.
