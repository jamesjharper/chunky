 # Durable Artifact Transfer Interview Record

> **Historical interview record.** Active upload/download are content-only
> `ContentId` operations; task bindings are specified separately.

This working record preserves questions, answers, and confirmed decisions for
durable artifact upload and download. It is not an authoritative specification.
The target specifications and implementation must not be changed until the user
confirms that the interview has reached a shared understanding.

## Discovered facts

- `03-durable-artifacts-upload-surface.md` currently defines durable request
  preparation as committing every supplied input through the ordinary artifact
  and cache-I/O contracts, producing committed `a-instance` locators.
- That document explicitly declines to define a streaming-ingest API or a
  bounded-memory guarantee.
- `05-durable-artifact-download-surface.md` is empty.
- `02-artifact-io-surface.md` exposes typed-value operations:
  `ArtifactSourceHandle.get_value(codec)` and
  `ArtifactTargetHandle.prepare(value, codec, fingerprint)`.
- `02-cache-management.md` currently describes caller materialization by reading
  a complete typed value from a semantic source and writing that value to a
  caller target.
- The local file cache implementation serializes into a temporary file during
  target preparation and atomically promotes the prepared file during commit.
- The cache contract includes reservations, waits, recovery, garbage
  collection, and recomputable semantic entries; these concerns are not
  automatically identical to the lifecycle of explicitly retained durable
  artifacts.

## Confirmed decisions

### 1. Relationship between durable artifact persistence and execution cache

Should durable artifact persistence be:

1. the execution cache itself, sharing its identity, retention, reservation,
   and garbage-collection semantics; or
2. a distinct semantic store/interface whose implementation may share the same
   physical backend and low-level transfer machinery with a cache provider?

Decision: choose option 2. Cache entries are reusable, recomputable
execution products governed by cache policy. Durable request inputs and
downloadable results require an explicitly durable lifecycle tied to stable
artifact-instance identity. Keeping those semantic responsibilities separate
allows one deployment to co-locate or deduplicate their bytes without making
durable availability depend on cache eviction and cache-resolution rules.

The separation must not break the artifact abstraction seen by stage inputs.
Stages must not need to know whether their selected source is backed by memory,
cache, or durable persistence.

Status: confirmed by the user.

### 2. Stage-facing values and infrastructure-facing transfer

Should artifact I/O distinguish two views of the same concrete artifact:

1. a semantic, typed-value view used by stages through `ArtifactReader`; and
2. a provider-neutral content-transfer view used by runtime infrastructure for
   persistence and materialization?

Under this model, every selected source remains represented by an
`ArtifactSourceHandle`, so stage binding does not change. A source or target
handle may additionally expose transfer capabilities. The runtime can then
stream or negotiate an optimized provider operation without making stages aware
of cache, durable storage, paths, SMB, S3, or HTTP.

Decision: adopt the two-view model. Keep typed artifact reading as the
ordinary stage contract, but add a transfer plane beneath or alongside it for
infrastructure. Do not require a durable object to be downloaded into memory or
into a local temporary file merely to satisfy the stage-facing abstraction.

Status: confirmed by the user.

### 3. Representation carried by the transfer plane

Should the transferable content for an artifact be one declared,
codec-compatible encoded representation, distinct from the artifact's semantic
fingerprint?

Under this model:

- the artifact declaration determines the representation contract;
- transferred bytes can be decoded by that declaration's codec;
- the representation has an explicit identity/version, normally derived from
  codec identity and version;
- byte length and a byte digest may accompany the representation when known;
- the artifact fingerprint continues to identify semantic artifact identity and
  is not assumed to equal the digest of stored bytes; and
- native move, clone, or server-side copy is valid only when it preserves the
  required representation unchanged.

Decision: yes. Define one canonical transferable representation per
artifact declaration for now. This gives providers a precise compatibility test
for optimized transfers while keeping semantic identity separate from physical
encoding. Supporting several interchangeable encodings for one artifact would
add negotiation and lifecycle complexity that is not currently required.

Status: confirmed by the user.

### 4. Streaming direction and ownership

Should the portable streaming fallback be pull-based, with the destination
owning ingestion?

The conceptual contract would be:

```text
transferable source
  metadata()
  open_byte_stream() -> asynchronous, bounded chunks

transactional target
  prepare_from(source)
  commit()
  abort()
```

`prepare_from(source)` lets the target choose its preferred ingestion strategy:

- use a compatible native move, clone, or server-side copy capability;
- otherwise pull chunks with backpressure into a temporary object, multipart
  upload, or other provider-specific staging area.

An HTTP request body can be represented as a one-shot transferable source. An
HTTP response can pull from the same source stream. Neither path requires the
entire representation in memory.

Decision: yes. Make the destination responsible for ingesting from a
pull-based asynchronous source. The destination knows its useful write sizes,
multipart rules, staging behavior, and native-copy facilities. A portable
`async bytes` stream provides the fallback and naturally supports backpressure
without prescribing filesystem blocks or cloud multipart details in the public
artifact contract.

Status: confirmed by the user.

### 5. One-shot and reopenable transfer sources

Should a transferable source explicitly declare whether it is:

- **one-shot**, where its byte stream may be opened only once, as with a live
  HTTP request body; or
- **reopenable**, where a fresh stream of the same immutable snapshot may be
  opened again, as with a reserved cache object or stable durable object?

With this distinction, consuming a one-shot source does not imply that the
runtime may retry it. If the content must be retried, hashed in a separate pass,
or delivered to several destinations, it must first be staged once into a
reopenable transactional target. Subsequent work reads from the staged or
committed source.

Decision: make one-shot sources first-class and expose replayability as a
capability. Do not require every web upload to be pre-spooled before entering
the artifact transfer abstraction, and do not pretend a consumed network body
can be reopened. Prefer one staging write followed by independent reads over
live fan-out to several targets, because live fan-out couples destination
backpressure and failure.

Status: confirmed by the user.

### 6. Authorization to consume a source

Should destructive transfer optimizations require an explicit consumable-source
capability, rather than being inferred from possession of a source handle?

The default transfer would preserve the source. A target may use rename or
another ownership-transfer operation only when all of the following hold:

- the source explicitly grants transfer of ownership of its backing content;
- the target can adopt it without changing the required representation;
- active reservations, aliases, or other readers do not require the source to
  remain at its current location; and
- failure semantics preserve or recover the content until target commit is
  complete.

Ordinary caller file handles, cache-hit handles, and durable-object handles
would normally be preserve-only. Chunky-owned temporary uploads and prepared
cache objects could be consumable. Movement “between cache entries” would
normally use metadata rebinding, content-addressed deduplication, reflink,
hardlink, or copy rather than removing an object still backing another valid
entry.

Decision: require explicit consumable ownership and default to preserving
the source. This allows fast rename/adoption where it is genuinely safe without
making `ArtifactSourceHandle` unexpectedly destructive.

Status: confirmed by the user.

### 7. Ownership handoff during transactional preparation

When a target adopts consumable backing content, should ownership transfer at
the successful completion of `prepare_from(source)`?

The lifecycle would be:

- before `prepare_from` succeeds, the consumable backing resource remains the
  source's responsibility;
- `prepare_from` may atomically rename or adopt it into target-controlled
  staging;
- once `prepare_from` returns success, the source must no longer use or clean up
  that backing resource;
- the target exclusively owns the prepared representation;
- `commit` makes it visible as the target artifact; and
- `abort` disposes of target-owned staging.

A target that cannot either complete native adoption or leave ownership
unambiguous must fall back to a preserving copy/stream operation. This ownership
guarantee applies to adoptable resources; a one-shot byte stream may naturally
be partially consumed when transfer fails and is separately governed by its
one-shot capability.

Decision: yes. Successful preparation is the clean handoff point because
it aligns ownership with the existing prepare/commit/abort transaction and
allows commit to remain a metadata/visibility transition.

Status: confirmed by the user.

### 8. Scope of the bounded-memory guarantee

Should bounded-memory operation be mandatory for representation transfer, while
typed codec conversion may explicitly be either streaming or buffering?

This distinguishes:

- **representation transfer**: source-to-target byte movement always supports
  bounded chunks and backpressure;
- **encoding**: converting a typed value into the transferable representation,
  which may be incremental when the codec supports it;
- **decoding**: converting the representation into the stage-facing type,
  which may inherently construct a complete in-memory value.

A web upload that already carries the declared representation can therefore be
persisted directly without `deserialize` followed by `serialize`. File-like
artifact codecs can provide incremental conversion. A codec for an ordinary
object may declare that conversion buffers the value.

Decision: make bounded-memory streaming a guarantee of the transfer
plane, not a false universal guarantee for all typed values. Replace or
supplement the current whole-`bytes` codec seam with explicit conversion
capabilities and declared buffering behavior. Infrastructure must avoid codec
round-trips when the incoming representation is already compatible.

Status: confirmed by the user.

### 9. Late-bound identity for streamed supplied artifacts

