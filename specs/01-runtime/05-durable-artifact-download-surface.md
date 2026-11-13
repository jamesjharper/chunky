# Durable Content and Task-Artifact Retrieval

## Purpose

Durable retrieval has two distinct operations:

1. resolve one task-requested artifact binding into its committed member list;
2. download one exact content object by `ContentId`.

The service does not treat a plural binding as one byte stream and does not
require an artifact schema or scoped request to retrieve raw bytes.

## Task artifact lookup

Conceptually:

```text
list_task_outputs(task_id)
inspect_task_output(task_id, ScopedArtifactRequest)
    -> TaskArtifactBinding
```

HTTP-oriented form:

```text
GET /task/{task_id}/outputs
GET /task/{task_id}/outputs?request={encoded-ScopedArtifactRequest}
```

The result is the current immutable snapshot of the requested binding:

```text
TaskArtifactBinding
  requested: ScopedArtifactRequest
  status
  members: ordered list<TaskArtifactMember>
  failure?

TaskArtifactMember
  ordinal
  content_id: ContentId
```

The member list is the committed ordered prefix. `AVAILABLE` means it is the
complete binding; `PRODUCING`/terminal failures do not assert completeness.

Lookup never concatenates member bytes and never synthesizes ZIP/TAR/container
content.

The original canonical scoped request is the correlation key. Duplicate
requested outputs are invalid, and there is no caller-chosen output key or
public task-artifact locator. Exact schema, selected producer, provenance, and
cache resolution remain internal task/planning state.

## Raw content open

Conceptually:

```text
inspect_content(content_id) -> ContentAvailability
open_content(content_id, byte_range?)
    -> DurableContentDownload | Unavailable
```

HTTP-oriented form:

```text
GET /content/blake3/{unpadded-base64url-digest}
```

`open_content` never waits for a task to produce a digest that does not yet
exist. A caller first polls/observes the task-artifact binding when it needs
future members.

`inspect_content` distinguishes at least committed/readable, not present or no
longer retained, corrupt/quarantined, and temporarily unavailable provider
state. It does not acquire a long-lived read reservation. `open_content`
atomically rechecks readable state while acquiring its reservation, so an
inspection result is never a promise that a later open will succeed.

## Download result

```text
DurableContentDownload
  content_id: ContentId
  byte_length
  retained_until?
  source
```

The source is a pull-based asynchronous exact-byte stream with bounded memory
and provider-selected chunking.

Raw download is independent of artifact schema and scoped request. Internal
typed access may separately use `ResolvedArtifactInterpretation` when an exact
schema context is required.

Chunk boundaries and sizes are non-semantic. A complete download yields exactly
`byte_length` bytes and then stable EOF. Whole-object buffering is never
required by the contract, and a slow consumer applies backpressure to the
source.

## Read reservation

Opening readable durable content atomically acquires a read reservation.
Garbage collection cannot remove the underlying content while the reservation is
active.

Each download handle owns its reservation until explicit close, cancellation,
context exit, or stale-reader recovery. EOF may close the current stream while a
reopenable handle remains open according to provider capability.

## Byte ranges

A provider may support one contiguous half-open range:

```text
[start,end)
```

Offsets are zero-based and validated before streaming. The full `ContentId`
remains metadata for the whole object and does not claim verification of an
isolated range.

For a supported range `[start,end)`:

- `0 <= start <= end <= byte_length`;
- omitted `end` means `byte_length`;
- invalid or unsupported ranges fail before response bytes are exposed; and
- the stream yields exactly `end - start` bytes.

Multipart HTTP range framing is a transport-layer feature built from separate
reserved opens; it is not part of the common content-source contract.

## Integrity

Open validates committed/readable state, expected byte length and available
provider-native integrity signals. A complete pre-read rehash is not required.
Detected corruption fails the read and quarantines the shared content object
until exact repair.

If corruption or provider failure is detected after some bytes were delivered,
the stream terminates with failure rather than successful EOF. Consumers must
not treat a partial transfer as a valid content object merely because the
expected digest was known in advance.

## Native transfer and source ownership

A download source may expose opaque provider-native transfer offers for
reflink, block clone, provider-side copy or equivalent optimization. The common
surface never exposes local paths, object-store keys, credentials or signed
provider URLs as semantic metadata.

Native transfer must preserve exact bytes, cancellation, mutation isolation and
the destination's transactional commit. Streaming remains the universal
fallback.

Possession of a download source never authorizes destructive movement of the
stored content. Source disposition is preserve-only. Closing a stream or handle
releases its reservation; it does not delete the content object or any durable
task/cache reference.

## Retention

An active read reservation permits the opened transfer to continue even if its
ordinary retention deadline passes. It does not silently refresh or extend
retention.

A task-artifact member may become unavailable when that task's retention reason
expires even if identical content remains retained for another purpose, unless
the raw content service's applicable retention scope still authorizes/readies it.

## Provenance

The MVP defines no public `/artifact/provenance/...` endpoint. Provenance remains
an internal planning/cache key. A future surface may expose completed-binding
lookup without changing this content-download contract.

## Explicit intermediate retrieval

An intermediate is guaranteed task-addressable and retained for download only
when the caller included its `ScopedArtifactRequest` in the requested outputs.
The caller inspects that `TaskArtifactBinding`, then opens each returned
`ContentId` independently. Incidental produced intermediates are intentionally
absent from the MVP public task lookup even when they exist transiently or in
the execution cache.

```text
request output: transcript/token_text
GET /task/{task_id}/outputs?request=transcript%2Ftoken_text
  -> [{ordinal: 0, content_id: blake3/Tokens...}]
GET /content/blake3/Tokens...
```

## Typed access and manifest evolution

Typed decoding is layered above raw content open. An internal
`ResolvedArtifactInterpretation` fixes `ArtifactSchemaId + ContentId`, so later
manifest versions never silently reinterpret persisted content. This record is
not a validation certificate: codec/semantic validation may fail lazily. Public
task-output lookup does not expose the exact schema.

## Authorization

The MVP does not standardize principal/tenant policy, but implementations apply
their configured access check before opening content. Possession of a
`ContentId` or scoped binding is not an authority grant. Future scope-based
visibility must preserve universal content equality.

## Cancellation

Cancelling a download stops byte transfer and releases its read reservation. It
does not affect task execution or other content retention reasons.

## Failures

Retrieval uses the common data-only failure envelope. Stable distinctions must
exist for malformed `ContentId` or scoped request, unknown task/requested output, binding not
yet available, content not present or no longer retained, corruption, invalid
or unsupported range, provider unavailability/capability failure and operation
cancellation.

A task binding in `PRODUCING` is an ordinary snapshot, not a failed lookup. A
caller that requires complete membership waits/polls the binding; it does not
ask `open_content` to wait on an unknown future digest.

## Invariants

1. Task-artifact lookup returns binding metadata, not bytes.
2. Raw download is by `ContentId`.
3. Plural bindings are downloaded member-by-member.
4. Active readers prevent content deletion.
5. Raw download never requires typed materialization.
6. Successful full-object EOF means exactly `byte_length` bytes were delivered.
7. Download-source ownership never permits destructive movement of shared
   content.
