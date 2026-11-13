# Durable Artifact Upload

## Purpose

Durable artifact upload turns one finite byte sequence into one immutable entry in the configured shared artifact cache and returns its [[../02-artifacts/01-artifact-model-surface#Content|ContentId]].

[help](../02-artifacts/01-artifact-model-surface#Content)

The runtime stores a faithful copy of the supplied bytes without interpreting, transcoding, canonicalizing, or validating their artifact representation. 

The contract supports three complementary ingress paths:

| Path | Starting condition | Transfer lifecycle | Typical use |
| --- | --- | --- | --- |
| **Direct publication:** | the caller already has a finite byte value, file, or one-shot byte source. | One operation transfers and publishes the artifact. | Local files, SDK calls, reliable bounded HTTP uploads. |
| **Upload session:** | the caller already has one finite logical object, but transfer may be long or resumable. | Begin a session, upload bound numbered parts through Chunky, then explicitly commit. | Multi-gigabyte files, unreliable networks, and request-size-limited HTTP paths. |
| **Live capture:** | the final byte sequence is generated over time and its length is not known when capture begins. | Begin capture, append strictly ordered client-sized segments, explicitly seal, then publish. | Live recordings that span multiple requests. |

All three paths converge on the same common publication, content-identity, retention, claim, and durable-request semantics. They differ only in how provisional bytes enter the runtime before immutable publication.

An open upload or live-capture session is not an artifact and is never bound directly as a stage input. Only a successfully committed finite byte sequence becomes an uploaded artifact.

## Artifact Upload Capability

The artifact upload capability encompasses the control plane for every ingress path.

It coordinates:

- provisional quota and provider-owned staging;
- `ContentId` computation or verification;
- immutable cache publication;
- upload-grace claims;
- configured retention policy;
- recovery and cleanup; and
- authoritative recovery of ambiguous session-operation results.
- (future) authorization and policy;

## Artifact Upload Lifecycle

Every upload method follows the same conceptual lifecycle:

```text
Provisioning -> Uploading -> Finalizing -> Grace -> Retention
```

These phases define the responsibility and visibility boundaries shared by direct publication, multipart upload, and live capture. Direct publication normally combines the first three phases in one operation; multipart upload and live capture expose durable provisional state across operations. Combining phases must not weaken their requirements.

### Shared boundaries

Before finalization, upload state is operational and provisional: it is not an artifact and cannot be resolved as a cache hit. Provisional operations and storage must be attributed to one ingress operation or durable session. Internal provider operations must remain scoped to that operation's or session's provisional destination.

Provider identifiers, paths, credentials, source bytes, and internal write units must not appear in results, progress, or failure diagnostics.

> Future authorization contracts, authorization, principals, tenants, ownership checks, tenant-attributed quota, cross-tenant isolation, and their related issue codes are outside the MVP. Every reference to those concepts in this document describes future behavior and is non-normative for the MVP. A future multi-tenant implementation may permit cross-tenant physical deduplication only under policy governing existence disclosure, encryption, authorization, and retention; one tenant must not otherwise learn whether another tenant has matching content.

### Provisioning

Provisioning validates every applicable option that does not require payload bytes, reserves a provisional quota, and creates any required session or staging state. A future authorization-capable implementation also authorizes the upload intent and attributes quota to the applicable tenant.

Provisioning assigns `upload_session_id` / `capture_session_id` to durable provisional state. These identifiers support status inspection and mutation recovery, but are not content identity.


### Uploading

Uploading receives bytes into provider-owned provisional storage through one of the following ingress methods:
- a complete finite source, 
- a non-sequential numbered multipart upload
- a sequential live capture stream 

#### Integrity assertions

`expected_content_digest` is an integrity assertion over the complete byte sequence accepted by the operation that declares it:
 - the direct source, 
 - assembled multipart artifact when declared at begin, or 
 - one numbered part when declared during part upload. 
 
 Complete-artifact and part digests have distinct scopes and do not replace one another.

> Note: Live capture does not accept an expected digest because its byte sequence is not known when capture begins; the runtime still computes the committed `ContentId` from the completed capture.

```text
ContentDigest
  algorithm
  value
```

The required integrity-digest algorithm is BLAKE3-256. A mismatch fails the scoped operation. Direct publication exposes no committed artifact; a mismatched part remains unaccepted and may be retried from byte zero; an assembled-artifact mismatch fails to commit with `ARTIFACT_UPLOAD_DIGEST_MISMATCH`, publishes nothing, and makes the session terminal because accepted parts cannot be replaced.

#### Provider staging and recovery

- Direct-publication staging is operation-scoped and non-resumable.
- Multipart and live-capture staging are durable provisional state identified by their session IDs. 
 
 A _filesystem_ based cache provider may stage a temporary file or immutable generation and atomically publish metadata, including by same-filesystem rename. 
 
 An _object-store_ provider may retain a session- or generation-named object and atomically map the final `ContentId` to that immutable generation; physical storage names need not equal semantic cache identity.

The provider must persist enough state to distinguish active, complete-but-unpublished, committed, abandoned, timed-out, failed, and ambiguous operations. It must eventually clean up abandoned or timed-out state, must not infer completeness from a data object alone, and must never promote uncertain bytes to a cache hit.

### Finalizing

Finalizing seals the finite representation; verifies active and transfer state, asserted length, integrity, and content identity; then commits the immutable cache entry and upload-grace claim as one logical publication.

It publishes authoritative metadata or a completion pointer atomically, marks the ingress operation committed, and releases or converts provisional resources. Only successful finalization makes an artifact visible to task submission or cache lookup.

Publication creates at most one immutable entry for a `ContentId` and never overwrites its existing byte sequence.

#### Content identity and existing entries

The `ContentId` is the committed cache identity of the complete byte sequence and is distinct from session IDs and integrity digests. When the caller supplies no `content_id`, the runtime computes one over the complete bytes using the runtime's default content-ID algorithm, incrementally where possible and without requiring a second read of a one-shot source.

The optional caller-provided `content_id` supplies the complete identity, including its algorithm and value. It may preempt the runtime-computed identity only under explicit trust policy for that caller and ingress path, and may be trusted without hashing or byte comparison only when that policy permits it.

The independent `expected_content_digest` is only an integrity assertion over the received bytes. It must be verified when supplied and never selects a `ContentId` or authorizes a caller-defined identity.

If a runtime-computed or verified caller-provided `ContentId` already identifies a valid entry, publication reuses it and may discard duplicate provisional bytes. A trusted caller-provided `ContentId` may reuse an existing entry without reading the new bytes only when policy explicitly permits that path.

#### Results

Every successful publication operation returns this result:

```text
DurableContentUploadResult
  content_id: ContentId
  grace_retained_until
  retention_deadline?
  byte_length: u64
```

**Where**
 - **`content_id`** identifies the complete byte sequence,
 - **`grace_retained_until`** is the effective absolute claim expiry, 
 - **`retention_deadline`** is the effective absolute retention expiry, and 
 - **`byte_length`** is the committed length. 

The effective retention deadline is established at publication and is never earlier than `grace_retained_until`. A non specified deadline will be determined by the runtime

Every publication operation returns the `DurableContentUploadResult`. If a session client loses a publication response, the session's status operation returns the authoritative publication result when the publication is committed.

### Grace

Grace is the short post-publication handoff period in which the upload-grace claim gives the caller time to submit work that references the artifact. 

The purpose of this is to mitigate long-term resource consumption for failed or interrupted task uploads. 

It begins only when publish-and-claim succeeds, is normally measured in minutes or hours, and is independent of session or capture duration.

**Grace Artifact Deletion**

If the claim reaches its expiry, the expiry event trigger a deletion of the uploaded artifact content. 

The runtime may adjust the requested grace TTL and returns its effective absolute deadline.

### Retention

Retention establishes the committed root's retention deadline. 

- When no retention beyond grace is requested, the effective retention TTL equals the effective grace TTL; 
- when a longer TTL is requested and permitted, the retention deadline may be later but must never be earlier than `grace_retained_until`. 

Provider policy may cap or adjust the request only within that lower bound. Retention is independent of provisional inactivity timeouts, downstream-artifact retention, task-input PERMISSIONS, and active-read reservations.

**Grace Claim Deletion**
Once the caller includes the returned `ContentId` in a [[../02-artifacts/01-artifact-model-surface#Supplied artifact binding|SuppliedArtifactBinding]] within a [[02-run-request-building-surface#Canonical durable request|DurableRunRequest]] or [[02-run-request-building-surface#Canonical durable request|RunRequest]], atomic task acceptance records the structurally valid binding and removes or converts the upload-grace claim for any present bound content before planning or execution.

> Note: Claim deletion is not contingent on artifact-schema validation or waiting for a stage to open the content.

Retention TTL was already established at publication and remains effective if the input is skipped or the task later becomes unplannable, cancelled, expired, or failed. 

Rejection before durable input bindings are structurally valid does not delete the claim.
**Reusing evicted Artifacts**

A run request may carry a `ContentId` for an upload that has previously been evicted or deleted, provided that the runtime determines that
- a cached downstream intermediates satisfy the requested work
- stages that would consume the uploaded root have downstream intermediates for all required outputs,
- and the root need not be present for any other reasion


## Upload Method: Direct Publication

### Purpose and Boundary

Direct publication is used when the caller already has one finite input source and can complete the transfer as one operation.

It is the simplest path for

- in-memory bytes;
- local files;
- SDK integrated streams;
- moderate uploads over reliable connections; and
- one-shot HTTP request bodies that can afford to restart from byte zero after interruption.

### Service Boundary

The runtime exposes separate operations for an in-memory byte sequence, a one-shot byte source, and a local file. The surface is transport-neutral; binding-specific request types and addressing metadata remain outside these objects.

**Every direct operation accepts:**

- **`desired_grace_ttl`**, which requests the upload-grace created only when publication succeeds; claim expiry deletes the artifact unless task acceptance has already deleted the claim;
- **`desired_retention_ttl`**, an optional request for retention beyond grace; effective retention is established at publication and is never shorter than the effective grace TTL;
- **`content_id`**, an optional caller-defined identity, including algorithm and value, governed by caller-provided content-ID policy; and
- **`expected_content_digest`**, an optional integrity assertion over the operation's complete input byte sequence.

`content_id` and `expected_content_digest` are independent. The former selects the committed cache identity when policy permits; the latter only validates that the bytes were received correctly.

#### Preflight checks
Before accessing the source, each operation performs all applicable checks that do not require reading bytes:

- provider durability, visibility, retention, and recovery capability;
- known byte length against configured limits;
- Provisional quota;
- caller-provided content-ID policy and algorithm support;
- expected digest algorithm support;
- requested TTL policy.

A preflight failure publishes no artifact. It does not consume caller-owned bytes or a preserved file. When the runtime owns a byte-source adapter, it closes that adapter without requesting source bytes.

Successful byte and byte-source operations return the common `DurableContentUploadResult`.

#### Common failure issues

These issues are documented once here and apply to the operations named in each entry.

##### All direct-publication operations

- **`PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** The caller is not authorized to publish the artifact or use the requested publication policy.
  - **Retry:** `retryable: false`. A new call is appropriate only after authorization or policy changes.
- **`OPERATION_CANCELLED`**
  - **Cause:** Chunky observes cancellation before the operation returns its ordinary result.
  - **Retry:** `retryable: true` only when Chunky can prove publication did not commit. A partial source restarts from byte zero with a new source instance; an unresolved publication returns `false`.
- **`ARTIFACT_STORAGE_FAILURE`**
  - **Cause:** Chunky cannot read or durably update storage required by the operation.
  - **Retry:** `retryable: true` only for a transient failure when Chunky can prove that a new attempt is safe. `retry_after_milliseconds` is included when known.

##### Preflight and publication policy

Applies before source consumption whenever the relevant fact is known, and during streaming when a limit can be established only from observed bytes.

- **`ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED`**
  - **Cause:** The known or observed complete byte length exceeds the configured artifact-ingress or provider limit for direct publication. Publication does not occur.
  - **Retry:** `retryable: false` for the same input and method. The caller must reduce the artifact or use a compatible multipart upload path.
- **`ARTIFACT_UPLOAD_STORAGE_PROVIDER_CAPABILITY_UNSUPPORTED`**
  - **Cause:** The configured provider cannot satisfy the requested durability, visibility, retention, recovery, or publication behavior.
  - **Retry:** `retryable: false`. The caller or operator must change the request or provider configuration.
- **`ARTIFACT_STORAGE_PROVIDER_CAPACITY_EXCEEDED`**
  - **Cause:** The storage provider cannot currently reserve, stage, or publish the required capacity.
  - **Retry:** `retryable: true` only when publication is known not to have committed. `retry_after_milliseconds` is included when the provider supplies a minimum delay.
- **`ARTIFACT_UPLOAD_DIGEST_UNSUPPORTED`**
  - **Cause:** The requested expected-content-digest algorithm is not supported by the runtime or selected provider path.
  - **Retry:** `retryable: false`. The caller must omit or change the assertion, or use a supporting configuration.
- **`ARTIFACT_UPLOAD_CONTENT_ID_INVALID`**
  - **Cause:** The caller-provided `ContentId`, including its algorithm or value, is malformed or incompatible with the selected publication path.
  - **Retry:** `retryable: false`. The caller must correct or omit `content_id`.
- **`ARTIFACT_UPLOAD_CONTENT_ID_NOT_TRUSTED`**
  - **Cause:** Policy does not permit this caller or ingress path to assign the requested `ContentId`.
  - **Retry:** `retryable: false`. The caller must omit `content_id` or obtain an applicable policy change.

##### Operations consuming a byte source

Applies to `publish_content_source`, `publish_content_file`, and bindings that adapt a request body into `ArtifactByteSource`.

- **`TRANSFER_SOURCE_INVALID`**
  - **Cause:** The supplied source fails or violates the `ArtifactByteSource` contract, or a file cannot provide a stable readable source under the declared ownership rules.
  - **Retry:** `retryable: false`. The caller must supply a new valid source; a one-shot source restarts from byte zero.
- **`TRANSFER_SOURCE_EXHAUSTED`**
  - **Cause:** A source with a known or asserted length reaches end-of-source before supplying the complete byte sequence.
  - **Retry:** `retryable: false`. The caller must provide a complete replacement source and restart from byte zero.
- **`ARTIFACT_UPLOAD_INPUT_FORM_UNSUPPORTED`**
  - **Cause:** The selected direct-publication operation or binding cannot accept the supplied input form.
  - **Retry:** `retryable: false`. The caller must choose a supported direct-publication operation or upload method.

##### Integrity and finalization

- **`ARTIFACT_UPLOAD_LENGTH_MISMATCH`**
  - **Cause:** A known or asserted complete length differs from the byte sequence observed before end-of-source. Publication does not occur.
  - **Retry:** `retryable: false`. The caller must correct the assertion or source and restart from byte zero.
- **`ARTIFACT_UPLOAD_DIGEST_MISMATCH`**
  - **Cause:** The complete observed byte sequence does not match `expected_content_digest`. Publication does not occur.
  - **Retry:** `retryable: false`. The caller must correct the bytes or digest assertion and begin a new logical attempt.
- **`ARTIFACT_UPLOAD_FINALIZATION_AMBIGUOUS`**
  - **Cause:** Chunky cannot prove whether an immutable publication was committed after accepting the complete source.
  - **Retry:** `retryable: false`. Direct publication has no durable operation identity through which to recover the result safely.


### Service Boundary: Publishing an in-memory byte sequence

`publish_content_bytes` publishes one already-materialized immutable byte value:

__Example:__

```python
upload = await runtime.publish_content_bytes(
    content_bytes=wav_bytes,
    desired_grace_ttl=timedelta(hours=2),
)
```

#### `publish_content_bytes()`

```text
runtime.publish_content_bytes(
  content_bytes,
  desired_grace_ttl,
  desired_retention_ttl?,
  content_id?,
  expected_content_digest?
) -> DurableContentUploadResult
```

- **`content_bytes`** is the complete finite source. Its in-memory length is authoritative for size checks.
  - The caller retains ownership of its byte value. It must not mutate shared backing storage while the operation is active.
  - The runtime may read the value incrementally but does not retain caller-owned memory after the operation finishes.

The operation verifies the expected digest when supplied, determines the complete content identity under caller-provided content-ID policy, publishes the immutable artifact, and returns the result.

### Service Boundary: Publishing a one-shot byte source:

`publish_content_source` publishes one finite sequential source whose bytes are read once under runtime-controlled read limits:

__Example:__

```python
upload = await runtime.publish_content_source(
    byte_source=source,
    total_byte_length=expected_size,
    expected_content_digest=expected_source_digest,
    desired_grace_ttl=timedelta(hours=2),
)
```

#### `publish_content_source()`

```text
runtime.publish_content_source(
  byte_source: ArtifactByteSource,
  total_byte_length?,
  desired_grace_ttl,
  desired_retention_ttl?,
  content_id?,
  expected_content_digest?
) -> DurableContentUploadResult
```

- **`byte_source`** is the complete `ArtifactByteSource` and is consumed at most once. After passing it to the operation, the caller must not read from or reuse it.
- **`total_byte_length`** is an optional assertion about the complete source length. A known value participates in preflight; the runtime always enforces observed-byte limits and fails on a final mismatch.

The runtime reads lazily and requests at most the number of bytes for which it currently has buffer capacity. It requests more only when provider staging is ready, preserving bounded memory and back pressure.

#### `ArtifactByteSource`
The provider-neutral source shape is conceptually:

```text
ArtifactByteSource
  read_up_to(max_byte_length):
    NonEmptyBytes | EndOfSource | ArtifactSourceError
```

The abstraction requires:

- The runtime chooses a positive `max_byte_length` for every read;
- a successful read returns between 1 and `max_byte_length` bytes;
- fewer bytes than requested do not indicate completion;
- `EndOfSource` is explicit and is the only outcome that seals the complete source;
- reads are ordered, sequential, and have exactly one consumer and at most one read in flight;
- `ArtifactSourceError` is a runtime-owned source error that terminates the source before completion; and
- cooperative cancellation is supplied through operation context rather than encoded as a read result.

Source read size and storage upload-part size are independent. The runtime may combine multiple source reads into one storage part. Callers cannot select storage part boundaries by controlling how bytes become available from the source.

`EndOfSource` allows digest verification and publication to continue. A source error, cooperative cancellation, or dropped consuming future before `EndOfSource` fails the publication and leaves incomplete staging unpublished. Dropping a future may defer physical cleanup, so staging must remain invisible and recoverable by cleanup without relying on asynchronous drop behavior.


**Rust:**
A Rust approximation is:

```rust
type ArtifactByteSource = Pin<Box<dyn tokio::io::AsyncRead + Send>>;
```

`AsyncRead` approximates this contract by using the runtime-supplied destination buffer as `max_byte_length` and a successful zero-byte read as `EndOfSource`. An implementation must not return a successful zero-byte read while source bytes remain.


**Python:**
A Python approximation is:

```python
from typing import Protocol

class ArtifactByteSource(Protocol):
    async def read_up_to(self, max_byte_length: int) -> bytes | None:
        """Return non-empty bytes, None at end-of-source, or raise ArtifactSourceError."""
        ...
```

`None` represents `EndOfSource`; `b""` is not a valid successful result. Source failures are raised as `ArtifactSourceError`. The runtime passes a positive `max_byte_length`, and the implementation must return no more than that many bytes.

**C ABI:**

A file-like C ABI can represent the source as opaque library-owned state and two callbacks. The runtime supplies the destination buffer, so it controls the maximum read size and no byte-buffer allocation crosses the library boundary.

```c
typedef struct ArtifactByteSource {
    void *context;

    int32_t (*read_up_to)(
        void *context,
        uint8_t *destination,
        size_t max_byte_length,
        size_t *bytes_read
    );

    void (*destroy)(void *context);
} ArtifactByteSource;
```
**`read_up_to`** returns:
- **`1`** for data, with `bytes_read` in `1..=max_byte_length`;
- **`0`** for `EndOfSource`, with `bytes_read` set to zero; or
- **`-1`** for `ArtifactSourceError`, with `bytes_read` set to zero.

The runtime calls `(read_up_to)(...)` sequentially and calls `destroy` exactly once when it has finished with the source. The library owns `context`; the runtime owns `destination`.

This is only the minimal shape. A production ABI should additionally define versioning, calling convention, detailed error retrieval, cancellation, symbol discovery, and protection against language-level unwinding across the C boundary.


### Service Boundary: Publishing a local file

`publish_content_file` opens and publishes one stable local file system path

__Example:__

```python
upload = await runtime.publish_content_file(
    path="input.wav",
    source_disposition="CONSUME",
    desired_grace_ttl=timedelta(hours=2),
    desired_retention_ttl=timedelta(days=1),
)
```

#### `publish_content_file()`

```text
runtime.publish_content_file(
  path,
  source_disposition? = PRESERVE,
  desired_grace_ttl,
  desired_retention_ttl?,
  content_id?,
  expected_content_digest?
) -> DurableContentUploadResult
```

- **`path`** identifies the complete source file. The runtime opens and closes any file handles it creates.
- **`source_disposition`** defaults to `PRESERVE`. `PRESERVE` leaves the source path caller-owned. `CONSUME` authorizes ownership transfer or post-commit removal.

> Caller Note: The caller must not modify, replace, lock, or remove the file while publication is active. The runtime records or validates the opened file's length and streams it with bounded memory and back pressure.

- For **`PRESERVE`**, publication never intentionally removes the source. 

- For **`CONSUME`**, the runtime preserves the source path until publication commits, then removes it or completes ownership transfer. 
  - Native adoption is permitted only when it preserves this caller-visible boundary. 
  - Failure to remove the source after a commit does not roll back publication.

If publication may have consumed or removed the source before the result was lost, the caller must not reopen the path as a retry. A new call is safe only after Chunky proves that the earlier publication did not commit and the source remains available.

### Service Boundary: HTTP Binding Recommendations

A direct HTTP binding should expose the raw request-body stream rather than collecting it into one byte array. Multipart form parsing is not required for the single-artifact contract.

Streaming provides bounded-memory transfer and back pressure.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/artifacts` | Stream one finite request body directly into immutable artifact publication and return `DurableContentUploadResult`. |

The request body is the content byte sequence. Binding-specific headers carry optional publication inputs such as `content_id`, `expected_content_digest`, `desired_grace_ttl`, and `desired_retention_ttl`. A successful response contains the common `DurableContentUploadResult`; failures use the common `ARTIFACT_UPLOAD` failure contract.

This is also the direct-publication path for a short live source that fits in one request, such as a sub-30-second IoT command capture. The HTTP handler streams the unknown-length request body into `publish_content_source` and returns `DurableContentUploadResult` after normal end-of-source and publication. It does not create a live-capture session, expose replay or resume, or require a separate completion request.

Unless policy accepts a caller-provided `content_id` without verification, Chunky computes or verifies the `ContentId` while consuming the request. A caller-provided `expected_content_digest` is available only when the caller knows it before sending or the binding reliably supports supplying it through request trailers; it is not required for one-request streaming publication.

The binding should:
-   preserve streaming back pressure;
-   avoid whole-body buffering;
-   enforce a route-specific maximum observed byte count;
-   configure that maximum no higher than any effective upstream request-body limit;
-   use `Content-Length` only as optional preflight and integrity information;
-   reject a known `Content-Length` that exceeds the permitted direct-publication size before consuming the body;
-   permit an unknown length when policy allows an unknown-length streaming request, while still stopping the source if the observed-byte limit is exceeded;
-   distinguish idle-transfer timeout from total request duration;
-   surface body-stream errors and handler cancellation without assuming that a disconnect after normal end-of-stream rolls back finalization; and
-   avoid application middleware defaults that reject otherwise valid bodies before the upload handler can stream them.


**Example HTTP binding:**

The request body is adapted into the runtime-owned byte-source type. The HTTP layer does not collect the body or create its own temporary staging file. The adapter may receive transport frames of arbitrary sizes, but the runtime reads through it using runtime-selected buffer sizes.

```rust
use futures_util::TryStreamExt;
use std::io;
use tokio_util::io::StreamReader;

pub fn direct_publication_routes(runtime: AppState) -> Router {
    Router::new()
        .route("/artifacts/upload/stream", post(publish_artifact))
        .with_state(runtime)
}

fn artifact_body_source(body: Body) -> ArtifactByteSource {
    let data = body
        .into_data_stream()
        .map_err(io::Error::other);
    Box::pin(StreamReader::new(data))
}

async fn publish_artifact(
    State(runtime): State<AppState>,
    headers: HeaderMap,
    body: Body,
) -> ApiResult<Json<DurableContentUploadResult>> {
    let options = parse_publish_content_options(&headers)?;
    let result = runtime
        .publish_content_source(
            artifact_body_source(body),
            options,
        )
        .await?;

    Ok(Json(result))
}
```

`parse_publish_content_options` maps caller-provided content ID, expected-content-digest, grace, and retention headers into `PublishContentOptions`; `Content-Length` is handled as optional request preflight. Observed-byte limits and idle-transfer timeouts wrap reads from the adapted source without materializing it.

A body-source error before `EndOfSource` fails the publication. Dropping the handler stops reading but does not guarantee that asynchronous abort code runs, so incomplete provider staging must remain unpublished and recoverable. A disconnect after `EndOfSource` may leave only the response outcome ambiguous.

#### Expected errors

HTTP bindings preserve the complete Chunky `Failure` envelope for runtime failures and apply the retry rules of the corresponding direct-publication operation. Malformed paths, headers, body framing, authentication middleware failures, upstream request-size rejection, and transport failures may be reported by the binding rather than as `ARTIFACT_UPLOAD`; callers must not infer byte acceptance or publication outcome from an HTTP status or a native transport error alone.

#### Large files

Direct publication should be used only when the complete artifact fits within the smallest request-body limit on the path between the caller and runtime.

For example, Cloudflare can apply a maximum body size to each proxied HTTP request and rejects an oversized request with `413 Payload Too Large` before the complete body reaches the origin.

Artifacts larger than that limit should use an **Upload Session** instead. The client transfers the artifact to Chunky using multiple independently bounded part requests, each comfortably below the upstream request limit, and then explicitly commits the completed session. Resume uses the authoritative accepted-part state reported by Chunky rather than client-estimated progress.


### Cancellation, retry, and progress

Cancellation before the complete source is durably staged stops further reads, fails the incomplete publication, and aborts staging where asynchronous cleanup can still run. Incomplete content remains unpublished even when physical cleanup is deferred.

After the complete source has been accepted, cancellation or caller disconnect does not roll back digest verification or commit. A lost response makes the result ambiguous; it does not make committed content uncommitted.

Direct publication cannot recover an ambiguous terminal result and never resumes a partially transferred source. When Chunky proves publication did not commit, bytes, byte sources, and files restart as new logical operations; a one-shot byte source restarts from byte zero with a new source instance.

The runtime does not expose durable direct-publication status. Bindings may report operation-local progress such as observed bytes, asserted total bytes, and finalization state through callbacks or events.


## Upload Method: Multipart Stream

### Purpose and Boundary
Upload Sessions exposes one client-facing transfer mechanism: a resumable multipart upload through Chunky. 

### Service Boundary
The mechanism transfers one finite artifact as independently retryable requests rather than one request containing the complete artifact. It is intended to:

- keep each request below configured client, proxy, edge, and server body-size limits;
- avoid restarting a multi-gigabyte transfer after a late network failure;
- bound the amount of data lost when one request is interrupted;

The complete artifact remains unpublished while the session is active. Successfully uploading every part is necessary but does not publish the artifact; publication requires an explicit commit.

**Process overview**

```text
begin_content_upload()
  -> upload_content_part(1)
  -> upload_content_part(2)
  -> ...
  -> upload_content_part(part_count)
commit_content_upload()
```

1. **Create the session.** The caller supplies the exact `total_byte_length` and session options. Chunky validates preflight policy, chooses one fixed `part_size`, derives `part_count`, and returns the session identity and current inactivity deadline.
2. **Upload the parts.** The caller sends each numbered part to Chunky. Every request contains one complete part. Parts may be sent sequentially or, concurrently and out of order.
3. **Recover after interruption.** The caller reads session status from Chunky and retransmits only parts not present in the authoritative accepted-part set. An incomplete part attempt restarts from that part's byte zero; there is no intra-part resume.
4. **Finalize the artifact.** After every part is accepted, the caller invokes `commit_content_upload`. Chunky verifies completeness, final integrity and identity, publishes the immutable artifact, creates the upload-grace claim, and returns `DurableContentUploadResult`.

The arrows show the logical lifecycle, not a required transfer order. Commit is the only step that requires all numbered parts to have been accepted.

#### Failure interface and retry rules

Every Chunky-originated failure from this service uses the universal [Failure Contract](07-failure-surface.md).

Callers use the issue `code`, typed context, and root `retry` directive for control flow; messages are diagnostic only. 

##### Retry Behaviour

A retry is safe only when `Failure.retry.retryable` is `true` and the operation-specific guidance below permits it. 

`retry_after_milliseconds`, when present, supplies the minimum wait defined by the common contract. 

##### Recovery Behavior

When a transport error, caller cancellation, timeout, or dropped response prevents the caller from knowing whether a session mutation was accepted, the outcome is ambiguous rather than automatically failed. 

The caller must resolve that outcome through `get_content_upload` before retransmitting a part or changing session state. A compatible commit retry may recover its recorded result.

A begin retry cannot recover an existing session and is safe only when Chunky proves that the earlier begin created no session. 

#### Common failure issues

These issues are documented once here and apply to the operations named in each entry.

##### All multipart-session operations

- **`PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** The caller is not authorized to create or access multipart artifact-publication operations.
  - **Retry:** `retryable: false`. A new call is appropriate only after authorization or policy changes.
- **`OPERATION_CANCELLED`**
  - **Cause:** Chunky observes cancellation before the operation returns its ordinary result.
  - **Retry:** Status and abort may return `retryable: true` because repeating them is safe. Begin returns `true` only when Chunky can prove that no session was created. Part upload and commit return `true` only when Chunky can prove no conflicting session state or bytes were accepted, or when a compatible outcome can be recovered. An ambiguous part upload returns `false` until status resolves it.
- **`ARTIFACT_STORAGE_FAILURE`**
  - **Cause:** Chunky cannot read or durably update storage required by the operation.
  - **Retry:** `retryable: true` only for a transient failure when repeating the operation is safe. `retry_after_milliseconds` is included when known. An ambiguous mutation returns `false` until session status resolves it.

##### Operations targeting an existing session

Applies to part upload, status, commit, and abort.

- **`ARTIFACT_UPLOAD_NOT_FOUND`**
  - **Cause:** The session identity is unknown, no longer recoverable, or not visible to the caller.
  - **Retry:** `retryable: false` for that session identity.
- **`ARTIFACT_UPLOAD_SESSION_OWNERSHIP_INVALID`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** The authenticated caller does not own the identified session or the request conflicts with its ownership binding.
  - **Retry:** `retryable: false`. The caller must use a session it owns; bindings may map an invisible foreign session to `ARTIFACT_UPLOAD_NOT_FOUND` instead.

##### Operations reserving or accepting provisional storage

Applies to begin and part upload; provider capacity may also be required during commit.

- **`ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED`**
  - **Cause:** The declared complete length exceeds the configured maximum total artifact size. No session is created.
  - **Retry:** `retryable: false` for the same declared length and configuration.
- **`ARTIFACT_UPLOAD_MULTIPART_SIZE_LIMIT_EXCEEDED`**
  - **Cause:** No valid multipart geometry can keep every part within the configured maximum multipart part size and permitted part count. No session is created.
  - **Retry:** `retryable: false` for the same declared length and configuration.
- **`ARTIFACT_UPLOAD_QUOTA_EXCEEDED`**
  - **Cause:** Creating the session or accepting the part would exceed the applicable provisional upload quota.
  - **Retry:** `retryable: true` only when the quota may clear without changing the request and no part acceptance is ambiguous. `retry_after_milliseconds` is included when known.
- **`ARTIFACT_STORAGE_PROVIDER_CAPACITY_EXCEEDED`**
  - **Cause:** The storage provider cannot currently reserve, stage, assemble, or publish the required capacity.
  - **Retry:** `retryable: true` only when no mutation is ambiguous. `retry_after_milliseconds` is included when the provider supplies a minimum delay.
- **`ARTIFACT_UPLOAD_STORAGE_PROVIDER_CAPABILITY_UNSUPPORTED`**
  - **Cause:** The configured provider cannot create the requested multipart session or perform its required assembly and finalization.
  - **Retry:** `retryable: false`. The caller or operator must change the request or provider configuration.

##### Begin and commit assertions

- **`ARTIFACT_UPLOAD_DIGEST_UNSUPPORTED`**
  - **Cause:** The requested complete-artifact digest algorithm is not supported by the runtime or selected provider path.
  - **Retry:** `retryable: false`. The caller must omit or change the assertion, or use a supporting configuration.
- **`ARTIFACT_UPLOAD_CONTENT_ID_INVALID`**
  - **Cause:** The caller-provided `ContentId`, including its algorithm or value, is malformed or incompatible with multipart publication.
  - **Retry:** `retryable: false`. The caller must correct or omit `content_id`.
- **`ARTIFACT_UPLOAD_CONTENT_ID_NOT_TRUSTED`**
  - **Cause:** Policy does not permit this caller or ingress path to assign the requested `ContentId`.
  - **Retry:** `retryable: false`. The caller must omit `content_id` or obtain an applicable policy change.
##### Existing-session mutation state

Applies to part upload, commit, and abort when the requested mutation is incompatible with the current lifecycle state.

- **`ARTIFACT_UPLOAD_TIMED_OUT`**
  - **Cause:** The inactivity deadline passed and the session is terminal.
  - **Retry:** `retryable: false`. The caller must begin a new upload session.
- **`ARTIFACT_UPLOAD_ABORTED`**
  - **Cause:** The session was explicitly aborted and cannot accept parts or commit.
  - **Retry:** `retryable: false`. The caller must begin a new upload session.
- **`ARTIFACT_UPLOAD_STATE_INVALID`**
  - **Cause:** The session is in a state that cannot perform the requested mutation, such as terminal `failed`, `finalizing`, or committed publication.
  - **Retry:** `retryable: false` unless status explicitly establishes that the requested operation is permitted. A compatible commit retry recovers the recorded result instead of returning this issue.

##### Part upload

- **`ARTIFACT_UPLOAD_PART_INVALID`**
  - **Cause:** `part_number` is outside the session's planned range or otherwise cannot identify one planned upload part.
  - **Retry:** `retryable: false`. The caller must use a valid planned part number.
- **`ARTIFACT_UPLOAD_PART_LENGTH_MISMATCH`**
  - **Cause:** The source does not supply the exact byte length required for the numbered part. The part remains absent.
  - **Retry:** `retryable: false` for the same source. The caller must retransmit the complete correctly sized part from byte zero.
- **`ARTIFACT_UPLOAD_PART_DIGEST_MISMATCH`**
  - **Cause:** The complete part does not match its `expected_content_digest`. The part remains absent.
  - **Retry:** `retryable: false` for the same bytes and assertion. The caller must correct one of them and retransmit the part from byte zero.
- **`ARTIFACT_UPLOAD_PART_CONFLICT`**
  - **Cause:** The part is already accepted with different bytes or conflicting integrity metadata. The accepted part is unchanged.
  - **Retry:** `retryable: false`. The caller must continue with the accepted part or begin a new upload session.
- **`TRANSFER_SOURCE_INVALID`**
  - **Cause:** The supplied part source fails or violates the `ArtifactByteSource` contract. Partial request bytes do not create or modify an accepted part.
  - **Retry:** `retryable: false`. The caller must use a new valid source and first confirm part acceptance through status.

##### Commit

- **`ARTIFACT_UPLOAD_PART_SET_INCOMPLETE`**
  - **Cause:** One or more planned parts are absent, so the session cannot be sealed for assembly.
  - **Retry:** `retryable: false` until the caller uploads every part reported absent by status.
- **`ARTIFACT_UPLOAD_LENGTH_MISMATCH`**
  - **Cause:** The assembled part sequence does not equal the complete length declared at begin. Publication does not occur.
  - **Retry:** `retryable: false`. The session is terminal when its accepted parts cannot be corrected; otherwise the caller must resolve the missing or invalid part state before retrying commit.
- **`ARTIFACT_UPLOAD_DIGEST_MISMATCH`**
  - **Cause:** The assembled artifact does not match the complete-artifact `expected_content_digest` recorded at begin. Publication does not occur and the session becomes terminally failed.
  - **Retry:** `retryable: false`. The caller must begin a new session with correct bytes or assertions.
- **`ARTIFACT_UPLOAD_FINALIZATION_AMBIGUOUS`**
  - **Cause:** Chunky cannot prove whether immutable publication committed.
  - **Retry:** `retryable: true` for the same compatible commit. The caller may inspect status first; `retry_after_milliseconds` is included when a minimum recovery delay is known.


### Service Boundary: Beginning an upload session

`begin_content_upload` validates the complete-object intent, reserves a provisional quota, chooses the fixed part geometry, and creates the session:

```text
runtime.begin_content_upload(
  total_byte_length,
  desired_inactivity_timeout?,
  desired_grace_ttl,
  desired_retention_ttl?,
  content_id?,
  expected_content_digest?
) -> ContentUploadPlan

ContentUploadPlan
  upload_session_id
  part_size
  part_count
  inactivity_timeout
  inactivity_deadline
```

**Parameters:**

- **`total_byte_length`** is the exact positive length of the complete artifact. Chunky validates it against size and quota policy and uses it to derive part geometry.
- **`desired_inactivity_timeout`** requests how long the provisional upload state may remain without qualifying API activity. Runtime policy may cap or reject it. The effective moving deadline is returned in `inactivity_deadline` and is independent of post-publication retention.
- **`desired_grace_ttl`** requests the upload-grace claim created only after successful publication.
- **`desired_retention_ttl`** optionally requests retention beyond grace. Retention is established at publication; its effective TTL equals the effective grace TTL unless a longer value is requested and permitted.
- **`content_id`** is an optional caller-defined identity for the complete artifact, including algorithm and value, governed by caller-provided content-ID policy. It is not a part digest.
- **`expected_content_digest`** is an optional integrity assertion for the complete artifact. Chunky stores it with the session and verifies it against the single contiguous byte sequence assembled in ascending part-number order during commit. It is distinct from the optional digest supplied for each upload part.

> Supplying `expected_content_digest` is recommended whenever the caller can compute it from the original complete artifact. It verifies that the final assembly has exactly the expected bytes and detects corruption, omission, duplication, or incorrect part ordering across the complete sequence.

**Result fields:**

- **`upload_session_id`** is the opaque identity used by every later operation on the session.
- **`part_size`** is the authoritative byte length of every non-final part.
- **`part_count`** is derived from `ceil(total_byte_length / part_size)`.
- **`inactivity_timeout`** is the effective inactivity duration selected under runtime policy and remains fixed for this session.
- **`inactivity_deadline`** is the current absolute time at which the session will time out if no further qualifying API activity occurs.

**Example:**

```python
plan = await runtime.begin_content_upload(
    total_byte_length=expected_size,
    desired_inactivity_timeout=timedelta(hours=24),
    desired_grace_ttl=timedelta(hours=2),
    desired_retention_ttl=timedelta(days=1),
    expected_content_digest=expected_artifact_digest,
)
```

#### Upload Session Preflight

Before creating session state, the runtime verifies:

- future authorisation;
- total byte length against limits;
- provisional quota;
- caller-provided content-ID policy and algorithm support;
- expected digest algorithm support;
- requested inactivity-timeout policy; and
- a part size that satisfies configured service, upstream request-size, and provider constraints without exceeding the permitted part count.

#### Upload Session Inactivity Timeout

The upload-session inactivity timeout is a sliding timeout, not a fixed session lifetime:

- `begin_content_upload` establishes the initial `inactivity_deadline` from the effective timeout;
- each successful API operation targeting the active session resets `inactivity_deadline` to the operation's activity time plus the effective timeout, including status reads and compatible retries; a future authorization-capable implementation resets it only after authorization succeeds;
- unknown-session traffic and requests that arrive after the deadline do not reset it; a future authorization rejection also does not reset it;
- once the deadline passes without qualifying activity, the session becomes terminally `timed_out`, and the runtime removes or schedules cleanup of its provisional parts and associated provider state; and
- the timeout becomes irrelevant once the session reaches any other terminal state.

The runtime must serialize timeout and session operations so an operation accepted before the current deadline is not raced by cleanup. The response to a non-terminal session operation reports the refreshed `inactivity_deadline` when that response includes session status.


#### Upload-part geometry

Chunky chooses one fixed `part_size` for the session; the caller cannot request a preferred size or submit arbitrary byte spans. Chunky derives the number of upload parts as:

```text
part_count = ceil(total_byte_length / part_size)
```

Upload parts are numbered contiguously from `1`. For part `n`:

```text
start_offset = (n - 1) * part_size
part_length  = min(part_size, total_byte_length - start_offset)
byte_range   = [start_offset, start_offset + part_length]
```

**Caller contract expectations**
Caller Design Note: Every non-final upload part therefore contains exactly `part_size` bytes. The final upload part contains the remaining bytes and may be shorter.

**`part_size` value rational**
Chunky chooses `part_size` comfortably below every configured request-body limit in the path to the service while accounting for memory, throughput, part-count, and provider constraints. Implementations should favor relatively small, reliable requests—commonly tens of megabytes—rather than treating the maximum permitted request size as the target.


### Service Boundary: Uploading parts

`upload_content_part` transfers one complete fixed-range part to Chunky:

```text
runtime.upload_content_part(
  upload_session_id,
  part_number,
  byte_source: ArtifactByteSource,
  expected_content_digest?
) -> AcceptedUploadPart

AcceptedUploadPart
  part_number
  content_digest?
```

**Request Parameters:**
- **`upload_session_id`** selects the active session. *A future authorization-capable implementation also checks caller ownership.*
- **`part_number`** is in `1..=part_count` and determines the part's byte range. 
- **`byte_source`** supplies the complete part from byte zero through the common runtime-sized `ArtifactByteSource` read contract. *Its length must equal `part_size`*, except for the final part whose expected length is derived from `total_byte_length`.
- **`expected_content_digest`** is an optional integrity assertion for this part's complete byte sequence only.

**Result fields:**
- **`part_number`** identifies the accepted upload part and makes the acceptance response self-identifying.
- **`content_digest`** is Chunky's computed digest of the accepted bytes when the request supplied `expected_content_digest`; otherwise it is absent.

Returning `AcceptedUploadPart` means the complete part is durably staged and its acceptance metadata is atomically recorded. If the caller does not receive the result, `get_content_upload` is the authoritative recovery mechanism.

Part acceptance, failure, and retry follow **Part acceptance and retry** in the resumable multipart transfer model.

**Internal multi part streaming expectation**
The upload body for one part uses the common `ArtifactByteSource` contract. Chunky may combine many source reads while receiving one upload part. Source-read sizes and transport-frame boundaries never define or alter upload-part boundaries. ie the internal structure needs to allow for mulipulate non overlapping writes to the same logical artifact record.

**Internal multipart target initialisation**
Chunky may allocate provisional storage lazily and map upload parts onto provider write units differently, provided the client-visible upload-part contract remains unchanged.

**Example:**

```python
parts = list(read_file_parts("input.wav", part_size=plan.part_size))

for part_number, part_bytes in parts:
    await runtime.upload_content_part(
        upload_session_id=plan.upload_session_id,
        part_number=part_number,
        byte_source=part_bytes,
        expected_content_digest=blake3_digest(part_bytes),
    )
```

#### Part acceptance and retry

Before consuming an upload-part body, Chunky verifies session state, the part number, and provisional quota. *A future authorization-capable implementation also verifies session ownership.* 

A part becomes accepted only after its complete expected byte range has been received, its length and optional expected content digest have been verified, its bytes are durably staged, and its metadata is recorded atomically. Accepting a part does not publish the artifact.

Chunky computes a BLAKE3 digest while receiving each part only if `expected_content_digest` was provided. If `expected_content_digest` is supplied and does not match, `ARTIFACT_UPLOAD_PART_DIGEST_MISMATCH` leaves the part absent and the attempt's bytes are removed or scheduled for cleanup.

A failed, interrupted, timed-out, or cancelled attempt leaves that part absent unless a status lookup shows that acceptance completed before the result became ambiguous. The caller retries an absent part in full. Retrying an already accepted identical part returns the existing acceptance without changing it; different bytes or conflicting integrity metadata return `ARTIFACT_UPLOAD_PART_CONFLICT` without changing the accepted part.


### Service Boundary: Inspecting status for resume

`get_content_upload` returns the authoritative session status, including which parts remain outstanding. A caller that lost its local progress state can use this response to reconstruct its progress and continue the upload:

```text
runtime.get_content_upload(
  upload_session_id
) -> ContentUploadStatus

ContentUploadStatus
  upload_session_id
  state: ContentUploadState
  inactivity_timeout
  inactivity_deadline
  expected_byte_length
  received_byte_length
  outstanding_parts[]: int
  publication_result?: DurableContentUploadResult

ContentUploadState
  reserved | receiving | received | finalizing
  committed | aborted | timed_out | failed
```
**Request Parameters:**

- **`upload_session_id`** selects the caller-owned session.
- **`state`** is the authoritative lifecycle state (see values defined below).
- **`inactivity_timeout`** is the session's fixed effective inactivity duration.
- **`inactivity_deadline`** is the current inactivity deadline, refreshed by this successful status request while the session remains active. 
- **`expected_byte_length`** is the complete length declared when the session began.
- **`received_byte_length`** is the sum of durably accepted segment lengths.
- **`outstanding_parts`** contains the authoritative part numbers that have not yet been accepted. While the session is resumable, only numbers in this collection must be retransmitted in full; callers must not infer acceptance from locally observed progress.
- **`publication_result`** is present exactly when `state` is `committed` and contains the authoritative `DurableContentUploadResult` returned by publication.

**State values and resume behavior:**

- **`reserved`** — No parts have been accepted. The session is resumable and may be aborted.
- **`receiving`** — Some parts are accepted. Continue by (re)transmitting only parts listed in `outstanding_parts`.
- **`received`** — All parts are accepted. The session is ready to commit; no retransmission is needed.
- **`finalizing`** — Commit has sealed the part set. Do not upload more parts; poll status or retry commit compatibly.
- **`committed`** — Publication succeeded. The session is terminal and non-resumable; compatible commit retries return the same result.
- **`aborted`** — The caller terminated the session. It is terminal and non-resumable, and provisional state is cleaned up.
- **`timed_out`** — No qualifying API activity occurred before the inactivity deadline. It is terminal and non-resumable, and provisional state is cleaned up.
- **`failed`** — An unrecoverable error prevented publication. It is terminal and non-resumable, and provisional state is cleaned up.

After `aborted`, `timed_out`, or `failed`, retrying the transfer requires a new upload session.

Lifecycle states move forward only. `committed`, `aborted`, `timed_out`, and `failed` never transition back to an active state.

__Example:__

```python
status = await runtime.get_content_upload(plan.upload_session_id)

outstanding_parts = set(status.outstanding_parts)
parts = list(read_file_parts("input.wav", part_size=plan.part_size))

for part_number, part_bytes in parts:
    if part_number in outstanding_parts:
        await runtime.upload_content_part(
            upload_session_id=plan.upload_session_id,
            part_number=part_number,
            byte_source=part_bytes,
            expected_content_digest=blake3_digest(part_bytes),
        )

upload = await runtime.commit_content_upload(
    upload_session_id=plan.upload_session_id,
)
```

### Service Boundary: Committing

`commit_content_upload` seals the session and publishes the assembled artifact. `upload_session_id` selects the caller-owned session. It verifies that all planned parts are accepted, assembles them in part-number order, verifies the complete-artifact digest when supplied, determines the `ContentId` under caller-provided content-ID policy, and applies the common finalisation sequence.

The returned `DurableContentUploadResult` is the common publication result. A compatible retry recovers an ambiguous commit outcome without creating another artifact.

```text
runtime.commit_content_upload(
  upload_session_id
) -> DurableContentUploadResult
```

```python
plan = await runtime.begin_content_upload(...)

# ...

upload = await runtime.commit_content_upload(
    upload_session_id=plan.upload_session_id,
)
```

#### Finalization

`commit_content_upload` verifies that every part from `1` through the plan's `part_count` is accepted and that each accepted part completed its length and digest checks. Chunky assembles the parts in ascending part-number order and verifies the observed total length and optional complete-artifact `expected_content_digest` over that single contiguous byte sequence before applying the common finalization sequence and determining the complete `ContentId`. A future authorization-capable implementation also verifies ownership.

If the assembled bytes do not match the expected digest recorded when the session began, commit returns `ARTIFACT_UPLOAD_DIGEST_MISMATCH`, publishes no artifact, and makes the session terminally failed.

### Service Boundary: Abort the session:

`abort_content_upload` terminates provisional upload state. This prevents new part acceptance, releases provisional quota, and removes or schedules cleanup of session-owned staging and internal provider state. It never removes an artifact that has already been committed.

```text
runtime.abort_content_upload(
  upload_session_id
) -> None
```

**`upload_session_id`** selects the caller-owned session to terminate.

### Service Boundary: HTTP Binding Recommendations

The status endpoint is also the resume endpoint. After an interrupted or ambiguous part request, the client checks `outstanding_parts` before deciding whether to retransmit that part.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/artifact/upload/multipart` | Begin a resumable multipart upload. |
| `PUT` | `/artifact/upload/multipart/{id}/parts/{part_number}` | Upload one complete numbered part to Chunky, with an optional expected content digest in request metadata. |
| `GET` | `/artifact/upload/multipart/{id}` | Return authoritative lifecycle and accepted-part status. |
| `POST` | `/artifact/upload/multipart/{id}/commit` | Assemble, finalize, and publish the upload. |
| `POST` | `/artifact/upload/multipart/{id}/abort` | Abort the provisional session. |

```rust
pub fn upload_session_routes(runtime: AppState) -> Router {
    Router::new()
        .route("/artifact/upload/multipart", post(begin_upload))
        .route("/artifact/upload/multipart/{id}", get(upload_status))
        .route(
            "/artifact/upload/multipart/{id}/parts/{part_number}",
            put(upload_part),
        )
        .route("/artifact/upload/multipart/{id}/commit", post(commit_upload))
        .route("/artifact/upload/multipart/{id}/abort", post(abort_upload))
        .with_state(runtime)
}

async fn begin_upload(
    State(runtime): State<AppState>,
    Json(options): Json<BeginContentUploadOptions>,
) -> ApiResult<Json<ContentUploadPlan>> {
    Ok(Json(runtime.begin_content_upload(options).await?))
}

async fn upload_status(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<ContentUploadStatus>> {
    Ok(Json(runtime.get_content_upload(&id).await?))
}

async fn upload_part(
    State(runtime): State<AppState>,
    Path((id, part_number)): Path<(String, u32)>,
    headers: HeaderMap,
    body: Body,
) -> ApiResult<Json<AcceptedUploadPart>> {
    let expected_content_digest = parse_content_digest(&headers)?;
    let result = runtime
        .upload_content_part(
            &id,
            part_number,
            artifact_body_source(body),
            expected_content_digest,
        )
        .await?;

    Ok(Json(result))
}

async fn commit_upload(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<DurableContentUploadResult>> {
    Ok(Json(runtime.commit_content_upload(&id).await?))
}

async fn abort_upload(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<StatusCode> {
    runtime.abort_content_upload(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}
```

A transport error, timeout, or dropped handler cancels only that part attempt; it does not abort the upload session. The client queries upload status before retransmission and treats `outstanding_parts` as authoritative. An outstanding part is retransmitted in full from byte zero; the HTTP binding exposes no intra-part resume operation.

#### Expected errors

HTTP bindings preserve the complete Chunky `Failure` envelope for runtime failures and apply the retry rules of the corresponding multipart operation. Malformed paths, headers, body framing, authentication middleware failures, upstream request-size rejection, and transport failures may be reported by the binding rather than as `ARTIFACT_UPLOAD`; callers must not infer part acceptance, session mutation, or publication outcome from an HTTP status or a native transport error alone. After an ambiguous part request, callers inspect authoritative status before retransmitting the part.

---

### Upload Session Staging and Recovery

The common provider-owned staging rules apply. Upload-session staging is durable provisional state identified by `upload_session_id`, not operation-scoped scratch data.

Chunky chooses one of two internal storage strategies without changing the client contract:

- **Chunky-managed staging.** When final storage cannot accept independent provisional writes, Chunky stores accepted upload parts in durable staging. At commit it exposes the ordered upload parts as one stable transfer source and writes the completed byte sequence to final storage.

- **Provider-backed provisional writes.** When final storage supports partial or multipart writes, Chunky proxies each incoming upload part into provider-managed provisional state. Provider identifiers, credentials, and provider write units remain internal to Chunky; an upload part need not correspond to one provider multipart part.

Both strategies preserve the same accepted upload-part semantics, status model, and commit operation. Chunky must durably persist enough session, inactivity-deadline, and internal provider identity to resume, finalise, abort, time out, or clean up after restart. Process-local temporary state is insufficient.

Recovery must distinguish

- a session with some accepted upload parts;
- a complete ordered upload-part set awaiting finalization;
- a committed artifact;
- abandoned staged parts;
- timed-out provider-managed provisional state; and
- ambiguous or failed finalization.

Recovery must never promote uncertain or partial bytes to a committed artifact.


## Upload Method: Live Capture Stream

### Purpose and Boundary

Live capture is used when the finite byte sequence does not yet exist when ingress begins and the capture must span multiple requests. For example, a live recording client may periodically flush bounded segments while continuing to record.

Live capture is not an indefinitely readable stage input. It is provisional ingress state that becomes one ordinary immutable uploaded artifact after sealing and publication.

### Service Boundary

Transfers one finite artifact as a sequence of strictly ordered append requests. It is intended to:

- accept an initially unknown total byte length;
- let the client supply one finite segment per request while the runtime enforces the capture's total size limit;
- reduce round trips by optionally carrying initial bytes on beginning and final bytes on completion;
- preserve bounded memory and back pressure for every request;
- publish only after an explicit completion operation seals the capture.

The capture remains unpublished while the session is active. Receiving segments, silence, a client-side end marker, or a server-side end-pointing decision does not publish the artifact; publication requires `complete_live_capture`.

**Process overview**

```text
begin_live_capture(initial_byte_source?)
  -> append_live_capture_segment()
  -> append_live_capture_segment()
  -> ...
complete_live_capture(final_byte_source?)
  -> publish immutable artifact
  -> submit a separate task
```

1. **Create the session.** The caller supplies session and publication options and may include the first byte source. Chunky validates preflight policy, durably accepts any initial bytes as the first segment, and returns the session identity, accepted byte length, and current inactivity deadline.
2. **Append capture segments.** The caller chooses each segment boundary and sends one complete segment at a time. Chunky serializes accepted requests and extends the accepted byte prefix in that order.
3. **Recover an individual request.** The caller retains the current segment and the previously acknowledged byte length until acceptance is known. It retries only after an explicitly recoverable failure or after authoritative status proves that the segment was not accepted. *It never blindly retries an ambiguous request.*
4. **Finalize the artifact.** The caller explicitly completes the capture and may include the final byte source in that request. Chunky appends any final segment, seals the byte sequence, verifies final assertions, publishes the immutable artifact, creates the upload-grace claim, and returns `DurableContentUploadResult`.

> The optional begin and completion bodies save round trips; they have the same acceptance rules as an ordinary append. A transport-level request timeout or dropped handler does not itself time out the session. The session times out only when its inactivity deadline passes without another qualifying API operation.

#### Live capture inactivity timeout

The live-capture inactivity timeout follows the common sliding session behavior defined for upload sessions:

- `begin_live_capture` establishes the initial `inactivity_deadline` from the effective timeout;
- each successful API operation targeting the active capture resets the deadline, including status reads and compatible retries; a future authorization-capable implementation resets it only after authorization succeeds;
- unknown-session traffic and requests that arrive after the deadline do not reset it; a future authorization rejection also does not reset it; and
- once the deadline passes without qualifying activity, the capture becomes terminally `timed_out`, and provisional bytes, ordering state, acknowledgements, and recovery metadata are removed or scheduled for clean-up.

Timeout processing must not race an operation accepted before the current deadline. The timeout is independent of the post-publication upload-grace claim, and timed-out provisional bytes are never exposed as an artifact.

#### Common failure issues

These issues are documented once here and apply to the operations named in each entry. Operation sections list only additional issues unique to that boundary.

##### All live-capture operations

- **`PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** The caller lacks permission to access artifact-publication operations.
  - **Retry:** `retryable: false`. A new call is appropriate only after authorization or policy changes.
- **`PRINCIPAL_MISSING_PERMISSIONS_FOR_LIVE_CAPTURE`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** The caller lacks permission to perform the requested live-capture operation.
  - **Retry:** `retryable: false`. A new call is appropriate only after authorization or policy changes.
- **`OPERATION_CANCELLED`**
  - **Cause:** Chunky observes cancellation before the operation returns its ordinary result.
  - **Retry:** Status and abort may return `retryable: true` because repeating them is safe. Begin returns `true` only when Chunky can prove that no capture session was created; a begin retry cannot recover an existing session. Append and completion return `true` only when Chunky can prove that no conflicting state or bytes were accepted, or when a compatible outcome can be recovered. An ambiguous mutation returns `false` until status resolves it.
- **`ARTIFACT_STORAGE_FAILURE`**
  - **Cause:** Chunky cannot read or durably update storage required by the operation.
  - **Retry:** `retryable: true` only for a transient failure when repeating the operation is safe. `retry_after_milliseconds` is included when known. An ambiguous mutation returns `false` until status resolves it.

##### Operations targeting an existing session

Applies to append, status, completion, and abort.

- **`ARTIFACT_UPLOAD_NOT_FOUND`**
  - **Cause:** The session identity is unknown, no longer recoverable, or not visible to the caller. The code deliberately does not disclose another principal's capture.
  - **Retry:** `retryable: false` for that session identity.

##### Operations reserving or accepting payload storage

Applies to begin, append, and completion. Size and source issues occur only when that request supplies bytes; capacity and quota issues may also occur while reserving or publishing storage.

- **`ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED`**
  - **Cause:** Accepting the supplied segment would exceed the configured total capture limit. Context includes `max_byte_length`; the segment is not accepted.
  - **Retry:** `retryable: false` for the same segment.
- **`ARTIFACT_STORAGE_PROVIDER_CAPACITY_EXCEEDED`**
  - **Cause:** The storage provider cannot currently reserve or stage the required capacity.
  - **Retry:** `retryable: true` only when no bytes were ambiguously accepted. `retry_after_milliseconds` is included when the provider supplies a minimum delay.
- **`ARTIFACT_STORAGE_QUOTA_EXCEEDED`**
  - **Cause:** Acceptance would exceed a runtime-wide artifact-storage quota.
  - **Retry:** `retryable: true` only when the quota may clear without changing the payload and no bytes were ambiguously accepted. `retry_after_milliseconds` is included when known.
- **`PRINCIPAL_ARTIFACT_STORAGE_QUOTA_EXCEEDED`** *(future authorization contract; not emitted by the MVP)*
  - **Cause:** Acceptance would exceed the caller principal's artifact-storage quota.
  - **Retry:** `retryable: true` only when the quota may clear without changing the payload and no bytes were ambiguously accepted. `retry_after_milliseconds` is included when known.
- **`TRANSFER_SOURCE_INVALID`**
  - **Cause:** The supplied source fails or violates the `ArtifactByteSource` contract. Partial request bytes do not extend the accepted prefix.
  - **Retry:** `retryable: false`. The caller must use a new valid source and, for append or completion, first confirm the accepted length through status.

##### Begin and completion

- **`ARTIFACT_UPLOAD_STORAGE_PROVIDER_CAPABILITY_UNSUPPORTED`**
  - **Cause:** The configured provider cannot create the requested live capture or perform its required finalization.
  - **Retry:** `retryable: false`. The caller or operator must change the request or provider configuration.

##### Append and completion session state:

- **`ARTIFACT_UPLOAD_TIMED_OUT`**
  - **Cause:** The inactivity deadline passed and the capture is terminal.
  - **Retry:** `retryable: false`. The caller must begin a new capture.
- **`ARTIFACT_UPLOAD_ABORTED`**
  - **Cause:** The capture was explicitly aborted and cannot accept bytes or complete.
  - **Retry:** `retryable: false`. The caller must begin a new capture.
- **`ARTIFACT_UPLOAD_LIVE_CAPTURE_APPEND_ALREADY_IN_PROGRESS`**
  - **Cause:** An earlier append is still resolving, so the accepted prefix is not yet authoritative. A new request body is not accepted.
  - **Retry:** `retryable: false` until `get_live_capture` establishes the earlier appends outcome.
- **`ARTIFACT_UPLOAD_ALREADY_SEALED`**
  - **Cause:** Completion has sealed the accepted prefix, so no incompatible bytes or assertions may be added.
  - **Retry:** `retryable: false`. A compatible completion retry recovers the recorded outcome instead of returning this issue.

##### Append, completion, and abort session state

- **`ARTIFACT_UPLOAD_LIVE_CAPTURE_STATE_INVALID`**
  - **Cause:** The capture is in a state that cannot perform the requested mutation, such as terminal `failed` or committed publication.
  - **Retry:** `retryable: false` unless later status explicitly establishes that the requested operation is permitted.

### Service Boundary: Beginning a live capture

`begin_live_capture` validates the capture intent, reserves a provisional quota, optionally accepts the first segment, and creates the session:

```text
runtime.begin_live_capture(
  initial_byte_source?: ArtifactByteSource,
  desired_inactivity_timeout?,
  desired_grace_ttl,
  desired_retention_ttl?,
) -> AcceptedLiveCaptureSegment

AcceptedLiveCaptureSegment
  capture_session_id
  inactivity_deadline
  received_byte_length
```

**Python Example:**

```python
plan = await runtime.begin_live_capture(
    initial_byte_source=encode_audio_frames(microphone.pre_roll_frames()),
    desired_inactivity_timeout=timedelta(seconds=10),
    desired_grace_ttl=timedelta(seconds=30),
    desired_retention_ttl=timedelta(seconds=0), # Will automatically upgrade to = desired_grace_ttl
)
```

#### Request parameters

- **`initial_byte_source`** optionally supplies the first segment.
- **`desired_inactivity_timeout`** requests how long provisional capture state may remain without qualifying API activity. Runtime policy may cap or adjust it. The effective moving deadline is returned in `inactivity_deadline` and is independent of post-publication retention.
- **`desired_grace_ttl`** requests the upload-grace claim created only after successful publication.
- **`desired_retention_ttl`** optionally requests retention beyond grace. Retention is established at publication; its effective TTL equals the effective grace TTL unless a longer value is requested and permitted.


#### Result fields
- **`capture_session_id`** is the opaque identity used by every later operation on the capture.
- **`inactivity_deadline`** is the current absolute time at which the capture will time out if no further qualifying API activity occurs.
- **`received_byte_length`** is the length of any initial bytes durably accepted before the plan was returned, or `0` when begin carried no bytes.


#### Additional error types
- **`ARTIFACT_UPLOAD_OPTIONS_INVALID`**
  - **Cause:** A requested inactivity timeout, grace TTL, retention TTL, or other begin option is invalid or rejected by runtime policy.
  - **Retry:** `retryable: false`. The caller must change the rejected option.


### Service Boundary: Appending capture segments

`append_live_capture_segment` appends one complete client-sized segment to the accepted byte prefix:

```text
runtime.append_live_capture_segment(
  capture_session_id,
  byte_source: ArtifactByteSource
) -> AcceptedLiveCaptureSegment

AcceptedLiveCaptureSegment
  capture_session_id
  received_byte_length
  inactivity_deadline
```

**Python Example:**

```python
async for segment in microphone.command_segments():
    acknowledgement = await runtime.append_live_capture_segment(
        capture_session_id=plan.capture_session_id,
        byte_source=segment,
    )
```

#### Request parameters
- **`capture_session_id`** selects the active caller-owned capture.
- **`byte_source`** supplies one finite, non-empty segment through the common runtime-sized `ArtifactByteSource` read contract. The API does not advertise or require a segment-size limit; the segment is constrained by the capture's remaining total-length allowance and binding-level request limits.

#### Result fields
- **`capture_session_id`** is the opaque identity used by every later operation on the capture.
- **`received_byte_length`** is the total accepted payload length after this segment is durably appended.
- **`inactivity_deadline`** is the current absolute time at which the capture will time out if no further qualifying API activity occurs.


#### Segment concatenation behavior

- Each accepted segment is concatenated immediately after the previously accepted byte prefix. Segment boundaries are transfer metadata only: they do not create artifact identity and are not encoded into the committed representation.

- One append becomes accepted only after its body reaches `EndOfSource`, its complete bytes are durably staged, and the new `received_byte_length` is recorded atomically. Partial request bytes never extend the accepted prefix.

- The caller may choose a different non-zero segment length for every request. Chunky does not derive a fixed part size or total part count. Requests cannot overlap, leave gaps, replace earlier bytes, or be processed concurrently.

- The ingress adapter is responsible for producing one finite representation. The artifact upload service remains media-agnostic and does not perform audio transcoding or semantic validation.


#### Segment acceptance and retry behavior

- Before consuming a segment body, Chunky verifies session state, provisional quota, and that no other append is being processed for the capture. A future authorization-capable implementation also verifies session ownership.

- If accepting the segment would exceed the configured maximum total artifact size, Chunky returns `ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED` with `max_byte_length` in issue context and leaves the accepted prefix unchanged. That failure is non-retryable for the same segment.

- A concurrent append returns `ARTIFACT_UPLOAD_LIVE_CAPTURE_APPEND_ALREADY_IN_PROGRESS` without consuming or accepting its body; it may be retried only after the earlier append's outcome is known.

- A transport error, timeout, cancellation, or dropped response can leave the outcome ambiguous. The caller must not immediately resend the segment. It first reads authoritative status, using the last acknowledged `received_byte_length` as the baseline. Chunky must serialize that observation after the original append outcome so it never reports an unchanged length while the original request may still commit:

  - if status reports the baseline length, the segment was not accepted and may be retried while the capture remains retryable;
  - if status reports the baseline plus the retained segment's length, the segment was accepted and must not be resent; and
  - if status cannot establish either outcome, the segment is not safely retryable and the caller must abandon it and normally abort the capture.

This is request-level retry safety, not upload-session resumability. The service does not retain an arbitrary accepted-part set, and a client that no longer has the current unacknowledged segment cannot reconstruct it through the live-capture API. The client needs to retain at most the current segment until its acceptance is known, not the complete capture until publication.

### Service Boundary: Inspecting capture status

`get_live_capture` returns authoritative lifecycle and progress status. Its recovery purpose is limited to resolving an ambiguous append or completion result.

```text
runtime.get_live_capture(
  capture_session_id
) -> LiveCaptureStatus

LiveCaptureStatus
  capture_session_id
  state: ContentUploadState
  inactivity_timeout
  inactivity_deadline
  received_byte_length
  publication_result?: DurableContentUploadResult

ContentUploadState
  reserved | receiving | received | finalizing
  committed | aborted | timed_out | failed
```

#### Request parameters

- **`capture_session_id`** selects the caller-owned capture.


#### Result fields

- **`capture_session_id`** selects the caller-owned session.
- **`state`** is the authoritative lifecycle state (see values defined below).
- **`inactivity_timeout`** is the session's fixed effective inactivity duration.
- **`inactivity_deadline`** is the current inactivity deadline, refreshed by this successful status request while the session remains active. Future authorization must succeed before refresh.
- **`received_byte_length`** is the length of the durably accepted segment prefix.
- **`publication_result`** is present exactly when `state` is `committed` and contains the authoritative `DurableContentUploadResult` returned by publication.

After `aborted`, `timed_out`, or terminal `failed` state, the capture cannot accept new bytes. A transport-level timeout of one append request does not itself create any of those terminal states.


### Service Boundary: Completing

`complete_live_capture` seals the capture and publishes its finite representation:

```text
runtime.complete_live_capture(
  capture_session_id,
  final_byte_source?: ArtifactByteSource,
  total_byte_length?
) -> DurableContentUploadResult
```


**Python Example:**

```python
# Begin carried the pre-roll. Each request below carries a client-sized
# segment and is retained only until its acceptance is known.
writer = await open_live_capture_writer(plan)

async for frame in microphone.command_frames():
    await writer.write(frame)
    if endpoint_detector.command_complete(frame):
        break

final_segment = writer.take_pending_bytes()
final_total_byte_length = writer.accepted_byte_length + len(final_segment)

upload = await runtime.complete_live_capture(
    capture_session_id=plan.capture_session_id,
    final_byte_source=final_segment,
    total_byte_length=final_total_byte_length,
)
```

#### Request parameters

- **`capture_session_id`** selects the active caller-owned capture.
- **`final_byte_source`** optionally supplies one last client-sized segment before sealing, avoiding a separate append request.
- **`total_byte_length`** optionally asserts the complete accepted payload length.

The returned `DurableContentUploadResult` is the common publication result. If a final byte source is present, it follows the ordinary total-length, serialization, and atomic-acceptance rules. A compatible completion retry recovers the recorded final-segment and publication outcome without appending the final bytes twice or creating another artifact.

#### Expected failure issues

- **`ARTIFACT_UPLOAD_INCOMPLETE`**
  - **Cause:** The accepted prefix or recovery state is insufficient to seal the intended representation.
  - **Retry:** `retryable: false` until the caller resolves status or changes the completion input while the capture remains active.
- **`ARTIFACT_UPLOAD_LENGTH_MISMATCH`**
  - **Cause:** `total_byte_length` does not equal the sealed representation's observed length. Context includes `actual_byte_length`; publication does not occur.
  - **Retry:** `retryable: false`. The caller must correct the assertion while the capture remains eligible for completion.
- **`ARTIFACT_UPLOAD_FINALIZATION_AMBIGUOUS`**
  - **Cause:** Chunky cannot prove whether immutable publication committed.
  - **Retry:** `retryable: true` for the same compatible completion. The caller may inspect status first; `retry_after_milliseconds` is included when a minimum recovery delay is known.


#### Finalization Behavior

After sealing:

- no new payload bytes are accepted;
- final length assertions are checked;
- the runtime computes the `ContentId` from the complete sealed representation; and
- the common immutable publication operation runs.

Receiving silence, a client-side end marker, or a server-side endpointing decision does not publish by itself. Those mechanisms cause the caller or capture coordinator to invoke `complete_live_capture`.

A capture remains provisional until both sealing and common publication succeed.


### Service Boundary: Abort the session

`abort_live_capture` terminates provisional capture state. It prevents new segment acceptance, releases capture quota, removes or schedules cleanup of provisional bytes, and marks the capture terminally aborted. It never removes an artifact that already committed.

```text
runtime.abort_live_capture(
  capture_session_id
) -> None
```

#### Request parameters

- **`capture_session_id`** selects the caller-owned capture to terminate.

#### Abort behavior

Aborting an already `aborted` capture returns success without changing its state. A known `timed_out` capture also needs no further abort and may return success after any required cleanup is scheduled.

### Service Boundary: HTTP Binding Recommendations

The begin and completion endpoints may carry request bodies so the first and final segments do not require separate append round trips. Binding-specific headers carry options such as the inactivity timeout, post-publication TTLs, and total length when the body carries artifact bytes.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/artifact/upload/capture` | Begin a live-capture session, optionally carrying the initial segment. |
| `POST` | `/artifact/upload/capture/{id}/segments` | Append one complete client-sized segment. |
| `GET` | `/artifact/upload/capture/{id}` | Return authoritative lifecycle and accepted-byte-count status. |
| `POST` | `/artifact/upload/capture/{id}/complete` | Optionally append the final segment, then seal and publish the capture. |
| `POST` | `/artifact/upload/capture/{id}/abort` | Abort the provisional capture. |

```rust
pub fn live_capture_routes(runtime: AppState) -> Router {
    Router::new()
        .route("/artifact/upload/capture", post(begin_capture))
        .route("/artifact/upload/capture/{id}", get(capture_status))
        .route("/artifact/upload/capture/{id}/segments", post(append_capture_segment))
        .route("/artifact/upload/capture/{id}/complete", post(complete_capture))
        .route("/artifact/upload/capture/{id}/abort", post(abort_capture))
        .with_state(runtime)
}

async fn begin_capture(
    State(runtime): State<AppState>,
    headers: HeaderMap,
    body: Body,
) -> ApiResult<Json<AcceptedLiveCaptureSegment>> {
    let options = parse_begin_live_capture_options(&headers)?;
    let initial_byte_source = optional_artifact_body_source(&headers, body)?;
    Ok(Json(
        runtime
            .begin_live_capture(initial_byte_source, options)
            .await?,
    ))
}

async fn capture_status(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<LiveCaptureStatus>> {
    Ok(Json(runtime.get_live_capture(&id).await?))
}

async fn append_capture_segment(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
    body: Body,
) -> ApiResult<Json<AcceptedLiveCaptureSegment>> {
    let acknowledgement = runtime
        .append_live_capture_segment(
            &id,
            artifact_body_source(body),
        )
        .await?;

    Ok(Json(acknowledgement))
}

async fn complete_capture(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    body: Body,
) -> ApiResult<Json<DurableContentUploadResult>> {
    let options = parse_complete_live_capture_options(&headers)?;
    let final_byte_source = optional_artifact_body_source(&headers, body)?;
    Ok(Json(
        runtime
            .complete_live_capture(&id, final_byte_source, options)
            .await?,
    ))
}

async fn abort_capture(
    State(runtime): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<StatusCode> {
    runtime.abort_live_capture(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}
```

A transport error, timeout, or dropped handler does not itself abort the capture session, but it may leave the segment outcome ambiguous. The client reads authoritative status and applies the accepted-byte-length recovery rule before retrying or sending a later segment.

#### Expected errors

HTTP bindings preserve the complete Chunky `Failure` envelope for runtime failures and apply the retry rules of the corresponding operation. Malformed paths, headers, body framing, authentication middleware failures, and transport failures may be reported by the binding rather than as `ARTIFACT_UPLOAD`; callers must not infer byte acceptance from an HTTP status or a native transport error alone.


### Live Capture Staging and Recovery

The common provider-owned staging rules apply. Live-capture staging is durable provisional state identified by `capture_session_id`, not operation-scoped scratch data.

Provider-managed provisional writes are normally unnecessary for short command captures, though Chunky may reuse them internally without exposing provider identifiers, credentials, or write units through the capture protocol.

Chunky must durably persist enough capture, accepted-byte-length, unresolved-append outcome, inactivity-deadline, and internal provider identity to inspect status, resolve an ambiguous request, finalize, abort, time out, or clean up after restart. Process-local temporary state is insufficient.

The minimal append-recovery contract is:

Recovery must reconstruct the single public `LiveCaptureStatus` and `AcceptedLiveCaptureSegment` definitions above. Retryability appears only in `Failure.retry`, not in status.

Recovery must distinguish an active capture with an ordered accepted prefix, a sealed capture awaiting publication, a committed artifact, an aborted or timed-out capture, and ambiguous or failed finalization. It does not expose accepted segments as an independently resumable part set. Recovery must never promote uncertain, partial, or unsealed bytes to a committed artifact.


## Failures

Ingress uses the universal [Failure Contract](07-failure-surface.md) with kind `ARTIFACT_UPLOAD`.

Issue codes concerning authorization, principals, tenants, or session ownership are reserved for the future authorization contract and are not emitted by the MVP.

### Universal issue codes used by ingress

- `OPERATION_CANCELLED`.

### Common issue codes

- `PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION`;
- `ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED`;
- `ARTIFACT_UPLOAD_STORAGE_PROVIDER_CAPABILITY_UNSUPPORTED`;
- `ARTIFACT_STORAGE_PROVIDER_CAPACITY_EXCEEDED`;
- `ARTIFACT_UPLOAD_LENGTH_MISMATCH`;
- `ARTIFACT_UPLOAD_DIGEST_MISMATCH`;
- `ARTIFACT_UPLOAD_DIGEST_UNSUPPORTED`;
- `ARTIFACT_UPLOAD_CONTENT_ID_INVALID`;
- `ARTIFACT_UPLOAD_CONTENT_ID_NOT_TRUSTED`;
- `ARTIFACT_UPLOAD_FINALIZATION_AMBIGUOUS`;
- `ARTIFACT_STORAGE_FAILURE`;
- `TRANSFER_SOURCE_INVALID`.

### Direct-publication issue codes

- `ARTIFACT_UPLOAD_INPUT_FORM_UNSUPPORTED`;
- `TRANSFER_SOURCE_EXHAUSTED`.

### Upload-session issue codes

- `ARTIFACT_UPLOAD_NOT_FOUND`;
- `ARTIFACT_UPLOAD_TIMED_OUT`;
- `ARTIFACT_UPLOAD_ABORTED`;
- `ARTIFACT_UPLOAD_STATE_INVALID`;
- `ARTIFACT_UPLOAD_SESSION_OWNERSHIP_INVALID`;
- `ARTIFACT_UPLOAD_QUOTA_EXCEEDED`;
- `ARTIFACT_UPLOAD_PART_INVALID`;
- `ARTIFACT_UPLOAD_MULTIPART_SIZE_LIMIT_EXCEEDED`;
- `ARTIFACT_UPLOAD_PART_LENGTH_MISMATCH`;
- `ARTIFACT_UPLOAD_PART_DIGEST_MISMATCH`;
- `ARTIFACT_UPLOAD_PART_CONFLICT`; and
- `ARTIFACT_UPLOAD_PART_SET_INCOMPLETE`.

### Live-capture issue codes

- `PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION`;
- `PRINCIPAL_MISSING_PERMISSIONS_FOR_LIVE_CAPTURE`;
- `ARTIFACT_UPLOAD_OPTIONS_INVALID`;
- `ARTIFACT_STORAGE_QUOTA_EXCEEDED`;
- `PRINCIPAL_ARTIFACT_STORAGE_QUOTA_EXCEEDED`;
- `ARTIFACT_UPLOAD_NOT_FOUND`;
- `ARTIFACT_UPLOAD_TIMED_OUT`;
- `ARTIFACT_UPLOAD_ABORTED`;
- `ARTIFACT_UPLOAD_LIVE_CAPTURE_STATE_INVALID`;
- `ARTIFACT_UPLOAD_LIVE_CAPTURE_APPEND_ALREADY_IN_PROGRESS`;
- `ARTIFACT_UPLOAD_ALREADY_SEALED`; and
- `ARTIFACT_UPLOAD_INCOMPLETE`.

Issue context may include session identity, the rejected option, expected and observed lengths, the effective `max_byte_length`, quota scope, live-capture `received_byte_length`, requested and effective inactivity-timeout values, and terminal state. Retryability and minimum delay are represented only by the root `Failure.retry` directive.

It never contains source bytes, credentials, internal provider operations, or provider paths.

## Invariants

### Common invariants

1. Every successful path publishes at most one immutable byte-cache entry.
2. No ingress path accepts or resolves an [[../02-artifacts/01-artifact-model-surface#Artifact request|ArtifactRequest]], [[../02-artifacts/01-artifact-model-surface#Scoped artifact request|ScopedArtifactRequest]], or [[../02-artifacts/01-artifact-model-surface#Artifact schema|ArtifactSchemaId]].
3. No ingress path performs artifact-specific validation or transcoding.
4. Only committed authoritative metadata or a completion pointer makes bytes visible under a `ContentId`.
5. Publication success includes an upload-grace claim.
6. Committed bytes are a faithful copy of the finite representation accepted for publication.
7. A caller-provided `ContentId` is trusted without byte verification only under explicit policy.
8. An expected content digest is verified against the complete input bytes of the operation that accepts it.
9. Physical provider naming is not semantic cache identity.
10. A lost session-completion response is recovered through status, not a second logical artifact.
11. Large-artifact execution does not require whole-artifact deserialization into memory.
12. Task submission occurs only after immutable publication.
13. Task acceptance atomically records structurally valid supplied bindings and removes or converts applicable grace claims. After cache resolution, every resolved input is verified and protected before execution; an unavailable resolved input fails the task rather than making execution wait for future bytes.
14. A byte-stream error, normal end-of-stream, cooperative cancellation, and dropped consumption are distinct outcomes.
15. Dropping an ingress future never makes incomplete provisional bytes visible as a committed artifact.

### Direct-publication invariants

1. Direct transfer is bounded-memory.
2. A direct stream is consumed at most once.
3. An interrupted uncommitted direct stream is not resumable.
4. A consumable file is removed or transferred from the caller only after artifact publication commits.
5. A disconnect after normal end-of-stream does not roll back a completed or committing publication.

### Upload-session invariants

1. Direct publication is the only one-shot upload surface; upload sessions transfer one known-length artifact as bounded numbered parts.
2. Chunky selects one authoritative `part_size` and derives `part_count` when the session begins.
3. Each part number identifies one fixed byte range; the caller cannot choose arbitrary part sizes or spans.
4. Every client part request terminates at Chunky; the caller never uploads directly to the storage provider.
5. One resumable multipart client contract applies whether Chunky uses managed staging or provider-backed provisional writes internally.
6. Upload-session staging is provisional and never visible as a cache hit.
7. Each qualifying operation on an active upload session resets its inactivity deadline; the timeout does not protect the committed artifact.
8. Authoritative accepted-part state determines resumability.
9. A part becomes accepted only when its complete body has the expected length, its optional expected content digest matches, and its bytes and metadata are durably recorded.
10. `expected_content_digest` on `upload_content_part` covers only that part and is never treated as a complete-artifact digest or `ContentId`.
11. An incomplete, length-mismatched, digest-mismatched, or otherwise failed part attempt is not resumable and is retried from byte zero.
12. Retrying an accepted part with different bytes or a conflicting digest fails without changing the accepted part.
13. Accepting every part does not itself publish the artifact.
14. Session abort or inactivity timeout never removes an already committed artifact.
15. Internal storage strategy does not change caller-visible session semantics or runtime-controlled finalization.
16. Cancelling one part attempt does not implicitly abort its upload session.
17. After an interrupted or ambiguous part result, authoritative session status determines whether the complete part is retransmitted.

### Live-capture invariants

1. Live capture is finite even though its length is unknown when it begins.
2. An open capture is provisional state, not published or addressable content.
3. An open capture is never bound directly as a stage input.
4. Accepted segments form one strictly ordered byte prefix; the client does not select append positions or sequence elements.
5. Explicit completion seals the byte sequence before content identification and publication.
6. No new payload bytes are accepted after sealing.
7. Capture inactivity timeout or abort never exposes partial bytes as an artifact.
8. A combined capture-and-submit facade composes publication and task submission without changing their separate atomic boundaries.
9. Cancelling or timing out one segment request does not itself place its capture session in the terminal `timed_out` state.
10. `received_byte_length` advances only after the complete segment has been durably accepted.
11. Live capture does not expose arbitrary ranges, concurrent appends, or an independently resumable accepted-part set.
12. Initial bytes on being and final bytes on completion follow the same atomic segment-acceptance rules as ordinary appends.
13. A size-limit failure reports the effective `max_byte_length` and accepts none of the request's bytes.
14. An append is retried only after an explicitly recoverable failure or authoritative status proves that its bytes were not accepted; an unresolved outcome is never retried blindly.
