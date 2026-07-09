# Run Contract Overview Decision Log

Source spec: `specs/00-overview/00-run-contract-overview.md`

Status: in interview

## Interview Order

1. Contract scope and public API boundaries
2. Request model and artifact addressing
3. Settings and default-resolution semantics
4. Stage contract and binding semantics
5. Planning and graph-shape invariants
6. Cache policy and cache-execution semantics
7. Materialization semantics
8. Failure, progress, and partial-outcome semantics

## Decisions



### D60. Target-less requested outputs
- Status: accepted
- Question: Should the run contract allow a requested output artifact to be declared with zero explicit caller materialization targets, meaning “make this semantic artifact available for this run and publish enough metadata/events to retrieve it later,” rather than requiring every requested output to name at least one target handle?
- Recommended answer: Yes. Keep outputs mandatory as the declaration of caller entitlement, but allow an output entry to have an empty target list so the run can request semantic availability without immediate materialization.
- Decision: Accepted.
- Why this matters:
  - It preserves the output-driven contract while enabling web-service retrieval patterns.
  - It separates semantic output entitlement from immediate caller-side materialization.
  - It creates a clean contract for later cache-based retrieval without inventing a second request surface.

### D61. Durability requirement for target-less outputs
- Status: accepted
- Question: When an output is requested with zero explicit targets, should that be valid only for artifacts that are durably retrievable after the run, rather than for non-cacheable artifacts that would disappear once the task finishes?
- Recommended answer: Yes. A target-less output should be allowed only when the contract can still satisfy the caller's later-read entitlement through durable publication, which in practice means the artifact must be cacheable for that request.
- Decision: Accepted.
- Why this matters:
  - It keeps the later-read contract honest.
  - It prevents target-less requests from silently producing ephemeral-only results.
  - It ties the web-service retrieval pattern to a concrete durability guarantee.

### D62. Contradictory cache policy on target-less outputs
- Status: accepted
- Question: If a caller requests a target-less output, should `cache_policy.do_not_cache=True` for that same artifact be a hard validation error, rather than letting execution proceed with contradictory instructions?
- Recommended answer: Yes. Treat it as a validation error.
- Decision: Accepted.
- Why this matters:
  - It rejects incompatible caller intent before planning.
  - It prevents the contract from promising later retrieval while forbidding durable publication.
  - It keeps web-service retrieval semantics explicit and deterministic.

### D63. Concrete retrieval references in results and events
- Status: accepted
- Question: When a requested output becomes available, should the contract require both terminal results and progress/event payloads to include a concrete cached-instance reference for that artifact, so a web client can immediately store and later re-use the retrieval identifier without re-deriving it?
- Recommended answer: Yes. Return the concrete cached-instance reference, not just the semantic artifact ID.
- Decision: Accepted.
- Why this matters:
  - It gives web-service clients a stable retrieval identifier at the moment the artifact becomes available.
  - It avoids forcing clients to reconstruct or rediscover the concrete published instance later.
  - It aligns task completion, eventing, and saved-task metadata around one shared reference model.

### D64. Emit retrieval references only after durable commit
- Status: accepted
- Question: Should the concrete cached-instance reference be emitted only once the artifact is actually committed and durably available, rather than earlier in “planned” or “in progress” events?
- Recommended answer: Yes. Emit it only after durable commit.
- Decision: Accepted.
- Why this matters:
  - It keeps retrieval references trustworthy.
  - It avoids exposing provisional identifiers for artifacts that may never become available.
  - It preserves a clean boundary between expected outputs and published outputs.

### D65. Include semantic artifact ID alongside concrete retrieval reference
- Status: accepted
- Question: When an output is durably published, should the result/event include both the concrete cached-instance reference and the semantic artifact ID, rather than assuming the concrete reference alone is enough?
- Recommended answer: Yes. Include both.
- Decision: Accepted.
- Why this matters:
  - It makes multi-output runs easier to reconcile.
  - It preserves the caller-visible contract slot alongside the exact retrievable instance.
  - It avoids forcing clients to recover semantic meaning indirectly from the concrete reference form.

### D66. Upload deduplication identity
- Status: accepted
- Question: For the upload-first web-service flow, should duplicate-upload detection be based on the same concrete cached-instance reference model (`artifact_id + fingerprint`) rather than on service-specific upload IDs?
- Recommended answer: Yes. Base deduplication on the same concrete reference model.
- Decision: Accepted for now.
- Why this matters:
  - It keeps upload detection, later retrieval, and saved-task rebinding on one identity scheme.
  - It avoids inventing a second identifier that must later be mapped back into the run contract.
  - It preserves a common identity surface across direct cache IO and service-facing upload flows.
