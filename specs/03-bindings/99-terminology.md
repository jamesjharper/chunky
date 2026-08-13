# Bindings Terminology

This document consolidates the domain terminology defined in the `specs/02-bindings` folder and carries forward the artifact terminology defined in `specs/01-artifacts/99-terminology.md`. It serves as a reference glossary for artifact bindings, cache management, and artifact runtime orchestration.

## Core Artifact Terms

### Artifact
A typed value that enters, moves through, or leaves the runtime. Artifacts include source inputs, settings, intermediate results, final outputs, diagnostics, and output requests.

### Artifact Model
The semantic model that lets the runtime reason consistently about artifact identity, compatibility, reuse, and materialization across stages.

### Artifact Role
The single role assigned to an artifact. The defined roles are `source`, `settings`, `intermediate`, `target`, and `diagnostic`.

### Source Artifact
An externally supplied input artifact. Source artifacts are always fingerprinted and may optionally be retained in durable storage for resume or recovery.

### Settings Artifact
A resolved configuration artifact. Effective settings are produced by combining declaration defaults, user-supplied settings, and inherited settings.

### Intermediate Artifact
An artifact produced by one stage and consumed by another. Intermediate artifacts are cached by default and usually derive their fingerprint from production pedigree rather than content hashing.

### Target Artifact
A requested final output artifact. From the producing stage's perspective, targets are write-only.

### Diagnostic Artifact
A requested inspection or observability artifact, such as logs, performance outputs, or stats. Diagnostics may be queried by task ID, but non-deterministic diagnostics are not reusable across later tasks.

## Identity and Declaration Terms

### Artifact ID
A stable semantic identifier that describes what an artifact means, rather than where it is stored or how it was produced.

### Locator Format
The preferred public semantic reference shorthand is:

```text
{domain}/{content_kind}/{phase-or-any}
```

Concrete artifact instances use:

```text
a-instance:/global/{domain}/{content_kind}/{phase-or-none}/fingerprint/{fingerprint}
```

Task-scoped requested outputs use:

```text
a-task:/global/{domain}/{content_kind}/{requested-phase}/task/{task_id}
```


### Artifact Ref Locator
The declaration-level semantic artifact reference surface. It may be written as canonical `a-ref:/...` or, in caller-facing examples, as a short path such as `source/audio` or `transcript/token_text/100`. It may use `any` for unresolved phase selection.

### Artifact Instance Locator
The `a-instance` URI surface for one immutable semantic artifact instance. It
pairs resolved artifact identity with the final semantic artifact fingerprint;
it does not identify a cache location or physical representation object.

### Task Artifact Locator
The `a-task` URI surface for a task-scoped requested-output binding. It carries
the stable requested reference and task ID, not a resolved fingerprint, and may
therefore retain an unresolved `any` phase.

### Projection Context
The leading contextual path segment in locator URIs. `global` means no projection. Projection context is presentation-only and does not participate in semantic equality, hashing, planning, cache lookup, or fingerprint payloads.

### Domain
The first part of an artifact ID. It names the semantic subsystem or concern area that owns the artifact.

### Content Kind
The second part of an artifact ID. It names the specific semantic value within a domain.

### Phase
An optional numeric suffix on an intermediate artifact family. A phase identifies concrete processing steps within a phased intermediate family.

### Artifact Key
The structured manifest identity of an artifact declaration. It contains `domain`, `content_kind`, and optional `phase`.

### Family
The non-phased identity of an artifact key: `{domain}/{content_kind}`. Multiple concrete phases can belong to the same family.

### Phased Family
An intermediate artifact family that explicitly publishes one or more phase-specific declarations.

### Phase Selector
The use of `artifact_id.phase` in an input requirement to request the closest available phase that does not exceed the requested phase number.

### Completed Value
The phase-less value of a phased family after graph evaluation has selected the concrete phase to use.

### Artifact Declaration
The semantic contract for an artifact schema. A declaration owns the artifact's identity, role, version, codec, cacheability, and optional default provider.

### Declaration Ownership
The rule that the stage or shared package defining an artifact schema is the authoritative owner of that artifact declaration.

### Default Provider
An optional declaration component that supplies a default settings value.

