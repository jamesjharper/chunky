# Artifact IO

## Purpose

Artifact I/O presents one semantic artifact abstraction to stages while giving
runtime infrastructure a separate transfer plane for canonical encoded content.

The two views are:

- typed value capabilities used by stages and local callers; and
- provider-neutral representation transfer used by persistence, cache, files,
  and transport adapters.

Stages do not know whether a selected source is backed by memory, cache, durable
persistence, a file, SMB, S3-like storage, or another provider.

## Artifact Handles

An `ArtifactHandle` is the execution-time binding for one concrete semantic
artifact instance:

```text
ArtifactHandle<T>
  declaration: ArtifactDeclaration<T>
  fingerprint
  source?: ArtifactSourceHandle<T>
  targets: ordered list<ArtifactTargetHandle<T>>
```

The resolved artifact ID and fingerprint derive the location-independent
`a-instance` locator. Any phase selection or terminal remapping is complete
before a planned stage input/output handle is created.

The handle is immutable after binding. Resource state belongs to the referenced
source and target handles.

An input normally has one source. A stage-produced output has one or more
targets selected by the runtime publication plan. A materialization binding
reuses an existing semantic source and adds an external target.

Supplied-content upload is different: its final fingerprint may be unknown
until representation validation completes. Upload therefore creates an
artifact instance; it does not require a pre-bound planned output handle.

## Artifact Instance

An `ArtifactInstance` identifies an available semantic artifact:

```text
ArtifactInstance<T>
  handle: ArtifactHandle<T>

  locator -> a-instance
  fingerprint
```

Availability asserts semantic identity, not storage location or retention
forever.

## Representation Descriptor

Each artifact declaration owns one canonical transferable representation:

```text
RepresentationDescriptor
  codec_id
  codec_version
  mime_type
  preferred_extension
```

Codec identity and version are authoritative. MIME type and extension are
descriptive.

The canonical representation is one finite ordered logical byte sequence.
Compound artifacts use a codec-defined streamable container or manifest.
Directory entries, symlinks, sparse extents, and multiple object-store objects
are not core artifact-transfer primitives.

Representation identity is separate from semantic artifact identity:

- artifact fingerprints describe semantic content or production pedigree;
- representation length and digest describe exact canonical bytes; and
- chunk boundaries never affect either identity.

## Capability Separation

Typed value I/O and encoded transfer are separate capabilities. A concrete
artifact handle may compose either or both:

```text
ArtifactSourceHandle<T>
  value_source?: ArtifactValueSource<T>
  transfer_source?: ArtifactTransferSource
  close()

ArtifactTargetHandle<T>
  value_target?: ArtifactValueTarget<T>
  transfer_target?: ArtifactTransferTarget
```

Examples:

- an in-memory stage value may initially expose only a value source;
- a cache or durable source normally exposes both through a codec adapter;
- a live HTTP request body may be transfer-only because it is upload input, not
  yet a committed stage-readable artifact;
- an in-memory publication target may be value-only; and
- cache, durable, and file targets normally accept transfer sources.

The capability interfaces are distinct, so implementations do not pretend that
every typed value is naturally streamable or that every byte stream is already
a valid typed artifact.

## Typed Value Source

A typed source returns the stage-facing value:

```text
ArtifactValueSource<T>
  read(codec) -> T
```

It may:

- return an existing in-memory object;
- decode a canonical transfer source through the declaration codec; or
- use provider-specific typed access while preserving the same value semantics.

`ArtifactReader` delegates to this capability. It never switches on concrete
provider type.

Constructing `T` may inherently require buffering. The bounded-memory guarantee
applies to representation transfer, not to every possible typed value.

## Transfer Source

An `ArtifactTransferSource` exposes canonical representation bytes:

```text
ArtifactTransferSource
  representation_descriptor
  replayability
  ownership_disposition
  byte_length?
  byte_digest?
  open_stream(byte_range?) -> asynchronous byte stream
  native_offers()
  close()
```

The portable fallback is a pull-based asynchronous byte stream. The consumer
controls backpressure. Chunk sizes and boundaries are implementation details.

