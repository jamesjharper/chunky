# Artifact I/O

## Purpose

Artifact I/O separates stage-facing typed values from provider-neutral transfer
of exact content bytes. It also defines the ordered binding reader/writer used
for `ONE` and `ONE_OR_MORE` stage edges.

Stages do not know whether content is backed by memory, cache, durable storage,
a local file, an object store, or another provider.

## Content transfer

A transfer source exposes one stable finite byte snapshot:

```text
ArtifactTransferSource
  content_id?
  byte_length?
  replayability
  ownership_disposition
  open_stream(byte_range?) -> asynchronous byte stream
  native_offers()
  close()
```

The portable fallback is a pull-based asynchronous byte stream with bounded
memory and backpressure. Transfer chunk boundaries have no semantic meaning.

A committed provider-backed source normally knows its `ContentId` and exact
byte length before open. A one-shot external source may learn them while being
ingested.

When an upload caller provides a `ContentId`, publication applies the upload
surface's caller-provided identity policy; that policy may permit the identity
without recomputing it. A separate expected content digest, when supplied, MUST
be verified over the complete exact byte sequence. Request construction itself
opens no stream and performs neither operation.

### Snapshot stability

A source is either:

- `ONE_SHOT`: at most one stream may be opened; or
- `REOPENABLE`: every open returns byte-for-byte identical content.

Native transfer offers may optimize copy/adoption but must preserve exact bytes,
ownership, mutation isolation, cancellation, transactional publication and
integrity.

## Codec

Each exact `ArtifactSchemaId` resolves to an immutable `ArtifactSchema`
containing its codec/interpretation contract.

```text
Codec<T>
  encode(value) -> ArtifactTransferSource
  decode(source) -> T
  buffering_capabilities
```

A codec may validate while decoding. Binding a `ContentId` to a scoped request
does not promise that the bytes were already successfully decoded. Invalid
content is reported when the consuming boundary discovers it.

Content identity is always the digest of exact encoded bytes. A codec does not
replace content identity with decoded semantic equality.

## Concrete artifact member

A stage-visible member is one exact content object under one exact schema:

```text
ArtifactMember<T>
  interpretation: ResolvedArtifactInterpretation
  read() -> T
  open_transfer_source() -> ArtifactTransferSource
```

`read()` may decode lazily and may buffer when the codec inherently requires
materialization.

## Input binding reader

Every stage input is exposed through one ordered asynchronous binding reader,
including `ONE` inputs.

```text
ArtifactBindingReader<T>
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  cardinality
  asynchronous iteration -> ArtifactMember<T>
  one() -> ArtifactMember<T>          # optional convenience
```

Conceptual usage:

```python
async for member in inputs.get_artifact(SOURCE_AUDIO):
    value = await member.read()
```

### Cardinality behavior

- required `ONE`: exactly one member;
- optional `ONE`: zero or one member;
- required `ONE_OR_MORE`: one or more members;
- optional `ONE_OR_MORE`: zero or more members.

An absent optional input is an already-closed empty binding. The core API does
not require a separate `get_optional_artifact() -> None` model.

### Live iteration

When an upstream output is configured for incremental publication, the reader
is live:

- iteration yields each committed member in binding order;
- waiting for the next member is cancellation-aware;
- successful binding closure produces normal end-of-sequence;
- upstream failure is reported after the already committed prefix;
- upstream cancellation remains distinguishable from successful EOF; and
- once yielded, a member is final and cannot later be revised or withdrawn.

A task cancellation must wake a reader blocked waiting for the next member.
Stage implementations should still call
`context.cancellation.raise_if_cancelled()` around expensive work and other
safe cancellation boundaries.

## Output binding writer

Every planned output edge is exposed as a runtime-owned ordered writer:

```text
ArtifactBindingWriter<T>
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  cardinality
  write_next(value) -> committed ArtifactMember<T>
```

The runtime owns member ordinals. Executors never provide sequence indexes.

`write_next(...)`:

1. verifies that another member is permitted by the declared upper bound;
2. encodes or accepts the member through the schema codec;
3. commits exact bytes to required publication targets;
4. determines and verifies the `ContentId`;
5. appends that digest at the next contiguous ordinal; and
6. makes the member visible according to the active publication policy.

For a `ONE` writer, a second `write_next(...)` fails immediately and the stage
invocation fails.

### Binding close

Executors do not explicitly close output bindings.

```text
executor returns normally
  -> runtime validates every minimum cardinality
  -> runtime closes bindings successfully
  -> reusable CompletedBinding records may commit
```

If the executor raises, cancellation wins, or final cardinality validation
fails:

- the invocation fails;
- open bindings do not become successful completed bindings;
- already committed members remain valid content and may remain visible through
  task-output history; and
- incomplete bindings are not reusable cache hits.

## Incremental publication

Incremental publication is independent of cardinality.

- `ONE + INCREMENTAL`: the one final member may become available before the
  stage invocation finishes.
- `ONE_OR_MORE + INCREMENTAL`: each final member may become available as it is
  committed; successful executor completion closes the sequence.
- `AT_COMPLETION`: committed work is not exposed to the requesting consumer as a
  complete task output until successful binding closure.

Incremental publication means **finality**, not provisional progress. A stage
must buffer internally until it can irrevocably commit a member.

## Publication plan

A member may need several targets, such as:

- Content Store;
- execution cache verification/publication;
- durable task-output retention;
- process-local caller targets.

The runtime publication plan owns target preparation, commit, abort and
ordering. A stage writes one semantic member through its binding writer and does
not directly manipulate cache, persistence or materialization targets.

For partial output cache hits, a writer may be a verification writer: the stage
still sees the complete resolved output set, but recomputed members for an
existing binding are compared against the immutable cached result instead of
silently replacing it.

## Materialization

Caller delivery is separate from content identity, artifact schema, binding
identity and cache identity.

Materialization may consume either the typed member or its transfer source. A
failed local target does not change a `ContentId` or invalidate an already
committed durable/cache object.

Process-local source and target handles attach to complete
`ScopedArtifactRequest` keys. Durable sources attach through
`SuppliedArtifactBinding` ordered members; target fan-out order is delivery
metadata and never binding-member order.

## Source and target ownership

Source and target handles are explicitly closeable. The runtime owns stage-bound
handle lifetimes and keeps shared provider reservations alive while dependent
consumers still require them.

Cancellation:

- stops pulling further transfer chunks promptly;
- aborts uncommitted targets;
- never rolls back content or target publication that already committed; and
- releases reservations when safe.

## Invariants

1. Every stage input is an ordered binding reader.
2. Every output is an ordered binding writer.
3. `ONE` is the one-member case of the same binding abstraction.
4. Runtime-assigned ordinals are contiguous and deterministic.
5. Published members are final.
6. Successful binding closure validates cardinality.
7. Failed/incomplete bindings never become completed cache hits.
8. Transfer chunks, multipart parts, and live-capture segments are unrelated to
   artifact-binding member boundaries.
9. Exact content bytes determine `ContentId` regardless of codec or provenance.
10. A plural binding is an ordered sequence of independent content objects,
    never a compound or concatenated content object.
