# Run Request Building

## Purpose

This specification defines I/O-free construction of local and durable run
requests after the artifact-binding redesign.

- `ArtifactRequest` is the unscoped, versionless `{domain}/{content-kind}`
  family selector.
- `ScopedArtifactRequest` is the canonical arbitrary-depth hierarchy ending in
  an `ArtifactRequest`.
- `SuppliedArtifactBinding` binds one scoped request to an ordered non-empty
  sequence of `ContentId` members.
- Requested outputs are unique `ScopedArtifactRequest` values.
- Local requests own process-local source and target handles; durable requests
  contain only serializable semantic requests and content identities.

Construction performs no manifest lookup and no content transfer. Planning,
acceptance, execution, task observation, and retention are owned by their
respective specifications.

## Canonical request grammar

```text
ArtifactRequest       = {domain}/{content-kind}
ScopedArtifactRequest = {scope-segment...}/{domain}/{content-kind}
```

Every segment matches `[a-z][a-z0-9_]*`; `/` is the only separator. Empty
segments, leading/trailing `/`, `.`, `..`, percent escapes, and non-canonical
case are invalid in the semantic value.
The final two segments form the artifact family; all preceding segments are
stable semantic binding scopes. At zero scope depth, the canonical wire text of
the two types is identical even though their semantic types are distinct.

Examples:

```text
source/audio
audio_preprocessing/audio_settings
speech_processing/audio_preprocessing/audio_settings
music_processing/audio_preprocessing/audio_settings
punctuation_repair/asr_transcribe/transcript/token_stream
```

The hierarchy is written downstream-to-upstream. It is one binding key, not a
storage path, producer chain, provenance claim, or second `at`/`scope` field.
Exact schema versions and producer constraints do not appear in the grammar.
Planning selects the exact schema and producer. A future diagnostic producer
constraint, if justified, must be a separate typed option.

## Recursive local mappings

Recursive mappings are the normative ergonomic form for local inputs and
outputs. A mapping node is either another mapping or a leaf value; mixed nodes
are invalid. Flattening joins every visited key with `/` and interprets the
complete path as one canonical `ScopedArtifactRequest`.

Flattening is deterministic:

1. preserve mapping iteration order;
2. validate every segment before joining;
3. require at least two complete-path segments;
4. recurse to arbitrary depth;
5. reject the same canonical complete path more than once; and
6. never consult a manifest or inspect source/target content.

Example from `src/python/chunky_demo/demo_support.py`:

```python
inputs={
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
}
```

This flattens to `source/audio`,
`audio_preprocessing/audio_settings`,
`speech_processing/audio_preprocessing/audio_settings`, and
`music_processing/audio_preprocessing/audio_settings`. The keys themselves
carry all hierarchy information.

## Local inputs

A local input leaf is a process-local source handle or an explicit ordered
non-empty sequence of source handles. A scalar is canonical shorthand for a
one-member sequence. Member order and repeated semantic member values are
preserved.

One complete input path may occur once. Repeating a mapping declaration does
not append and never uses last-write-wins. Optional absence is represented by
omitting the leaf, not by an empty value.

The planner resolves supplied bindings using deterministic
longest-applicable-suffix matching. For an occurrence
`speech_processing/audio_preprocessing/audio_settings`, the binding at that
exact path shadows `audio_preprocessing/audio_settings` only within the speech
branch. It does not field-merge with the shallower value. A selected supplied
binding terminates upstream traversal for the satisfied requirement.

The builder only preserves keys and values. Graph-dependent applicability,
schema compatibility, edge cardinality, optionality, ambiguity, and cycle
validation belong to planning.

## Durable inputs

The canonical durable input envelope is:

```text
SuppliedArtifactBinding
  request: ScopedArtifactRequest
  members: ordered-non-empty-list<SuppliedArtifactMember>

SuppliedArtifactMember
  ordinal: zero-based integer
  content_id: ContentId
```

Canonical JSON:

```json
{
  "request": "source/audio",
  "members": [
    {"ordinal": 0, "content_id": "blake3/AbCd..."},
    {"ordinal": 1, "content_id": "blake3/EfGh..."}
  ]
}
```

The explicit member envelope is the canonical external encoding. Ordinal must
equal list position. APIs may offer a source-language scalar `ContentId` or a
list of `ContentId` values as builder-only shorthand, normalized immediately to
the explicit envelope; such shorthand is not canonical durable wire data.
Duplicate member IDs are valid and order is semantic. Repeating the same
canonical request envelope is invalid.

Durable inputs are semantically equivalent to local nested input leaves with
source handles at the same scoped keys. They differ only at the transfer
boundary: durable requests refer to already-published content.

## Requested outputs

Each requested output is one unique canonical `ScopedArtifactRequest`:

```text
RequestedArtifactOutput
  request: ScopedArtifactRequest
  cache_policy?: ArtifactCachePolicy
```

