# Handover prompt: artifact identity, scoped bindings, task outputs, and request wire format

You are continuing a design/specification cleanup in the Chunky repository at:

```text
/Users/harperj/Desktop/projects/personal/chunky
```

The repository is pre-stable. Prefer a direct coherent redesign over compatibility layers. Do not preserve legacy locator forms unless the user explicitly asks. Preserve unrelated working-tree changes: `specs/02-artifacts/01-artifact-model-surface.md` already contained a large user-authored rewrite before this thread.

Before editing specifications, read:

```text
AGENTS.md
specs/SPECS_RULE.md
specs/artifact-binding-redesign-refactor-notes.md
specs/Chunky-artifact-binding-redesign-Q1-Q139.md
specs/02-artifacts/01-artifact-model-surface.md
specs/01-runtime/02-run-request-building-surface.md
specs-back/02-artifacts/01-artifact-model-surface.md
specs-back/01-runtime/02-run-request-building-surface.md
specs-back/06-planning/01-task-planning.md
src/python/chunky_demo/demo_support.py
```

Follow the repository instruction to use the codebase knowledge graph for code discovery. The indexed project name is:

```text
Users-harperj-Desktop-projects-personal-chunky
```

## Current task context

The user is cleaning up the post-redesign artifact specifications. The thread began with the `### Content` section of `specs/02-artifacts/01-artifact-model-surface.md`. That section has already been rewritten to remove its TODOs and now defines:

- exact finite-byte content identity;
- BLAKE3-256 with canonical algorithm identifier `blake3`;
- canonical `blake3/{unpadded-base64url}` serialization;
- validation and byte-length verification rules; and
- an empty-content example digest.

Only that `### Content` section was changed by the assistant. The rest of the file contains substantial pre-existing user changes and unresolved TODOs.

The discussion then uncovered a broader identity and request-model problem. Do not perform a large refactor until the user confirms the final terminology and wire model.

## Current conceptual model

The strongest conceptual direction reached so far is:

```text
ContentId
  universal identity and primary address of one exact finite byte sequence

ArtifactSchema
  complete immutable interpretation contract that may read or write content

ArtifactSchemaId
  exact identity of one schema version, used by frozen planning and persisted
  resolution rather than ordinary caller requests

ArtifactRequest
  versionless planning request/selector

ScopedArtifactRequest
  hierarchy of semantic binding scopes ending in an ArtifactRequest

SuppliedArtifactBinding
  ScopedArtifactRequest bound to an ordered non-empty sequence of ContentIds

TaskArtifactBinding
  requested ScopedArtifactRequest resolved to an ordered sequence of ContentIds
  plus public binding status and failure information

ResolvedArtifactInterpretation
  internal/persisted ArtifactSchemaId + ContentId relationship

PlannedArtifactBinding
  internal exact schema, cardinality, selected source/producer and provenance
```

Important conclusions:

1. `ContentId` is the universal public identity for content. A caller can upload
   or download content using only its `ContentId`; no artifact schema is
   required at either transfer boundary.
2. The same content may be readable through more than one schema. An exact
   `ArtifactSchemaId + ContentId` pair describes one resolved interpretation,
   not the primary identity or address of the artifact.
3. `ArtifactSchema` replaces the current `ArtifactDeclaration<T>` concept, and
   `ArtifactSchemaId` names its exact immutable identity. The exact ID remains
   serializable for persistence, replay and diagnostics, but is not part of an
   ordinary caller request.
4. Retire ambiguous public `a-ref` and `a-instance` locator names. Use
   `ArtifactSchemaId` when exact interpretation identity is actually required,
   `ArtifactRequest`/`ScopedArtifactRequest` for caller intent, and `ContentId`
   for content transfer.
5. Use `ContentId`, with canonical `blake3/{digest}` text, rather than the old
   semantic upload fingerprint as the public content identity.
6. Do not use `ArtifactPath` as the public semantic type. It overlaps with
   `ArtifactRequest` and suggests a filesystem or storage location. Use
   `ScopedArtifactRequest` for a hierarchy-qualified request.
7. Do not use `VersionlessArtifactAddress`. A supplied value is a planning
   binding between a scoped semantic request and one or more exact content
   members. Use `SuppliedArtifactBinding`.
8. A requested output identifies a semantic binding, not necessarily one
   content object. Its result may contain zero, one or multiple ordered content
   members according to the producing stage contract.
9. Exact producer selection is not an MVP caller feature. Scope selects a
   semantic binding occurrence; the planner chooses and records the concrete
   producer. A future diagnostic constraint must be a separate typed field, not
   a hierarchy segment or artifact-path suffix.
10. Public task-output responses contain the original scoped request, binding
    status, ordered `{ordinal, content_id}` members and public failure data.
    Exact schema, selected producer, provenance and cache details remain in the
    internal task/planning record for MVP.

## Refined conclusion: hierarchy is part of the binding key

The earlier handover draft proposed a separate `at: [...]` scope and a separate
`producer_chain`. That wire direction is now superseded. It duplicated
information already carried by the hierarchical request structure.

The older demo is the clearest evidence:

```python
"music_processing": {
    "audio_preprocessing": {
        "audio_settings": InMemorySource(...),
    },
}
```

The nested keys encode one scoped artifact request:

```text
music_processing/audio_preprocessing/audio_settings
```

The request must not encode that hierarchy a second time in an `at`, `scope`,
or `producer_chain` field. Local nested mappings and durable binding envelopes
with an explicit `request` field are alternate representations of the same
scoped binding keys.

The current Python normalizer already demonstrates a restricted form of this
model in `RunRequest._flatten_request_value`. It recursively flattens nested
mappings, but permits only these old shapes:

```text
DOMAIN/KIND
DOMAIN/KIND/PHASE
PROJECTION/DOMAIN/KIND
PROJECTION/DOMAIN/KIND/PHASE
```

The redesigned model should replace the one-segment `projection_context` with
an arbitrary-depth hierarchy.

## Proposed type decomposition and hierarchical grammar

Use the following type model during the next design pass:

