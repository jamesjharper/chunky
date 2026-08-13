# Durable Artifact Download

## Purpose

Durable artifact download resolves a concrete artifact instance or task-relative
output and opens its canonical encoded representation through a reserved,
bounded-memory transfer source.

The primitive surface returns bytes, metadata, and lifecycle state. Typed
decoding is layered above it through the declaration codec.

This document defines service behaviour, not HTTP framing or exact
language-binding spelling.

## Service Operations

Conceptually,

```text
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
```

`open` never silently waits. `await_open` waits only for a pending `a-task`.

The authoritative provider behavior is defined in
[Durable Artifact Persistence](../03-bindings/04-artifact-persistence.md).
Transfer sources are defined by
[Artifact IO](../02-artifacts/02-artifact-io-surface.md).

## Locators

The service accepts:

- `a-instance`, which identifies one concrete semantic artifact instance; or
- `a-task`, which identifies one task-scoped requested output and may initially
  retain an unresolved phase selector.

Locators are location-independent and contain no cache, filesystem, SMB,
bucket, region, or provider address.

Possession of a locator does not authorize access.

## Availability

Expected lifecycle outcomes use:

```text
ArtifactAvailability
  Pending(task/output state)
  Available(resolved a-instance, representation metadata, retained_until?)
  Unavailable(reason, resolved a-instance?)
```

An `a-instance` is never `Pending`.

`Unavailable` includes:

- terminally failed or cancelled task output;
- resolved output whose task artifact claim expired;
- concrete artifact no longer retained; and
- task/output record expired according to its applicable lifecycle.

Malformed locators, unauthorized access, invalid ranges, provider faults, and
contract violations are structured operation failures rather than availability
variants.

## Inspect

Inspection returns current availability without opening content or promising
that a later open will be successful.

For `a-task`, it distinguishes:

- pending resolution/execution;
- successful binding to a concrete instance;
- terminal failure/cancellation;
- resolved but expired artifact; and
- currently downloadable artifact.

Inspection does not refresh claims.

## Non-Waiting Open

`open`:

1. parses and authorises the locator;
2. resolves `a-task` when already possible;
3. returns `Pending` rather than blocking when the output is not ready;
4. returns terminal `Unavailable` when no instance can become available;
5. validates the durable record and representation object; and
6. atomically acquires a read reservation before returning content.

Availability may change between `inspect` and `open`; `open` is authoritative
for acquiring readable content.

## Waiting Open

`await_open` waits through a pending `a-task` until:

- its output binds to a committed durable `a-instance`;
- it becomes terminally unavailable;
- the caller cancels; or
- the caller's deadline expires.

Waiting does not affect task execution and does not create an artifact claim.
After resolution, it uses the same validation and atomic reservation path as
a non-waiting open.

An `a-instance` follows the non-waiting path immediately.

## Download Result

Success returns:

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

The digest is the canonical BLAKE3-256 v1 representation digest. It is distinct
from the artifact fingerprint embedded in `artifact_instance`.

The result may include a safe suggested filename derived from artifact identity,
fingerprint, and preferred extension. Original upload filenames are optional
presentation context, not authoritative durable-instance metadata.

## Read Reservation

Opening atomically acquires a read reservation after validating committed,
readable content. Garbage collection must not remove the durable record or its
representation object while the reservation is active.

Each download handle:

- owns one read reservation;
- permits at most one active byte stream;
- may reopen sequentially when its source capability is reopenable; and
- must be explicitly closed to release the reservation.

EOF closes the active stream but leaves a reopenable handle valid. Cancellation,
explicit close, or async-context-manager exit closes the stream and releases the
reservation. One-shot binding helpers may close automatically after EOF.

Concurrent full or range reads use independent download opens and reservations.
Stale-reader recovery is the backstop for abandoned handles.

## Streaming

The source exposes a pull-based asynchronous byte stream with backpressure.
Whole-file buffering is never required by the download contract.

Chunk boundaries and sizes are non-semantic and provider-selected. A transport
adapter may stream the source directly to an HTTP response. A file target may
use native-copy negotiation or the streaming fallback.

The source may expose opaque native-transfer offers. It does not expose local
paths, SMB handles, bucket keys, signed URLs, or provider credentials through
the common contract.

## Python-Like Example: Stream into Cache

The following example uses illustrative binding names. It shows optional
durable-to-cache localisation through the common transfer contracts without
prescribing an exact Python API.

```python
download = await runtime.artifacts.await_open(task_output_locator)
expected_instance = runtime.artifacts.parse_instance(
    download.artifact_instance
)
declaration = runtime.artifacts.resolve(expected_instance.artifact_key)

try:
    resolution = await runtime.cache.resolve(
        declaration=declaration,
        fingerprint=expected_instance.fingerprint,
    )

    if isinstance(resolution, CacheWriteReservation):
        cache_target = resolution.target_handle
        try:
            await cache_target.prepare_from(
                download.source,
                expected_instance=expected_instance,
            )
            await cache_target.commit()
        except BaseException:
            await cache_target.abort()
            raise
    elif isinstance(resolution, CacheHit):
        # Another localization already committed the same semantic instance.
        await resolution.source_handle.close()
    else:
        # Production orchestration waits with task cancellation, then calls
        # cache.resolve(...) again. Wait completion does not itself imply a hit.
        await resolution.wait_handle.wait()
finally:
    await download.close()
```