The envelope is canonical so output-scoped policy can evolve without changing
request identity. It contains no caller-chosen output key, exact producer, exact
schema, target handle, or expected member count. Duplicate canonical requested
paths are invalid even when other metadata differs.

An unscoped request selects the unique manifest-declared default/terminal
occurrence for its family. If planning finds no such occurrence, or more than
one equally applicable occurrence, it fails missing or ambiguous rather than
choosing arbitrarily. A caller uses a deeper scoped request to select a repeated
occurrence explicitly.

An explicitly requested intermediate is an additional planning root and is
treated exactly like a final requested output for status, retention, and
retrieval. It may still feed downstream stages, and planning shares an existing
producer invocation when the effective invocation identity is equal.

Local intermediate example using the demo's existing vocabulary:

```python
RunRequest(
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

`transcript/token_text` is an unscoped requested binding. Its target is a local
delivery attachment, not artifact identity or durable request data.

## Local output metadata and fan-out

A local output leaf normalizes to:

```text
LocalRequestedArtifactOutput
  request: ScopedArtifactRequest
  targets: ordered-list<ArtifactTargetHandle>
  cache_policy?: ArtifactCachePolicy
```

A scalar target is shorthand for a one-target list. An explicit output object
is required when attaching cache policy or multiple targets. Target order is
delivery fan-out order and is unrelated to artifact-member ordinals. One
produced member may be delivered to several targets; one plural binding is
never concatenated into a synthetic member. A target-capability mismatch is a
planning/materialization error, not a change to request identity.

Cache reuse never removes delivery obligations. Process-local targets are
sinks; downstream stages consume the internal binding rather than rereading a
target file.

## Cache policy

```text
enum ArtifactCacheBehavior
  INHERITED
  CACHE
  NO_CACHE
  REFRESH
  READ_ONLY

ArtifactCachePolicy
  request: ScopedArtifactRequest
  behavior
  desired_ttl?
```

Policy keys are complete scoped requests, including hierarchical intermediate
bindings. One policy per canonical key is allowed. Resolution remains output
policy, then task policy, then stage/schema default. `REFRESH` controls cache
reuse and publication; it is unrelated to hierarchy shadowing or successive
same-family production.

## Run policy

`RunPolicy` controls only non-semantic execution behavior such as resource
admission and publication visibility. A forced incremental mode unsupported by
the selected edge is an explicit compatibility failure and never causes
semantic replanning.

## Canonical durable request

```json
{
  "version": 1,
  "inputs": [
    {
      "request": "source/audio",
      "members": [
        {"ordinal": 0, "content_id": "blake3/Source..."}
      ]
    },
    {
      "request": "speech_processing/audio_preprocessing/audio_settings",
      "members": [
        {"ordinal": 0, "content_id": "blake3/Speech..."}
      ]
    },
    {
      "request": "music_processing/audio_preprocessing/audio_settings",
      "members": [
        {"ordinal": 0, "content_id": "blake3/Music..."}
      ]
    }
  ],
  "outputs": [
    {"request": "render/webvtt_transcript"},
    {"request": "transcript/token_text"}
  ]
}
```

The request is data-only. It contains no live handles, filesystem paths,
credentials, provider locations, schema IDs, producer chains, or provenance
records.

## Validation boundaries

Builders aggregate deterministic structural issues without manifest lookup or
content I/O, including malformed segments or `ContentId`, a path shorter than
two segments, empty supplied members, non-contiguous ordinals, duplicate input
or output requests, mixed mapping/leaf nodes, invalid output metadata, and
invalid cache/run policy.

Planning later validates declared scope paths, graph applicability, schema and
cardinality compatibility, unscoped-output ambiguity, and cycles. Edge-owned
`ONE`/`ONE_OR_MORE`, optionality, and publication capability remain independent
of request identity.

Durable task acceptance is the later effectful boundary. It rejects the
submission if any supplied `ContentId` is absent, unreadable, or unauthorized.
Success atomically verifies every member, creates active/recoverable-task
protection, removes or converts applicable upload-grace claims, persists the
request and task, and returns a receipt. Failure creates no task, no partial
protection, and no collection gap.

## Builder lifecycle

`build()` freezes one immutable request. Local handles transfer explicitly from
caller to builder to request to execution. A failed build leaves the builder
and its handles valid for correction; closing an unbuilt builder releases its
owned handles. Runtime cleanup owns transferred handles across success,
failure, and cancellation.

## Invariants

1. Hierarchy appears once, in the canonical scoped request.
2. Each complete input or output path is declared at most once.
3. A supplied binding is non-empty; optional absence is omission.
4. Member order and duplicate `ContentId` values are semantic.
5. Local construction and durable serialization use the same scoped keys.
6. Builders are structural, manifest-independent, and I/O-free.
7. Concrete producers, schema IDs, provenance, and cache locations are internal.
8. Every durable supplied member is verified and protected atomically with task
   creation.