```text
ArtifactRequest
  {domain}/{content-kind}

ScopedArtifactRequest
  {scope-segment...}/{ArtifactRequest}

SuppliedArtifactBinding
  request: ScopedArtifactRequest
  members: ordered-non-empty-list<ContentId>

TaskArtifactBinding
  requested: ScopedArtifactRequest
  status
  members: ordered-list<ordinal + ContentId>
  failure?

ResolvedArtifactInterpretation (internal/persisted)
  schema: ArtifactSchemaId
  content: ContentId

PlannedArtifactBinding (internal)
  exact schema
  declared cardinality and publication capability
  selected source or producer
  binding provenance

ContentId
  blake3/{digest}
```

Examples of hierarchy depth:

```text
source/audio
audio_preprocessing/audio_settings
speech_processing/audio_preprocessing/audio_settings
music_processing/audio_preprocessing/audio_settings

punctuation_repair/transcript/token_stream
punctuation_repair/asr_transcribe/transcript/token_stream
```

The same family can then appear in each distinct semantic form:

```text
ArtifactRequest
  transcript/token_stream

ScopedArtifactRequest
  punctuation_repair/transcript/token_stream

SuppliedArtifactBinding
  request: punctuation_repair/transcript/token_stream
  members:
    - blake3/xxxxx

TaskArtifactBinding
  requested: punctuation_repair/transcript/token_stream
  status: AVAILABLE
  members:
    - ordinal: 0
      content_id: blake3/yyyyy
```

These types are related but not interchangeable:

1. `ArtifactRequest` asks for a compatible member of an artifact family. It
   contains no hierarchy, content identity or exact schema version.
2. `ScopedArtifactRequest` places that request at one occurrence in the
   planning hierarchy. It still does not identify bytes or an exact schema.
3. `SuppliedArtifactBinding` says that an ordered sequence of exact content
   members satisfies that scoped request. A singular binding has one member.
   The planner still resolves compatible stage requirements and readers.
4. `TaskArtifactBinding` reports the ordered content members produced for one
   requested binding. While production is active it exposes the evolving
   committed prefix; member count may be unknown until execution completes.
5. An exact schema selected to read or produce a member is resolution metadata.
   It does not replace the member's universal `ContentId` address.
6. `ResolvedArtifactInterpretation` and `PlannedArtifactBinding` are internal or
   persisted resolution records, not caller locators and not public content
   addresses.

The final `{domain}/{content-kind}` identifies the artifact family. Every
preceding segment scopes the request. Content identity is carried separately in
the binding envelope rather than appended to the semantic request.

The unscoped case is valid: with zero scope segments,
`transcript/token_stream` is both the `ArtifactRequest` serialization and the
serialization of its root `ScopedArtifactRequest`. The semantic types remain
distinct even where their wire text is identical.

The exact scoped-request separators remain an open syntax decision. `ContentId`
serialization is independently canonical and does not contain schema or scope.

## Meaning of the hierarchy

The hierarchy identifies where an artifact binding applies while a request is
planned. It is not a storage provider path and is not proof that any named
producer created the content. Its segments are stable semantic binding-scope
names advertised by the manifest/stage graph. One or more stages may advertise
interest in the same artifact family at such a scope; the planner still chooses
the concrete producer or consumer.

The working direction is downstream to upstream, matching the old demo:

```text
music_processing
  audio_preprocessing
    audio_settings
```

and the successive-producer example:

```text
punctuation_repair
  asr_transcribe
    transcript/token_stream
```

With this textual direction, a shallower applicable binding is a segment suffix
of a deeper occurrence. The planner therefore performs longest-applicable-suffix
matching while it walks backwards from a requested output. For example,
`audio_preprocessing/audio_settings` is a suffix of
`speech_processing/audio_preprocessing/audio_settings`.

Do not silently equate logical scope names with exact `producer_id` values. The
demo uses logical names such as `music_processing`, while the stage registry
uses IDs such as `audio.detect_music`. Hierarchy segments identify binding
occurrences, not producer provenance. The stage/edge contract must still define
which stable scope names may appear and how stages advertise requirements and
outputs within them.

## Universal binding shadowing

The hierarchy generalizes the old settings-specific override mechanism to all
supplied input roles. For example:

```text
inputs:
  - request: audio_preprocessing/audio_settings
    members: [blake3/Base...]
  - request: speech_processing/audio_preprocessing/audio_settings
    members: [blake3/Speech...]
  - request: music_processing/audio_preprocessing/audio_settings
    members: [blake3/Music...]
```

The planner resolves the longest applicable suffix. A deeper matching binding
shadows a shallower binding for that branch. This is immutable binding
selection, not content mutation.

Required rules:

1. A binding at the identical complete path may be declared at most once.
2. Same-path conflicts fail; there is no last-write-wins behavior.
3. A deeper binding shadows a shallower compatible binding only within the
   deeper branch.
4. A supplied binding terminates upstream producer traversal for the satisfied
   requirement.
5. Schema compatibility, cardinality, optionality and cycle validation still
   apply.
6. The scoped key mechanism is universal; settings are not a special scoping
   system. Resolution behavior remains role-specific: supplied inputs shadow,
   while requested outputs are independent planning roots and do not shadow one
   another.
7. Every semantically effective scoped binding contributes to invocation
   identity and production provenance.

## Successive producers and replacement

The specs should avoid calling successive production an artifact overwrite.
Content and completed bindings remain immutable. A later producer consumes an
earlier binding of the same artifact family and publishes a new binding.

The existing `processing_order` and `max_processing_order` stage metadata may
still drive producer discovery. The caller-facing hierarchy selects or exposes
a semantic binding occurrence without adding a second producer-chain array:

```text
punctuation_repair/asr_transcribe/transcript/token_stream
```

Supplying a binding for:

```text
request: punctuation_repair/transcript/token_stream
members:
  - blake3/SuppliedTokens...
```

satisfies the token-stream requirement within `punctuation_repair` and cuts off
further upstream production for that selected requirement:

```text
supplied token stream
        -> punctuation_repair
        -> requested repaired token stream
```

A more specific path such as:

