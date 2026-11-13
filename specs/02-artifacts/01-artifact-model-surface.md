# Artifact Model

## Purpose

This specification defines Chunky's artifact identity model, versionless
artifact requests, exact artifact schemas, and scoped content bindings.

The key distinction is between:

- **content identity**: one exact finite byte sequence;
- **artifact schema**: how content is interpreted by a stage or codec;
- **artifact binding**: an ordered zero/one/many-member value on a stage edge;
- **production provenance**: the deterministic identity of a complete binding;
- **task-output observation**: the evolving public result for one
  caller-requested scoped binding.

Storage, transfer, cache, provenance hashing, task lifecycle, and stage execution
are defined by their owning specifications.


Artifact Parts


Version

Domain:


## Identity and request types

Chunky deliberately uses different types for exact bytes, exact
interpretations, caller intent, hierarchy-qualified binding keys, supplied
values, and task results. They are related but not interchangeable. The old
public locator names are retired.

### Content
A content object is one finite ordered byte sequence. `ContentId` is it's a universal identity, its physical deduplication key, and its primary public upload/download address.

```text
ContentId
  algorithm: string
  value: string
```

The required runtime-generated algorithm is BLAKE3-256. Its canonical algorithm identifier is `blake3`, and that identifier permanently denotes the 32-byte BLAKE3 output. A caller-defined `ContentId` supplies both its algorithm identifier and value; the upload surface may accept a non-default algorithm only under explicit caller-provided content-ID policy.

Algorithm identifiers and their meanings are part of the storage contract; they are not runtime-configurable and must never be reassigned or reinterpreted after content has been stored.

> A future runtime-generated algorithm requires a distinct identifier and an explicit specification update. Implementations must reject an unknown caller-provided algorithm unless explicit upload policy defines and permits it.

The digest is calculated over the exact logical bytes only. Byte length is mandatory integrity and storage metadata but is not part of content equality.

The canonical textual form is `{algorithm}/{canonical-value}`. For the required runtime-generated algorithm it is:
```text
blake3/{unpadded-base64url-digest}
```

The BLAKE3-256 payload is exactly 43 Base64URL characters and contains no `=`
padding. For example, the digest of an empty content object is:

```text
blake3/rxNJufX5oaagQE3qNtzJSZvLJcmtwRK3zJqTyuQfMmI
```

Parsing must reject a non-canonical algorithm identifier or algorithm-specific value. For `blake3`, it must reject padded or otherwise non-canonical Base64URL, a payload that does not decode to exactly 32 bytes, or a value whose canonical re-encoding differs from the input.

Unless explicit caller-provided content-ID policy permits a trusted identity without byte verification, verification must recompute the identified algorithm over supplied or retrieved bytes and reject an identity or byte-length mismatch.

Two content objects have the same content identity exactly when their algorithm identifiers and digest values are equal. Byte length is verified metadata, not
part of equality.

### Artifact schema

`ArtifactSchema` and `ArtifactSchema<T>` define one complete immutable contract for interpreting or writing content as a typed artifact value.

An `ArtifactSchema`'s identity is expressed by `ArtifactSchemaId`.

```text
ArtifactSchemaId
  domain: string
  content_kind: string
  schema_version: positive integer
```

Its canonical serialization is:

```text
{domain}/{content-kind}/v{schema-version}
```

Examples:

```text
source/audio/v1
transcript/token_stream/v2
render/webvtt_transcript/v1
```


// TODO: ADD DEFINITION OF WHAT "DOMAIN" and "CONTEXT_KIND" ACTURALLY MEAN

`domain` and `content_kind` name the artifact family.

`schema_version` fixes all schema-owned codec, validation, and interpretation semantics; incompatible changes require a new version. Exact schema IDs are serializable for frozen planning, persistence, replay, and diagnostics, but ordinary caller requests do not contain them.

`ArtifactSchemaId` does not contain:
- processing order;
- producer identity;
- cardinality or optionality;
- content identity or provenance;
- task identity;
- storage/provider information;
- projection/presentation context; or
- a separately serialized codec version.

### Artifact request

`ArtifactRequest` is the unscoped, versionless selector for one artifact family

advertised by stages:

```text
ArtifactRequest
  domain
  content_kind
```

Its canonical text is exactly two non-empty canonical path segments:

```text
{domain}/{content_kind}
```

Examples:

```text
transcript/token_stream
render/webvtt_transcript
```