Should durable ingest allow the final supplied-artifact fingerprint and
`a-instance` locator to remain unknown until content preparation has completed?

The operation would:

1. allocate an opaque ingest/preparation ID;
2. stream or adopt content into provider-owned staging;
3. compute the supplied semantic content contribution during that pass when
   possible;
4. when single-pass semantic fingerprinting is unavailable, reread the now
   reopenable staged representation using bounded memory;
5. derive the final artifact fingerprint from artifact identity plus the
   semantic content contribution; and
6. commit staging under the resulting `a-instance` identity.

This avoids reading a one-shot source once for fingerprinting and again for
upload. It also means durable ingest cannot require an ordinary
`ArtifactTargetHandle` whose final fingerprint is already known before
preparation. Instead, ingest is a factory for a committed artifact instance;
the resulting durable source handle fits the normal stage-facing artifact
abstraction after commit.

Decision: permit late-bound identity. The current “fingerprint known
before target binding” rule remains appropriate for planned stage outputs, whose
identity comes from production pedigree, but should not be imposed on ingesting
unknown supplied content.

Status: confirmed by the user.

### 10. Immutability and idempotent publication

Should a committed durable `a-instance` be immutable, with concurrent or
repeated publication of the same identity resolving idempotently?

The intended behavior would be:

- commit is create-if-absent, never overwrite;
- if the same `a-instance` and compatible representation are already committed,
  the new staging object is discarded and publication succeeds by reusing the
  existing instance;
- a representation byte digest may be used to validate exact stored
  representation equality without confusing it with the semantic artifact
  fingerprint;
- if the same semantic instance identity resolves to incompatible
  representation metadata or bytes, publication fails as an integrity or
  canonicalization conflict rather than choosing a winner; and
- references and retention records may change independently, but the committed
  artifact content does not.

Decision: yes. Immutable, idempotent publication makes retry and
concurrency safe, enables deduplication, and maps naturally to atomic filesystem
rename/create, SMB create semantics, and conditional object-store puts.

Status: confirmed by the user.

## Pending question

### 11. Retention as explicit claims

Should durable artifact retention be represented by explicit claims that are
separate from immutable stored content and from cache retention?

The model would be:

- durable preparation creates a preparation/submission claim for each published
  input;
- durable task acceptance atomically creates task claims for all required input
  artifacts and later for committed outputs;
- accepting a task does not consume the preparation claim because a
  `DurableRunRequest` is cloneable and reusable;
- active download/read reservations temporarily protect content independently
  of longer-lived claims;
- garbage collection may remove content only when no live claim or read/write
  reservation protects it; and
- cache TTLs and cache task claims remain cache policy and do not count as
  durable-store claims unless an implementation explicitly coordinates the two
  stores below their public contracts.

The immutable `a-instance` identifies content; it is not itself a promise that
the content will be retained forever. Preparation and submission results must
therefore make applicable retention deadlines observable.

Recommendation: yes. Explicit claims avoid tying object lifetime to process-local
handles or clone counting, support reusable durable requests, and allow
filesystem, SMB, and object-store implementations to use different physical
retention mechanisms behind one lifecycle contract.

The user clarified that a live durable-store read reservation must independently
prevent garbage collection, exactly as a cache read reservation does. A
collector may mark an unclaimed artifact as eligible, but must not remove its
readable representation until all active read reservations have ended.

Status: confirmed by the user.

### 12. Read-reservation lifetime for streamed downloads

Should resolving a durable artifact for download atomically acquire a read
reservation that remains held for the complete lifetime of the opened transfer
source?

The reservation would:

- be acquired only after validating that the committed representation is
  readable;
- close the race between lookup and garbage collection;
- remain active while bytes are streamed, regardless of retention-claim expiry;
- be released on end-of-stream, explicit close, cancellation, client disconnect,
  or transfer failure; and
- be recoverable if the reading process dies without cleanly closing it.

The HTTP layer need not understand durable-store garbage collection. It only
owns and closes the returned download source according to the source-handle
lifecycle.

Decision: yes. Bind the reservation to the transfer source lifetime,
using the same lookup-plus-reserve atomicity and stale-reservation recovery
principles already required for cache reads.

Status: confirmed by the user.

### 13. Optional byte-range reads

Should a durable transfer source optionally support opening a bounded byte range
of the canonical representation?

The baseline contract remains a sequential stream from byte zero. A source may
add capabilities for:

- known total representation length;
- opening `[offset, end)` or `[offset, length]` ranges;
- reporting range support and any provider constraints; and
- multiple independently reserved range streams.

Ranges apply to encoded representation bytes, not to typed artifact elements or
semantic records. Providers that lack efficient range reads can omit the
capability rather than emulate it by reading and discarding a large prefix.

Decision: yes, as an optional capability. It enables resumable HTTP
downloads and maps naturally to files, SMB range reads, and S3 `Range` requests
without burdening the minimum streaming contract or pretending every artifact
is seekable.

Status: confirmed by the user.

### 14. Client-resumable upload sessions

Should this design define durable, externally resumable upload sessions, or
limit its required ingest contract to one transfer attempt per prepared target?

These are separate concerns:

- a provider may use buffered file writes or S3 multipart upload internally
  while fulfilling one `prepare_from(source)` call; and
- resuming after a client disconnect requires a persistent upload-session
  protocol with session IDs, accepted ranges or parts, offset reconciliation,
  expiry, authorization, and eventual assembly.

Decision: do not make externally resumable upload sessions part of the
required artifact persistence contract in this design. Require bounded-memory,
cancellable one-attempt streaming and allow providers to use multipart
internally. Preserve room for a later resumable-ingest service layered over the
same staging and commit model rather than exposing chunk assembly in artifact
source/target handles now.

Status: confirmed by the user.

### 15. Opaque native-transfer capabilities

Should native transfer optimization be negotiated through opaque provider
capabilities, with byte streaming as the universal fallback?

The target-owned ingestion sequence would be:

1. inspect source representation and ownership metadata;
2. try a recognized native capability, such as atomic adoption, reflink,
   hardlink, filesystem copy, SMB-side copy, or S3 server-side copy;
3. verify that the operation satisfies representation, ownership, atomicity,
   and integrity requirements; and
4. fall back to pulling the source byte stream.

The common artifact contract would not expose local paths, SMB file handles,
bucket/key pairs, pre-signed URLs, or provider credentials. A provider may issue
an opaque transfer offer that compatible targets recognize. Unknown offers are
ignored.

Decision: yes. Keep optimization extensible and provider-owned. Specify
observable semantics and fallback behavior, not a fixed cross-backend operation
enum or backend-specific locator fields in artifact handles.

Status: confirmed by the user.

### 16. Required representation integrity metadata

Should every committed durable representation record include:

- exact byte length; and
- a strong byte digest with an explicit algorithm/version?

The target must establish both before commit. During streamed ingestion it can
calculate them incrementally. If the source supplies expected values, they are
validation assertions and must match; they are not blindly trusted.

A native transfer may reuse integrity metadata from an already committed,
validated source when the provider operation guarantees exact-byte
preservation. Provider-specific values such as an S3 ETag must not be treated as
the required digest unless their precise algorithm and semantics satisfy the
contract.

This representation digest remains separate from the semantic content
contribution and final artifact fingerprint.

Decision: yes. Make length and a versioned strong representation digest
mandatory durable metadata. They support corruption checks, range/download
metadata, and safe idempotency while preserving optimized transfers between
trusted committed sources.

Status: confirmed by the user.

### 17. Read-time integrity verification

Should opening a durable source validate committed metadata and readability
without requiring a full representation rehash before every read?

The baseline would be:

- ingest verifies or establishes length and digest before commit;
- open atomically validates committed state, representation metadata, backing
  object identity, and provider-specific cheap integrity signals;
- the source exposes the authoritative length and digest to infrastructure and
  download layers;
- a provider may verify bytes incrementally while they are read, use native
  checksums, or perform background scrubbing;
- a detected mismatch fails the read, quarantines or marks the representation
  corrupt, and prevents future successful opens; but
- the common contract does not force a complete pre-read solely to rehash bytes.

Decision: yes. Treat commit-time validation as authoritative and permit
provider-specific ongoing integrity checks. Mandatory full rehash-before-read
would add latency and duplicate I/O, while rehash-during-delivery cannot promise
that zero corrupt bytes were already emitted.

Status: confirmed by the user.

### 18. Logical commit record as the visibility boundary

Should durable publication become visible through one authoritative, atomically
created commit record after representation staging is complete?

The logical sequence would be:

1. write or adopt an immutable staged representation;
2. establish semantic identity, representation identity, length, and digest;
3. atomically create the committed instance record using create-if-absent
   semantics; and
4. expose an instance to readers only when that record exists and validates.

The commit record may be a transactional database row, an atomically renamed
manifest, or a conditionally created object. Backing content that exists without
a valid commit record is never readable as a committed artifact and is eligible
for orphan recovery. A committed record must never point to content that can be
removed independently while it remains live.