```text
request: render_webvtt/punctuation_repair/transcript/token_stream
members:
  - blake3/xxxxx
```

applies only to that repeated binding occurrence under the WebVTT branch.

The stage specifications must define how successive producers advertise their
input and output binding scopes. Each successive producer must be capable of
consuming an earlier form and producing a later form of the same artifact
family. The concrete selected producer remains planning/provenance metadata;
other dependencies remain ordinary graph dependencies and are not encoded into
the scoped request.

## Durable upload and download reconciliation

The backed-up durable-upload boundary is conceptually correct: upload and
download operate on exact content without requiring artifact-schema knowledge.
Every artifact is primarily addressable by `ContentId`, and the same content may
later be interpreted through more than one compatible schema.

The public transfer model is therefore content-only:

```text
POST /content
  stream bytes with no known identifier
  -> calculate and return ContentId

PUT /content/{ContentId}
  stream bytes with a caller-defined ContentId
  -> apply caller-provided identity policy and return ContentId

optional expected content digest
  -> verify received bytes only; does not select ContentId

GET /content/{ContentId}
  -> stream exact bytes
```

Endpoint spelling remains a surface decision, but the semantic boundary does
not: upload accepts no `ArtifactRequest` or `ScopedArtifactRequest`, performs no
artifact-specific interpretation, and does not resolve a schema. Successful
publication returns the content result and retention/grace metadata defined by
the upload specification, with `ContentId` replacing the old semantic
fingerprint identity.

Binding content into a task request is a separate operation performed by the
caller when constructing the request:

```text
SuppliedArtifactBinding
  request: punctuation_repair/transcript/token_stream
  members:
    - blake3/xxxxx
```

That binding asserts where the content may satisfy a stage artifact
requirement. It does not change content identity and does not require the caller
to select an exact schema version. The planner resolves compatible stage
requirements and readers against the frozen runtime; malformed or unreadable
content may fail operational validation or execution without changing its
`ContentId`.

Request construction remains manifest-independent and performs no content I/O.
Durable task acceptance, however, must reject the submission if any supplied
`ContentId` is absent, unreadable or unauthorized. No task is created on such a
failure. Successful acceptance atomically:

1. verifies every supplied content member is currently resolvable;
2. creates active/recoverable-task protection for every member;
3. removes or converts each applicable upload-grace handoff claim; and
4. creates the task.

There must be no partial task creation, partial protection or collection gap.
Task protection remains through active, recoverable and `STOPPING` states and
ends only when the task actually terminalizes. Ordinary retention and other
independent claims may outlive it; requested-output retention does not
implicitly retain supplied inputs. This intentionally rejects the backed-up
optimization that allowed a missing supplied root when downstream cache hits
might make it unnecessary.

Multipart and live-capture operations continue to use opaque session IDs while
content is provisional. Their begin state carries transfer and retention
options, not a scoped artifact request. Commit/seal returns a content upload
result only after the finite byte sequence and its `ContentId` are known.

Possession of a supplied binding or `ContentId` is not authorization. Physical
paths, bucket keys, credentials and provider URLs remain outside semantic
identifiers.

## Identity and equality boundaries

Do not collapse these equality relations:

```text
Content equality and universal artifact address
  ContentId

Scoped binding-key equality
  ScopedArtifactRequest

Supplied binding equality
  ScopedArtifactRequest + ordered member ContentIds

Resolved interpretation equality, when needed internally
  exact ArtifactSchemaId + ContentId

Production provenance
  selected producer invocation + behavior version
  + effective ordered input bindings
  + effective semantic settings/bindings
  + resolved output set
```

A scoped request identifies a semantic binding occurrence, not stored bytes.
Two distinct scoped bindings may contain the same `ContentId`, and one content
object may be interpreted through several exact schemas. Neither relationship
creates a second content identity or a second physical object.

## Local and durable request forms

Local builders should preserve the recursive object form:

```python
inputs={
    "music_processing": {
        "audio_preprocessing": {
            "audio_settings": InMemorySource(...),
        },
    },
}
```

Durable serialization must replace process-local source handles with explicit
content bindings while preserving the same scoped keys:

```json
{
  "inputs": [
    {
      "request": "source/audio",
      "members": ["blake3/Source..."]
    },
    {
      "request": "speech_processing/audio_preprocessing/audio_settings",
      "members": ["blake3/Speech..."]
    },
    {
      "request": "music_processing/audio_preprocessing/audio_settings",
      "members": ["blake3/Music..."]
    }
  ],
  "outputs": [
    "render/webvtt_transcript"
  ]
}
```

These forms share the same canonical scoped binding keys but remain distinct
request representations: local requests own source/target handles, while
durable requests contain only serializable content identities and semantic
requests. Each complete input path may be declared once and contains one
ordered member sequence. Member order and duplicate `ContentId` values are
semantic; repeated declarations of the same complete path are invalid.

## Worked example from `demo_support.py`

`src/python/chunky_demo/demo_support.py` uses the older artifact types and
names, but its recursive request structure is the clearest executable example
of the desired ergonomics. Preserve the structure while replacing the legacy
identity and override concepts.

### Configuring branch-specific inputs

The relevant input configuration is:

```python
def build_inputs(audio_path: Path) -> dict[str, object]:
    return {
        "source": {
            "audio": FileSource(audio_path),
        },
        "audio_preprocessing": {
            "audio_settings": InMemorySource(
                AudioSettings({"sample_rate_hz": 44100, "channels": 1})
            ),
        },
        "speech_processing": {
            "audio_preprocessing": {
                "audio_settings": InMemorySource(
                    AudioSettings({"sample_rate_hz": 16000})
                ),
            },
        },
        "music_processing": {
            "audio_preprocessing": {
                "audio_settings": InMemorySource(
                    AudioSettings({"sample_rate_hz": 48000, "channels": 2})
                ),
            },
        },
        "settings": {
            "asr": InMemorySource({"model": "placeholder-asr"}),
            "language": InMemorySource({"language": "en"}),
            "diarization": InMemorySource({"model": "placeholder-diarizer"}),
            "alignment": InMemorySource({"mode": "word"}),
            "vtt": InMemorySource({"include_speakers": True}),
            "render": InMemorySource({"style": "caption-rich"}),
        },
    }
```

