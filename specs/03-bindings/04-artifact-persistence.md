# Durable Artifact Persistence

## Purpose

Durable artifact persistence retains uploaded content, accepted task inputs, and
committed requested-output members independently from execution-cache eviction.
Exact bytes live in the shared Content Store.

This module also owns task-artifact binding snapshots used by durable/public
output retrieval.

## Boundaries

Durable persistence owns:

- upload-grace claims;
- upload/requested content retention deadlines;
- active durable-task input protection;
- committed requested-output member retention;
- requested `ScopedArtifactRequest` records and `TaskArtifactBinding` state;
- content read reservations for durable/public download;
- claim/reference recovery and garbage collection.

It does not own:

- stage execution;
- producer selection;
- provenance construction;
- cache hit/miss decisions; or
- physical byte-object identity.

## Durable content record

Durable presence is keyed by `ContentId` and references a committed Content
Store object.

```text
DurableContentRecord
  content_id
  byte_length
  created_at
  retention_deadline?
  presentation metadata?
```

A `ContentId` may be shared by many tasks, scoped bindings, artifact schemas, provenance
bindings and retention reasons.

## Upload publication

Upload is schema-independent. Successful upload stores one exact content object
and establishes:

- its `ContentId`;
- exact byte length;
- upload-grace claim; and
- ordinary retention deadline according to upload/runtime policy.

No artifact request, scoped binding, schema, semantic fingerprint, or
provenance is accepted at upload.

### Idempotent publication operations

Content publication itself is create-if-absent and idempotent by `ContentId`.
The public compatibility, replay and retry rules are owned by the durable
content-upload specification.

Persistence must record the active/terminal operation outcome durably enough to
honor that contract. Successful outcome recording and the original content/grace
publication are one recoverable transaction boundary; replay reads the recorded
outcome and never creates a new grace claim.

## Upload grace and task acceptance

Upload grace is a temporary handoff claim giving the caller time to submit a
durable task that references the uploaded content.

Durable task acceptance requires every explicitly supplied `ContentId` to be
currently present, readable, authorized, and claimable.

For each accepted input binding, acceptance atomically:

1. records the complete scoped binding and ordered `ContentId` list on the task;
2. establishes active-task protection for every member; and
3. deletes or converts corresponding upload-grace handoff claims when present;
4. creates the task and its requested-output records.

For plural bindings the operation is all-or-nothing. A task is never accepted
with only part of an explicitly supplied input protected.

There must be no collection gap between removal of upload grace and task
protection.

A missing, unreadable, unauthorized, expired, or unclaimable supplied member
rejects submission; no task or partial claims are created, and no grace claim is
partially deleted. The previous behavior that accepted missing roots in hope of
downstream cache hits is not part of the MVP.

## Active task input protection

An accepted durable task keeps all supplied content members protected while the
task is active or recoverable, even if their ordinary upload/content retention
TTL expires during execution.

`active_expires_at` starts expiry cleanup; it does not immediately remove this
protection. Protection remains until the task actually terminalizes after safe
cleanup.

At terminalization, task input protection is released. Inputs are not
automatically retained for the task-output artifact-retention period.
They remain only while protected by another independent retention reason,
ordinary content retention TTL, task, claim, or active read reservation.

## Requested output members

Each committed requested-output member is durable as soon as publication
commits, even when the containing binding is still producing.

A later stage/task failure does not invalidate already committed members.
Requested-output retention protects committed members according to task artifact
retention policy.

The binding record separately determines whether those members constitute a
successfully complete output.

## Task artifact binding

One original canonical `ScopedArtifactRequest` on one task keys a binding
snapshot:

```text
TaskArtifactBinding
  requested: ScopedArtifactRequest
  status
  members: ordered list<TaskArtifactMember>
  failure?                 # applicable binding/task failure envelope

TaskArtifactMember
  ordinal
  content_id: ContentId
```

Exact schema, selected producer invocation, binding provenance, and cache
resolution are persisted internally and omitted from this public response.

### Binding status

```text
PENDING
PRODUCING
AVAILABLE
FAILED
CANCELLED
EXPIRED
```

- `PENDING`: no member committed yet and production may still occur.
- `PRODUCING`: one or more members are committed but the binding has not closed.
- `AVAILABLE`: the binding closed successfully; the member list is complete.
- `FAILED`: the binding did not close because execution failed.
- `CANCELLED`: cancellation prevented successful closure.
- `EXPIRED`: active-task expiry prevented successful closure.

`members` always means successfully committed members so far. There is no failed
member list. For `ONE_OR_MORE`, runtime cannot infer unattempted future members.

A binding that closed successfully remains `AVAILABLE` even if another output
branch later causes the overall task to fail.

## Task artifact retention

Committed requested-output members receive task-output protection while the task
is active. At terminalization they remain retained for the effective task
artifact TTL, including committed members of incomplete/failed bindings.

This guarantee includes explicitly requested intermediates. Incidental produced
or cached intermediates are not public task outputs and receive no inferred
task-output retention.

Task-record retention and content retention remain separate. A task-artifact
binding may become unavailable after its output claim expires even if identical
content remains in the Content Store for another reason.

## Content open

Opening durable content by `ContentId`:

1. parses and scopes the identifier;
2. validates durable presence/retention;
3. atomically acquires a content read reservation; and
4. returns a transfer source for exact bytes.

Active reads prevent collection until close.

Raw content retrieval does not require the runtime to install an artifact
schema or codec.

## Authorization boundary

The MVP does not standardize principal/tenant policy. Implementations MUST apply
their configured access check during durable acceptance and content open;
failure is unauthorized rather than proof of absence. Identifiers remain
location-independent and do not themselves grant authority. Future scope-based
visibility must not change `ContentId` equality.

## Recovery

Recovery reconciles:

- upload publication and grace claims;
- atomic task-input handoff;
- active-task input protection;
- requested-output member publication;
- task-artifact binding state;
- terminal retention transitions;
- stale read reservations; and
- interrupted deletion.

Recovery is idempotent and concurrency-safe.

## Invariants

1. Durable availability is independent of execution-cache eviction.
2. Upload stores content without requiring schema/provenance.
3. Durable submission atomically protects every explicitly supplied content
   member or rejects the task.
4. Active task input protection survives through STOPPING and ends only at task
   terminalization.
5. Committed requested-output members survive later binding/task failure.
6. Task output lookup resolves the original scoped request to a binding
   snapshot, not one content object or public locator.
7. Task-output status and overall task status are related but independent.