Decision: yes. Specify atomic logical visibility through the commit
record, while leaving providers free to use filesystem rename, SMB-safe
publication, database transactions, or conditional object creation.

Status: confirmed by the user.

### 19. Atomic publication and initial retention

Should successful publication atomically ensure both a committed instance and
its caller-required initial retention claim?

This applies whether publication:

- creates a new immutable representation; or
- discovers and idempotently reuses an existing compatible instance.

There must be no observable state in which publication reports success but the
required claim is absent, nor a gap in which garbage collection can remove the
instance before the claim is attached. Providers may use one transaction or an
equivalent recovery-safe protocol, but expose one logical outcome.

Decision: yes. Treat “publish and retain for this purpose” as one atomic
store operation. A bare content commit may remain an internal staging/recovery
step, not the successful result returned to durable preparation or task output
publication.

Status: confirmed by the user.

### 20. Partial multi-input preparation failure

If durable request preparation fails after publishing some inputs, should it
release the preparation claims created by that failed operation while leaving
immutable content eligible for deduplication and garbage collection?

The behavior would be:

- abort every incomplete target;
- close all remaining owned sources;
- release claims created specifically for the failed preparation;
- never delete or roll back content synchronously if another claim,
  reservation, or concurrent publication protects it; and
- allow unclaimed representations and commit records to be reclaimed by normal
  garbage collection.

Crash recovery can reach the same result because preparation claims and staging
records are expiring/recoverable.

Decision: yes. Roll back the failed operation's ownership claims, not
shared immutable content. This avoids retaining inputs for a request that was
never produced while preserving safe deduplication and concurrency.

Status: confirmed by the user.

This decision was subsequently superseded by decision 21. Upload is
per-artifact, so there is no multi-input preparation transaction. Each upload
independently aborts or publishes and retains its own artifact. Request
construction performs no upload I/O.

## Pending question

### 21. Per-artifact upload rather than whole-request preparation

The initially proposed `DurablePreparationResult` containing a
`DurableRunRequest` was rejected. It followed the current
`03-durable-artifacts-upload-surface.md`, which conflates publishing supplied
artifacts with converting an entire local request.

The user's intended workflow is:

1. upload artifacts independently, one at a time;
2. receive one committed `a-instance` locator for each successful upload; and
3. construct a `DurableRunRequest` separately using those locators.

The proposed per-artifact result is conceptually:

```text
DurableArtifactUploadResult
  artifact_instance: a-instance
  upload_id
  retained_until
```

Decision: replace whole-request durable preparation with a per-artifact
upload/publish surface. Keep `DurableRunRequest` construction separate. Do not
retain the current conversion operation as a compatibility wrapper unless a
later decision identifies a distinct need for a convenience workflow.

Status: confirmed by the user.

### 22. Artifact identity supplied to upload

Should each upload identify a fully resolved artifact declaration/reference and
provide content in that declaration's canonical transfer representation, while
the service derives the final fingerprint and `a-instance`?

The caller would provide:

- the fully resolved artifact ID or an equivalent manifest-resolvable
  declaration reference;
- representation metadata sufficient to establish compatibility with the
  declaration's canonical codec representation; and
- the transferable source.

The caller would not choose the final artifact fingerprint or
`a-instance` locator. The service validates the declaration and representation,
computes the semantic content contribution, derives the fingerprint, and
returns the committed locator.

Decision: yes. Require a manifest-known, fully resolved artifact identity
at upload time and make final instance identity service-derived. This prevents
callers from publishing arbitrary bytes under a self-asserted semantic identity
and avoids unresolved phase selectors in committed instance locators.

Status: confirmed by the user.

### 23. Primitive upload input

Should the primitive per-artifact upload operation accept a transferable source
already in the declaration's canonical encoded representation?

Under this model:

- an HTTP body can be wrapped directly as a one-shot transfer source;
- a stable file can be wrapped as a reopenable and potentially consumable
  transfer source;
- an existing durable or cache artifact can supply its transfer view;
- an in-memory typed source is adapted by the declaration codec into a transfer
  source, using streaming encoding when supported; and
- upload never calls `get_value()` merely to persist already compatible
  representation bytes.

The source carries or is paired with a representation descriptor that must match
the declaration's canonical representation contract. MIME type may be useful
metadata, but codec identity/version is authoritative.

Decision: yes. Make canonical representation transfer the primitive
upload seam, with typed values handled by adapters. This is the simplest path to
bounded-memory web upload and native file/object adoption without changing what
stages receive.

Status: confirmed by the user.

### 24. Canonical representation validation before commit

Should upload validate that supplied bytes are a valid canonical representation
for the resolved artifact declaration before committing them?

The declaration codec or a companion representation validator would:

- reject malformed or incompatible content;
- compute the semantic content contribution according to the declaration's
  equality rules;
- validate canonical-form constraints when those constraints are meaningful;
  and
- do so incrementally when supported, or against the bounded-memory staged
  source when a second pass is required.

Raw byte/file artifacts can define validation as identity plus length/digest.
Structured formats may parse incrementally. Formats whose semantic
canonicalization requires full decoding may perform a second bounded-memory
read or explicitly declare their conversion limits.

Decision: yes. A representation descriptor is an assertion, not proof.
Do not publish content that the declared codec cannot subsequently consume or
whose semantic fingerprint was derived without applying the declaration's
content-equality rules.

Status: confirmed by the user.

### 25. Primitive download result

Should the primitive download/open operation resolve an `a-instance` into a
reserved transfer source for its canonical representation rather than returning
a typed value?

Conceptually:

```text
open_durable_artifact(a-instance) -> DurableArtifactDownload
  artifact_instance
  representation_descriptor
  byte_length
  byte_digest
  source
```

The returned source holds the read reservation established in decision 12 and
supports sequential streaming plus optional ranges. An HTTP layer may stream it
directly. A file target may ingest it through native-copy negotiation or the
stream fallback. A stage or local typed caller may adapt it through the
declaration codec and the ordinary `ArtifactReader` view.

Decision: make canonical representation access the primitive download seam and
accept either a concrete `a-instance` locator or a task-relative `a-task`
locator. Layer typed decoding above it.

For `a-instance`, the service opens that concrete committed artifact. For
`a-task`, it first resolves the task-output binding to its eventual concrete
instance, which may not yet be known or available.

This preserves bounded-memory download and keeps transport concerns out of stage
code.

Status: confirmed by the user.

## Pending question

### 26. Pending `a-task` behavior

When download receives an `a-task` whose output is not yet available, should the
core surface distinguish non-blocking resolution from cancellation-aware
waiting?

Conceptually:

```text
inspect_download(a-task) ->
  Pending
  Available(a-instance)
  Unavailable(reason)

await_download(a-task, cancellation/deadline) ->
  DurableArtifactDownload
  or terminal Unavailable(reason)
```

An `a-task` preserves its requested reference, possibly including an unresolved
phase, and remains stable while planning resolves it. Opening the byte source
occurs only after the task binding points to a committed durable `a-instance`;
that open then atomically acquires the read reservation.

Decision: provide both behaviors rather than making locator acceptance
silently block. A transport layer can choose immediate status, polling, or
waiting according to its own protocol while the artifact service supplies one
authoritative resolution state machine.

Status: confirmed by the user.

### 27. Lifetime of `a-task` resolution records

Should the task-to-artifact resolution record remain queryable for the retained
lifetime of the durable task record, even if the referenced artifact content has
expired under its separate retention policy?

This allows download/query to distinguish at least:

- unknown or expired task record;
- output still pending;
- task/output terminally failed or cancelled;
- output resolved to a specific `a-instance`, but that durable content is no
  longer retained; and
- output resolved and is currently downloadable.

The mapping does not itself retain artifact bytes. The task's artifact claim
does that according to the separate artifact-lifecycle policy.

The recommendation to retain a successful resolution only with the durable task
record was rejected. The user expects a successfully resolved `a-task` to follow
the output artifact's lifetime and potentially outlive the task record.

Two lifetimes still need to be distinguished:

1. the task-output artifact claim created for this output; and
2. the deduplicated physical instance remaining because any claim from any
   request or task still retains it.

Recommendation: retain the successful `a-task -> a-instance` binding for the
lifetime of this task output's artifact claim, not merely while identical bytes
remain due to unrelated claims. Pending, failed, and cancelled bindings have no
artifact lifetime and remain governed by task-record retention.

Status: confirmed by the user.

### 28. Task-scoped artifact-retention policy

The initial recommendation for per-output durable retention was refined by the
user. The intended model is:

- each independent artifact upload accepts a TTL or uses the store's local
  default;
- a `DurableRunRequest` carries an incoming task artifact-retention
  configuration;
- atomic task acceptance extends protection for all referenced input artifacts
  to the request's required horizon; and
- outputs receive the same task-scoped retention when they commit.

Recommendation: represent the TTL “bump” by atomically creating task-specific
retention claims, not by mutating the upload claim or a single global expiry on
deduplicated content. The effective content lifetime is the maximum protection
provided by all live claims. Cache TTL and task-record retention remain
semantically separate.