The recursive keys normalize conceptually to:

```text
source/audio
  -> FileSource(audio_path)

audio_preprocessing/audio_settings
  -> AudioSettings(sample_rate_hz=44100, channels=1)

speech_processing/audio_preprocessing/audio_settings
  -> AudioSettings(sample_rate_hz=16000)

music_processing/audio_preprocessing/audio_settings
  -> AudioSettings(sample_rate_hz=48000, channels=2)

settings/asr
settings/language
settings/diarization
settings/alignment
settings/vtt
settings/render
  -> corresponding InMemorySource values
```

The final two key segments form the `ArtifactRequest`; preceding segments in
deeper forms qualify it as a `ScopedArtifactRequest`. The source handle is the
value bound at that key. There is no second `at`, `scope`, or `producer_chain`
field.

For audio preparation, the deeper speech and music bindings shadow the
shallower `audio_preprocessing/audio_settings` binding only in their respective
branches. They do not field-merge with the shallower binding. The example's
`AudioSettings` default-value provider independently fills omitted fields, so
the speech value resolves to 16 kHz mono while the music value resolves to
48 kHz stereo.

The resulting effective bindings require distinct audio-preparation
invocations for speech and music. Transcription and diarization can share the
speech preparation invocation because both resolve the same scoped settings.
This is why scoped bindings must participate in invocation identity.

The names `speech_processing` and `music_processing` are evidence for semantic
binding scopes rather than exact producer IDs. The redesigned manifest/stage
contract must declare these names and define whether stages, requirement edges,
or another owning construct introduces them.

### Requesting an intermediate artifact for local delivery

The current demo requests only the final WebVTT artifact. With its existing
artifact names, the same ergonomic surface can explicitly request the ASR token
text intermediate as an additional output:

```python
def build_request_with_tokens(
    audio_path: Path,
    output_path: Path,
    token_text_path: Path,
) -> RunRequest:
    return RunRequest(
        inputs=build_inputs(audio_path),
        outputs={
            "render": {
                "webvtt_transcript": AtomicFileTargetHandle(output_path),
            },
            "transcript": {
                "token_text": AtomicFileTargetHandle(token_text_path),
            },
        },
    )
```

This example deliberately uses the demo's existing `transcript/token_text`
family rather than renaming it to `transcript/token_stream`. The redesign must
preserve the structural lesson, not copy the old schema vocabulary blindly.

`transcript/token_text` is a root `ScopedArtifactRequest` with no preceding
scope segments. Requesting an intermediate this way promotes it to a
caller-requested output; it does not stop it from also satisfying downstream
alignment. The planner should share the producer invocation when the effective
inputs and settings are identical.

`AtomicFileTargetHandle` is a process-local delivery attachment, not part of
artifact identity or durable semantic request data. The local runtime streams
or materializes the resolved intermediate into that target after it commits.

If more than one occurrence of the same artifact family can be produced, the
caller uses a deeper `ScopedArtifactRequest` to select the intended occurrence.
The exact scope qualification cannot be finalized until the binding-scope
declaration contract is decided. The request must fail as ambiguous rather than
selecting an occurrence arbitrarily.

### Downloading an intermediate artifact durably

The durable equivalent makes the intermediate an explicit requested output.
For now, do not introduce a separate caller-chosen `output_key`; the original
canonical `ScopedArtifactRequest` correlates each declaration with its task
result. Duplicate declarations of the same canonical requested output are
invalid.

```text
requested_outputs:
  - render/webvtt_transcript
  - transcript/token_text
```

After the token binding commits, task inspection reports the requested binding
and its zero, one or multiple ordered content members:

```text
GET /task/{task_id}/outputs

GET /task/{task_id}/outputs?request={encoded-ScopedArtifactRequest}

TaskArtifactBinding
  requested: transcript/token_text
  status: AVAILABLE
  members:
    - ordinal: 0
      content_id: blake3/Tokens...
```

The client downloads each member directly through its universal content
address:

```text
GET /content/blake3/Tokens...
```

For a plural intermediate binding, task lookup preserves member order and
returns one ordinal and `ContentId` per member. Each member is opened as its own
byte stream; download never concatenates the binding into a synthetic content
object. The same `ContentId` may appear at more than one ordinal, and the member
count need not be known when the request is submitted.

While the producer is active, the response exposes its evolving committed
prefix. Committed members are final even if later production fails. Binding
status carries production, successful completion or failure; `AVAILABLE` is the
successful complete state and no separate `complete` boolean is added. A
successfully completed requested-output binding remains `AVAILABLE` even if an
unrelated task branch later fails.

Downloading through a task output is not a cache read. Cache keys and provider
locations remain internal, and a cache hit does not create a public locator.
The task result maps the requested semantic binding to content IDs; the Content
Store supplies bytes by those IDs.

Under the recommended MVP boundary, an intermediate is guaranteed to be
task-addressable and retained for download only when the caller requested it as
an output. If the caller did not request it, it may exist transiently or in a
cache, but task output lookup does not expose it. Supporting post-hoc discovery
of every produced intermediate would require a separate task-produced-binding
inspection API plus explicit visibility and retention rules; do not obtain that
behavior by exposing cache internals.

## Requested outputs and task results

`producer_id + artifact` identifies an output capability, not one invocation or
one caller-requested output. The same producer may run more than once under
different hierarchical bindings.

The public `TaskArtifactBinding` stores each caller-requested
`ScopedArtifactRequest`, its status, public failure information, and the
resulting ordered member ordinals and `ContentId` values. The caller correlates
results by the original canonical scoped request. Exact schema, selected
producer/invocation, provenance and cache-resolution details remain in the
internal persisted task/planning record for MVP.

Do not introduce caller-chosen `output_key`. Retain collection lookup and a
focused `GET` lookup by encoded canonical `ScopedArtifactRequest`, but defer a
canonical `a-task` locator scheme until the scoped-request grammar is stable and
a portable locator has a demonstrated use.