### Codec
The conversion and validation contract between a typed artifact value and its
canonical representation. A codec defines a representation descriptor,
`encode`, `decode`, `validate_and_fingerprint`, and explicit buffering
capabilities. Its encoded interface uses transfer sources rather than requiring
complete byte arrays.

### Canonical Representation
The declaration-owned logical byte form used for cache, persistence, transfer,
and materialization. Chunk boundaries, physical layout, and provider-specific
storage encoding are not semantic.

### Representation Descriptor
The stable identity of a canonical representation format, including codec ID,
codec version, media type, and optional extension.

### Extension
Storage metadata exposed by a codec, including its leading dot such as `.json` or `.vtt`. It does not imply that a consumer receives a file path.

### Cacheable
The declaration property indicating whether an artifact may be reused through
the runtime cache. It does not determine durable publication.

## Planning Terms

### Artifact Capability
A static stage advertisement that references a declared artifact the stage can input or output. Capabilities must be known during backward traversal of the task graph.

### Input Capability
A capability stating that a stage can consume a referenced artifact.

### Output Capability
A capability stating that a stage can produce a referenced artifact.

### Artifact Requirement
A dynamic stage input requirement computed from planning context. It references an artifact declaration and records whether that input is required.

### Stage Node Context
The read-only planning context used while building the task graph. It is used to inspect expected stage outputs and resolved or inherited settings.

### Backward Traversal
The graph-construction process that works backward from requested outputs to determine required stages and artifacts.

### Terminal Remapping
Planner-owned resolution that maps a requirement or requested output to the concrete artifact instance that should satisfy it. Stages do not perform this remapping themselves.

### Production Pedigree
The planner-provided provenance used to fingerprint stage-produced outputs and intermediates.

## Runtime and IO Terms

### Artifact Handle
The execution-time binding for one concrete artifact instance after fingerprint resolution and source or target resolution.

### Resolved Artifact ID
The fully evaluated artifact identity on a handle, after phase selection and terminal remapping are complete.

### Artifact Instance
The available semantic artifact associated with one resolved artifact handle.

### Artifact Source Handle
The runtime object representing one selected readable source. It may expose a
typed value source, a canonical transfer source, or both.

### Artifact Target Handle
The runtime object representing one selected writable destination. It may
expose a typed value target, a canonical transfer target, or both.

### Artifact Transfer Source
A source of canonical representation bytes. Its portable form is a pull-based
asynchronous stream; it also declares replayability, ownership disposition, and
optional provider-native transfer offers.

### Artifact Transfer Target
A transactional destination for canonical representation bytes. It prepares
from a source, then commits or aborts. The target selects streaming, copy,
clone, rename, multipart, or another safe provider operation.

### Representation Object
An immutable stored canonical representation addressed by representation digest
and byte length. Semantic cache and durable records refer to it.

### Durable Artifact Claim
A persistence-owned retention reference protecting a durable semantic artifact
record. Upload-grace, active-task, terminal-task, and administrative claims may
have different lifecycles.

### Source
The readable binding on an artifact handle. Inputs have one resolved source, and cache-hit outputs may also expose a source.

### Target
A writable destination bound to an artifact handle. Produced outputs may bind one or more targets before stage execution.

### Materialization
The act of writing an available semantic artifact to caller-provided output destinations such as files or sinks.

### Target-less Output
A requested output with zero caller materialization targets. It asks the run to make the semantic artifact durably retrievable and report its committed cached-instance reference.

### Input Binding
An artifact handle with a resolved source and usually no targets.

### Output Binding
An artifact handle that either represents a cache hit or carries pre-resolved targets for production.

### Materialization Binding
A binding that reuses an existing semantic source while adding caller-requested output targets.

### Cache Hit
An output case where the artifact is already available in cache, so the producing stage can be skipped.

### Read Reservation
A provider-owned reservation that keeps a representation safely readable while
a consumer is using it. Garbage collection must not delete the representation
while any read reservation is active.

### Write Reservation
A provider-owned reservation that protects an unpublished target while content
is being prepared or committed.

### Content Fingerprint
The source-handle contribution that represents semantic content equality for supplied inputs. It is not always the final artifact fingerprint.