Status: confirmed by the user.

### 29. Pre-submission upload grace claim

Should a successful independent upload create a short, non-refreshing
pre-submission grace claim?

The proposed behavior is:

- the claim begins when publish-and-retain succeeds, not when streaming starts;
- the caller may request a TTL within provider limits, otherwise a local default
  of a few hours applies;
- the upload result exposes the absolute `retained_until` deadline;
- ordinary inspection or download does not silently refresh the grace claim;
- accepted durable submission creates the longer task claim established in
  decision 28; and
- if no task or other owner claims the artifact, it becomes garbage-collection
  eligible after the grace deadline and active reservations end.

Decision: yes. Keep the upload claim deliberately short and
non-refreshing so abandoned uploads do not become long-lived storage leaks.
Renewal, if supported, should be explicit and subject to provider policy.

Status: confirmed by the user.

### 30. Start of task artifact-retention TTL

Should the requested task artifact TTL describe post-terminal retention, with
separate active-task claims protecting artifacts while execution is live or
recoverable?

The lifecycle would be:

- atomic submission acceptance acquires active task claims for every referenced
  input before the upload grace claims may expire;
- active task claims protect required inputs for the complete active/recovery
  lifetime, independent of the requested post-terminal TTL;
- committed outputs immediately receive active task claims;
- terminalization converts or extends all task artifact claims to a common
  `terminal_at + requested_artifact_ttl` deadline; and
- successful `a-task` bindings follow those output claims.

Decision: yes. Measure caller-visible task artifact retention from
terminalization, not acceptance or individual output commit. This prevents a
long-running task from consuming the promised download/reuse window before its
results are available.

Status: confirmed by the user.

### 31. Artifact retention after failure or cancellation

Should the task's configured post-terminal artifact TTL apply after every
terminal outcome, not only successful completion?

The retained set would include:

- every durable input referenced by the accepted request; and
- every output artifact that reached a valid committed state before failure or
  cancellation.

Incomplete prepared outputs are aborted and never retained as artifacts.
Failed or cancelled outputs without committed instances remain task-record
states only.

Decision: yes. Retaining valid inputs and committed partial outputs for
the configured window supports diagnosis, retry, and explicit reuse. The caller
already controls the TTL, so failure should not silently change the artifact
lifecycle policy.

Status: confirmed by the user.

### 32. Separate value and transfer capability interfaces

Should typed-value I/O and encoded-representation transfer be separate
capability interfaces, composed by artifact handles and adapters rather than
placed into one mandatory protocol?

Conceptually:

```text
typed value source<T>        transfer source
  read(codec)                  metadata
                               open byte stream
                               optional native offers

typed value target<T>        transactional transfer target
  prepare(value, codec)        prepare_from(source)
  commit / abort               commit / abort
```

A stage input binding still resolves to one artifact source handle with the
typed capability required by `ArtifactReader`. A durable/cache/file-backed
handle can additionally expose a transfer capability. Codec adapters can bridge
typed values to representations. A live HTTP body may implement only the
transfer-source capability because it is upload input, not yet a committed
stage-readable artifact.

Decision: yes. Keep the capability interfaces distinct and compose them
behind artifact handles. Refactor the current value-only source/target
protocols directly rather than preserving compatibility wrappers in this
pre-stable codebase.

Status: confirmed by the user.

### 33. Replace the whole-`bytes` codec contract

Should the authoritative codec interface stop requiring
`serialize(value) -> bytes` and `deserialize(bytes) -> value`, and instead work
through transfer sources?

Conceptually, a codec would define:

- its canonical representation descriptor;
- encoding a typed value as a transfer source;
- decoding a transfer source into the typed value;
- validation and semantic-content fingerprinting of a representation; and
- declared buffering/streaming capabilities for those operations.

A codec may still use complete byte buffers internally when appropriate, and
bindings may offer private convenience helpers for small values. Complete
`bytes` would no longer be the cross-module artifact I/O contract.

Decision: yes. Replace the bytes-only seam directly. Keeping it as the
authoritative interface would allow infrastructure to accidentally reintroduce
whole-file buffering and would make streaming an optional side channel rather
than the normal representation model.

Status: confirmed by the user.

### 34. Transfer-source snapshot stability

Should every transfer source represent one stable encoded snapshot for its
lifetime, with reopenable sources guaranteeing byte-for-byte identical streams
on every open?

The implications are:

- an immutable durable object or reserved cache object can be reopenable;
- provider-owned staging can be reopenable;
- a live HTTP body is one-shot;
- a caller file path may be declared reopenable only when its wrapper can
  enforce or validate snapshot stability;
- an unstable local file source must be treated as one-shot and staged before a
  second pass or retry; and
- length, digest, semantic fingerprint, and transferred bytes must all describe
  the same snapshot.

Decision: yes. Make snapshot stability a hard transfer-plane invariant.
Do not use a soft “probably unchanged” expectation for operations that depend on
reopening. Providers remain free to use file descriptors, locks, immutable
objects, version IDs, or validation metadata to satisfy it.

Status: confirmed by the user.

### 35. Encode once before multi-store publication

When a typed artifact value must be published to both durable persistence and
execution cache, should the runtime create one stable canonical representation
source and let each transactional target ingest from it?

The flow would be:

1. the codec exposes or produces one stable representation source;
2. if that source is one-shot and more than one target needs it, Chunky stages
   it once into a reopenable provider-owned source;
3. durable and cache targets independently prepare from that same snapshot,
   using native transfer where compatible; and
4. no target independently reserializes the typed value.

This replaces the current cache-target pattern in which each
`prepare(value, codec, fingerprint)` may serialize the whole value into its own
temporary file.

Decision: yes. Encode or stage once, then transfer the canonical snapshot
to each store. This makes the transfer plane the shared seam between artifact
I/O, durable storage, cache, and materialization.

Status: confirmed by the user.

### 36. Durable commit as the success boundary

For a requested output of a durable task, should committed durable publication
be the artifact-success boundary, with cache publication treated as a separate
reuse concern?

The consequences would be:

- an output is not reported downloadable until its durable commit and task
  claim succeed;
- a cache entry alone is not a durable task output;
- a cache hit selected for a durable output must be transferred into durable
  persistence before the output succeeds;
- failure to publish the durable instance fails that output even if a cache
  entry committed;
- failure to populate or refresh cache does not invalidate an already committed
  durable output, though it is reported diagnostically and cache reservations
  are cleaned up; and
- local, non-durable tasks may retain their separately specified cache and
  materialization semantics.

Decision: yes. Make durable storage authoritative for durable output
availability. Cache publication must not be part of the same all-or-nothing
semantic commit because cache is evictable reuse state, not durable output
ownership.

Status: confirmed by the user.

### 37. Which task artifacts enter durable persistence

Should durable persistence automatically contain only:

- independently uploaded supplied inputs; and
- successfully committed requested outputs of durable tasks?

Stage intermediates would remain execution artifacts governed by cache and
active-task recovery claims unless they are themselves explicitly requested for
durable publication. A restarted task may reuse protected cache intermediates
or recompute them if unavailable.

Decision: yes. Do not automatically publish every intermediate into the
durable artifact store. That would duplicate cache responsibilities and create
long-lived artifacts that callers never requested. Durable execution must still
protect required cache entries for active/recoverable work according to the
cache contract.

Status: confirmed by the user.

### 38. Binding durable inputs to stages

Should a durable `a-instance` be directly resolvable as an ordinary
stage-facing artifact source, with cache localization remaining an invisible
optimization?

The runtime may:

- decode or stream directly from the reserved durable source;
- transfer it into a compatible local/shared cache entry and bind the cache
  source;
- stage it locally when the codec or stage-facing type requires local random
  access; or
- use native provider transfer where available.

The stage continues to receive the same typed `ArtifactReader` abstraction. A
failure to populate an optional cache copy must not make an otherwise readable
durable input unavailable; the runtime can fall back to the durable source.

Decision: yes. Make the durable store a valid source provider in the
artifact binding model, not a pre-cache download service. Allow transparent
localization based on capability and performance policy without exposing source
location to stages.

Status: confirmed by the user.

### 39. Separate immutable representation objects from semantic entries

Should cache entries and durable artifact records reference immutable
representation objects rather than each owning a separate physical copy?

Conceptually:

```text
representation object
  representation identity
  byte length
  byte digest
  immutable backing content

cache entry ------------------\
                               > references representation object
durable artifact instance ----/
```

The cache and durable store remain distinct semantic modules with separate
claims, reservations, resolution, and garbage-collection policy. A deployment
may back them with one representation-object service or with separate services.

For a local filesystem implementation this enables:

- atomically adopting a Chunky-owned temporary file into an object path once;
- creating or replacing cache entries through metadata references;
- reusing identical representation bytes without copying;
- using reflink, hardlink, or filesystem copy when object adoption cannot use
  rename; and
- removing backing content only when no cache/durable record or active transfer
  still references it.