This supersedes the caller-facing exact-producer decisions in historical Q81,
Q83, Q124 and Q138. It narrows Q17, Q30, Q82, Q85, Q86, Q90 and Q94 by replacing
`a-task` identity with task-result lookup keyed by the original scoped request.
Q125 remains valid: at most one output capability per stage/schema is
compatible with one producer running multiple scoped invocations.

## Verified implementation behavior and parity defect

The old demo and planner tests remain important evidence. Two downstream
branches push 16 kHz and 48 kHz audio settings and must create two
`audio.prepare` invocations. The speech branch may share one invocation between
transcription and diarization, while the music branch requires another.

Python currently passes the three focused planner-refactor tests. Rust passes
the default-value-provider test but fails the two tests that expect distinct
path-sensitive `audio.prepare` invocations, finding one instead of two. Do not
fix this unless implementation work is requested, but retain it as evidence
that hierarchical bindings must affect invocation identity in both languages.

Relevant locations:

```text
src/python/chunky_demo/demo_support.py
src/python/chunky/Pipeline/pipeline_models.py
src/python/chunky/Artifacts/artifact_models.py
src/python/chunky/Planning/TaskRequestGraph/
src/python/chunky_tests/test_planner_refactor.py

src/rust/crates/chunky/src/Artifacts/artifact_models.rs
src/rust/crates/chunky/src/Planning/task_request_graph/
src/rust/crates/chunky/tests/planner_refactor.rs
```

## Required specification changes

Treat this as a coordinated spec migration. Do not update one locator grammar in
isolation.

### `specs/00-overview/01-run-contract-overview.md`

1. Rewrite the end-to-end flow around `ContentId`, scoped supplied bindings and
   scoped requested outputs.
2. Remove public `a-ref`, `a-instance` and `a-task` terminology from the primary
   caller flow.
3. Show both task-output collection lookup and focused lookup by encoded
   canonical `ScopedArtifactRequest`.
4. Preserve the distinction between content storage, completed binding cache,
   task observation and retention/protection claims.
5. State that durable acceptance verifies and atomically protects every supplied
   `ContentId`; missing content creates no task.

### `specs/01-runtime/00-design-principles.md` and `specs/01-runtime/01-runtime-builder-surface.md`

1. Update the identity/publication principle to use `ContentId`, versionless
   scoped requests and internal exact interpretation records.
2. Rename public manifest-facing `ArtifactDeclaration` terminology to
   `ArtifactSchema` and exact identity to `ArtifactSchemaId` where those types
   are exposed by builder examples or feature contracts.
3. Preserve the deep-module boundary: public requests and task results remain
   semantic/content-oriented while exact producer, schema, provenance and cache
   coordination stay internal.
4. Keep Content Store, execution cache and artifact persistence as distinct
   service slots over shared exact content.

### `specs/02-artifacts/01-artifact-model-surface.md`

1. Define `ContentId` as the universal identity and primary upload/download
   address for one exact finite byte sequence.
2. Define `ArtifactRequest` as the unscoped, versionless artifact-family
   selector advertised by stages.
3. Define `ScopedArtifactRequest` as an arbitrary-depth hierarchy ending in an
   `ArtifactRequest`; do not retain `ArtifactPath` as an alias.
4. Replace the unresolved one-segment `scope?`/old `projection_context` idea
   with the hierarchy grammar.
5. Define `SuppliedArtifactBinding` as one scoped request plus ordered
   `ContentId` members; remove `VersionlessArtifactAddress`, public `a-instance`
   and the ambiguous public `ArtifactInstanceLocator` direction.
6. Treat exact schema plus content as resolved interpretation metadata, not the
   primary artifact identity or download locator. More than one schema may read
   the same content.
7. State equality separately for content, scoped binding keys, ordered binding
   members, resolved interpretations and production provenance.
8. Keep producer identity out of caller-facing binding paths. Hierarchy selects
   semantic binding occurrences; planning records the selected producer.
9. Move producer-ID syntax and processing-order mechanics to their owning stage
   and planning specifications, leaving concise references here.
10. Define internal/persisted `ResolvedArtifactInterpretation` and
    `PlannedArtifactBinding` records. Retire `a-ref` in favor of the explicit
    `ArtifactSchemaId` name while preserving exact schema identity for frozen
    planning, replay and diagnostics.
11. Runtime-generated canonical `ContentId` text is `blake3/{digest}`; its digest
    field uses unpadded Base64URL. A policy-accepted caller-defined ID carries
    its own algorithm and canonical value.

### `specs/02-artifacts/02-artifact-io-surface.md`

1. Make source/target handles bind against scoped artifact requests and ordered
   content bindings as appropriate.
2. Specify exact-content verification at upload when a caller asserts a
   `ContentId`; request construction does no transfer I/O.
3. Preserve ordered plural bindings without inventing compound content objects.
4. Clarify that transfer chunk/part boundaries are unrelated to binding-member
   boundaries.

### `specs/01-runtime/02-run-request-building-surface.md`

1. Restore recursive local input/output mappings as a normative ergonomic form.
2. Define their deterministic flattening into complete
   `ScopedArtifactRequest` strings.
3. Permit arbitrary-depth hierarchy instead of the old maximum of one
   projection segment.
4. Remove the `bindings[] + at[]` direction from the active redesign.
5. Do not add a separate caller-facing `producer_chain`; scoped requests select
   binding occurrences while planning selects concrete producers. Remove the
   MVP `/producer/{producer_id}` request suffix; any future diagnostic producer
   constraint is a separate typed option.
6. Define duplicate `ScopedArtifactRequest` failures and deterministic
   longest-applicable-suffix input shadowing. Keep graph-dependent applicability,
   compatibility and ambiguity validation in planning rather than the builder.
7. Define durable input envelopes as scoped requests with ordered `ContentId`
   members and their semantic equivalence to local nested mappings with source
   handles.
8. Represent requested outputs by canonical `ScopedArtifactRequest` without a
   separate caller-chosen `output_key` for now. Duplicate canonical requested
   outputs are invalid.
