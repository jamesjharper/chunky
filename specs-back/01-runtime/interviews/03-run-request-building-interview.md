# Run Request Building Interview Record

This working record preserves decisions made while reviewing
`03-run-request-building.md`, including their rationale and supersession
history. The confirmed results through decision 90 were consolidated into the
authoritative specifications on 2026-07-21. Those owning specifications now
govern; this record remains the source for unresolved interview branches.

## Confirmed decisions

### 1. `CanonicalRunRequest` is exclusive to the durable path

`RunRequest` is the caller-facing, process-local submission and may contain live
source and target handles. A live request may be bound for execution without
first becoming a `CanonicalRunRequest`.

`CanonicalRunRequest` is specifically the serializable, durable, replayable
computation contract. It is not a mandatory normalized representation of every
run.

Both paths converge at an internal bound execution request so planning,
scheduling, execution, progress reporting, result construction, and output
materialization can remain shared:

```text
durable: RunRequest -> prepare/persist -> CanonicalRunRequest -> BoundRunRequest
live:    RunRequest -> bind live handles ---------------------> BoundRunRequest
```

Status: confirmed by the user.

### 2. Canonical inputs are committed durable artifact instances

Every input in a `CanonicalRunRequest` must be a committed, immutable
`a-instance` locator. Its bytes and required metadata must already be held by a
runtime-managed durable artifact provider before the canonical request is
accepted.

Canonical inputs cannot be source handles, ordinary filesystem paths, open
streams, mutable URLs, credentials, callbacks, or ephemeral workspace
references. Importing or uploading such a source is preprocessing and happens
before canonical execution.

This restriction does not apply to live `RunRequest` execution, which may bind
and consume a source handle directly without canonicalization.

Status: confirmed by the user.

### 3. Canonical outputs are durable semantic requests

A `CanonicalRunRequest` contains only the semantic identities of its requested
outputs. It contains no delivery destinations.

Acceptance guarantees that each requested output can resolve to a committed
durable `a-instance`, whether by a cache hit or by committing newly produced
bytes. A running task may expose an `a-task` reference which resolves to that
instance on successful completion.

A canonical request must reject an inherently non-cacheable requested output or
an output whose policy specifies `do_not_cache`. Those combinations are
available only to live execution.

Status: confirmed by the user.

### 4. No specified `OutputDeliveryPlan`

`OutputDeliveryPlan` and `PreparedRunSubmission` are not caller-facing models and
should not be documented in `03-run-request-building.md`.

Output target handles remain part of the caller-facing `RunRequest`. When the
runtime needs to separate durable computation from local delivery during
execution, it may retain the targets in an ordinary private data structure. That
private representation is an implementation decision and does not need a named
specification-level abstraction.

Status: confirmed by the user, who explicitly rejected the previous agent's
addition of `OutputDeliveryPlan` as unnecessary.

### 5. Direct canonical submission has no local delivery attachments

A caller that submits a `CanonicalRunRequest` directly cannot attach local
target handles. Successful execution returns durable artifact locators, and the
caller retrieves or materializes those artifacts separately through the
artifact-provider API.

Callers that need output written directly to a file, stream, memory buffer,
callback, or another process-local destination use the `RunRequest` surface.
This avoids reintroducing `OutputDeliveryPlan` indirectly as an argument to the
canonical execution API.

Status: confirmed by the user.

### 6. Live inputs are not uploaded durably by default

Live execution consumes supplied source handles directly and does not first
publish their bytes to the durable artifact provider.

The implementation may spool input into a private task-scoped workspace when
needed for repeated reads, seeking, codec conversion, or stage interoperability.
This does not create a durable `a-instance`, does not make the request replayable,
and must be cleaned up with the task. Computing a content fingerprint likewise
does not imply publication of the input bytes.

A caller explicitly chooses durable preprocessing when it wants the source
uploaded and represented in a `CanonicalRunRequest`.

Status: confirmed by the user.

### 7. Every runtime has a cache provider; the universal default is workspace-backed

The execution architecture does not make the cache provider optional. Every
runtime uses the same cache-facing binder, planner, and runner path.

When no provider is configured, the runtime builder installs a simple
workspace-backed cache provider. This is the universal default for the runtime,
not a provider selected only when a live request arrives. The builder therefore
does not need to know whether the resulting runtime will be used for live or
canonical execution, and the runtime does not need parallel live and durable
cache configurations.

Integrators building a more complex or durable system are expected to configure
a provider suited to their storage, persistence, and operational requirements.

Status: confirmed by the user after refining the initial proposal that caching
might be optional for live execution.

### 8. The default workspace cache is runtime-scoped and ephemeral

When the caller does not provide a workspace, the runtime builder creates and
owns a private temporary workspace. Its cache contents may be reused by multiple
live runs on that runtime instance. Closing the runtime closes active handles
and deletes the owned workspace.

A caller may control workspace placement and cleanup by supplying an explicit
workspace, but a workspace path alone does not constitute the durability
guarantee required by canonical execution.

The default provider cannot promise that artifacts survive runtime shutdown.
Canonical preprocessing and canonical execution must therefore reject it during
capability validation rather than silently weakening the replayability contract.

Status: confirmed by the user.

### 9. Canonicalization is always explicit

Running a `RunRequest` follows the live path. It does not upload supplied inputs
or become durable merely because the runtime has a durable cache provider.

The caller must invoke a separate asynchronous canonicalization operation to
upload and commit source inputs and produce a `CanonicalRunRequest`. Cache hits
or incidental cache writes during live execution do not retroactively make that
run canonical or replayable.

This prevents cache-provider configuration from silently changing the latency,
privacy, storage cost, or lifecycle semantics of ordinary live execution.

Status: confirmed by the user.

### 10. Canonical requests may be constructed directly

A caller that already has committed durable `a-instance` inputs may construct a
`CanonicalRunRequest` directly from those locators and its semantic output
requests. It does not need to create and preprocess an intermediate
`RunRequest`.