- Notes:
  - User clarification: leave room for a future optimization so a client does not need to upload a full large file just to discover that it already exists.

### D67. Reserve a future artifact-existence preflight
- Status: accepted
- Question: Should the contract explicitly reserve a future preflight “does this concrete artifact already exist?” check, using the same semantic artifact ID plus fingerprint identity, without requiring that optimization in the first version?
- Recommended answer: Yes. Reserve it now, but do not require it yet.
- Decision: Accepted.
- Why this matters:
  - It keeps the identity model stable while allowing later transport optimization.
  - It avoids forcing the first version to solve large-upload short-circuiting immediately.
  - It gives future web-service APIs a clear compatibility path for existence probing.

### D68. Persisted task state boundary
- Status: accepted
- Question: For the future resume model, should the persisted task-management service own only task metadata and request configuration, and never persist live scheduler state, partial stage state, or in-flight progress snapshots across process restarts?
- Recommended answer: Yes. Keep persistence limited to task metadata and restartable request configuration.
- Decision: Accepted.
- Why this matters:
  - It preserves the cache-driven replay model already established for restart.
  - It avoids coupling persisted records to process-local scheduler and resource state.
  - It keeps restart semantics simpler and more robust across implementation changes.

### D69. Persisted task record shape
- Status: accepted
- Question: Should a persisted task record be required to store the original normalized `RunRequest` shape plus enough planning-visible pipeline compatibility metadata to decide whether restart is allowed, rather than storing only an application-level task ID and ad hoc fields?
- Recommended answer: Yes. Store the normalized request contract and explicit compatibility metadata.
- Decision: Accepted.
- Why this matters:
  - It makes restart eligibility deterministic.
  - It keeps persistence backends aligned on what constitutes the same restartable task.
  - It preserves one shared request model across in-memory and durable task management.

### D70. Explicit planning-visible compatibility identity
- Status: accepted
- Question: Should the minimum restart-compatibility metadata include a planning-visible pipeline or manifest identity, so the task manager can deterministically say “this saved task cannot start under the currently loaded pipeline configuration”?
- Recommended answer: Yes. Require a planning-visible compatibility identity.
- Decision: Accepted.
- Why this matters:
  - It makes restart gating explicit.
  - It separates “temporarily incompatible environment” from malformed task data.
  - It anchors saved-task resumability to the same planning-visible semantics as the run contract.

### D71. Non-terminal stored incompatibility state
- Status: accepted
- Question: When a saved task's compatibility identity does not match any currently available pipeline configuration, should that task remain stored in a non-terminal “cannot start yet” condition rather than being rewritten as failed?
- Recommended answer: Yes. Keep it stored and non-terminal.
- Decision: Accepted.
- Why this matters:
  - It distinguishes environment blockage from execution failure.
  - It allows restartability to become available later without mutating task history.
  - It preserves the original task request as still meaningful even when it is temporarily unrunnable.

### D72. Keep compatibility-waiting out of live run state
- Status: accepted
- Question: Should this “cannot start yet because no compatible pipeline is available” condition belong to the persisted task-management layer only, rather than expanding the live `RunHandle` task-state enum used for active in-process runs?
- Recommended answer: Yes. Keep it in the persisted-task layer.
- Decision: Accepted.
- Why this matters:
  - It preserves the meaning of the live run lifecycle states.
  - It keeps compatibility gating clearly pre-admission.
  - It avoids mixing durable queue semantics into the in-process run-handle model.

### D73. Stable persisted-task identity across restarts
- Status: accepted
- Question: When a persisted task is restarted, should it retain the same logical persisted-task identity for client correlation even though it creates a fresh live in-process run with its own new `task_id`?
- Recommended answer: Yes. Keep a stable persisted-task ID and create a new live run ID each time execution is re-entered.
- Decision: Accepted.
- Why this matters:
  - It separates durable task identity from ephemeral execution attempts.
  - It supports client correlation, audit history, and restart diagnostics.
  - It keeps the live run model free to allocate fresh per-attempt runtime state.