An artifact request contains no scope, schema version, content identity, or producer constraint. Planning resolves it against the frozen manifest.

### Scoped artifact request

//  TODO: Why cant "ArtifactRequest" be merged into ScopedArtifactRequest?

`ScopedArtifactRequest` identifies one occurrence of an artifact-family binding
in the planning hierarchy. Its canonical text is an arbitrary-depth sequence of
zero or more scope segments followed by the two `ArtifactRequest` segments:

```text
{scope-segment...}/{domain}/{content-kind}
```

Every semantic segment uses `[a-z][a-z0-9_]*`; `/` is the only segment
separator. Empty segments, leading/trailing `/`, `.`, `..`, percent escapes,
and non-canonical case are invalid in the semantic value. HTTP query/path
embedding applies ordinary percent-encoding to the complete already-canonical
value and does not change its identity.

Examples:

```text
transcript/token_stream
punctuation_repair/transcript/token_stream
punctuation_repair/asr_transcribe/transcript/token_stream
music_processing/audio_preprocessing/audio_settings
```

The final two segments form the `ArtifactRequest`; every preceding segment is a
stable semantic binding-scope name declared by the manifest. The zero-scope
serialization is textually identical to its `ArtifactRequest`, while the two
semantic types remain distinct. Segments are written downstream-to-upstream,
so a shallower applicable key is a suffix of a deeper occurrence.

The hierarchy is not a storage path, producer chain, or proof of provenance.
It must not contain exact producer IDs. The planner selects and records a
concrete producer separately.

### Supplied artifact binding

`SuppliedArtifactBinding` binds one complete scoped request to an ordered,
non-empty sequence of exact content members:

```text
SuppliedArtifactBinding
  request: ScopedArtifactRequest
  members: ordered-non-empty-list<ContentId>
```

Member order and duplicate `ContentId` values are semantic. A singular value is
a one-member binding. Optional absence is represented by no binding, never an
empty supplied binding. One complete scoped key may be declared at most once.

### Task artifact binding

`TaskArtifactBinding` is the public observation of one requested output:

```text
TaskArtifactBinding
  requested: ScopedArtifactRequest
  status: binding status
  members: ordered-list<TaskArtifactMember>
  failure?: public failure

TaskArtifactMember
  ordinal: zero-based integer
  content_id: ContentId
```

While production is active, members are the evolving committed prefix. Each
committed member is final even if later production fails. `AVAILABLE` is the
successful complete status; there is no separate `complete` boolean. Task
results are correlated by the original canonical `ScopedArtifactRequest`, not
by a caller-chosen output key or public task-artifact locator.

### Internal resolved records

Exact interpretation and production choice remain internal or persisted
resolution metadata:

```text
ResolvedArtifactInterpretation
  schema: ArtifactSchemaId
  content: ContentId

PlannedArtifactBinding
  request: ScopedArtifactRequest
  schema: ArtifactSchemaId
  cardinality and publication capability
  selected source or producer invocation
  expected binding provenance
```

More than one schema may read the same content. Consequently,
`ArtifactSchemaId + ContentId` identifies one resolved interpretation, not a
second content object and not the primary upload/download address. Decoding may
still reject malformed or incompatible bytes when the selected reader consumes
them.


## Artifact roles

Artifacts retain semantic roles such as:

```text
SOURCE
SETTINGS
INTERMEDIATE
TARGET
DIAGNOSTIC
```

Role is declaration metadata used for manifest validation and documentation. It
is not part of content identity.

Settings remain semantic inputs but have additional planner behavior described
under **Settings** and the fingerprint/provenance specification.

### Role details
Every artifact has exactly one role:

```python
class ArtifactRole(str, Enum):
    SOURCE = "source"
    SETTINGS = "settings"
    INTERMEDIATE = "intermediate"
    TARGET = "target"
    DIAGNOSTIC = "diagnostic"
```

### Source role 
Source artifacts are externally supplied inputs.

Source artifacts are fingerprinted when selected. Live request binding reads the
source directly and does not publish it to cache by default. Durable reuse
requires explicit cache publication or canonicalization.

__Example:__
```text
source/audio
```
#### IO
Source artifacts must provide a source handle for reading content.

__Example as input file:__
```python
inputs={
  "source/audio": AudioSource.from_file_path("input.wav"),
}
```

### Settings role:
Settings artifacts are resolved configuration values.