The two legitimate entrances to the canonical model are therefore explicit
asynchronous canonicalization of a handle-based `RunRequest`, and validated
direct construction from already-durable artifacts. Both entrances enforce the
same canonical invariants.

Status: confirmed by the user.

### 11. `RunRequest` and `CanonicalRunRequest` have separate builders

The public API has a `RunRequestBuilder` and a
`CanonicalRunRequestBuilder`. Separate builders make the accepted inputs and
resulting guarantees explicit and deterministic rather than representing a
union of valid states behind one fluent interface.

Neither builder performs I/O. Each builder only normalizes and validates its
accumulated description and returns its corresponding immutable request type.

Decision 85 clarifies that both builders are standalone and
manifest-independent; this validation is structural rather than runtime
support validation.

Converting an existing `RunRequest` into a `CanonicalRunRequest` is instead an
asynchronous service operation (whose precise home may be specified elsewhere).
That operation performs required uploads and fails when the request or configured
provider cannot satisfy canonical requirements, such as a requested output that
cannot be cached durably.

Status: decided by the user, overriding the recommendation to use one builder
with two terminal operations.

### 12. Canonical validation is shared, with operational checks in the service

`CanonicalRunRequestBuilder.build()` enforces structural invariants only. This
includes locator syntax, normalized uniqueness, schema-valid policy data, and
internally contradictory declarations. It does not require or inspect a frozen
runtime manifest.

Canonical deserialization uses the same structural validation. The receiving
runtime separately owns manifest support, output cacheability, graph
satisfiability, cache availability, and operational failures such as unreadable
instances or insufficient provider durability.

No supported construction or deserialization path may produce an invalid
`CanonicalRunRequest`.

Status: originally confirmed, then corrected by the user during question 84 to
remove manifest-dependent validation from the builder.

### 13. Canonicalization rejects local output targets and is a consuming conversion

The asynchronous conversion service must reject a `RunRequest` containing any
local output target. A request intended for conversion must declare only
targetless semantic outputs that can be retrieved durably.

The service must not silently omit targets or encourage the original live
request and converted canonical request to be executed independently. The
operation is conceptually a consuming conversion, analogous to Rust's
`object.into()`, even in bindings that cannot enforce move semantics in the type
system.

Because conversion reads and durably publishes all supplied inputs, it may
mutate or invalidate the source request and release input handles as their data
is committed rather than retaining duplicate in-memory data unnecessarily. The
precise success and failure ownership rules remain to be decided next.

Status: decided by the user, overriding the recommendation to accept and omit
local targets.

### 14. Canonicalization consumes the request on entry

The conversion service takes ownership of the complete `RunRequest` when
canonicalization begins. Each source handle is read, durably committed, then
closed and released as soon as that input is safely represented by an
`a-instance`.

The original request is permanently consumed on success, failure, or
cancellation. Bindings without enforceable move semantics must detect attempted
reuse and fail clearly. On an unsuccessful conversion, the service closes all
remaining owned handles. Artifacts committed before the failure may remain in
the cache as unreferenced reusable content and do not require rollback.

Status: confirmed by the user.

### 15. Canonicalization supports cancellation and structured progress

The complete canonicalization operation uses the same cancellation and progress
primitives as runtime execution. Coverage includes validation, source reads,
fingerprinting, upload, and durable commit waits.

Progress identifies individual inputs and overall completion, and reports byte
counts when the source provides a meaningful size. Cancellation follows the
agreed consuming-failure semantics: owned handles are closed, completed commits
may remain, and no `CanonicalRunRequest` is returned.

Status: confirmed by the user.

### 16. Cacheable live outputs commit before local materialization

For live execution, an output that is cacheable under its effective policy is
resolved from or committed to the configured cache provider before it is
materialized to caller targets. With the universal default provider, that cache
is the runtime workspace.

An output with `do_not_cache`, or an inherently non-cacheable output, uses the
live non-cacheable binding and is delivered directly. Target handles do not
participate in artifact identity and do not affect reuse decisions.

Status: accepted by the user.

### 17. Live requests may have cache-backed targetless outputs

A live `RunRequest` may request an output without a local target only when the
artifact is cacheable and its effective policy permits caching. Execution
returns the resulting `a-instance` locator.

The locator inherits the configured provider's lifetime. Under the default
workspace provider it is valid only while that runtime workspace remains
available; use of a durable provider does not by itself make the live request
canonical or replayable.

The builder rejects a targetless output that is inherently non-cacheable or has
`do_not_cache`, because it would have neither storage nor a delivery destination.

Status: confirmed by the user.

### 18. Output fan-out is ordered and fail-fast

Multiple targets for one semantic output are materialized in the order in which
they appeared in the original `RunRequest`. Delivery stops at the first target
failure; later targets are not attempted.

The failure does not roll back an earlier cache commit or any target writes that
already completed, and no cross-target atomicity is promised. Multiple targets
are expected to be an uncommon convenience case.

Status: decided by the user, retaining the existing behavior instead of
best-effort delivery to every target.

### 19. Targetless live outputs use `expect_cached_output`

`RunRequestBuilder` names a targetless output request
`expect_cached_output(a-ref)`. The name communicates that the result is retained
through the configured cache provider and inherits that provider's lifetime; it
does not promise durability.

`CanonicalRunRequestBuilder` uses `expect_output(a-ref)`, where durable
production is guaranteed by the canonical request contract.

Status: confirmed by the user.

### 20. Live handles and committed locators use distinct input methods

`RunRequestBuilder.include_input(a-ref, SourceHandle)` accepts only a live source
handle. `RunRequestBuilder.include_cached_input(a-instance)` accepts only a
committed artifact locator.

`CanonicalRunRequestBuilder` exposes only
`include_cached_input(a-instance)`. Because an `a-instance` contains its
canonical artifact identity, no separate `a-ref` argument is required; the
builder validates that identity against the frozen manifest.

Status: confirmed by the user.

### 21. Live execution consumes its request

Submitting a live `RunRequest` transfers ownership of all source and target
handles to the runtime. The same request cannot be submitted again; bindings
without move semantics must detect reuse and report a clear consumed-request
error.