### Artifact Fingerprint
The final identity hash for a concrete artifact instance. Supplied inputs derive it from content, while produced artifacts may derive it from pedigree.

### Artifact Instance Reference
A concrete public semantic reference to an immutable artifact instance. It
pairs the semantic artifact ID with the final artifact fingerprint; a bare
fingerprint is not a complete public reference.

### Bootstrap
The point where a handle becomes fully assembled with declaration, fingerprint, and resolved source or targets.

## Reader and Writer Terms

### Artifact Reader
The runtime reader bound to a complete input artifact handle. It reads a typed value from the handle's source and may memoize it for the invocation.

### Artifact Writer
The runtime writer bound to a complete output artifact handle. It creates one
canonical representation, prepares compatible semantic targets from it,
commits them in order, and aborts prepared targets on failure.

### Prepare
The target-handle lifecycle step that stages artifact content without
publishing it as complete.

### Commit
The target-handle lifecycle step that publishes the prepared value and releases any reservation.

### Abort
The target-handle lifecycle step that discards prepared output and releases any reservation when publication does not complete.

### Semantic Target
A target that makes the artifact itself available to the system, such as a
cache target, durable-persistence target, or in-memory publication target.

### Materialization Target
A caller-provided target written after the semantic artifact has successfully become available.

### In-Memory Publication Target
An output target that makes a produced artifact available directly to downstream stages during the current task.

### Intermediate Artifact Handle
An example combined target/source handle used for in-memory publication of intermediates. It is written as a target, then read later as a source after commit.

## Bindings Terms

### Binding
A runtime object that connects the planner's semantic artifact graph to concrete stage execution. A binding answers which handle a stage should read from or write to for an artifact at a specific moment.

### Artifact Binding
The resolved runtime association between an artifact declaration or identity and the concrete source or target handles used for execution.

### Artifact Bindings Provider
The runtime component that constructs supplied bindings, stage input bindings, stage output bindings, and materialization bindings.

### Stage-Facing Contract
The uniform `execute(inputs, outputs)` interface exposed to stage code regardless of whether artifacts come from supplied sources, cache hits, in-memory publication, or caller targets.

### InputArtifacts
The stage-facing keyed collection of already-resolved `ArtifactReader` instances.

### OutputArtifacts
The stage-facing keyed collection of already-resolved `ArtifactWriter` instances.

### Artifact Selector
The lookup selector accepted by stage-facing collections. It may be an `ArtifactDeclaration`, resolved `ArtifactKey`, or artifact ID string.

### Supplied Binding
A graph-root binding produced from caller-supplied content by fingerprinting a concrete source handle and constructing an available `ArtifactInstance`.

### Intermediate Binding
A binding used for stage inputs or stage outputs during task execution.

### Output Materialization Binding
A binding used to write an already-available semantic artifact to caller-requested terminal outputs after semantic publication is complete.

### Stage Input Binding
The injection of an already-available artifact instance into an `ArtifactReader` for stage execution.

### Stage Output Binding
The pre-execution resolution of all planned outputs for a stage, including cache-hit cases, reserved-write cases, wait cases, and non-cacheable publication writers.

### Bound Output
A planned stage output that has been resolved to either an available instance, a writer, or a wait state before stage execution.

### Produced Output Set
The concrete subset of bound outputs actually written by a stage invocation.

### Output Resolution
The runner-owned process that determines which semantic artifacts were actually produced by inspecting bound writers after stage execution returns.

### Stage Skip
The case where the runner does not execute a stage because every planned cacheable output is already available and no non-cacheable work remains.

## Cache Resolution Terms

### Cache
The shared coordination state that stores, resolves, reserves, publishes, reuses, and removes semantic artifacts across concurrent tasks.

### Cache Coordination
The cache's responsibility for atomic lookup, validation, reservation, publication, wait signaling, recovery, and garbage-collection safety.

### Cache Resolution
The atomic process of resolving an expected artifact fingerprint into a hit, write reservation, or wait result.

### Cache Resolution Contract
The `ArtifactCache.resolve(...)` contract that returns exactly one of `CacheHit`, `CacheWriteReservation`, or `CacheWait`.