`prepare_from(download.source, ...)` makes the cache target responsible for
ingestion. It pulls the durable source with bounded memory and backpressure, or
uses a compatible native-copy offer, and publishes nothing until `commit()`.
The example does not materialize a typed value or concatenate chunks into one
in-memory byte string.

The durable download handle retains its read reservation until `close()`. The
cache target independently owns its write reservation until `commit()` or
`abort()`. A real localisation loop retries `resolve(...)` after `CacheWait`;
failure to create an optional cache copy does not make an otherwise valid
durable artifact unavailable.

## Byte Ranges

A provider may support one contiguous half-open range:

```text
[start, end)
```

The rules are:

- offsets are zero-based bytes;
- `0 <= start <= end <= byte_length`;
- omitted `end` means `byte_length`;
- invalid or unsupported ranges fail before streaming; and
- the stream yields exactly `end - start` bytes.

The full-object digest remains metadata and does not assert verification of one
isolated range. A transport builds multipart ranges from independently reserved
opens.

## Integrity

Commit-time representation validation is authoritative.

Open validates:

- committed durable-record state;
- representation-object reference and metadata;
- exact stored length/digest metadata; and
- cheap provider-native integrity signals.

The service does not require a complete pre-read rehash before streaming.
Providers may verify during read, use trustworthy native checksums, or perform
background scrubbing.

Detected corruption fails the read and quarantines the shared representation
object for every referencing cache/durable record. Corruption is never hidden by
silently rebinding the semantic instance.

## Retention

`retained_until`, when visible, reports the applicable claim deadline. The
active read reservation is separate:

- it allows the opened transfer to continue after the claim deadline;
- it does not silently refresh or extend the claim; and
- after the handle closes, the artifact may become collectible immediately when no
  other claim/reservation protects it.

A successful `a-task -> a-instance` binding follows that task output's artifact
claim and may outlive the task record. It does not survive merely because an
unrelated claim retains identical deduplicated content.

## Manifest Evolution and Typed Access

Primitive raw download relies on committed representation metadata. It remains
available even if the current runtime no longer installs the original
declaration codec.

Typed decoding or use as a current task input requires a compatible current
declaration and codec. Incompatible versions fail rather than reinterpret old
bytes.

## Cancellation

Cancellation or client disconnect:

- stops pulling further chunks;
- closes the active source stream;
- releases the read reservation; and
- does not affect task execution or longer-lived artifact claims.

## Progress

Structured progress may distinguish:

- task resolution/waiting;
- source open/reservation;
- byte transfer;
- cleanup; and
- completion.

Byte counts and total length are reported when available. Chunk sizes and
transport framing remain adapter details.

## Excluded Concerns

This contract does not define:

- HTTP status codes, headers, or authentication;
- pre-signed/direct client-to-provider download;
- multipart HTTP range framing; or
- typed file/object materialization conveniences.

These may be layered over the reserved transfer source.

## Failures

Download uses the universal [Failure Contract](07-failure-surface.md) with kind
`ARTIFACT_DOWNLOAD`. Stable issue codes distinguish:

- `ARTIFACT_LOCATOR_INVALID`;
- `ARTIFACT_LOCATOR_UNKNOWN`;
- `ARTIFACT_READ_UNAUTHORIZED`;
- `TASK_OUTPUT_TERMINALLY_UNAVAILABLE`;
- `ARTIFACT_NOT_RETAINED`;
- `ARTIFACT_CONTENT_CORRUPT`;
- `BYTE_RANGE_INVALID`;
- `BYTE_RANGE_UNSUPPORTED`;
- `PROVIDER_UNAVAILABLE`;
- `PROVIDER_CAPABILITY_UNSUPPORTED`;
- `OPERATION_CANCELLED`;
- `OPERATION_DEADLINE_EXCEEDED`; and
- `ARTIFACT_STORAGE_FAILURE`.

Issue context may include the requested locator, resolved instance locator,
requested range, committed length, provider capability, and retry hint. It
never contains artifact bytes, credentials, or provider paths. Provider causes
may be attached as bounded diagnostics.

## Invariants

1. Download accepts both `a-instance` and `a-task`.
2. Non-waiting open never blocks on a pending task output.
3. Returned bytes are the committed canonical representation.
4. Open and read-reservation acquisition are atomic.
5. Active readers prevent garbage collection.
6. Download never requires typed materialization.
7. Cache availability never substitutes for durable availability.