The runtime closes all owned handles at terminal completion, failure, or
cancellation, and may release a handle earlier once it can no longer be used.
Repeated execution requires separately built requests with independently usable
handles.

Status: confirmed by the user.

### 22. Live cancellation and progress cover the complete lifecycle

The controlled live-run lifecycle begins with input reading and fingerprinting
and ends only after required target delivery and cleanup. Cancellation and
structured progress cover cache lookup, reservation waits, workspace writes and
commits, planning, scheduling, stage execution, cache-hit materialization,
serialization, target writes, and cleanup.

A run cannot report successful completion before required target
materialization finishes. Cancellation stops before the next fan-out target and
interrupts the active target when its handle supports cancellation, after which
all remaining owned handles are closed.

Status: confirmed by the user.

### 23. Output targets are transactional individually

Each live output target follows a `prepare`, `commit`, and `abort` lifecycle.
Preparation may serialize and stage data without making it final; commit
finalizes that target. Failure or cancellation before successful commit requires
an abort during cleanup, and abort must be safe after a partially completed
prepare.

Successful commits are not rolled back. Ordered fan-out completes the full
prepare/commit pair for one target before beginning the next, so earlier targets
remain committed if a later target fails.

Status: confirmed by the user.

### 24. Source snapshot consistency is a soft guarantee

A source handle should represent one stable logical snapshot, and any separately
reported fingerprint should identify the same content that execution reads.

This is a soft integrity expectation rather than a mandate to reshape the
architecture aggressively. Implementations should add inexpensive TOCTOU guards
when they are minimally intrusive. They are not required to spool, duplicate, or
re-read every source solely to prove snapshot consistency. Sources that cannot
offer reasonable stability may be captured into the workspace when practical.

Status: confirmed by the user with the explicit qualification that enforcement
remain proportionate and minimally intrusive.

### 25. Cache policy does not apply to supplied inputs

A live source handle is not published as a cache entry merely because its
artifact declaration is cacheable. A supplied `a-instance` already exists in a
provider and is used as supplied. Explicit canonicalization is the sole
operation that turns a live input into a committed durable instance.

`ArtifactCachePolicy` controls lookup and publication of artifacts produced by
runtime execution. Builders reject cache-policy entries targeting supplied
inputs because directives such as `do_not_cache` and `ignore_existing_cache`
would be redundant or misleading for them.

Status: confirmed by the user.

### 26. Canonical tasks pin artifacts for their recovery window

Accepting a canonical task retains or pins every required input `a-instance`.
Newly produced requested outputs are retained similarly while the durable task
record promises recovery or retrieval.

These pins last at least until the task is terminal and its configured recovery
window has ended. Ordinary artifact TTL cannot shorten the required pin.
Canonical durability means independence from the submitting process within that
retention contract; it does not promise permanent storage.

Status: confirmed by the user.

### 27. `desired_ttl` is a best-effort cache hint

`desired_ttl` applies only when execution creates a new produced-artifact cache
entry. It does not change an existing entry and cannot shorten a task-held pin.
Providers may clamp or ignore it according to their configured retention policy.

The guaranteed canonical recovery period comes from the task-retention contract,
not from this per-artifact hint.

Status: confirmed by the user.

### 28. Cache reads and writes are independently controllable

Produced-artifact cache behavior supports four combinations:

| Ignore existing | Do not publish | Behavior |
|---|---|---|
| no | no | reuse a hit; compute and publish on a miss |
| yes | no | recompute without accepting a hit, then publish |
| no | yes | reuse a hit; compute without publishing on a miss |
| yes | yes | recompute without accepting a hit and without publishing |

Canonical requested outputs cannot disable publication, but may ignore an
existing entry to request fresh durable production.

The user agreed with the behavior matrix and suggested that a single enum may
express it more cleanly than two boolean fields. The representation is the next
decision.

### 29. Cache behavior uses one four-state enum

`ArtifactCachePolicy` replaces the two cache booleans with one mode:

```text
DEFAULT   = reuse a hit; compute and publish on a miss
REFRESH   = ignore a hit; recompute and publish
READ_ONLY = reuse a hit; compute without publishing on a miss
BYPASS    = ignore a hit; compute without publishing
```

`desired_ttl` remains an independent optional hint.

Status: confirmed by the user.

### 30. Output kind constrains cache mode

The builders enforce this matrix:

| Output kind | Valid modes |
|---|---|
| live output with a local target | all modes |
| live `expect_cached_output` | `DEFAULT`, `REFRESH` |
| canonical requested output | `DEFAULT`, `REFRESH` |
| internal intermediate artifact | all modes |

`READ_ONLY` cannot guarantee a stored targetless result after a miss, and
`BYPASS` guarantees that no such result is stored. A targeted live output always
has a delivery destination, so every mode remains meaningful.

Status: confirmed by the user.

### 31. Builders aggregate independent validation errors

Both builders normalize the complete accumulated description and report all
independently detectable build-time errors in one structured failure. An error
whose validation depends on a failed prerequisite is omitted to avoid misleading
cascades.

This aggregation applies only to pure builder validation. Runtime I/O and
execution operations remain fail-fast unless another decision explicitly says
otherwise.

Status: confirmed by the user.

### 32. Builders consume ownership only on successful build

Adding a live handle transfers ownership to `RunRequestBuilder`. A successful
`build()` consumes the builder and transfers those handles into the immutable
`RunRequest`. A failed build leaves the builder valid so the caller can correct
structural errors and retry.

Discarding or explicitly closing an unbuilt builder closes its owned handles.
Bindings without move semantics reject mutation after a successful build.
`CanonicalRunRequestBuilder` follows the same successful-consumption and
failed-retry behavior, although it owns locator values rather than live handles.

Status: confirmed by the user.

### 33. Canonicalization commits every supplied input

The conversion service reads and durably commits every input in the consumed
`RunRequest`, even if later planning might not select that input.