Decision: yes. Introduce the immutable representation object as a
lower-level storage concept shared optionally by cache and durable persistence.
Do not expose it as semantic artifact identity or collapse the two higher-level
stores.

Status: confirmed by the user.

### 40. Representation-object identity

Should an immutable representation object be identified only by its versioned
strong byte digest plus exact length, independent of artifact and codec
identity?

Artifact declarations, codec identity/version, semantic fingerprints, and
`a-instance` locators remain on cache-entry and durable-instance records. The
lower object layer only answers: “these are exactly the same bytes.”

If two codec representations happen to contain identical bytes, they may safely
share one physical object while retaining distinct semantic metadata. If an
existing object under the same digest key has a different length or fails
validation, the store raises an integrity conflict rather than reusing it.

Decision: yes. Make the object layer purely content-addressed using a
versioned digest namespace and length validation. Avoid embedding semantic
identity in physical storage keys.

Status: confirmed by the user.

### 41. Object-store references and garbage collection

Should the representation-object service own atomic references/reservations
that cache and durable stores attach to their semantic records?

The required invariants would be:

- publishing a cache entry or durable instance atomically establishes its
  reference to a committed representation object;
- removing or replacing a semantic record releases only that record's
  reference;
- active transfer preparation and reads hold temporary object reservations;
- attaching a new reference cannot race object deletion;
- an object becomes collectible only when it has no semantic references and no
  active transfer reservations; and
- physical deletion and orphan recovery belong to the object service, not to
  cache or durable providers manipulating paths directly.

Decision: yes. Give the lower-level object service authoritative
reference accounting and atomic attach/detach operations. Do not rely on OS
hardlink counts or cross-module directory scans as the lifecycle contract,
though implementations may use those as internal aids.

Status: confirmed by the user.

### 42. Backend-independent logical consistency

Should every persistence provider satisfy strong logical publication and
reference semantics even when its filesystem, SMB share, or object backend does
not directly provide them?

At the service boundary:

- a successful commit is immediately resolvable by subsequent opens;
- create-if-absent and idempotent conflict detection are authoritative;
- reference attachment and deletion eligibility cannot race;
- readers never observe uncommitted or partially published content;
- directory/bucket listing is not the source of truth for committed state; and
- delayed physical cleanup is permitted after logical deletion, provided the
  object cannot be reopened as live content.

A provider may use a transactional metadata coordinator, conditional requests,
commit manifests, retries, or recovery records to meet these guarantees.

Decision: yes. Define strong logical semantics at the Chunky provider
boundary and make weaker backend consistency an implementation problem. This
keeps SMB- and S3-like providers substitutable without weakening artifact
correctness for every caller.

Status: confirmed by the user.

### 43. Declared durability and sharing capabilities

Should persistence providers declare the durability and visibility guarantees
they actually provide, with durable runtime operations validating required
capabilities before accepting work?

Capabilities should describe observable guarantees rather than backend brands,
including whether committed artifacts:

- survive process restart;
- survive runtime/host restart within the provider's declared failure domain;
- are visible to all workers that may execute or serve the durable task; and
- support the required recovery and retention operations.

Replication count, SMB configuration, cloud storage class, and similar backend
choices remain provider configuration. A private runtime temporary directory
may implement the same interfaces for local use but cannot satisfy shared
durable-task requirements.

Decision: yes. Make durability and worker visibility explicit provider
capabilities and fail preflight before upload/submission when they are
insufficient.

Status: confirmed by the user.

### 44. Cancellation of streaming and native transfers

Should transfer cancellation guarantee a prompt logical terminal outcome and
eventual cleanup, while allowing an already-issued native backend operation to
finish physically when it cannot be interrupted?

Required behavior:

- streamed transfer checks cancellation between bounded chunks and stops
  pulling further bytes;
- incomplete targets abort and release write reservations;
- cancelled downloads close their source and release read reservations;
- an uninterruptible native operation may finish into target-owned staging, but
  cancellation prevents publication unless commit had already completed;
- cleanup/recovery later removes abandoned staging or recognizes an already
  completed commit; and
- cancellation never reports that a committed artifact was rolled back.

Decision: yes. Specify cancellation at the logical transaction boundary,
not as a promise that every filesystem, SMB, or cloud operation can be
physically interrupted.

Status: confirmed by the user.

### 45. Phase-aware transfer progress

Should upload and download expose phase-aware structured progress with optional
byte totals rather than promise one continuous percentage?

Relevant phases may include:

- resolving or waiting for an `a-task`;
- transferring into or out of staging;
- validating/canonicalizing;
- publishing/committing;
- cleaning up; and
- completed.

Within a byte-moving phase, progress reports logical bytes completed and total
bytes when known. A native backend operation may report indeterminate progress
until it completes. Progress frequency, instantaneous rate, filesystem block
size, and multipart part details remain implementation concerns.

Decision: yes. Make phase and byte counts authoritative where available,
while allowing indeterminate native operations. This is accurate enough for web
and task progress surfaces without leaking backend mechanics or inventing false
percentages.

Status: confirmed by the user.

### 46. Backend-independent structured failures

Should upload, download, and transfer operations expose stable structured domain
failures rather than provider-specific exceptions?

The common taxonomy should distinguish at least:

- invalid or unknown artifact/task locator;
- pending task output versus terminally unavailable output;
- expired/not-retained artifact;
- unsupported or mismatched representation;
- malformed/noncanonical content;
- length or digest mismatch;
- immutable identity/canonicalization conflict;
- invalid or unsupported byte range;
- source exhausted or ownership/capability violation;
- provider unavailable or required durability unsupported;
- committed content detected as corrupt;
- cancellation; and
- internal storage/recovery failure.

Provider-specific diagnostics and retry hints may be attached, but callers can
branch on stable categories.

Decision: yes. Normalize failures at the module boundary and preserve
causes for diagnostics. This enables consistent mapping into bindings and a
future HTTP layer without making HTTP status codes part of this design.

Status: confirmed by the user.

### 47. Size and capacity enforcement during upload

Should providers enforce configured artifact-size and capacity/quota limits both
before and during streaming?

The contract would require:

- reject a known declared length that exceeds policy before reading the source;
- count actual representation bytes and abort immediately when an unknown or
  dishonest-length stream exceeds its limit;
- verify declared and actual length equality when a length was supplied;
- reserve or otherwise account for required capacity before commit where the
  provider needs it;
- release provisional capacity on abort, cancellation, or recovery; and
- report stable limit/capacity failures without exposing backend credentials or
  layout.

Codec validation may impose separate decoded-resource limits where malformed or
compressed content could exhaust CPU, memory, or temporary space.

Decision: yes. Make enforcement streaming-aware and provider-configured;
never rely solely on HTTP `Content-Length` or source metadata.

Status: confirmed by the user.

### 48. Transparent at-rest transformations

Should representation-object identity describe the canonical bytes exposed to
transfer clients while permitting providers to transform physical storage
transparently?

Examples include provider-managed encryption, compression, sparse allocation,
deduplicated blocks, and cloud storage-class encoding. Such transformations:

- do not change representation digest, length, codec identity, or semantic
  artifact identity;
- must reproduce the exact canonical byte stream and range semantics advertised
  by the provider;
- remain provider configuration and metadata;
- may affect which native-copy offers are compatible; and
- must not expose encryption keys or backend details through artifact handles.

Decision: yes. Define representation identity at the logical byte
boundary and allow transparent at-rest transformations behind it. If a
transformation changes bytes visible to clients, it is a different artifact
representation rather than an at-rest implementation detail.

Status: confirmed by the user.

### 49. Express ownership intent, not a move/copy command

Should callers express whether a source may be consumed, while targets choose
the concrete transfer operation?

The public contract would not accept commands such as `MOVE`, `COPY`,
`HARDLINK`, or `SERVER_SIDE_COPY`. Instead:

- preserve-only sources must remain usable after successful transfer;
- consumable sources transfer backing-resource ownership at successful prepare;
- targets select the safest efficient operation from recognized capabilities;
  and
- observable content, atomicity, ownership, and failure semantics are identical
  regardless of which operation was selected.

For the object-backed cache decided above, “moving between cache entries” is
normally reference rebinding. Importing a Chunky-owned temporary file may use
rename/adoption. Exporting a durable/cache object to a caller path normally
preserves the stored object and may use reflink, hardlink where isolation is
safe, filesystem copy, or streaming.

Decision: yes. Make ownership disposition the caller-visible intent and
leave operation selection to providers. A forced move/copy enum would leak
storage topology and could demand an unsafe or impossible operation.

Status: confirmed by the user.

### 50. Native optimization must preserve mutation isolation

Should native file operations be permitted only when they preserve the
immutability and ownership isolation of every live representation reference?

In particular:

- reflink/copy-on-write clones are suitable when later target mutation cannot
  alter the source object;
- a hardlink must not expose a store-owned immutable inode through an ordinary
  caller-writable target path;
- rename is valid only for explicitly consumable exclusive backing resources;
- block cloning or server-side copy must preserve exact logical bytes; and
- when isolation cannot be established, the provider falls back to an actual
  copy or stream.

