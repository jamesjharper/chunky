# Durable Task Service

## Contract

Durable acceptance persists normalized hierarchical supplied bindings and each
unique original requested `ScopedArtifactRequest`. Duplicate canonical
requested outputs are rejected before task creation.

Acceptance atomically verifies presence, readability, and authorization for
every supplied `ContentId`, establishes task-input protection, removes or
converts applicable upload-grace claims, and creates the task. Failure creates
no task and performs no partial claim mutation.

Each requested output has one public `TaskArtifactBinding`:

```text
TaskArtifactBinding
  requested: ScopedArtifactRequest
  status
  members: ordered-list<{ordinal, content_id: ContentId}>
  failure?
```

It exposes the evolving committed prefix. Members are final once listed;
`AVAILABLE` is successful closure and needs no separate completion boolean.
Binding status is independent of overall task status, so a successfully closed
requested intermediate remains `AVAILABLE` if an unrelated branch later fails.

Exact `ArtifactSchemaId`, selected producer invocation, binding provenance, and
cache resolution are persisted in the internal plan/execution record and are
not ordinary MVP response fields.

Collection and focused output lookup are owned by
[Durable Artifact Download](05-durable-artifact-download-surface.md), keyed by
the original canonical scoped request. There is no caller-chosen output key and
no canonical task-artifact locator scheme.

Task-input protection remains through active, recoverable, and `STOPPING`
states and ends only at actual task terminalization. Requested-output retention
does not implicitly retain supplied inputs afterward.