Canonicalization does not invoke planning to predict and prune unused roots.
Doing so would couple conversion to transient cache state, defaults, and
producer selection, and would make the canonical request cease to preserve the
complete caller-supplied root set. Callers avoid unwanted upload cost by omitting
speculative inputs from the request they convert.

Status: confirmed by the user, who noted that pruning would make the
architecture awkward.

### 34. Live execution leaves unselected inputs unread

Backward planning opens or reads a supplied live input only when the selected
plan binds it to a requirement. An unselected handle is not fingerprinted or
copied into the workspace and does not force a stage to run.

The consumed request still owns unused handles, so the runtime closes them
during cleanup. Progress may identify them as unused without reporting input
read work that did not occur.

Status: confirmed by the user.

### 35. Selected supplied inputs are authoritative

When planning selects an explicitly supplied artifact root, its live source
handle or exact `a-instance` takes precedence over a different compatible cache
entry, an applicable default, or a stage capable of producing the same artifact.

Produced-artifact cache policy cannot cause the planner to ignore an explicitly
supplied root. This precedence applies only when that input is selected; unused
supplied roots retain the lazy behavior already decided.

Status: confirmed by the user.

### 36. Canonical requests do not identify a runtime manifest

`CanonicalRunRequest` does not contain a manifest or runtime-configuration
identifier. A web-service caller should not need to know internal stage
configuration, and an older request may intentionally be processed by a newer
compatible runtime configuration.

On initial submission and recovery, the executing service validates the request,
artifact locators, policies, and planning requirements against its current
frozen manifest. It fails gracefully before stage execution when the current
configuration cannot run the request.

If useful for diagnostics, the configuration that actually processed or
rejected a task may be recorded in task execution metadata rather than embedded
in the canonical request. Pre-execution incompatibility, runtime failure, and a
possible persisted-task dead-letter mechanism require further decisions.

Status: decided by the user, overriding the recommendation to embed a required
manifest compatibility identifier.

### 37. Persisted pre-execution incompatibility is distinct from runtime failure

A newly submitted canonical request that fails validation against the current
manifest is rejected before a durable task is created.

A previously persisted request that becomes invalid during recovery enters a
distinct terminal `INCOMPATIBLE` state without starting any stage. Its original
request and structured validation reasons are retained for dead-lettering and
later redrive after a compatible runtime is deployed.

`FAILED` remains reserved for failures after execution begins.

Status: confirmed by the user.

### 38. Canonical deserialization is strict and fail-closed

Canonical JSON requires the exact schema name and a supported positive version.
Deserialization rejects unknown fields, enum or policy variants, duplicates
after normalization, and noncanonical locator spellings. Configured document,
collection, and string-size limits are enforced before expensive validation.

Serialized data can never reconstruct live handles, paths, credentials, or
callbacks. Future extension uses an explicitly supported schema version rather
than permissive handling of the current format.

Status: confirmed by the user.

### 39. Canonical collection encoding is deterministic

Canonical inputs, requested outputs, and policy entries use set/map semantics;
insertion order does not change the request. Duplicate normalized references are
invalid. Encoders sort artifact locators, policy keys, and unordered run-policy
collections deterministically.

This supports stable comparison, hashing, persistence, and diagnostics. It does
not apply to live `RunRequest` target lists, whose order remains behaviorally
significant.

Status: accepted by the user.

### 40. Canonical equality is not task identity

Submitting the same `CanonicalRunRequest` more than once creates distinct tasks
by default. Deterministic encoding may yield a stable request fingerprint, but
submission identity, progress, cancellation, audit history, and `REFRESH`
behavior remain per task.

Services that need retry idempotency use a separate submission idempotency key
owned by the persisted-task layer.

Status: confirmed by the user.

### 41. Live output locators are conditional on actual cache entries

Every requested live output receives structured result information containing
its semantic artifact reference, fingerprint, and target-delivery statuses. An
`a-instance` locator is present only when the output was resolved from or
committed to a real cache entry.

`DEFAULT` and `REFRESH` yield a locator after successful cache resolution or
commit. `READ_ONLY` yields one only on a hit. `BYPASS` and inherently
non-cacheable outputs do not yield one. The runtime does not invent locators
for in-memory values or direct writes. Targets skipped after fail-fast fan-out
are marked unattempted.

Status: confirmed by the user.

### 42. Canonical outputs receive immediate stable `a-task` indirections

Durable task acceptance allocates one stable `a-task` locator for every
requested canonical output before planning or execution begins. The locator is
initially unresolved; it acts as a durable pointer whose eventual `a-instance`
cannot yet be known.

Planning establishes the relationship to the fingerprinted cache value expected
to satisfy the output. A cache hit or successful commit later makes the pointer
dereferenceable. Non-success terminal outcomes retain structured status rather
than pretending an artifact exists. Task locators remain available for the
configured task-retention window.

Status: confirmed by the user with the explicit clarification that the
`a-task`-to-`a-instance` relationship becomes knowable only after planning.

### 43. Task artifact pointers distinguish planned from available

Each `a-task` output relationship has these nonterminal states:

```text
UNRESOLVED = task accepted; no instance identity selected
PLANNED    = frozen planning selected the expected fingerprinted instance
AVAILABLE  = the selected instance is committed and readable
```

A planned identity may be observable for diagnostics, but content cannot be
dereferenced until `AVAILABLE`. Failure, cancellation, or incompatibility moves
the task artifact to a corresponding terminal error rather than pointing at an
uncommitted cache entry.

Status: confirmed by the user.

> This decision's original explanation treated the planned instance identity as
> task-record state. Decisions 46-48 refine that model: plans are not persisted,
> and `a-task` resolution is instead derived from task lifecycle plus cache-owned
> provenance. Decision 49 below supersedes the original state definitions.

### 44. Canonical acceptance is atomically durable

Canonical task acceptance is acknowledged only after the persisted-task layer
has durably recorded the validated complete request, task identity, every
initially `UNRESOLVED` output `a-task` record, and retention claims for all input
instances.

