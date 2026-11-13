# Durable Artifact Persistence

## Purpose

Durable artifact persistence stores semantic artifact-instance records and
task-output bindings whose canonical bytes live in a
[Representation Store](03-representation-store.md).

It is distinct from execution cache:

- cache content is reusable, recomputable execution state governed by cache
  resolution and eviction policy;
- durable artifacts are explicitly published inputs or requested durable task
  outputs governed by claims; and
- the two modules may share representation objects without sharing semantic
  records or lifecycle policy.

Runtime upload and download behavior is defined in:

- [Durable Artifact Upload](../01-runtime/03-durable-artifacts-upload-surface.md)
- [Durable Artifact Download](../01-runtime/05-durable-artifact-download-surface.md)

## Boundaries

The durable persistence provider owns:

- immutable committed `a-instance` records;
- upload-grace and task artifact claims;
- successful `a-task -> a-instance` bindings;
- lookup and atomic read-reservation acquisition;
- publication and claim transactions;
- provider capability reporting;
- durable-record recovery; and
- record-level garbage collection.

It does not own:

- typed stage execution;
- codec implementation;
- cache resolution or cache retention;
- task-record status retention;
- HTTP authentication or framing; or
- physical representation-object lifecycle.

## Durable Artifact Record

Conceptually:

```text
DurableArtifactRecord
  artifact_instance: a-instance
  resolved_artifact_id
  semantic_fingerprint
  representation_descriptor
  representation_object_key
  byte_length
  byte_digest
  state
  created_at
```

The record preserves enough representation metadata for raw canonical download
even when the current runtime no longer installs the original codec.

`a-instance` remains a location-independent semantic locator. It contains no
filesystem path, SMB handle, bucket, region, provider, or store identity.
Physical resolution belongs to the configured service scope.

## Immutability

A committed `a-instance` is immutable.

Publication is create-if-absent:

- a compatible existing instance is reused idempotently;
- an existing instance with a different valid representation is an integrity,
  nondeterminism, or canonicalization conflict;
- no caller may overwrite one semantic identity with different bytes; and
- records may be repaired only by restoring their exact validated
  representation.

The representation digest is not the semantic artifact fingerprint. The
artifact fingerprint follows declaration-specific semantic identity
rules.[^content-provenance-revisit]

[^content-provenance-revisit]: **Design revisit:** Separate artifact content
    identity from production provenance. Artifact fingerprints should likely
    contain only content identity, for both supplied and stage-produced
    artifacts, while declaration identity, stage pedigree, settings, and output
    identity form a separate provenance/execution-cache key that resolves to
    output content fingerprints. Under that model, upload is independent of
    `a-ref`, may accept a caller-calculated expected content fingerprint that
    the runtime verifies, and validates `a-ref` compatibility when the content
    is used. This note records a coordinated redesign to revisit; the current
    normative text has not yet been reconciled.

## Publication

Publication consumes one stable canonical transfer source or promotes one
trusted committed source.

For untrusted supplied content, publication:

1. validates the fully resolved manifest-known artifact declaration;
2. stages or adopts the canonical representation;
3. validates representation compatibility and canonical form;
4. derives the semantic content contribution;
5. derives the final supplied-artifact fingerprint and `a-instance`;
6. establishes length and representation digest;
7. creates or reuses the immutable representation object; and
8. atomically commits the durable record, object reference, and initial claim.

The final fingerprint may remain unknown until staging and validation finish.
An opaque publication transaction ID identifies pre-fingerprint work.

If semantic validation cannot run during the one-shot ingest pass, it rereads
the provider-owned reopenable staging source with bounded memory. It never
rereads a consumed network source.

## Trusted Promotion

A validated committed cache or durable source may preserve its existing
semantic identity without re-decoding or re-fingerprinting content when it
provides:

- a concrete `a-instance`;
- matching resolved artifact identity;
- matching canonical representation descriptor;
- validated length and digest; and
- an active read/object reservation.

Promotion uses object-reference attachment when stores share a representation
store. Otherwise it uses a compatible native copy or byte stream.

Untrusted uploads never use this fast path.

## Atomic Publication and Claim

Successful publication always means:

- a committed durable record exists;
- its representation-object reference exists; and
- the caller-required initial claim exists.

These outcomes are one logical transaction whether publication creates new
content or reuses an existing instance. There is no garbage-collection gap
between publication and claim attachment.

## Claims

Claims retain durable semantic records and their object references. They are
independent of read/write reservations and cache TTL.

The required claim kinds are:

```text
UPLOAD_GRACE
ACTIVE_TASK_INPUT
ACTIVE_TASK_OUTPUT
TERMINAL_TASK_ARTIFACT
```

Each claim has:

- an opaque claim ID;
- authorization/storage scope;
- owning purpose;
- creation time;
- optional expiry; and
- provider-specific operational metadata.