9. Resolve scalar/list shorthand and canonical ordered-member encoding while
   keeping one declaration per complete binding path.
10. Reconcile cache-policy placement with hierarchical intermediate bindings as
    well as requested outputs.
11. Include the `demo_support.py` branch-settings example and an explicit
    intermediate-output example in the normative examples.
12. Preserve edge-owned `ONE`/`ONE_OR_MORE` plus optionality. A supplied binding
    is non-empty when present; optional absence is represented by no supplied
    binding, not an empty supplied value.
13. Keep builder validation structural and I/O-free. Document durable task
    acceptance as the later boundary that verifies, protects and claims every
    supplied content member atomically.

### `specs/01-runtime/02-run-request-building-surface.wit`

1. Replace `content-digest` with `content-id` and replace flat
   `artifact-request` strings that permit producer suffixes with canonical
   `ScopedArtifactRequest` strings.
2. Remove `artifact-instance` and public exact-producer request forms.
3. Model each durable input as one scoped request plus an ordered non-empty list
   of explicit `{ordinal, content_id}` members.
4. Model requested outputs without `output_key`; duplicate canonical scoped
   requests are invalid.
5. Keep WIT DTOs data-only and align their validation issue contexts with the
   Markdown contract.

### `specs/01-runtime/03-durable-artifacts-upload-surface.md`

1. Preserve upload as a public content-only operation that accepts no artifact
   request, scoped binding or schema.
2. Return `ContentId` plus publication/grace/retention metadata after successful
   publication.
3. Support an optional caller-defined `ContentId`, including algorithm and value,
   under explicit trust policy. Keep it independent from
   `expected_content_digest`, which only verifies received bytes.
4. Keep publication create-if-absent and deduplicated by `ContentId`.
5. Carry only transfer, content-identity, integrity, grace and retention state in multipart and
   live-capture begin operations; return the content upload result after
   commit/seal.
6. Replace the old `fingerprint` name with caller-provided `ContentId` while
   preserving its policy-controlled identity override separately from integrity
   digests.
7. Preserve provisional-state, recovery, idempotency, grace and retention
   semantics. Define the revised task handoff explicitly: successful task
   acceptance verifies and pins all supplied content while deleting grace
   claims atomically; missing content rejects submission.

### `specs/01-runtime/03-durable-artifacts-upload-surface.wit`

1. Define separate `content-id` and `content-digest` records. Both carry an
   algorithm and value; caller-provided content identity and byte-integrity
   verification remain independent.
2. Ensure every direct, multipart and live-capture completion returns the same
   content upload result with `ContentId`, length and effective retention/grace
   deadlines.
3. Remove artifact request, schema and instance inputs from the WIT surface;
   represent the policy-controlled caller identity override as `content-id`.
4. Keep transfer state provisional until publication completes under the
   applicable identity and integrity policies.

### `specs/01-runtime/05-durable-artifact-download-surface.md`

1. Define public download directly by `ContentId`; no schema or scoped request
   is required to retrieve exact bytes.
2. Preserve read reservations, range semantics, integrity verification and
   provider-neutral streaming.
3. Update task-output inspection so every requested binding exposes its ordered
   committed member ordinals and `ContentId` values, binding status and public
   failure information only.
4. Include the explicit-intermediate flow: request the intermediate by its
   `ScopedArtifactRequest`, inspect that task output, then download each member
   by `ContentId`.
5. State whether non-requested produced intermediates are intentionally absent
   from public task lookup or are exposed through a separate inspection API.
6. Provide collection lookup plus focused `GET` lookup by encoded canonical
   `ScopedArtifactRequest`; defer a canonical `a-task` locator scheme.

### `specs/01-runtime/06-durable-artifact-operations-surface.md`

1. Replace the open `ContentDigest`/`a-task` split with `ContentId` raw-content
   operations and `TaskArtifactBinding` observation keyed by scoped request.
2. Keep raw content metadata/retention operations separate from task-output
   observation and requested-output retention.
3. Do not add public provenance lookup, `a-instance` operations or a canonical
   `a-task` locator for MVP.
4. Define whether focused task-output lookup lives here or in the download/task
   service, with exactly one owning specification.

### `specs/01-runtime/07-failure-surface.md` and `specs/01-runtime/07-failure-surface.wit`

1. Replace `content_digest`, `a-ref`, `a-instance`, producer-constraint and
   output-key contexts with `ContentId` and canonical scoped-request contexts.
2. Add or align durable-acceptance failures for missing, unreadable and
   unauthorized supplied content; these failures create no task.
3. Preserve member ordinal and binding-level failure contexts without inventing
   failures for unknown future plural members.
4. Keep request-builder structural failures separate from acceptance I/O,
   planning compatibility and execution/decoding failures.
5. Update the WIT variants and Markdown issue-code ownership together.

### `specs/01-runtime/04-run-surface.md` and `specs/01-runtime/06-durable-task-surface.md`

1. Carry normalized hierarchical bindings into task acceptance and planning.
2. Persist each requested output by its complete original canonical
   `ScopedArtifactRequest`; reject duplicate canonical declarations.
3. Return public `TaskArtifactBinding` entries containing only the original
   request, binding status, public failure information, and ordered committed
   member ordinals and `ContentId` values.
4. Persist selected producer, exact schema interpretation, provenance and cache
   details internally; do not expose them in the ordinary MVP task response.
5. Treat an explicitly requested intermediate exactly like any other requested
   output for observation, failure state and durable retrieval.
6. Preserve evolving committed-prefix observation, `AVAILABLE` as successful
   completion without a separate boolean, and binding status independent of
   overall task status.
7. Provide collection and focused scoped-request lookup without `output_key`;
   defer canonical `a-task` identity.
8. Reject durable submission when any supplied `ContentId` is unavailable.
   Atomically create task protection, remove grace claims and create the task;
   perform no partial mutation on failure.

### `specs/01-runtime/99-internals/05-run-binding-and-execution.md`

1. Replace exact `ArtifactRef`/`ArtifactInstance` binding flow with internal
   `ArtifactSchemaId`, `ResolvedArtifactInterpretation` and
   `PlannedArtifactBinding` records.