From the caller's perspective this is all-or-nothing. After a crash, either no
accepted task exists or the complete task, protected inputs, and all output
pointers are recoverable. Planning cannot begin before this boundary succeeds.

Status: confirmed by the user.

### 45. Canonical task cancellation is durable

Cancellation of an accepted canonical task is acknowledged only after the
persisted task record is marked cancelled. Workers and recovery logic use that
record as the source of truth across process restarts.

Cancellation covers planning, cache reservation waits, cache reads and commits,
scheduling, and stage execution. An output already `AVAILABLE` before
cancellation wins the race remains available; unresolved outputs become
terminal `CANCELLED`.

Status: confirmed by the user, with emphasis on labeling the persisted task
record as cancelled.

### 46. Canonical execution progress is not persisted

No plan, execution percentage, stage checkpoint, or other in-progress state is
stored for recovery. After worker or process loss, a nonterminal task restarts
admission and planning from its original `CanonicalRunRequest` against the
current runtime configuration and current cache contents.

Only fully committed cache artifacts carry reusable work across attempts.
Uncommitted work has no durable recovery meaning, because artifacts may have
been removed and the runtime may have changed. Live progress may still be
reported to observers, but it is ephemeral and may restart from zero.

The persisted task record can therefore be largely immutable after acceptance;
the exact terminal-state and cancellation mutations remain to be clarified.

Status: decided by the user, overriding the recommendation to persist coarse
progress checkpoints.

### 47. Persisted tasks use immutable submission plus terminal outcome

After acceptance, task identity, canonical request, input retention claims, and
stable output `a-task` identities are immutable. Plans, execution attempts,
reservations, and progress are never written into the task record.

The record may later acquire one terminal outcome: `COMPLETED`, `FAILED`,
`INCOMPATIBLE`, or `CANCELLED`, together with final output-instance mappings or
structured failure reasons. Recovery of a nonterminal record starts a fresh
attempt. Cancelled records remain available for the normal retention window
rather than being deleted immediately.

Status: confirmed by the user.

### 48. The cache owns TTL-bound task-artifact provenance

The cache provider stores relationships identifying which tasks consumed or
produced an artifact instance. For each requested output, this includes the
committed `a-instance` that satisfied the task, whether newly produced or
obtained as a cache hit.

These relationships provide retrospective `a-task` resolution and have their
own task-history TTL. Artifact entry lifetime remains independently governed;
expiration or deletion of the underlying entry can make the relationship no
longer resolvable.

This metadata is not a persisted execution plan or progress checkpoint.
`PLANNED` may instead be derived from an active task with no committed output
relationship yet.

Status: confirmed by the user after re-examining the current locator and cache
implementation. Decision 50 simplifies this from typed provenance to an untyped
task-instance relation.

### 49. `a-task` state is derived from task lifecycle and cache availability

Public resolution follows this model:

| Task and provenance state | Resolution |
|---|---|
| task active, with no committed requested-output relationship | `PLANNED` |
| relationship points to a committed readable instance | `AVAILABLE` with its `a-instance` |
| task terminal non-success, with no available instance | `FAILED`, `CANCELLED`, or `INCOMPATIBLE` |
| task no longer active or known, with no readable relationship | `UNRESOLVABLE` |
| relationship exists but its entry expired or disappeared | `UNRESOLVABLE` |

`PLANNED` means only that an active task may still satisfy the output; it does
not assert that a plan or expected fingerprint was persisted. One output may
become `AVAILABLE` while other task work continues.

Status: confirmed by the user. This supersedes the original interpretation in
decision 43 while preserving the public state name.

### 50. Cache task-instance relations are untyped

The cache stores a TTL-bound relation with the conceptual key and value:

```text
(task_id, task_a_ref) -> (resolved_canonical_a_ref, fingerprint)
```

It is written whenever a task touches a committed cache instance, regardless of
whether that instance was found as a hit or newly committed. The corresponding
`a-instance` is reconstructed from the resolved canonical artifact reference and
fingerprint. Decision 53 adds the separate resolved reference because a stable
requested output key may contain an unresolved phase.

No produced, consumed, or requested-output classification is required for this
relationship. Existing producer metadata may remain a private diagnostic, but
it is not part of the public resolution contract. The relation has task-history
TTL independent from artifact-entry lifetime.

Status: confirmed by the user, replacing the proposed typed provenance model.

### 51. Task-instance resolution is unique per canonical artifact reference

For one task, one task-facing artifact reference resolves to zero or one
resolved artifact reference and fingerprint. Projection and phase are part of
the resolved canonical reference and distinguish genuinely different artifacts.

An attempt to associate a different second fingerprint with the same
`(task_id, canonical_a_ref)` is an internal planning or binding conflict and
must fail rather than overwrite the existing relation.

Status: confirmed by the user.

### 52. Live tasks expose provider-lifetime `a-task` locators

Live tasks expose `a-task` locators for requested outputs backed by committed
cache instances. They use the same task-instance relation as canonical tasks.

Under the default workspace provider these locators remain usable only while the
runtime's task history and workspace exist. They do not survive runtime
shutdown. Direct and non-cacheable outputs have no resolvable `a-task` because
no cache instance exists. Canonical tasks retain the stronger persistence and
recovery guarantees.

Status: confirmed by the user.

### 53. `a-task` uses the stable requested output reference

An `a-task` is keyed by task ID and the caller-requested output reference. That
reference may contain an unresolved phase such as `any`, allowing the locator to
remain stable when recovery replans against a changed runtime.

Its cache relation stores both the fully resolved canonical artifact reference
selected by the successful plan and its fingerprint:

```text
(task_id, requested_a_ref)
    -> (resolved_canonical_a_ref, fingerprint)
```

The resulting `a-instance` always uses the resolved reference. This requires the
task-locator model to permit unresolved request references, unlike the current
POC's concrete-phase-only parser.

Status: confirmed by the user.

### 54. Relation values use fully resolved final-graph artifact identity

The relation's `resolved_canonical_a_ref` contains the projection context,
canonical domain and content kind, and a concrete phase selected by the final
planned graph. It cannot contain `any` and must be compatible with the current
manifest and the cache entry's stored artifact metadata.