### Snapshot stability

Every source represents one stable encoded snapshot for its lifetime. Length,
digest, semantic fingerprinting, and transferred bytes must describe that same
snapshot.

A source declares:

- `ONE_SHOT`: at most one stream may be opened; or
- `REOPENABLE`: every open yields byte-for-byte identical content.

A mutable file path may claim `REOPENABLE` only when its wrapper enforces or
validates snapshot stability. Otherwise it is one-shot and must be staged before
a second pass, retry, or multiple destinations.

### Ownership disposition

A source is:

- `PRESERVE`: successful transfer must leave its backing content usable; or
- `CONSUMABLE`: a compatible target may adopt its exclusive backing resource.

Possession of a source never implies destructive permission. Caller files,
cache hits, and durable objects are normally preserve-only. Chunky-owned
temporary files may be consumable.

### Native offers

Native offers are opaque provider capabilities. Compatible targets may use
rename, reflink, block clone, provider-side copy, metadata attachment, or
another optimized path.

The common contract does not expose paths, bucket keys, SMB handles, signed
URLs, or credentials. Unknown offers are ignored and streaming remains the
universal fallback.

Native operations must preserve exact bytes, ownership, mutation isolation,
transactional visibility, integrity, cancellation, and failure semantics.

### Ranges

Sequential full streaming is required. A source may support one zero-based
half-open byte range `[start, end)`.

Invalid ranges fail before streaming. Multipart range framing uses several
independently opened sources and belongs to a transport adapter.

## Typed Value Target

A typed target accepts a stage-facing value:

```text
ArtifactValueTarget<T>
  prepare(value, codec, fingerprint)
  commit()
  abort()
```

In-memory publication is the primary example. A target that persists canonical
bytes should use a transfer target through a codec adapter rather than
serializing independently.

## Transfer Target

A transactional transfer target accepts a canonical source:

```text
ArtifactTransferTarget
  prepare_from(source, expected_instance?)
  commit()
  abort()
```

The target owns ingestion. It may use a recognized native offer or pull bounded
chunks according to its own block, multipart, and memory policy.

The state machine is:

```text
OPEN -> PREPARED -> COMMITTED
                  \-> ABORTED
```

Preparation succeeds at most once. Commit and abort are idempotent:

- repeated commit returns the same committed outcome;
- repeated abort safely completes cleanup;
- abort never rolls back a committed artifact; and
- commit after confirmed abort fails as terminally aborted.

For a consumable backing resource, ownership transfers when
`prepare_from(source)` succeeds. Before success the source owns it; afterward
the target owns staging. A provider unable to preserve unambiguous ownership
uses a preserving transfer.

## Codec

The authoritative codec contract operates on transfer sources rather than
complete `bytes`:

```text
Codec<T>
  representation_descriptor
  encode(value) -> ArtifactTransferSource
  decode(source) -> T
  validate_and_fingerprint(source) -> semantic content contribution
  buffering_capabilities
```

The codec:

- defines the declaration's canonical representation;
- converts between typed and transfer views;
- validates canonical representation content;
- defines supplied-content semantic equality; and
- declares whether encode, decode, and validation are incremental or buffering.

Bindings may provide private small-value byte helpers. Complete `bytes` are not
the cross-module artifact I/O contract.

An incoming representation descriptor is an assertion, not proof. Upload
validates content through the codec or a declaration-owned validator before
commit.

Raw byte/file artifacts may define semantic content equality as the canonical
byte digest. Structured formats may parse incrementally. A format requiring
full typed materialization must declare that behavior rather than claiming a
bounded-memory conversion.

## Semantic Fingerprints

Supplied artifacts derive their final fingerprint from:

```text
hash(
  resolved artifact identity,
  declaration-defined semantic content contribution,
)
```

The semantic content contribution may differ from the representation digest.
For example, an audio declaration may fingerprint canonical decoded samples
while retaining a distinct encoded byte digest.

Stage-produced artifact fingerprints continue to derive from production
pedigree. They are known before output target binding and are not created by
serializing the produced value again.[^content-provenance-revisit]