Settings are normal artifacts. A caller-supplied settings artifact takes precedence over the declaration default for that artifact. Declaration defaults are resolved lazily only after planning determines that the setting is relevant. Path-pushed or inherited settings are planner-owned effective-settings overlays, not a separate hidden configuration channel.

The fully resolved effective settings value is fingerprinted. A defaulted
settings value is treated as a supplied graph-root value after it is resolved,
even when the caller did not explicitly provide it. This does not imply
publication of caller-supplied or defaulted settings.

A durable settings root is supplied as a `SuppliedArtifactBinding`. The runtime
may bind and read its `ContentId` members before graph planning, so
settings-dependent planning hooks see the same value they would see from an
ordinary local source handle.

__Examples:__
```text
speech_detection/vad_settings
speech_detection/vad_detector_profiles
speaker_detection/diarization_settings
speech_detection/diarization_detector_profiles
```

#### IO
__Example as input object:__
```python
inputs={
  "speaker_detection/diarization_settings": DiarizationSettings.from_object({
    "detect_min_speakers": 2,
    "detect_max_speakers": 10,
  }),
  "render/webvtt_settings": WebVttSettings.from_object({
        "include_speakers": True,
  })
}
```

__Example as input file:__
```python
inputs={
  "speaker_detection/diarization_settings": DiarizationSettings.from_json_file("./diarization_settings.json")
}
```

### Intermediate role
Intermediate artifacts are produced by one stage and consumed by another.

Intermediate artifacts are cached according to their declaration and effective
produced-artifact cache mode. Their availability controls dependency readiness
and downstream data flow. Stage-produced intermediates are fingerprinted from
production pedigree rather than content values. An explicitly supplied
intermediate is fingerprinted and bound as a supplied input and is not
implicitly published.


__Examples:__
```text
utterance_detections/vad_stream
utterance_detections/diarization_stream
utterance_detections/utterance_stream
speaker_detection/speaker_role_token_stream
chunk_plan/chunk_segments
```

#### IO
Intermediate artifacts can optionally provide a target or source handle.

__Example as output:__
```
outputs={
  "speaker_detection/speaker_role_token_stream": SpeakerRoleTokenStreamTargetArtifact.to_file_path("speaker_role_token_stream.csv"),
},
```
__Example as input:__
```
inputs={
  "speaker_detection/speaker_role_token_stream": SpeakerRoleTokenStreamSourceArtifact.from_file_path("speaker_role_token_stream.csv"),
},
```


### Target role
Target artifacts are requested final outputs.

Target artifacts are write only from the perspective of the producing stage. Their semantic values may be reused when the requested target fingerprint matches a previously produced target.

__Examples:__
```text
target/embeddings
target/transcript
```

#### IO
Live targets normally have one or more caller materialization handles. A live
request may instead declare `expect_cached_output`, and canonical outputs are
always targetless. Both targetless forms require a cacheable declaration with
`DEFAULT` or `REFRESH` mode; their retention guarantee comes from the provider
and task contract.

__Example as output:__
```
outputs={
  "render/webvtt_transcript": WebVttTargetArtifact.to_file_path("output.vtt"),
},
```

__Example as target-less output:__
```
outputs={
  "render/webvtt_transcript": [],
},
```

### Diagnostic role
Diagnostic artifacts are requested inspection outputs.

Non-deterministic diagnostic artifacts are write only from the perspective of the producing stage. They cannot be reused by subsequent tasks, but can be requested by the task configuration.

They can also be queried outside of the task flow by a given task id. 

__Examples:__
```text
{stage}/logs
{stage}/performance
performance/all_stages
speaker_detection/detect_stats
```

#### IO
Diagnostics require a target handle to be configured as part of the task configuration to be written.
The stage declaration should expose the expected diagnostic output format.

__Examples:__
```
outputs={
  "speaker_detection/diagnostics_detect_stats": DiagnosticLogArtifact.to_file_path("status.log"),
},
```





## Artifact bindings

A binding is the ordered collection of concrete content members satisfying one
stage input, stage output, or task-output request. Each member is independently
addressable by `ContentId` and is interpreted through the exact
`ArtifactSchemaId` selected for that edge.

```text
ArtifactBinding
  request: ScopedArtifactRequest
  ordered members: [ContentId, ...]
```

### Equality:
The list is ordered, and not a set:
```text
[A, B, C] != [A, B]
[A, B]    != [B, A]
```