### D74. Carry both persisted-task ID and live run ID in events and results
- Status: accepted
- Question: When a persisted task is restarted and begins a new live run, should emitted events and terminal results carry both the stable persisted-task ID and the fresh live `task_id`, rather than only one of them?
- Recommended answer: Yes. Carry both.
- Decision: Accepted.
- Why this matters:
  - It lets clients correlate a durable task across multiple live execution attempts.
  - It preserves exact attribution for each concrete run attempt.
  - It supports resumed event delivery over re-established channels without losing per-attempt diagnostics.

### D75. Resumed eventing guarantees snapshot continuity, not historical replay
- Status: accepted
- Question: For future resumed eventing over an existing or re-established channel, should the contract promise continuity of logical task identity and current snapshot state, but not promise replay of every transient historical progress event unless a later event-log feature is added?
- Recommended answer: Yes. Promise stable task identity and a coherent current snapshot, not full historical event replay.
- Decision: Accepted.
- Why this matters:
  - It keeps resumed eventing practical without requiring durable event sourcing.
  - It gives clients a reliable recovery point after channel interruption.
  - It preserves room for a richer replay feature later without overcommitting now.

### D76. Reserve future event replay extension
- Status: accepted
- Question: Should the spec explicitly reserve a future event replay or cursor-resume extension, but keep it out of the first persisted-task/eventing contract?
- Recommended answer: Yes. Reserve it, but do not require it.
- Decision: Accepted.
- Why this matters:
  - It leaves room for richer catch-up semantics later.
  - It keeps the first resumed-eventing contract narrow.
  - It avoids accidental dependence on transient in-memory event history.

### D77. Graceful shutdown preserves restartable task records, not live execution state
- Status: accepted
- Question: For a future graceful global shutdown capability, should the contract require the task-management layer to stop admitting new live runs first, then preserve restartable persisted-task records for affected durable tasks, rather than trying to serialize in-flight scheduler state?
- Recommended answer: Yes. Shutdown should quiesce admission and preserve restartable task records, not checkpoint live execution internals.
- Decision: Accepted.
- Why this matters:
  - It aligns shutdown behavior with the accepted cache-driven restart model.
  - It keeps graceful shutdown implementation-bounded.
  - It makes durable task handling during shutdown predictable and auditable.

### D78. Shutdown handling differs for durable versus in-memory tasks
- Status: accepted
- Question: During graceful global shutdown, should a durable persisted task that was actively running be transitioned back into a resumable persisted state for later re-submission, while a purely in-memory non-durable `run_away(...)` task is simply terminated with no restart guarantee?
- Recommended answer: Yes. Distinguish them explicitly.
- Decision: Accepted.
- Why this matters:
  - It preserves the existing non-durable `run_away(...)` contract.
  - It gives durable tasks a defined shutdown recovery path.
  - It prevents accidental over-promising of resumability for in-memory-only runs.

### D79. Distinct shutdown-interruption outcome
- Status: accepted
- Question: When a live execution attempt is interrupted by graceful global shutdown, should its terminal result/reporting distinguish “stopped due to shutdown for later resubmission” from ordinary user cancellation or task failure?
- Recommended answer: Yes. Distinguish it explicitly.
- Decision: Accepted.
- Why this matters:
  - It improves client and operator observability.
  - It prevents shutdown-preserved work from being misclassified as failure or user cancellation.
  - It supports accurate audit trails and resumed execution semantics.

### D80. Durable persisted-task execution is a higher-level orchestration layer
- Status: accepted
- Question: Should the overview treat durable persisted-task execution as a future orchestration layer built on top of the same underlying `RunRequest` and execution core, rather than as a third separate run API alongside `run(...)` and `run_away(...)`?
- Recommended answer: Yes. Keep it as a future orchestration layer over the same core contract.
- Decision: Accepted.
- Why this matters:
  - It preserves the accepted “one execution core, two current surfaces” model.
  - It keeps durable submission and restart concerns out of the base run API.
  - It gives later service/task-manager specs a clean layering boundary.

### D81. Direct cache IO is the common foundation for upload-first and later retrieval workflows
- Status: accepted
- Question: For the overview document, should we explicitly state that the direct artifact-cache IO surface is the foundation for both upload-first web-service workflows and later output retrieval workflows, so those two use cases are described as one coherent contract family rather than separate ad hoc features?
- Recommended answer: Yes. State that explicitly.
- Decision: Accepted.
- Why this matters:
  - It unifies upload, dedup/existence checks, cached-root rebinding, and later output retrieval under one model.
  - It gives future service-facing specs a cleaner architectural foundation.
  - It reduces the risk of duplicating identity or retrieval semantics across adjacent features.