2. Preserve lazy representation decoding: acceptance proves content presence,
   not that every selected reader will successfully interpret the bytes.
3. Resolve supplied scoped bindings before upstream traversal, then apply
   longest-applicable-suffix shadowing and traversal cutoff.
4. Keep binding provenance and `CompletedBinding` as execution-cache identity;
   `ContentId` remains physical content identity.
5. Preserve live committed-prefix readers, final committed members, shared
   active production and cancellation/failure distinctions.

### `specs/03-bindings/01-bindings.md`

1. Define hierarchical binding keys and longest-applicable-suffix resolution
   for supplied inputs.
2. Define same-path conflict, deeper-path shadowing and traversal cutoff.
3. Apply scoped keys to every artifact role but define role-specific resolution:
   supplied inputs shadow; requested outputs remain independent planning roots.
4. Define ordered plural member semantics at each full binding path.
5. Preserve edge-owned `ONE`/`ONE_OR_MORE` plus optionality, runtime-assigned
   ordinals, semantic duplicates and independently valid members.
6. Keep active/failed `TaskArtifactBinding` observation separate from immutable
   reusable `CompletedBinding`, which exists only after successful closure.
7. Preserve incremental committed-prefix semantics independently of
   cardinality and publication policy.

### `specs/03-bindings/00-storage-boundaries.md`, `specs/03-bindings/03-content-store.md`, and `specs/03-bindings/04-artifact-persistence.md`

1. Preserve `ContentId` as the physical/durable content key.
2. Make `ContentId` the public upload/download address as well as the physical
   deduplication key.
3. Define aliasing: many semantic bindings and schema interpretations may refer
   to one content object.
4. Define retention and claims so supplied/task binding references protect the
   underlying content object.
5. Keep upload grace, task-input protection, active reads and ordinary retention
   as claims over content identity.
6. Guarantee task-output retention for explicitly requested intermediates; do
   not infer public retention merely because an incidental intermediate was
   cached.
7. Define atomic durable acceptance across presence/access verification,
   active-task protection, upload-grace deletion and task creation. Missing
   content or any failed check leaves no task and no partial claims.
8. Keep task input protection through active, recoverable and `STOPPING` states,
   release it only at terminalization, and do not let output retention
   implicitly pin inputs afterward.

### `specs/03-bindings/02-cache-management.md`

1. Ensure cache lookup/reuse distinguishes production provenance and effective
   hierarchical bindings even when output content is equal.
2. Keep `REFRESH` separate from hierarchical shadowing and successive
   production.
3. Define cache policy targeting for complete `ScopedArtifactRequest` values.

### `specs/04-fingerprinting/01-fingerprints.md`

1. Require every semantically effective hierarchical binding to contribute to
   invocation identity and produced-binding provenance.
2. Do not hash hierarchy into `ContentId`.
3. Do not use processing order as a substitute for actual producer identity.
4. Preserve ordered binding members and duplicates in provenance input.
5. Preserve exact selected `ArtifactSchemaId`, producer behavior version,
   effective settings and resolved output set in the canonical provenance
   payload.
6. Keep provenance internal to planning/cache for MVP; do not expose it through
   ordinary task responses or public lookup/input APIs.

### `specs/05-stages/stages-spec.md`

1. Define stable manifest-declared binding-scope segment names independently of
   exact producer IDs.
2. Define how stage input requirements propagate or extend the hierarchy.
3. Define validation for successive same-family producers represented by a
   hierarchy.
4. Retain `processing_order`/`max_processing_order` only as producer-selection
   metadata, not artifact or content identity.
5. Resolve the current producer-ID character-set mismatch: active artifact text
   uses underscore-safe IDs while implementation/examples use dotted IDs.
6. Remove caller/debug producer constraints from the MVP request contract while
   retaining stable producer identity for planning and provenance.
7. Preserve at most one output capability per stage/schema; this does not limit
   the number of scoped invocations of that capability.
8. Preserve edge-owned cardinality, optionality and incremental-publication
   capability independently of artifact identity.

### `specs/06-planning/01-task-planning.md`

1. Make hierarchical binding resolution a first-class planning input.
2. Specify deterministic longest-applicable-suffix matching and traversal
   cutoff for supplied inputs.
3. Distinguish supplied content, selected producer capability and concrete
   producer invocation.
4. Include hierarchy/effective bindings in invocation deduplication.
5. Define how semantic binding scopes interact with ordinary `processing_order`
   producer discovery without treating scope names as producer IDs.
6. Validate missing, ambiguous, incompatible and cyclic hierarchy paths.
7. Preserve distinct invocations of one stage/schema under different effective
   bindings.
8. Allow a requested intermediate to become an additional planning root while
   still sharing an already-required producer invocation when its effective
   invocation identity is equal.
9. Retain an internal `PlannedArtifactBinding` with exact schema, declared
   cardinality/publication capability, selected source/producer and expected
   binding provenance.
10. Do not expose exact-producer selection in caller requests; report the
    selected producer only through internal planning/diagnostic state for MVP.

### `specs/07-scheduling/02-persisted-task-lifecycle.md`

1. Persist original and resolved hierarchical requested-output information.
2. Correlate requested outputs and task results by the original canonical
   `ScopedArtifactRequest`; do not add caller-chosen output keys for now.
3. Protect all content members reachable from supplied and produced bindings.
4. Ensure task recovery reconstructs the same resolved binding paths and
   invocation identities.
5. Preserve the independent status and retention claim of each explicitly
   requested intermediate output.
6. Preserve task-input protection through active, recoverable and `STOPPING`
   states and release it only when the task terminalizes.
7. Keep requested-output binding status independent of overall task status and
   preserve already committed members across later branch failure.

### `specs/07-scheduling/eventing-and-task-control-model-spec.md`

1. Carry scoped binding occurrence and effective-binding identity into shared
   active-production demand and event contexts.
2. Preserve stage-to-stage incremental readiness, committed-prefix failure and
   cancellation semantics; client-to-running-task input streaming remains
   deferred.