### Cache Hit
A valid committed cache entry whose returned source handle already owns a read reservation.

### Cache Write Reservation
The exclusive right to prepare and commit a fingerprint through a returned cache target handle.

### Cache Wait
A resolution result indicating another producer currently owns the write reservation for the expected fingerprint.

### Cache Wait Handle
A notification mechanism that becomes ready after the active write reservation is released. It does not own a reservation or artifact value.

### Force Write
Resolution behavior that deliberately ignores an existing committed entry for selection purposes while still preserving exclusive write semantics for the fingerprint.

### Partial Cache Hit
A stage-output-set case where some cacheable outputs are already committed hits and others are reserved for production, with no active waits.

### Output-Set Binding
The stage-level orchestration policy that applies across the set of planned outputs even though cache resolution remains atomic per fingerprint.

### Provisional Binding
A temporary hit source or reserved writer acquired during an output-set resolution pass that may need to be closed or aborted if the pass is abandoned.

### Wait State
The condition where stage admission is deferred because at least one planned cacheable output returned `CacheWait`.

## Cache Entry Terms

### Cache Entry
A stored semantic artifact instance addressed by final artifact fingerprint and accompanied by authoritative metadata.

### Cache Entry Key
The cache lookup identity for a concrete artifact instance. It is the final artifact fingerprint.

### Cache Entry Metadata
The authoritative semantic and serialized-form metadata associated with a committed cache entry.

### Required Validation Metadata
The metadata fields that must match the expected binding before an entry can be reused as a hit.

### Operational Metadata
Non-identity cache metadata used for diagnostics, retention, inspection, and recovery.

### Completion Marker
The backend-specific signal that a prepared cache entry has become fully committed and is eligible to be returned as a hit.

### Entry State
The observable lifecycle state of a cache entry. Defined states include `absent`, `preparing`, `committed`, `invalid`, `quarantined`, and `deleting`.

### Committed Entry
A completed cache entry eligible to be returned as a hit after metadata validation and read-reservation acquisition.

### Preparing Entry
Staged data and metadata associated with an active write reservation that must not be visible as a hit.

### Invalid Entry
A cache entry whose contents or metadata are unreadable, mismatched, or otherwise not valid for reuse.

### Quarantined Entry
An entry isolated from normal reuse because it failed validation, recovery, or read checks and must not be exposed as a hit.

### Deleting Entry
An entry currently claimed for garbage collection or deletion and therefore not available for reuse.

## Reservation Terms

### Reservation
An operational cache record that protects a fingerprint or committed entry while it is being read or written.

### Reservation ID
The operational identity of a reservation record. It is not part of semantic artifact identity.

### Reservation Owner
The task, invocation, or stage recorded as owning a reservation.

### Reservation Inspection
Administrative cache lookup over reservations for diagnostics, recovery, and cleanup.

### Read-Reservation Lifetime
The period during which a cache-hit source remains protected from deletion while the owning task or scoped binding may still consume it.

### Write-Reservation Lifetime
The period during which a producer exclusively owns the right to publish a fingerprint until commit or abort reaches a terminal state.

### Reservation Release
The idempotent end of a reservation through source close, commit, abort, cancellation, or conservative recovery.

## Cache-Backed Handle Terms

### Cache Source Handle
An `ArtifactSourceHandle` returned from a cache hit that already owns a read reservation.

### Cache Target Handle
An `ArtifactTargetHandle` returned from a cache write reservation that owns the exclusive write reservation through prepare and commit or abort.

### Cache-Hit Artifact Handle
An `ArtifactHandle` bound to a cache source handle for an already-available committed artifact.

### Cache-Miss Artifact Handle
An `ArtifactHandle` bound to both a cache target and an in-memory publication
target for a cacheable produced artifact.

### Prepare-All-Before-Commit
The invariant that every semantic target is prepared before any target is committed.

### Semantic Commit
The publication of the semantic artifact itself, independent from caller-requested materialization.

## Concurrency and Waiting Terms

### Atomic Resolve
The guarantee that lookup, validation, and reservation acquisition are one atomic cache operation from the perspective of concurrent tasks.