Stage and invocation identifiers are not added unless the artifact
specification already defines them as part of semantic artifact identity.

Status: confirmed by the user.

### 55. Relations are written only for committed readable instances

A cache hit creates or updates the task-instance relation only after the chosen
entry is successfully opened or bound. A newly produced artifact creates it
atomically with, or immediately after, successful cache commit.

Expected fingerprints, reservations, and planned producers do not create a
relation. Failure before commit leaves no mapping, so an active task remains
`PLANNED` rather than exposing a nonexistent instance.

Status: confirmed by the user.

### 56. Task-instance relations do not pin cache entries

Task-instance relations are lookup/history metadata, not retention claims.
Canonical task pins remain the separate mechanism protecting required artifacts
during the recovery window. Live task relations do not prevent cache eviction.

Deleting an instance removes or invalidates its task relations immediately. A
relation may also expire before its artifact; the direct `a-instance` can remain
usable even though the old `a-task` becomes `UNRESOLVABLE`.

Status: confirmed by the user.

### 57. This spec defines canonicalization behavior, not its concrete service API

`03-run-request-building.md` specifies the conversion contract: consume a
targetless `RunRequest`, validate eligibility, upload every live input, support
cancellation and progress, release handles progressively, and return a valid
`CanonicalRunRequest` without planning or executing it.

Concrete service naming, construction, dependency injection, runtime-builder
wiring, and remote transport belong in later or neighboring specifications.

Status: confirmed by the user.

### 58. Canonicalization moves to a dedicated runtime specification

`03-run-request-building.md` ends after defining the two request models,
builders, request policies, pure validation, and canonical wire representation.
It retains only a short canonicalization eligibility summary and link.

The consuming asynchronous transformation, including upload, ownership,
cancellation, progress, capability checks, and cleanup, moves into a focused new
runtime subdocument, provisionally `05-run-request-canonicalization.md`.

Status: confirmed by the user.

### 59. Live execution and durable submission use distinct public surfaces

`runtime.run(RunRequest)` performs process-local live execution.
`durable_task_service.submit(CanonicalRunRequest)` performs persisted submission
and returns durable task identity or a handle.

A caller may await the durable handle, and a live run may execute in the
background, but waiting behavior never changes durability. A deployment may
place both capabilities behind one facade while retaining distinct method names
and validation paths.

Status: confirmed by the user.

### 60. Detached live execution uses `start`

`runtime.start(RunRequest)` is the detached counterpart to
`await runtime.run(RunRequest)`. Both consume the request and use the same
in-process controller, cancellation, progress, cache, and delivery behavior.

`start` returns immediately with a live handle but does not imply persistence;
the task disappears if its owning runtime process and workspace disappear. The
existing `run_away` name is replaced by `start`.

Status: confirmed by the user.

### 61. Durable submission returns a serializable receipt

`durable_task_service.submit(CanonicalRunRequest)` returns a data-only submission
receipt containing durable task identity and its requested-output task locators.
Task status, cancellation, awaiting, and output resolution operate through the
service by task identity. Client libraries may wrap the receipt in a convenient
process-local handle, but that wrapper is not the interoperable contract.

The user noted that an `a-task` already contains its requested artifact
reference, making a map keyed by the same reference redundant.

Status: serializable receipt confirmed.

### 62. Submission receipts contain a sorted list of task locators

The receipt's output collection is a deterministically sorted list of `a-task`
locators. Each locator already embeds its requested artifact reference, so no
redundant map key is serialized. Consumers parse the locator when they need the
requested reference.

Status: confirmed by the user.

### 63. Submission receipts retain an explicit task ID

`TaskSubmission.task_id` remains the primary identity for task-level status,
cancellation, awaiting, and redrive operations even though each output locator
also embeds it. Receipt deserialization verifies that every `a-task` contains
the same task ID.

Status: confirmed by the user.

### 64. `RunPolicy` cannot change the graph

`RunPolicy` controls scheduling and admission only. It cannot affect graph
topology, producer selection, artifact identity or fingerprints, requested
outputs, cache behavior, or materialization.

Semantic production choices belong in settings artifacts or an explicitly
fingerprinted planning policy. The current statement that run policy may
influence planning or producer selection is a specification error.

Status: confirmed emphatically by the user.

### 65. Detailed run policy belongs in the scheduling specification

`03-run-request-building.md` retains the optional field on both request types,
its builder method, the scheduling-only invariant, and stable serialization
requirements.

Resource classes, concurrency limits, validation details, runtime-wide
capacity interaction, fairness, and future constraint types move to
`07-scheduling/eventing-and-task-control-model-spec.md`.

Status: accepted by the user.

### 66. Cache policy intent and mechanics have separate owners

`03-run-request-building.md` owns the caller-facing `ArtifactCachePolicy`, its
four modes, TTL hint, builder API, and output-kind validation matrix.

`03-bindings/02-cache-management-spec.md` owns hit/miss/refresh mechanics,
reservations, publication, TTL enforcement, cancellation, and garbage
collection.

Status: confirmed by the user.

### 67. Detailed handle contracts belong to Artifact I/O

`03-run-request-building.md` states which source and target handles builders
accept and how ownership transfers into builders, requests, conversion, and live
execution.

`02-artifacts/02-artifact-io-spec.md` exclusively defines source snapshot and
close expectations, cancellation behavior, and target
`prepare`/`commit`/`abort`. The request spec references those contracts when
describing consumption and ordered fan-out.

Status: confirmed by the user.

### 68. `a-task` ownership is split across three modules

`02-artifacts/01-artifacts-spec.md` owns locator grammar, requested-versus-resolved
references, and public resolution states.

`03-bindings/02-cache-management-spec.md` owns task-instance relation storage,
lookup, TTL, non-pinning behavior, and garbage collection.

`07-scheduling/eventing-and-task-control-model-spec.md` owns active and terminal
task lifecycle facts and failure reasons. The run-request spec only documents
where these locators appear and links to the owning contracts.