3. Ensure STOPPING and cleanup retain supplied-content protection until actual
   terminalization.
4. Keep task/attempt IDs, publication mode and scheduling policy out of content
   and semantic binding identity.

### Terminology, overview examples and expected-stage documents

1. Update `specs/02-artifacts/99-terminology.md`,
   `specs/03-bindings/99-terminology.md` and
   `specs/05-stages/99-terminology.md` after the core model is approved.
2. Update `specs/99-stage-implementations/02-expected_stages.md` examples to show
   hierarchical successive producers without calling them overwrites.
3. Audit stage-specific specs that use exact artifact-instance or producer
   constraint language, beginning with
   `specs/99-stage-implementations/02-speech-detection/01-vad-speech-detection/spec.md`;
   use scoped bindings and internal resolved interpretations instead.
4. Preserve cardinality, incremental publication, cache/provenance and
   processing-order terminology that remains valid; do not rename unrelated
   stage payload fields merely because they contain words such as `version` or
   `fingerprinted`.

### Historical and working design documents

1. Append a post-Q139 reconciliation section to
   `specs/Chunky-artifact-binding-redesign-Q1-Q139.md`; do not rewrite the
   original answers. Record the new scoped-binding decisions and the exact Qs
   superseded or narrowed.
2. Update or clearly mark `specs/artifact-binding-redesign-refactor-notes.md` as
   superseded where it still requires `ContentDigest`, public `a-ref`,
   `a-instance`, `a-task` or producer-constrained requests.
3. Mark affected files under `specs/01-runtime/interviews/` as historical and
   point readers to the normative rewritten surfaces. Do not silently edit the
   interview record to make earlier decisions appear never to have existed.
4. Leave `specs/06-planning/older-versions/` as historical snapshots; exclude
   them from the normative refactor and from terminology consistency checks.

## Decisions established in this pass

1. `ContentId` is the universal identity and primary public upload/download
   address for exact content. It contains algorithm plus value and serializes as
   `{algorithm}/{canonical-value}`; the runtime-generated default is
   `blake3/{unpadded-base64url}`.
2. Upload and download are schema-independent. Upload does not accept a scoped
   artifact request and does not resolve an exact schema.
3. `ArtifactSchema` names the immutable interpretation contract and
   `ArtifactSchemaId` its exact versioned identity. Retire public `a-ref` and
   `a-instance`; exact schema/content interpretation remains internal or
   persisted metadata.
4. A `SuppliedArtifactBinding` associates one `ScopedArtifactRequest` with an
   ordered non-empty sequence of `ContentId` members.
5. A requested output identifies one semantic binding. Its task result reports
   an ordered sequence of member ordinals and `ContentId` values whose count may
   be unknown at submission time.
6. Do not introduce caller-chosen `output_key` values for now. Correlate a
   requested output and its result by the original canonical
   `ScopedArtifactRequest`.
7. Hierarchy segments identify semantic binding scopes rather than exact
   producer provenance. Concrete producers are selected and recorded by
   planning.
8. Exact producer constraints are not part of the MVP caller request. A future
   diagnostic constraint is a separate typed option, never a hierarchy segment
   or artifact-path suffix.
9. Public task results contain the original scoped request, binding status,
   public failure information and ordered `{ordinal, content_id}` members.
   Schema, producer, provenance and cache-resolution details remain internal for
   MVP.
10. Durable acceptance rejects missing, unreadable or unauthorized supplied
    content. It atomically verifies members, establishes task protection,
    removes grace claims and creates the task; failure performs no partial
    mutation.
11. Keep task-output collection and focused `GET` lookup by scoped request, but
    defer a canonical `a-task` locator scheme.

## Important unresolved decisions

Resolve these before the coordinated normative rewrite:

1. **Binding-scope declarations:** which manifest/stage construct declares
   stable hierarchy segment names and how input/output requirements propagate
   or extend them.
2. **Unscoped output selection:** how an unscoped artifact-family request selects
   the normal/terminal occurrence and when multiple occurrences must instead
   fail as ambiguous.
3. **Durable request member encoding:** whether to adopt the explicit
   `{request, members}` envelope shown here as canonical and which scalar/list
   shorthands, if any, external bindings accept.
4. **Requested-output encoding:** whether durable outputs are a list of
   canonical `ScopedArtifactRequest` strings or use an envelope for future
   request constraints, while still omitting `output_key`.
5. **Local output metadata:** how ordered target fan-out and cache policy attach
   to a hierarchical requested output without confusing target order with
   ordered artifact-member cardinality.
6. **Post-hoc intermediate discovery:** whether task inspection exposes only
   caller-requested outputs, as recommended for MVP, or also exposes incidental
   produced bindings with explicit visibility and retention rules.
7. **Authorization:** future scope-based visibility must not change universal
   content equality or make possession of a `ContentId` an authority grant.

## Recommended execution order

1. Resolve the remaining binding-scope ownership, unscoped-output selection and
   durable input/output envelope decisions; approve the worked examples.
2. Rewrite the artifact model, artifact I/O contract and artifact terminology
   around `ContentId`, `ArtifactSchemaId`, scoped requests and ordered bindings.
3. Rewrite run-request Markdown, WIT and failure contracts together so builders,
   wire DTOs and validation contexts agree.
4. Rewrite upload Markdown/WIT, Content Store, persistence and storage boundaries
   together, including atomic durable acceptance and task protection.
5. Rewrite run, durable-task, download, durable-artifact operations and task
   lifecycle/control surfaces together, including minimal public task results
   and focused scoped-request lookup.
6. Update bindings, cache, fingerprinting, stages, planning and run-binding
   internals together so cardinality, incremental production, exact internal
   resolution and provenance remain coherent.
7. Update the overview, design principles, runtime builder, explicit terminology
   files and expected/stage-specific examples.
8. Append the post-Q139 reconciliation and mark working/interview documents as
   historical or superseded without rewriting their original decisions.
9. Only then refactor Python and Rust, deleting the old projection/override and
   public locator compatibility machinery rather than preserving it alongside
   the new model.