The effective content lifetime is the protection supplied by all live claims
and reservations. Claims do not mutate one global expiry on deduplicated
content.

## Upload-Grace Claims

Independent upload creates a short pre-submission grace claim:

- its TTL begins after publish-and-claim succeeds;
- the caller may request a TTL within provider policy;
- otherwise the provider uses its configured short default;
- ordinary reads and inspections do not refresh it;
- the upload result exposes its absolute deadline; and
- expiry makes an otherwise unclaimed artifact collectible after active
  reservations finish.

The claim may be explicitly released or renewed using its opaque claim ID.
Release affects only that claim. Renewal is policy-bounded and returns the
effective deadline.

## Durable Task Claims

Atomic task acceptance records supplied `a-ref` and upload-fingerprint bindings
without requiring every referenced cache entry to be present. It therefore
creates no eager task-input claims merely because a fingerprint appears in the
request.

After cache evaluation selects stages for execution, required-input acquisition:

- resolves only the inputs consumed by those stages;
- validates authorization, integrity, manifest compatibility, and readability;
- atomically attaches task-input claims to all inputs required for the stage or
  attaches none; and
- fails the task if any required input cannot be acquired.

An expiring upload-grace claim and required-input acquisition may race.
Successful atomic task-claim attachment wins; otherwise the task reports the
required input as unavailable. A missing upload used only by cache-satisfied,
skipped stages is never acquired and does not fail the task.

Committed requested outputs receive active task-output claims immediately.
Intermediates remain governed by cache and active-task recovery policy unless
explicitly requested for durable publication.

## Task Artifact TTL

A durable request supplies one task-scoped post-terminal artifact TTL or uses
the configured durable-artifact default.

While the task is active or recoverable, active claims protect its required
inputs and committed outputs independently of that TTL.

At terminalization:

```text
retained_until = terminal_at + effective_artifact_ttl
```

Active input and output claims become or are extended by terminal task-artifact
claims with that common deadline. The rule applies to completed, failed, and
cancelled tasks:

- referenced valid inputs remain claimed;
- valid outputs committed before terminalization remain claimed;
- incomplete outputs are aborted and never become durable artifacts; and
- outputs without instances remain task-record states only.

Task-record retention, cache TTL, upload-grace TTL, active/recovery deadline,
and task artifact TTL are separate policies.

## Task Artifact Bindings

An `a-task` identifies one task-scoped requested output. It may retain an
unresolved phase selector until planning determines the Completed Value.

Its lifecycle states include:

```text
PENDING
AVAILABLE(a-instance)
UNAVAILABLE(reason)
```

For a successful output, the `a-task -> a-instance` binding follows that task
output's artifact claim. It may outlive the task record, but it does not remain
merely because an unrelated claim retains the same deduplicated instance.

Pending, failed, and cancelled bindings have no artifact claim. Their visibility
follows task-record retention.

The provider must distinguish:

- unknown or expired task record;
- pending output;
- terminal failed or cancelled output;
- resolved instance whose task artifact claim expired; and
- currently available durable instance.

## Availability

Expected lookup outcomes use:

```text
ArtifactAvailability
  Pending(task/output state)
  Available(a-instance, representation metadata, retained_until?)
  Unavailable(reason, resolved a-instance?)
```

An `a-instance` is never `Pending`.

Malformed locators, authorization failures, invalid ranges, provider failures,
and contract violations are structured operation failures rather than
availability variants.

Inspection does not acquire a long-lived read reservation and may race a later
open. Open revalidates availability and atomically acquires its reservation.

## Open and Read Reservations

Opening a durable artifact:

1. resolves `a-task` when required;
2. validates the durable record and representation-object reference;
3. acquires a representation read reservation atomically with validation; and
4. returns a canonical transfer source.

Garbage collection must not remove the record or representation while the
reservation is active.

Each returned download handle owns one read reservation and permits at most one
active byte stream. Sequential reopening is allowed when its source capability
supports it. Concurrent full or range reads use independent opens.

The reservation remains held until explicit handle close, cancellation, or
stale-reader recovery. EOF closes one stream but does not close a reopenable
handle. One-shot binding helpers may close the handle automatically after EOF.

## Waiting

Non-waiting open never silently blocks on a pending `a-task`.

Cancellation-aware waiting:

- waits only while the task binding is pending;
- opens and reserves the concrete instance when it becomes available;
- returns terminal unavailability when the task/output cannot produce an
  instance; and
- respects caller cancellation or deadline without affecting task execution.

Transport layers may choose inspection, polling, or waiting.

## Authorization Scope

Locators identify artifacts; they do not authorize access.

Records, task bindings, claims, inspection, and opens are scoped to the
configured service authorization/storage namespace. A service may report an
instance unavailable without revealing whether matching content exists in
another scope.