### Duplication
Duplicate content members are valid when the producing or supplying semantics require them.
i.e., the following is valid
```text
[A, A, B]
```

### Discrete Members
Each member of a binding is a discrete, individual, self-contained artifact.

***For example,***
A multi-file recording upload may be split across multiple files. Each file must be independently valid, i.e. include correct file headers, and should not be chunked representation of a complete file artifact.

Storage-level multipart upload, object sharding, and transfer chunking are separate implementation concerns and never create artifact-binding members.

## Cardinality

Cardinality belongs to stage input/output edges, not artifact schemas. The same
exact `ArtifactSchemaId` may be used by different stages with different
cardinality. The stage specification owns declaration and validation; this
section defines only the shared meaning.

```text
enum ArtifactCardinality
  ONE
  ONE_OR_MORE

ArtifactEdgeCardinality
  cardinality: ArtifactCardinality
  optional: bool
```

Meaning:

```text
required + ONE          = exactly 1
optional + ONE          = 0..1
required + ONE_OR_MORE  = 1..N
optional + ONE_OR_MORE  = 0..N
```

For `ONE_OR_MORE`, the runtime knows the cardinality class but does not know the
final `N` until the producing binding closes successfully.


## Producer identity and processing order

Stable producer IDs and `processing_order`/`max_processing_order` are owned by
the stage and planning specifications. They select and identify concrete
production capabilities, but are never content identity, schema identity, or
segments of a caller-facing scoped request. A stage may advertise at most one
output capability for a given exact schema while running that capability in
multiple distinct scoped invocations.

## Settings

## What are Setting Artifacts
Settings are semantic artifact values whose final effective values may be resolved from caller overrides and declaration defaults before complete stage requirements are known.

#### Setting Requirements
They remain part of the artifact model, but the planner may expose an effective
settings view to requirement/output resolution. Settings schemas must define a
deterministic finite value tree and its canonical encoding; JSON is one
possible representation, not a required storage format.

Production provenance includes a separate deterministic fingerprint of the final merged effective settings set. The provenance specification owns that calculation.

## What are not Setting Artifacts
Runtime-only configuration such as credentials, service endpoints, resource limits, retries, and transport tuning is not a settings artifact unless the contract explicitly intends it to change semantic outputs.

## Request and declaration resolution

The normal resolution path is:

```text
ArtifactRequest
  transcript/token_stream
        ↓ hierarchy qualification
ScopedArtifactRequest
  punctuation_repair/transcript/token_stream
        ↓ manifest and producer resolution
ArtifactSchemaId + selected producer invocation
        ↓ planning/provenance
PlannedArtifactBinding
```

The caller's request identity remains stable even when the planner resolves an
exact schema and producer internally.

## Equality and canonicalization
- Content equality and universal address: `ContentId`.
- Artifact-family request equality: `ArtifactRequest`.
- Scoped binding-key equality: complete canonical `ScopedArtifactRequest`.
- Supplied binding equality: scoped key plus ordered member `ContentId` values.
- Resolved interpretation equality: exact `ArtifactSchemaId + ContentId`.
- Production provenance: selected producer invocation and behavior version,
  effective ordered input bindings, effective semantic settings/bindings, and
  resolved output set.

Canonical serializers must produce one stable representation. Parsers may
accept documented shorthand only where the receiving API makes the expected
value type unambiguous.

## Location and authorization
Artifact and content identifiers contain no filesystem path, object-store key, region, cache-provider name, credential, or authorization grant.

Content identity is globally stable as an equality relation. Actual presence, retention, and retrieval are scoped to the configured runtime/service. Authorization and tenant isolation are future capabilities; the MVP does not claim that possession of an identifier authorizes access.

## Invariants

1. One `ContentId` denotes one exact finite logical byte sequence.
2. Byte length is not part of content identity.
3. `ArtifactSchemaId` identifies one immutable schema and contains no producer
   or processing-order information.
4. Caller artifact requests are versionless; planning resolves exact schema
   versions.
5. Scope hierarchy is part of binding-key equality but never content identity.
6. Cardinality belongs to bindings/edges, not schemas.
7. Ordered bindings preserve order and duplicates.
8. Multi-member bindings do not create synthetic compound content objects.
9. Producer identity and processing order affect planning/provenance only.
10. Exact schema plus content is resolved interpretation metadata.
11. Public task results expose only the original scoped request, status,
    public failure, and ordered committed content members.