Status: confirmed by the user.

### 69. Request binding and execution convergence get a runtime subdocument

A focused internal runtime document defines live-handle binding and
canonical-cache binding converging into private `BoundRunRequest`, followed by
one shared planner and runner path. It also owns provider use and cleanup at
that boundary.

`BoundRunRequest` is not public. This document may replace or formalize the
unfinished misspelled `05-pipleline-internals.md`, provisionally as
`06-run-binding-and-execution.md` or an equivalent final ordering.

Status: confirmed by the user.

### 70. Persisted task lifecycle gets a scheduling subdocument

A new scheduling document, provisionally
`07-scheduling/02-persisted-task-lifecycle.md`, owns atomic canonical acceptance,
immutable submission and terminal outcome, retention pins,
restart-from-scratch attempts, durable cancellation, incompatibility versus
runtime failure, dead-letter/redrive behavior, and task-history retention.

The existing eventing/task-control specification retains generic in-process
handles, cancellation primitives, progress snapshots, results, and live task
states.

Status: accepted by the user.

### 71. Workspace cache behavior and default installation have separate owners

`03-bindings/02-cache-management-spec.md` defines the workspace provider's cache
behavior, atomicity, lifecycle, cleanup, and lack of restart durability.

`01-runtime/99-internals/99-runtime-builder-inbuilts.md` installs it universally
when no provider is configured. `01-runtime/01-runtime-builder-surface.md` defines how callers
replace it with an explicitly configured provider.

Status: confirmed by the user.

### 72. Instance locators are relative to a logical cache namespace

`CanonicalRunRequest` contains no provider identifier, backend path, bucket, or
tenant credential. The receiving durable task service resolves each
`a-instance` within its configured logical cache namespace and rejects
submission before task creation when an entry is absent, unreadable, or
incompatible.

Services must share a logical namespace or explicitly import artifacts before
transporting a canonical request between them.

Status: confirmed by the user.

### 74. Direct Artifact Cache I/O belongs to cache management

Publication, retrieval, fingerprinting, reservation, streaming, and atomicity
requirements for direct artifact-cache I/O move to
`03-bindings/02-cache-management-spec.md`, which already has the corresponding
responsibility area.

Request and canonicalization documents retain only concise upload-first examples
showing how returned `a-instance` locators enter a canonical builder.

Status: accepted by the user.

### 75. Settings and defaults are split across request, artifact, and planning specs

`03-run-request-building.md` states only that caller-supplied settings are
ordinary input artifacts and includes a concise builder example.

`02-artifacts/01-artifacts-spec.md` owns settings identity, codec behavior, and
fingerprint participation. `06-planning/01-task-planning.md` owns lazy default
discovery, relevance, precedence, and binding.

Status: confirmed by the user.

### 76. Integration patterns belong in the run-contract overview

`00-overview/01-run-contract-overview.md` explains the absent-client and
present-client patterns. Web-service flows upload inputs, submit a canonical
request, and later resolve `a-task`. A simple local CLI uses live file handles
and direct targets by default, choosing upload and canonical submission only for
explicit durable/background operation.

The request-building spec retains focused type construction examples rather
than owning adapter architecture.

Status: confirmed by the user.

### 77. Requests may contain zero explicit inputs

Both builders permit an empty input collection. A runtime may satisfy requested
outputs entirely from declaration defaults, generated sources, or stages with
no external requirements. Graph satisfiability remains a planning decision.

Status: confirmed by the user.

### 78. Requests require at least one output

Both builders reject an empty output collection. Requested artifacts are the
roots of backward planning and define the task's work and result. Side-effect-only
operations require a separate service contract rather than an invisible
runtime output.

Status: confirmed by the user.

### 79. Duplicate output declarations are explicit

Repeated `expect_output_target` calls for one live output append targets in
order. Mixing `expect_cached_output` with targeted declarations for the same
artifact is rejected, as is repeating `expect_cached_output`.

`CanonicalRunRequestBuilder` rejects repeated `expect_output` declarations after
artifact-reference normalization.

Status: confirmed by the user.

### 80. Unreachable known cache policy is non-fatal

Builders accept policy for manifest-known produced artifacts. Planning does not
add work merely to make such policy relevant. If the final graph does not touch
the artifact, its policy is ignored and may be reported as unused diagnostics.

Unknown artifact references remain validation errors.

Status: accepted by the user.

### 81. Recovery may reuse same-task instances despite `REFRESH`

On the first attempt, `REFRESH` ignores compatible shared entries created by
other tasks. If this task commits an artifact and records its task-instance
relation, a later recovery attempt may reuse that exact instance.

Recovery still replans from scratch; the cache relation merely carries completed
artifact work across attempts. `BYPASS` work cannot be recovered because no
committed instance exists.

Status: confirmed by the user.

### 82. Nonterminal recovery may replace stale task-instance relations

A recovery attempt reuses a same-task relation only when its resolved reference,
fingerprint, and cache metadata exactly match the new plan. An incompatible old
relation is ignored and may be removed or replaced when new work commits.

Replacement is permitted only while the task remains nonterminal. Terminal task
mappings are immutable until their TTL expires. At every instant there remains
at most one effective mapping for a task-facing artifact reference.

Status: confirmed by the user, refining the uniqueness rule in decision 51.

### 83. Terminal task failure does not hide committed outputs

Each requested output resolves independently. An output with a committed,
readable task-instance relation remains `AVAILABLE` even when the overall task
later becomes `FAILED` or `CANCELLED`. An output without a committed relation
resolves to the applicable terminal task error.

Partial artifact availability does not change the overall terminal status.

Status: confirmed by the user.

### 84. Unsupported canonical graphs become `UNPLANNABLE` tasks

Malformed canonical data is rejected before task creation. Submission also
rejects unavailable or incompatible input instances and a provider incapable of
canonical durability.

A structurally valid request whose output is unknown, non-cacheable, or cannot
be produced by the current graph is accepted as a durable task. Planning begins
from that persisted request and terminates it as pre-execution `UNPLANNABLE`
without starting any stage.