[^content-provenance-revisit]: **Design revisit:** Separate artifact content
    identity from production provenance. Artifact fingerprints should likely
    contain only content identity, for both supplied and stage-produced
    artifacts, while declaration identity, stage pedigree, settings, and output
    identity form a separate provenance/execution-cache key that resolves to
    output content fingerprints. Under that model, upload is independent of
    `a-ref`, may accept a caller-calculated expected content fingerprint that
    the runtime verifies, and validates `a-ref` compatibility when the content
    is used. This note records a coordinated redesign to revisit; the current
    normative text has not yet been reconciled.

A trusted committed cache or durable source may preserve its validated
`a-instance` without re-decoding or re-fingerprinting when representation
compatibility and exact-byte preservation are established.

## Artifact Reader

`ArtifactReader<T>` exposes the typed stage-facing view:

```text
ArtifactReader<T>
  read() -> T
```

It requires a bound source with a typed value capability. If the concrete source
is transfer-backed, binding composes the declaration codec adapter before the
stage sees it.

Reader disposal does not independently close a task-owned source handle. The
runner owns source-handle lifetime so several downstream stages may consume one
artifact while its cache or durable reservation remains active.

## Artifact Writer and Publication Plan

A stage writes a semantic value once. The runtime publication plan determines
which value and transfer targets are required.

For transfer targets:

1. the codec exposes or creates one stable canonical representation source;
2. if more than one target needs a one-shot representation, the runtime stages
   it once into a reopenable source;
3. every transfer target prepares from that same snapshot; and
4. no target independently re-encodes the typed value.

Value-only targets may receive the typed value directly.

Target commit ordering and failure authority belong to the publication plan:

- for a requested output of a durable task, durable commit plus its task claim
  is the availability boundary;
- cache publication is separate reusable execution state;
- a cache failure does not invalidate an already committed durable output;
- a cache entry alone is not a durable task output; and
- local tasks follow their configured cache and materialization contracts.

A cache hit selected for a durable output must be promoted or transferred into
durable persistence before the output is reported available.

## Materialization

Caller file, object, stream, or diagnostic delivery is separate from semantic
artifact publication.

Materialization consumes the existing semantic source through either:

- a typed value adapter; or
- its canonical transfer source.

For file targets, preparation occurs in target-controlled staging on the
destination filesystem. Commit performs the strongest requested atomic
create/replace operation. Existing destinations remain unchanged and new
destinations remain absent until commit.

Failure to materialize a caller target does not invalidate a committed durable
artifact or valid cache entry.

## Source and Target Ownership

Source handles are explicitly closeable. Closing releases reservations and
provider resources. Upload consumes and closes its supplied transfer-source
handle for every terminal outcome; this does not delete preserve-only backing
content.

Targets own staging after successful preparation and must clean it on abort or
recovery.

Bindings without language-enforced moves must track consumed/terminal state and
reject reuse.

## Cancellation and Progress

Streaming checks cancellation between bounded chunks. An issued native
operation may finish physically when it cannot be interrupted, but cancellation
prevents publication unless commit already won and schedules cleanup.

Progress is phase-aware:

- resolving/waiting;
- transferring;
- validating/canonicalizing;
- committing;
- cleaning up; and
- completed.

Byte counts and totals are reported when meaningful. Native operations may be
indeterminate until completion. Block size, part size, and progress frequency
are private implementation details.

## Errors

Artifact I/O exposes provider-neutral structured categories for malformed
content, representation mismatch, length/digest mismatch, exhausted source,
ownership violation, invalid range, cancellation, corruption, capacity failure,
and storage/recovery failure.

Provider-specific causes may be attached as diagnostics.

## Invariants

1. Stages consume one storage-independent typed artifact abstraction.
2. Canonical representation transfer never requires whole-file buffering.
3. Typed conversion declares when it buffers.
4. One source handle denotes one stable semantic/representation snapshot.
5. Reopenable transfer sources return identical bytes on every open.
6. A native operation never weakens preservation or mutation isolation.
7. Multiple persistence targets reuse one encoded snapshot.
8. Semantic fingerprints and representation digests are never conflated.