### Exclusive Write Semantics
The rule that at most one live write reservation may exist for a fingerprint at any time.

### Shared Read Semantics
The rule that multiple tasks may hold read reservations on the same committed fingerprint concurrently.

### Cancellation-Aware Wait
A wait performed through the task cancellation seam so cancelling the waiter does not affect the active producer.

### Wait Completion
The transition where a wait handle becomes ready because the active writer committed, aborted, failed, was cancelled, or was recovered.

### Waiter Notification
The cache mechanism that makes waiting tasks eligible to retry after a write reservation is released.

### Deadlock Avoidance
The requirement that the runner release provisional reservations from an output-set pass before waiting on another output fingerprint.

## Fingerprint Terms

### Fingerprinting
The deterministic derivation of semantic artifact identity used for cache lookup, reuse, provenance, and invalidation.

### Stable Structured Hashing
The use of a deterministic structured payload encoding and stable hash function for fingerprint generation.

### Canonical Encoding
The stable serialized representation used as hash input for a fingerprint payload.

### Supplied Artifact Fingerprint
The fingerprint built from declared artifact identity and supplied semantic content.

### Resolved Settings Fingerprint
The fingerprint of the fully resolved effective settings value after defaults, user inputs, and inherited overrides are applied.

### Stage Pedigree Fingerprint
The stable hash of a stage invocation's producer identity, required input fingerprints, effective settings, and behavior-affecting provider or model versions.

### Order-Stable Input Aggregation
The requirement that pedigree construction use a stable ordering over concrete input bindings rather than traversal or execution order.

### Stage-Produced Artifact Fingerprint
The final fingerprint built from resolved artifact identity, schema and codec
identity, and the stage production pedigree.[^content-provenance-revisit]

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

### Output-Specific Identity
The rule that different outputs from the same stage invocation share pedigree but still receive distinct final fingerprints because each output contributes its own declaration and codec identity.

### Terminal-Remapped Identity
The resolved stored artifact identity used when a concrete phased output is remapped to a phase-less terminal identity.

## Lifecycle and Recovery Terms

### Stage Admission
The point at which the runner allows a stage to execute after its output bindings have been resolved into a coherent runnable state.

### Coherent Output Set
A planned output subset whose bindings permit a clear runner action: skip, wait, or execute.

### Stage Failure
The case where stage execution ends unsuccessfully before all required bound outputs are written.

### Task Cancellation
The accepted cancellation state in which waits stop, unwritten writers abort, and task-owned sources close without invalidating already committed semantic entries.

### Cache Prepare Failure
Failure while staging cache data or metadata before publication. It leaves no valid hit and requires abort and reservation release.

### Cache Commit Failure
Failure while publishing a prepared cache entry. It leaves the entry unavailable until retry or recovery resolves it.

### Cache Read Failure
Failure while reading or deserializing a cache hit after metadata validation, typically requiring source close, invalidation or quarantine, and re-resolution or task failure.

### Recovery
The conservative cache process that inspects and repairs stale reservations, incomplete entries, invalid generations, and startup leftovers.

### Stale Read Reservation
A read reservation whose owner is known not to be running and cannot be resumed through a supported recovery path.

### Stale Write Reservation
A write reservation whose owner is known not to be running, requiring inspection of associated prepared or committed data before release.

### Startup Scan
The durable-backend inspection pass over reservation records, temporary files, mismatched metadata, partial entries, and obsolete generations before they are trusted or removed.

## Retention and Diagnostics Terms

### Garbage Collection
The cache process that removes committed or invalid entries only when they have no active reservations and satisfy retention policy.

### Collection Policy
The backend retention policy used to prioritize eligible entries for deletion.

### Collection Atomicity
The requirement that eligibility verification and deletion claim be atomic with respect to resolution and reservation acquisition.

### Orphan Cleanup
The removal of abandoned temporary files, dangling metadata, unreferenced generations, and other incomplete cache objects that are not valid committed entries.

### Cache Event
An internal emitted record describing cache lookup, reservation, wait, prepare, commit, abort, recovery, or deletion activity.

### Progress Model
The higher-level runtime status model that may collapse lower-level cache events into user-visible stage states such as blocked while waiting for another task.