This is distinct from recovery-time `INCOMPATIBLE` and runtime `FAILED`.

Status: accepted by the user.

### 85. Both request builders are standalone and manifest-independent

`RunRequestBuilder` and `CanonicalRunRequestBuilder` are standalone formatters
and structural validators. Neither is created by, bound to, or aware of a
configured runtime manifest.

They validate locator syntax and normalization, duplicates, output presence,
method/source-kind consistency, policy contradictions visible from request data,
and ownership state. The runtime or durable service validates known artifacts,
target compatibility, artifact cacheability, provider capability, and graph
support when it consumes the built request.

Status: confirmed by the user.

### 86. Canonicalization preflights eligibility before upload

After taking ownership, the conversion service verifies before reading any
source that outputs have no local targets, requested outputs are manifest-known
and declared cacheable, output cache modes permit publication, and the provider
supports canonical durability.

This is not graph planning and does not prove that a producer path exists. If
preflight fails, the request remains consumed and its handles are closed without
upload. The user identified `Planning/manifest_graph.py` as a likely home or
source for the manifest-backed validation capability.

Status: confirmed by the user.

### 87. Canonical encoding normalizes away default policies

Absent and explicitly default policies are semantically identical. Canonical
construction omits an empty/default `RunPolicy` and removes per-artifact
`ArtifactCachePolicy(mode=DEFAULT, desired_ttl=None)` entries.

Only non-default fields are encoded, with all remaining entries sorted
deterministically. Requests differing only by explicit versus implicit defaults
therefore have identical canonical data.

Status: confirmed by the user.

### 88. Canonical TTL hints use positive whole seconds

Language bindings may expose idiomatic duration types. Canonical JSON encodes a
non-default hint as `desired_ttl_seconds` with a positive integer value. Zero,
negative, fractional, non-finite, and otherwise invalid values are rejected.

Absence means no request-specific hint; provider clamping or ignoring remains
permitted.

Status: confirmed by the user.

### 89. Canonicalization uses normal cache I/O without a streaming guarantee

Canonicalization commits every input through the ordinary artifact and cache-I/O
contracts. The run-request and canonicalization specifications do not introduce
a separate streaming-ingest API or bounded-memory guarantee.

Individual handles and providers may stream internally as an implementation
capability. Settings may naturally be encoded from small JSON/YAML-like values
through their artifact codec.

Status: confirmed by the user after rejecting the proposed mandatory
bounded-memory contract as unnecessary complexity.

### 90. Request terminology distinguishes lifecycle from representation

The process-local request form is `LocalRunRequest`, built by
`LocalRunRequestBuilder`. The data-only form used for durable submission and
replay is `DurableRunRequest`, built directly by `DurableRunRequestBuilder` or
produced asynchronously from a consumed local request.

Prose uses *local request* rather than *live request*. The conversion operation
is *durable request preparation*, with illustrative naming such as
`durable_request_preparer.prepare(local_request)`. The word *canonical* is
reserved for representation concerns such as locator normalization, canonical
JSON encoding, and deterministic field ordering.

The version-1 JSON schema identifier is `chunky.durable-run-request`. This is a
pre-stable terminology replacement, not a compatibility alias; the superseded
public names and schema identifier are not retained.

Status: confirmed by the user.

## Open decisions

To be resolved one at a time during the interview.

### Deferred: client isolation and authorization

A future design needs a client/user identity boundary that partitions task and
artifact discovery and access. Without knowledge and authority for that identity,
one client must not learn about another client's tasks or artifacts.

This interview intentionally does not specify identity representation,
credentials, authorization persistence, revocation, tenancy, or enforcement
mechanics. Those concerns must be revisited in a dedicated security/multi-tenant
design rather than expanded inside the run-request model.

### Unresolved: cache ingestion and transfer capabilities

Question 90 incorrectly framed cache-provider durability primitives as a
provider "supporting canonical tasks." Canonical task semantics belong above
the cache and this framing was rejected.

The actual unresolved boundary is how Artifact/Cache I/O imports and exports
resources efficiently:

- adoption or relocation when an already encoded resource can be atomically
  renamed or moved into provider-owned storage;
- streamed copying when bytes must cross storage boundaries, such as local disk
  to S3 or S3 to a local workspace;
- codec compatibility and metadata validation determining whether existing bytes
  may be transferred directly or must be materialized and re-encoded;
- ownership, rollback, cancellation, progress, and cleanup for each transfer
  mode.

This likely belongs primarily in `02-artifacts/02-artifact-io-spec.md` and
`03-bindings/02-cache-management-spec.md`, with canonicalization consuming the
resulting cache-ingest abstraction rather than defining it.

## Provisional cross-spec routing

This routing is advisory until its individual boundaries are confirmed:

- `01-runtime/03-run-request-building.md`: request models, separate builders,
  request policies, pure validation, and canonical wire representation;
- a new runtime canonicalization document: consuming live-to-canonical
  conversion, upload behavior, ownership, cancellation, and progress;
- `01-runtime/04-run-surface.md`: live and canonical submission surfaces, run
  handles, result consumption, and public lifecycle integration;
- `07-scheduling/eventing-and-task-control-model-spec.md`: persisted task
  records, restart-from-scratch attempts, terminal states, durable cancellation,
  incompatibility, dead-letter/redrive behavior, and ephemeral progress;
- `02-artifacts/01-artifacts-spec.md`: `a-task` grammar, stable requested output
  identity, resolved instance identity, and resolution states;
- `02-artifacts/02-artifact-io-spec.md`: source ownership and snapshot guidance,
  plus target prepare/commit/abort behavior;
- `03-bindings/02-cache-management-spec.md`: provider durability capabilities,
  retention pins, task-instance relations, TTL and garbage collection, workspace
  provider semantics, and cache modes;
- `06-planning/01-task-planning.md`: fully resolved final-graph artifact
  references and per-task instance uniqueness;
- runtime builder and inbuilt-provider specs: installation and lifecycle of the
  universal workspace-backed default cache provider.