Decision: yes. Treat isolation as a correctness precondition, not a
performance preference. Hardlinking may still be used inside a provider-owned
immutable namespace whose mutation rules are enforced.

Status: confirmed by the user.

### 51. Transactional caller-file materialization

Should a caller file target always prepare into target-controlled staging on the
destination filesystem and publish only at commit?

The target may populate staging by reflink, native copy, or bounded streaming.
Commit then uses the strongest supported atomic replace/create operation.
Until commit:

- an existing destination remains unchanged;
- a new destination is absent;
- cancellation or failure removes staging; and
- no reader of the final path observes partial bytes.

If the destination backend cannot provide the requested atomic create/replace
semantics, target creation or preflight fails rather than silently weakening the
contract.

Decision: yes. Preserve transactional visibility for file
materialization even when the content transfer itself is streamed or optimized.

Status: confirmed by the user.

### 52. Direct client-to-provider transfer

Should pre-signed URLs or other direct client-to-S3/SMB transfer mechanisms
remain outside the required artifact persistence contract?

The required path remains:

- callers provide or consume a transfer source through the application/service
  boundary;
- the provider may use native backend operations internally; and
- bounded-memory streaming is always available.

A future delegated-transfer layer may issue time-bounded provider access and
then finalize validation, semantic fingerprinting, commit, and claims. Such a
layer must solve authorization and lease lifetime explicitly and cannot treat a
successful client PUT as an automatically committed artifact.

Decision: yes. Keep direct client delegation out of this core design while
leaving the staging/commit model capable of supporting it later. Do not expose
pre-signed URLs or backend credentials through ordinary artifact handles.

Status: confirmed by the user.

### 53. Explicit upload-grace claim management

Should the upload result contain an opaque claim/upload ID that authorizes
explicit release and policy-bounded renewal of its pre-submission grace claim?

Conceptually:

```text
DurableArtifactUploadResult
  artifact_instance
  upload_claim_id
  retained_until
  representation_descriptor
  byte_length
  byte_digest

release_upload_claim(upload_claim_id)
renew_upload_claim(upload_claim_id, desired_ttl)
```

Release or expiry affects only that claim and never deletes content protected by
another claim or active reservation. Renewal is constrained by provider policy
and returns the effective deadline. Durable task submission uses the artifact
locator and creates its own task claim; it does not need to consume the upload
claim ID.

Decision: yes. The short TTL remains the crash-safe fallback, while
explicit release reduces abandoned storage and bounded renewal supports callers
that legitimately need more preparation time.

Status: confirmed by the user.

### 54. Download identity and retention metadata

Should a successful download/open result report both the requested locator and
the concrete resolved instance, plus the applicable claim deadline when one
exists?

Conceptually:

```text
DurableArtifactDownload
  requested_locator: a-instance | a-task
  artifact_instance: a-instance
  retained_until?
  representation_descriptor
  byte_length
  byte_digest
  source
```

The active read reservation guarantees the opened source remains readable until
closed even if `retained_until` passes during transfer. It does not silently
extend the longer-lived claim. When a direct `a-instance` is opened and several
claims exist, the service may report the effective known retention horizon or
omit it if the caller is not authorized to inspect claim details.

Decision: yes. Preserve both task-relative and concrete identity in the
result and expose useful retention information without confusing it with the
read reservation.

Status: confirmed by the user.

### 55. One reservation per download handle

Should each successful download/open result own one read reservation and permit
at most one active byte stream at a time?

The source may be reopened sequentially while the handle remains valid when its
provider capability allows it. Concurrent full or range reads use independent
download opens, each with its own reservation and cancellation lifecycle.

Closing the download handle closes/cancels its active stream and releases the
reservation. Reaching normal end-of-stream may automatically close a
single-use download handle, while explicit close remains required as the
reliable cleanup operation.

Decision: yes. Use one reservation per download handle rather than a
handle that owns an unbounded tree of child streams. Independent opens make
parallel range delivery possible without ambiguous resource ownership.

Status: confirmed by the user.

### 56. Explicit download-handle closure

Should the read reservation remain owned by the download handle until explicit
close, rather than being released automatically whenever one opened stream
reaches EOF?

Normal EOF closes that stream but leaves a reopenable handle valid. Explicit
`close`, cancellation, or an async-context-manager exit releases the
reservation. Convenience APIs that promise one-shot consumption may
automatically close the handle after EOF. Stale-reservation recovery remains the
backstop for abandoned handles.

Decision: yes. Keep core ownership explicit and consistent with existing
artifact source-handle lifetime. Bindings can provide safe one-shot helpers for
HTTP responses and simple downloads.

Status: confirmed by the user.

### 57. Upload consumes the transfer-source handle

Should invoking per-artifact upload transfer ownership of the supplied transfer
source handle to the upload operation for every terminal outcome?

The service must close and release the handle on success, failure, or
cancellation. This consumes the handle object but does not imply destructive
ownership of its backing content:

- preserve-only file/object sources keep their backing content;
- one-shot streams are exhausted or closed;
- consumable backing resources follow the prepare-time ownership handoff from
  decision 7; and
- callers that need another transfer create another handle or use a separate
  reopenable source.

Bindings without language-enforced moves must reject later reuse of the
consumed handle.

Decision: yes. Make upload ownership explicit and total so cleanup does
not depend on caller behavior and one-shot/consumable semantics remain honest.

Status: confirmed by the user.

### 58. Same fingerprint cannot overwrite different valid bytes

Should cache publication of an already existing semantic artifact fingerprint
be idempotent only when the canonical representation is compatible and
byte-identical?

The consequences are:

- recomputation producing the same fingerprint and representation digest reuses
  the existing immutable object/entry;
- producing different valid representation bytes for the same fingerprint is a
  nondeterminism, codec-canonicalization, or fingerprint-integrity conflict;
- `FORCE_WRITE` may force stage execution and repair an invalid, corrupt, or
  missing cache record, but must not silently replace a different valid
  representation under the same semantic identity; and
- cache and durable persistence enforce the same `a-instance` immutability rule.

Decision: yes. Refine the current force-write/overwrite documentation to
separate “force recomputation” from “permit conflicting identity overwrite.”
This makes cache results safe to promote into durable persistence.

Status: confirmed by the user.

### 59. Trusted promotion of an existing committed instance

Should a validated committed cache or durable source be promotable into another
store without recomputing its semantic fingerprint from content?

The fast path requires:

- a trusted committed source record with a concrete `a-instance`;
- matching resolved artifact identity and canonical representation descriptor;
- validated length and representation digest;
- an active source read/object reservation; and
- exact-byte-preserving object attach, native copy, or stream transfer.

When cache and durable records share the same representation-object service,
promotion can be an atomic durable-record/object-reference/claim transaction
with no byte movement. Untrusted uploads still undergo canonical validation and
service-derived fingerprinting.

Decision: yes. Preserve already validated semantic identity across
trusted committed-store transfers and avoid unnecessary decode/fingerprint
passes.

Status: confirmed by the user.

### 60. Shared-object corruption is global

When a representation object is detected as corrupt, should the object service
mark it unreadable for every referencing cache and durable record?

The behavior would be:

- new opens through any semantic record fail with structured corruption;
- active readers fail when their provider detects the mismatch;
- cache invalidation may detach cache records but cannot delete or repair over
  durable references;
- provider-level replica repair may restore the exact object under the same
  digest only after full validation;
- re-uploading or recomputing exact canonical bytes may repair the object
  through a controlled object-service operation; and
- semantic records and claims can remain for diagnosis/recovery rather than
  being silently rebound to different bytes.

Decision: yes. Corruption belongs to the shared physical object layer and
must not be scoped to whichever higher-level module noticed it first.

Status: confirmed by the user.

### 61. One finite byte sequence per representation

Should each canonical artifact representation be one finite ordered byte
sequence with one length and digest?

Directory trees, datasets, or compound artifacts would use a codec-defined
container or manifest representation. That representation may itself be
generated and consumed incrementally, but the core transfer plane would not
expose filesystem entries, symlinks, sparse extents, or multiple S3 objects as
artifact-level primitives.

Providers remain free to shard, compress, or deduplicate the byte sequence
physically behind the logical representation-object boundary.

Decision: yes. Keep one logical byte sequence as the portable contract.
It maps cleanly to HTTP bodies, files, SMB streams, and object-store objects.
Native directory-tree semantics would greatly complicate portability and can be
introduced later through a versioned compound representation if required.

Status: confirmed by the user.

### 62. Dedicated artifact-persistence service

Should upload, download/open, and upload-claim management belong to one
dedicated artifact-persistence service exposed by the configured runtime?

Conceptually:

```text
runtime.artifacts.upload(...)
runtime.artifacts.inspect(...)
runtime.artifacts.open(...)
runtime.artifacts.await_open(...)
runtime.artifacts.release_upload_claim(...)
runtime.artifacts.renew_upload_claim(...)
```

