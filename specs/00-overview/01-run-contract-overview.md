# Run Contract Overview

## Public model

Chunky exposes three independent public concepts:

```text
ContentId
  universal identity and upload/download address of exact finite bytes

ScopedArtifactRequest
  semantic hierarchy-qualified binding key ending in {domain}/{content-kind}

TaskArtifactBinding
  one requested scoped binding's status, failure, and ordered ContentId members
```

Exact `ArtifactSchemaId`, selected producer invocation, production provenance,
and cache resolution are frozen internal planning/persistence data. They are not
ordinary caller locators.

## End-to-end durable flow

```text
bytes + optional caller-defined ContentId + optional expected content digest
  -> POST /content or PUT /content/{ContentId}
  -> effective ContentId + length + grace/retention metadata

ContentId + ScopedArtifactRequest
  -> SuppliedArtifactBinding
  -> durable run request with unique scoped requested outputs
  -> atomic acceptance
  -> task

task + requested ScopedArtifactRequest
  -> TaskArtifactBinding
  -> ordered {ordinal, ContentId} committed members
  -> GET /content/{ContentId}
  -> exact bytes
```

Upload is content-only. It accepts no artifact request or schema. It either
calculates the `ContentId` or accepts a caller-defined algorithm and value under
explicit identity policy; an independent expected content digest only verifies
the received bytes. Multipart and live-capture session identifiers name
provisional transfer state only; commit/seal returns the same content upload
result.

A durable input separately asserts where content applies:

```json
{
  "request": "speech_processing/audio_preprocessing/audio_settings",
  "members": [
    {"ordinal": 0, "content_id": "blake3/Speech..."}
  ]
}
```

The request is versionless and manifest-independent. During durable acceptance,
the service verifies that every supplied member is present, readable, and
authorized. It atomically establishes active/recoverable-task protection,
removes or converts upload-grace claims, and creates the task. Any failed check
creates no task and no partial claims.

## Scoped planning

The final two request segments form the artifact family. Preceding segments are
stable semantic binding scopes declared by the manifest:

```text
ArtifactRequest
  audio_preprocessing/audio_settings

ScopedArtifactRequest
  speech_processing/audio_preprocessing/audio_settings
```

Hierarchy is written downstream-to-upstream. Supplied inputs use deterministic
longest-applicable-suffix resolution, so a deeper binding shadows a compatible
shallower binding only inside its branch and terminates upstream traversal for
that requirement. Requested outputs are independent planning roots and do not
shadow one another. Concrete producers are selected by planning and never
encoded as hierarchy segments.

## Requested outputs

Durable outputs are unique canonical scoped requests without caller-chosen
output keys:

```json
{
  "outputs": [
    {"request": "render/webvtt_transcript"},
    {"request": "transcript/token_text"}
  ]
}
```

An explicitly requested intermediate is retained and observed like any other
requested output. Incidental intermediates are not exposed through the MVP task
output API merely because they were produced or cached.

Collection lookup:

```http
GET /task/{task_id}/outputs
```

Focused lookup, where the query value is the percent-encoded canonical
`ScopedArtifactRequest`:

```http
GET /task/{task_id}/outputs?request={encoded-ScopedArtifactRequest}
```

Example result:

```json
{
  "requested": "transcript/token_text",
  "status": "AVAILABLE",
  "members": [
    {"ordinal": 0, "content_id": "blake3/Tokens..."}
  ]
}
```

The member list is the evolving committed prefix while production is active.
Committed members remain final if later production fails. `AVAILABLE` is the
successful complete binding status, with no separate completion boolean. One
binding may have zero, one, or many members according to the selected edge
contract; downloads open each member independently and never concatenate them.

## Service boundaries

- The **Content Store** owns immutable exact bytes keyed by `ContentId`,
  physical read/write reservations, integrity, and garbage collection.
- The **execution cache** owns reusable successful `CompletedBinding` records
  keyed by production provenance. Equal output bytes do not collapse distinct
  provenance records.
- **task observation** owns each requested `TaskArtifactBinding`, including its
  independent status and public failure information.
- **artifact persistence** owns semantic upload-grace, task-input, and
  requested-output retention claims and coordinates durable opens over Content
  Store objects.

A cache hit is not a public content locator, and task-output lookup is not a
cache read. Requested-output retention does not implicitly retain supplied
inputs after task terminalization. Content storage, completed-binding caching,
task observation, and retention/protection claims remain separate even when
they refer to the same `ContentId`.

## Local parity

Local builders preserve recursive mappings with process-local handles:

```python
inputs={
    "music_processing": {
        "audio_preprocessing": {
            "audio_settings": InMemorySource(...),
        },
    },
}
```

This is the object-form representation of the same canonical scoped key
`music_processing/audio_preprocessing/audio_settings`. Local targets are
delivery attachments; they do not change semantic binding identity or durable
wire data.

## Security boundary

A `ContentId` is universal equality, not an authority grant. Semantic
identifiers contain no provider path, bucket key, URL, credential, tenant, or
physical location. Authorization remains an independent check at acceptance
and retrieval.