Physical representation deduplication across security scopes is allowed only as
provider policy and must not expose cross-scope records, metadata, timing
guarantees, or claim control.

## Raw and Typed Access

Raw canonical download uses committed representation metadata and does not
require the current manifest to install the original codec.

Typed decoding or use as a new task input requires a compatible current
declaration and codec. An incompatible codec version never reinterprets stored
bytes.

Caller-supplied original filenames are optional presentation context, not
durable artifact metadata. A service may derive a safe suggested name from
artifact identity, fingerprint, and preferred extension.

## Provider Capabilities

Providers declare observable capabilities, including:

- process and host restart survival;
- visibility to every worker that may execute or serve a durable task;
- logical atomicity and recovery;
- claim and reservation support;
- supported TTL bounds;
- range reads;
- required size/capacity limits; and
- compatibility with a configured representation store.

Durable upload and task submission fail preflight when required capabilities are
missing. A runtime-private temporary provider may implement the interfaces but
does not satisfy shared durable-task requirements.

## Idempotent Operations

Publication is content-idempotent by immutable instance identity.

Upload may additionally accept an authorization-scoped caller idempotency key.
The provider records compatible request identity and active/terminal outcome.
A retry returns or awaits the same outcome. Conflicting reuse fails.

The same key replays a recorded terminal failure rather than starting another
transfer. Retrying a genuinely failed logical upload requires a new key and a
new source and starts from byte zero. Operation idempotency is not resumable
upload.

Returning a recorded successful outcome does not refresh, recreate, or extend
its upload-grace claim. The claim may already be expired or released.

## Cancellation and Recovery

Cancellation:

- aborts incomplete publication;
- closes sources owned by the operation;
- releases logical reservations when safe;
- prevents publication after cancellation unless commit already won; and
- never rolls back a committed record.

Recovery reconciles:

- expired publication leases;
- committed records missing initial claims or object references;
- claims without records;
- task claims around acceptance or terminalization;
- stale read reservations;
- interrupted record deletion; and
- ambiguous idempotency outcomes.

Recovery is idempotent and concurrency-safe.

## Garbage Collection

A durable record becomes eligible for logical deletion only when:

- it has no live durable claim;
- it has no active read/write reservation;
- it is not participating in recoverable publication; and
- no successful `a-task` binding with a live task-output claim requires it.

Logical deletion removes availability and detaches the representation-object
reference atomically. Physical object cleanup follows representation-store
policy and may be delayed.

## Failures

The provider uses the universal
[Failure Contract](../01-runtime/07-failure-surface.md) with kind
`ARTIFACT_PERSISTENCE`. Stable issue codes are:

- `ARTIFACT_LOCATOR_INVALID`;
- `ARTIFACT_LOCATOR_UNKNOWN`;
- `TASK_OUTPUT_PENDING`;
- `TASK_OUTPUT_TERMINALLY_UNAVAILABLE`;
- `ARTIFACT_NOT_RETAINED`;
- `REPRESENTATION_UNSUPPORTED`;
- `REPRESENTATION_MISMATCH`;
- `ARTIFACT_CONTENT_INVALID`;
- `REPRESENTATION_LENGTH_MISMATCH`;
- `REPRESENTATION_DIGEST_MISMATCH`;
- `ARTIFACT_IDENTITY_CONFLICT`;
- `BYTE_RANGE_INVALID`;
- `BYTE_RANGE_UNSUPPORTED`;
- `TRANSFER_SOURCE_EXHAUSTED`;
- `TRANSFER_SOURCE_OWNERSHIP_VIOLATION`;
- `PROVIDER_DURABILITY_INSUFFICIENT`;
- `PROVIDER_CAPACITY_EXCEEDED`;
- `ARTIFACT_CONTENT_CORRUPT`;
- `OPERATION_CANCELLED`; and
- `ARTIFACT_STORAGE_FAILURE`.

Provider-specific causes and retry hints may be attached as diagnostics.

## Conformance

Every durable provider must pass scenarios covering:

- late-bound uploaded identity;
- canonical validation;
- idempotent concurrent publication;
- atomic publication plus grace claim;
- grace expiry versus task acceptance;
- task claims through active and terminal lifecycles;
- `a-task` waiting and retention;
- read reservation versus GC;
- trusted cache promotion;
- raw download across manifest evolution;
- corruption quarantine;
- cancellation and crash recovery; and
- insufficient provider capability rejection.

## Invariants

1. Durable availability never depends on cache eviction policy.
2. A committed `a-instance` never changes representation.
3. Publication success includes an initial claim.
4. Required-input acquisition claims every input for an executing stage or
   acquires none of them.
5. Active readers prevent deletion.
6. A successful `a-task` binding follows its task artifact claim.
7. Raw canonical download never requires typed materialization.