The exact names remain open. The service resolves artifact declarations through
the runtime's frozen manifest and delegates storage to the configured durable
provider. Durable request builders consume returned locators but do not perform
I/O. Durable task submission and execution use the same service internally for
claims and output publication.

Decision: yes. Replace the current cache-oriented
`runtime.publish_artifact(...)` sketch and whole-request preparation service
with one cohesive artifact-persistence module reachable from the runtime,
without putting transport or HTTP concerns into it.

Status: confirmed by the user.

### 63. `a-instance` remains location-independent

Should `a-instance` remain a semantic locator without durable-store, cache,
filesystem, SMB, bucket, or region identity embedded in it?

The configured artifact-persistence service resolves the locator within its
storage scope. The same semantic locator may be present in several stores, and a
service may report it unavailable locally. Moving or replicating content does
not change artifact identity.

Provider/object locators and native-transfer offers remain opaque internal
capabilities. If deployments later need explicit federation or tenant/storage
scope, that scope should be represented at the service/authentication boundary
or through a separately designed locator context rather than appending physical
addresses to `a-instance`.

Decision: yes. Preserve location-independent semantic locators and keep
physical resolution in the service.

Status: confirmed by the user.

### 64. Raw download survives manifest evolution

Should primitive canonical-representation download rely on committed artifact
metadata rather than require the current runtime manifest to contain a
compatible declaration?

The behavior would be:

- upload requires a current manifest-known resolved declaration so content can
  be validated and fingerprinted;
- committed durable records preserve artifact identity and full representation
  descriptor;
- raw download/open by `a-instance` or resolved `a-task` can stream those bytes
  after store-level validation even if the current runtime lacks the old codec;
- typed decoding or use as an input to a new task requires a compatible current
  declaration/codec and fails clearly when unavailable; and
- no current codec is allowed to reinterpret stored bytes under an incompatible
  representation version.

Decision: yes. Keep durable byte retrieval available across manifest
evolution while requiring explicit codec compatibility for typed semantic use.

Status: confirmed by the user.

### 65. Original filenames are not artifact-instance metadata

Should caller-supplied original filenames remain optional contextual metadata,
not authoritative metadata on a deduplicated `a-instance`?

The committed instance retains its canonical MIME type/representation descriptor
and preferred extension. A download service may derive a deterministic safe
suggested filename from artifact identity, fingerprint, and extension. A
transport or application may preserve a caller label on its own upload claim or
request record when needed.

Two uploads of the same semantic instance with different local filenames
therefore do not conflict or mutate the durable record. Filenames never affect
semantic fingerprinting, representation-object identity, or path construction
without sanitization.

Decision: yes. Keep original names out of immutable instance identity and
storage layout; treat them as optional presentation context.

Status: confirmed by the user.

### 66. Authorization scope is separate from locator identity

Should artifact records, task bindings, claims, and opens be scoped by the
configured service's authorization/storage namespace even though
`a-instance` remains location-independent?

The consequences are:

- possession or guessability of an `a-instance` does not authorize inspection,
  claim renewal, or download;
- an authorized service scope may report an instance unavailable without
  revealing whether identical content exists elsewhere;
- physical representation-object deduplication across security scopes is
  permitted only as a provider policy and must not make cross-scope records,
  metadata, timing guarantees, or claim control visible; and
- task-relative `a-task` resolution is authorized against its task namespace.

Decision: yes. Keep authorization context at the service boundary and
semantic identity in locators. The HTTP authentication mechanism remains out of
scope, but the persistence contract must require authorization before
resolution or transfer.

Status: confirmed by the user.

### 67. Atomic durable submission input claiming

Should durable task acceptance atomically validate and attach task claims to
every referenced input artifact before allocating/accepting the task?

The acceptance transaction must ensure:

- every input `a-instance` exists, is committed, uncorrupted, authorized, and
  compatible with the current request/manifest;
- every required task claim is attached for the active/recovery lifetime;
- no artifact can be garbage-collected between validation and claim attachment;
  and
- if any input or claim operation fails, no durable task is accepted and no
  partial task claims remain.

The upload grace claim may expire concurrently; successful atomic task claim
attachment wins, otherwise submission fails as unavailable and the caller may
renew/re-upload as appropriate.

Decision: yes. Make all-input claim acquisition part of the existing
atomic durable submission boundary.

Status: confirmed by the user.

### 68. Default task artifact-retention TTL

When a `DurableRunRequest` omits its task-scoped artifact TTL, should submission
use a configured durable-artifact default?

The service validates an explicit or defaulted TTL against provider policy and
returns the effective value in the submission receipt. This default is distinct
from:

- the short upload-grace default;
- cache TTL defaults;
- active/recovery deadline; and
- task-record retention.

Decision: yes. Allow omission with a clearly configured artifact
retention default, then surface the effective duration. Do not infer it from
other TTL settings.

Status: confirmed by the user.

### 69. Distinct retention fields in submission receipts

Should `DurableSubmissionReceipt` explicitly distinguish task-record retention
from task artifact retention?

Conceptually:

```text
DurableSubmissionReceipt
  task_id
  output_artifacts: list<a-task>
  completion_deadline
  task_record_retention_duration
  artifact_retention_duration
```

The artifact `retained_until` deadline is not present yet because decision 30
measures it from the eventual terminal timestamp. Terminal task/output state can
report the resulting absolute deadline.

Decision: yes. Rename the current ambiguous
`retention_duration_seconds` field and expose both effective policies
separately.

Status: confirmed by the user.

### 70. Durable request inputs use concrete instance locators

Should `DurableRunRequest` accept only concrete `a-instance` locators as
supplied inputs and rename `include_cached_input(...)` accordingly?

Recommendation:

```text
include_artifact_instance(a-instance)
```

or an equivalently clear final name.

An `a-task` may be inspected or awaited through the artifact-persistence
service, after which its resolved `a-instance` can be included. Accepting a
pending `a-task` directly would introduce durable task-to-task dependency and
waiting semantics into submission, which are outside this transfer design.

Decision: yes. Remove cache-specific naming and require concrete input
identity at atomic submission.

Status: confirmed by the user.

### 71. Cache I/O is not the durable artifact API

Should direct cache I/O cease to be the public upload/download path for durable
artifacts?

The cache provider still needs internal operations to:

- resolve, reserve, read, and write reusable execution entries;
- open a validated cache source by semantic instance identity when available;
- ingest from or expose a canonical transfer source;
- promote a trusted cache hit into durable persistence; and
- support diagnostics, invalidation, recovery, and garbage collection.

However, independent caller upload, durable claims, `a-task` resolution, and
download belong only to the artifact-persistence service. Cache eviction must
never determine whether that service reports a durable instance available.

Decision: yes. Rewrite the cache spec's “Direct Cache I/O” and explicit
ingestion sections as internal cache/provider transfer capabilities. Remove
cache-oriented durable upload examples and terminology rather than preserving a
second compatibility surface.

Status: confirmed by the user.

### 72. Representation store as an internal provider boundary

Should the immutable byte-object layer be specified as an internal
`RepresentationStore`-style provider contract used to compose cache and durable
persistence, but not exposed through the application runtime API?

This contract owns:

- transactional object preparation, commit, and abort;
- byte-addressed lookup;
- validated length/digest metadata;
- opaque native-transfer offers;
- temporary read/write reservations;
- semantic-record reference attachment/detachment; and
- object recovery and garbage collection.

Cache and durable artifact providers may share one implementation or use
separate implementations. Application callers interact only with artifact,
request, and task services.

Decision: yes. Make the object layer a real binding/provider seam so the
file-cache example, future SMB provider, and S3-like provider can implement it
coherently, while keeping it private from ordinary artifact consumers.

Status: confirmed by the user.

### 73. Explicit provider composition and namespace ownership

Should cache and durable providers share a `RepresentationStore` only by being
explicitly composed with the same provider instance/configured storage
namespace?

The runtime builder must:

- avoid inferring sharing merely because paths, buckets, or endpoints appear
  equal;
- reject configurations where independent object coordinators claim the same
  physical namespace;
- ensure one authoritative reference/GC domain per representation namespace;
- validate that all workers use compatible store identity and metadata schema;
  and
- fail fast on conflicting or ambiguous ownership.

Decision: yes. Make storage topology explicit and validate it during
runtime construction. This follows the codebase's pre-stable preference for
clear ownership over compatibility merging.

Status: confirmed by the user.

### 74. Idempotent transactional target finalization

Should transfer targets use a single-use preparation state machine with
idempotent terminal finalization?

Conceptually:

```text
OPEN -> PREPARED -> COMMITTED
                  \-> ABORTED
```

- `prepare_from` succeeds at most once and cannot be reused with new content;
- repeated `commit` after a successful/ambiguously reported commit returns the
  same committed outcome;
- repeated `abort` after abort succeeds as cleanup;
- abort never rolls back an already committed artifact and reports the
  committed outcome;
- commit after a confirmed abort fails as terminally aborted; and
- provider recovery can query/reconcile the transaction ID after process or
  network failure.

