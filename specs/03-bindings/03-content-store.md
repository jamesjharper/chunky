# Content Store

## Purpose

The Content Store is Chunky's authoritative physical layer for immutable exact
content bytes. It is shared by execution cache and durable artifact persistence
when those services are explicitly composed over the same store namespace.

The store answers:

> Where are these exact bytes, and can they be read safely?

It does not understand artifact schemas, task outputs, producer provenance or
cache policy.

## Content object

```text
ContentObject
  content_id
  byte_length
  state
  provider_metadata
```

The logical object key and public upload/download address is `ContentId`. The initial required digest is
BLAKE3-256 over the exact logical bytes.

Byte length is mandatory metadata and must match observed bytes, but is not part
of content identity.

Many scoped semantic bindings, exact schema interpretations, tasks, and
completed provenance bindings may reference the same `ContentObject` whenever
their exact bytes are identical.

## Logical and physical bytes

The digest describes the logical bytes exposed through the common transfer
source. Providers may transparently encrypt, compress, shard, sparse-allocate,
or deduplicate physical storage if they reproduce the exact logical byte
sequence and preserve declared range behavior.

Provider paths, object keys, signed URLs, credentials and storage topology are
private implementation details.

## Object states

Observable logical states are:

```text
PREPARING
PREPARED
COMMITTED
CORRUPT
DELETING
```

Only `COMMITTED` objects are openable through normal reads.

## Transactional write

```text
OPEN -> PREPARED -> COMMITTED
                  \-> ABORTED
```

`prepare_from(source)`:

- consumes or copies one stable finite transfer source;
- enforces configured size/capacity limits;
- computes BLAKE3-256 and exact length incrementally;
- records enough state for recovery; and
- never exposes partial bytes as a committed content object.

`commit()`:

- atomically creates or reuses the immutable object by `ContentId`;
- is create-if-absent and idempotent;
- verifies compatible exact length/content for an existing object; and
- reports integrity conflict rather than choosing between conflicting bytes.

`abort()` is idempotent and never removes an already committed content object.

## Reads and reservations

Opening a content object atomically validates readable state and acquires a read
reservation.

```text
ReservedContentSource
  object
  open_stream(byte_range?)
  close()
```

The reservation prevents physical deletion until close or stale-reader
recovery.

Sequential full-object streaming is required. Providers may also support one
contiguous zero-based half-open byte range `[start,end)`.

For a supported range, `0 <= start <= end <= byte_length`; omitted `end` means
`byte_length`. Invalid ranges fail before streaming and a successful stream
yields exactly `end - start` bytes. The whole-object digest remains metadata for
the complete object and does not validate an isolated range by itself.

## References and retention

Execution-cache records, durable task/input/output records, and other semantic
owners may hold references/claims that keep a content object from collection.
Those owners decide *why* the content is retained; the Content Store only
ensures that physical deletion cannot occur while any authoritative reference,
claim, active reservation, or recoverable operation protects it.

Removing one cache/durable record detaches only that record's protection. It
does not delete content still needed elsewhere.

## Native transfer

A transfer target may use provider-native adoption, reflink, block clone,
provider-side copy, or another opaque optimization when exact-byte identity,
source ownership, mutation isolation, cancellation and transactional commit are
preserved. Bounded-memory byte streaming is the universal fallback.

### Source ownership and handoff

Every ingestion source declares whether it is preserve-only or explicitly
consumable. Possession of a source does not by itself permit destructive move or
adoption.

- preserve-only sources remain caller/provider-owned and may only be copied or
  shared through mutation-safe primitives;
- consumable sources may transfer backing-object ownership only as part of an
  atomic successful commit; and
- failure or cancellation before commit leaves source ownership unchanged.

After commit, a consumable source must not retain a second mutable alias that
could change the stored logical bytes. Provider-native offers are opaque
capabilities, not semantic storage addresses.

## Integrity

Every committed object has:

- one `ContentId`; and
- exact byte length.

Trusted native-copy paths may preserve previously verified digest/length only
when the operation guarantees byte identity. Otherwise ingestion verifies them.

Detected corruption:

- fails the read;
- marks the shared object `CORRUPT`;
- prevents normal opens through every referencing cache/durable record; and
- may be repaired only by restoring the exact same bytes and validating them.

Semantic records are never silently rebound to different content.

## Cancellation and recovery

Streaming checks cancellation between bounded chunks. Cancellation prevents an
uncommitted publication from becoming visible, but never rolls back a commit
that already won.

Recovery reconciles stale reads/writes, ambiguous commits, orphan staging,
missing physical content, corruption, unreferenced objects and interrupted
deletion. Recovery is idempotent and concurrency-safe across workers sharing a
namespace.

## Garbage collection

A content object is eligible for deletion only when it has:

- no semantic retention/reference protecting it;
- no active read reservation;
- no active write/preparation reservation; and
- no recoverable state that may still publish or attach protection.

Logical deletion prevents new opens before physical cleanup begins.

## Provider composition

Each content-store namespace has one authoritative coordinator for reference
accounting, reservations, recovery and GC. Runtime construction must reject
ambiguous or incompatible sharing.

The cache and durable-persistence services may depend on one shared Content
Store; the Content Store never depends on those higher-level services.

## Capacity, errors and conformance

Providers may enforce object-size, total-capacity, provisional-storage,
concurrency and range-capability limits. Limits are checked before consuming a
source when knowable and otherwise during bounded ingestion. A rejected or
interrupted write never exposes partial bytes as committed content.

Provider-neutral failure categories distinguish malformed source/range,
length/digest mismatch, exhausted or failed source, ownership violation,
capacity exhaustion, corruption, cancellation and storage/recovery failure.
Provider-specific causes may be attached only as diagnostics.

A conforming provider must demonstrate:

- concurrent create-if-absent publication of identical content is idempotent;
- conflicting bytes cannot commit under one digest;
- committed reads reproduce exact logical bytes and range lengths;
- reservations prevent deletion and stale reservations are recoverable;
- cancellation cannot expose an uncommitted object;
- reference release and garbage collection are race-safe; and
- native-transfer optimization is observationally equivalent to bounded
  streaming.

## Invariants

1. One `ContentId` always denotes one exact logical byte sequence.
2. Byte length is metadata, not identity.
3. Committed content is immutable.
4. No active reservation or authoritative retention reference may be collected.
5. Physical storage topology never appears in semantic artifact identities.
6. Native optimizations never weaken exact-byte streaming semantics.
7. Destructive source adoption requires an explicit consumable source and a
   successful atomic ownership handoff.