Decision: yes. Make terminal operations idempotent so filesystem, SMB,
and object-store providers can safely reconcile ambiguous completion without
duplicating publication or deleting committed content.

Status: confirmed by the user.

### 75. Leased operation reservations and crash recovery

Should in-progress representation writes and read reservations use renewable
leases with provider-configured stale recovery?

The provider must:

- heartbeat active long-running streams/native operations;
- distinguish operation leases from artifact retention claims;
- reconcile an expired write lease as committed, abortable staging, or orphaned
  physical work using its transaction record;
- release expired read protection only after the reader lease is stale;
- notify cache/store waiters after recovery where applicable; and
- ensure recovery itself is concurrency-safe across workers.

Lease duration and heartbeat frequency are operational configuration, not
artifact API parameters. A slow but healthy transfer must keep its lease alive.

Decision: yes. Extend the cache reservation recovery model to the shared
representation and durable-store operations rather than inventing permanent
locks or relying on process cleanup.

Status: confirmed by the user.

### 76. Optional upload-operation idempotency key

Should per-artifact upload accept an optional caller-supplied idempotency key so
a client can recover an ambiguously lost success response without retransmitting
content?

The key would be scoped by authorization namespace and upload operation. The
service records:

- the resolved artifact identity and compatible request metadata;
- active, terminal success, or terminal failure state;
- the successful upload result and grace claim; and
- an expiry at least sufficient for the upload retry window.

Retrying the same compatible operation returns or awaits the same outcome.
Reusing the key with conflicting artifact or representation metadata fails.
This is not resumable upload: if the first attempt genuinely failed before
commit, a new attempt still streams from the beginning.

Decision: yes. Add optional operation idempotency independently of
content deduplication and resumable sessions. It is especially valuable for
large web uploads with ambiguous response delivery.

Status: confirmed by the user.

### 77. Initial representation digest algorithm

Should the initial canonical representation digest be BLAKE3-256 in an
explicitly versioned digest namespace?

Conceptually:

```text
digest_algorithm = blake3-256
digest_version = 1
digest = ...
byte_length = ...
```

Providers may also retain native checksums, but the shared representation key
and cross-provider validation use the specified digest. A future algorithm
change creates a new namespace/version and never reinterprets existing keys.

Decision: yes. Reuse the repository's existing BLAKE3 convention and
version the digest contract from the start. This is fast for streamed local
files while remaining backend-independent.

Status: confirmed by the user.

### 78. Single contiguous half-open byte ranges

Should the optional range-open capability accept one contiguous half-open range
`[start, end)` over the canonical representation?

Rules:

- offsets are zero-based bytes;
- `0 <= start <= end <= byte_length`;
- an omitted end means `byte_length`;
- invalid or unsatisfiable ranges fail before streaming;
- the returned stream yields exactly `end - start` bytes;
- the full-representation digest remains metadata but is not claimed to verify
  an isolated partial range; and
- multipart/multi-range response framing belongs to a transport layer using
  several independently reserved opens.

Decision: yes. Use one unambiguous range primitive and keep HTTP multipart
semantics out of the persistence contract.

Status: confirmed by the user.

### 79. Minimal artifact-persistence operation set

Should the normative service behavior be organized around this minimal
operation set, while concrete binding names may vary idiomatically?

```text
upload(
  resolved_artifact,
  consumed_transfer_source,
  desired_grace_ttl?,
  idempotency_key?,
) -> DurableArtifactUploadResult

inspect(a-instance | a-task) -> ArtifactAvailability

open(
  a-instance | a-task,
  byte_range?,
) -> DurableArtifactDownload | Pending | Unavailable

await_open(
  a-instance | a-task,
  byte_range?,
  cancellation/deadline?,
) -> DurableArtifactDownload | Unavailable

release_upload_claim(upload_claim_id)
renew_upload_claim(upload_claim_id, desired_ttl) -> retained_until
```

`open` never waits silently. `await_open` differs only for a pending `a-task`;
concrete available instances follow the same validation and reservation path.
Transport adapters and path/file convenience constructors live in bindings,
not in the core service contract.

Decision: yes. Specify these behaviors and result variants without
overcommitting the cross-language design to exact method spelling.

Status: confirmed by the user.

### 80. Availability is a discriminated lifecycle state

Should inspection and non-waiting open use a discriminated availability state
for expected artifact lifecycle outcomes?

Conceptually:

```text
ArtifactAvailability
  Pending(task/output status)
  Available(resolved a-instance, representation metadata, retained_until?)
  Unavailable(reason, resolved a-instance?)
```

`Unavailable` includes normal terminal facts such as failed/cancelled output,
expired content, or a resolved instance that is no longer retained. A malformed
locator, unauthorized operation, invalid range, provider failure, or contract
violation remains a structured operation failure rather than a lifecycle
variant.

An `a-instance` inspection is never `Pending`. `open` returns a reserved
download only from `Available`; `await_open` waits through `Pending` and returns
when it becomes `Available` or terminally `Unavailable`.

Decision: yes. Use an explicit state value for polling and task-relative
resolution, consistent with the run-state design.

Status: confirmed by the user.

### 81. Cross-specification update scope

When implementation of the agreed design begins, should the documentation
change update every authoritative surface affected rather than only the upload
and download files?

At minimum, the expected scope is:

- rewrite `01-runtime/03-durable-artifacts-upload-surface.md` around
  per-artifact upload and grace claims;
- author `01-runtime/05-durable-artifact-download-surface.md`;
- revise `02-artifacts/02-artifact-io-surface.md` for value/transfer
  capabilities and the streaming codec contract;
- substantially revise `03-bindings/02-cache-management.md` for transfer-based
  cache I/O, immutable representation references, promotion, and force-write
  integrity;
- specify the internal representation-store and durable-persistence provider
  boundaries in the appropriate bindings/spec location;
- update runtime builder capability/configuration requirements;
- update durable request building, artifact TTL, locator input naming, run
  submission receipts, task output retention, and `a-task` resolution;
- update structured failure documentation and terminology; and
- repair overview/examples that still describe cache publication as durable
  upload.

Decision: yes. Treat consistency edits as part of the design change and
remove superseded cache-oriented or whole-request paths rather than leaving
transitional documentation.

Status: confirmed by the user.

### 82. Separate binding specs for representation and durable persistence

Should the binding-layer design use two focused new specifications rather than
placing all persistence behavior in cache management?

Recommended split:

- `03-bindings/03-representation-store.md` — immutable logical byte objects,
  transactional preparation, references, reservations, native offers,
  integrity, recovery, and object garbage collection;
- `03-bindings/04-artifact-persistence.md` — durable `a-instance` records,
  upload/task claims, `a-task` bindings, publication/open semantics, provider
  capabilities, and durable-record recovery.

`02-cache-management.md` would consume the representation-store contract for
cache entries, while runtime upload/download specs consume the durable artifact
persistence contract.

Decision: yes. Keep the content-object layer, durable semantic store, and
execution cache as three deep modules with explicit relationships. Combining
them in the cache document would obscure the separation established in decision
1.

Status: confirmed by the user.

### 83. Transfer chunks are non-semantic

Should chunk boundaries and sizes remain entirely non-semantic and
provider-selected?

The transfer contract carries an ordered byte sequence with backpressure.
Sources and targets may split, coalesce, buffer, align, or bypass chunks to suit:

- filesystem and block-device alignment;
- kernel copy/sendfile-style operations;
- SMB request sizing;
- object-store multipart thresholds;
- memory budgets; and
- transport flow control.

No digest, fingerprint, range, retry, or artifact identity depends on chunk
boundaries. Implementations must remain within configured memory/cancellation
bounds, but the common artifact API does not prescribe a universal chunk size.

Decision: yes. Keep chunks as private flow-control units so providers can
use storage-native optimizations without changing observable artifact
semantics.

Status: confirmed by the user.

### 84. Provider conformance scenarios

Should the new provider specifications define shared conformance scenarios that
every representation-store and durable-persistence implementation must satisfy?

The scenarios should cover at least:

- one-shot large upload with bounded memory and backpressure;
- late-bound semantic fingerprint and idempotent publication;
- preserve versus consumable source ownership;
- native adoption/copy and streaming fallback equivalence;
- create-if-absent races and conflicting representation detection;
- atomic record/reference/claim attachment;
- read reservation versus garbage collection;
- range reads and concurrent independent reservations;
- cancellation and crash recovery during transfer and commit;
- stale lease recovery;
- shared-object deduplication and corruption quarantine;
- cache-hit promotion to durable output without re-encoding;
- upload-grace expiry versus atomic task submission;
- task TTL conversion at terminalization; and
- provider capability rejection for insufficient durability or visibility.

Decision: yes. Specify reusable behavioral tests/invariants without
prescribing backend implementation. The current local file providers can serve
as the first executable reference.

Status: confirmed by the user.

## Shared Understanding and Authorization

After confirming decisions 1–84, the user confirmed that the interview had
reached shared understanding and authorized the specifications to be updated
from this decision record.
