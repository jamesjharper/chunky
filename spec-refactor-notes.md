# Spec Refactor Notes

Purpose: summarize source-code changes needed to align the current Python and Rust implementations with the reconciled run-contract specs.

This file is intentionally implementation-facing. The normative behavior lives in the specs, especially `00-run-contract-overview.md`, `pipeline-spec.md`, `01-task-planning.md`, and `eventing-and-task-control-model-spec.md`.

## Current implementation baseline

Both Python and Rust already have useful pieces of the reconciled model:

- `CachedArtifactReference` exists as `artifact_id + fingerprint`.
- Direct `ArtifactCacheIO.publish/open` exists.
- Supplied cacheable roots are published during binding unless exact `do_not_cache` opts out.
- Cached-root references can be rebound through cache before planning.
- Output fan-out exists as ordered target lists.
- The runner separates semantic artifact production from caller materialization.

The remaining work is mostly request validation, result/progress shape, and durable-task boundary modeling.

## R1. Target-less requested outputs

### Intended design

A `RunRequest.outputs` entry may have an empty target list. This is an explicit request for semantic availability and later retrieval, not an omitted output.

Validation rules:

- outputs remain mandatory as a map;
- an individual requested output may have zero targets;
- the artifact must be known and cacheable for this request;
- `cache_policy[artifact_id].do_not_cache=True` for the same artifact is a hard validation error;
- target-less status must not alter graph shape or producer selection.

### Python changes

- Change `_collect_flattened_request_outputs(...)` in `src/python/chunky/pipeline.py` so an empty sequence is accepted and normalized to `()`, instead of appending `outputs for ... must contain at least one target`.
- Add request validation after artifact lookup to reject target-less outputs whose declaration is not cacheable.
- Add validation that rejects `cache_policy[artifact_id].do_not_cache` when `output_targets[artifact_id] == ()`.
- Add tests covering:
  - empty target list accepted for cacheable requested output;
  - empty target list rejected for non-cacheable output;
  - empty target list plus `do_not_cache=True` rejected;
  - graph/planner requested output set is unchanged by target-less delivery.

### Rust changes

- `RunRequest.outputs: BTreeMap<ArtifactKey, Vec<Arc<dyn ArtifactTargetHandle>>>` can already represent empty target lists. Add explicit validation in `ChunkyPipeline::execute` or a new request-validation helper.
- Reject target-less outputs for non-cacheable declarations.
- Reject target-less outputs when exact `ArtifactCachePolicy.do_not_cache` is true.
- Add tests matching the Python cases.

## R2. Requested-output retrieval references

### Intended design

When a requested output becomes durably available, result and progress/event payloads must include both:

- the requested semantic artifact ID;
- the concrete committed `CachedArtifactReference`.

The reference is emitted only after durable cache commit. It must not be exposed for merely planned, pending, or in-progress outputs.

### Python changes

- Introduce a compact output-result model instead of only `TaskRunResult.output_fingerprints` and `materialized_targets` counts. Suggested shape:
  - `OutputResult.artifact_id`
  - `OutputResult.status`
  - `OutputResult.fingerprint`
  - `OutputResult.cached_reference`
  - `OutputResult.targets`
  - `OutputResult.error_ids`
- Add a `TargetMaterializationResult` for ordered fan-out reporting rather than only storing the final successful count.
- In `ExampleTaskRunner.run(...)`, after resolving each requested output instance:
  - compute `CachedArtifactReference(instance.handle.declaration.artifact_id, instance.fingerprint)` only if the artifact is durably committed/retrievable;
  - for target-less outputs, skip materialization and mark the output succeeded once the reference exists;
  - for target fan-out, still include the reference if semantic production/cache resolution succeeded before materialization.
- Decide how to prove durability for a cache hit or cacheable produced output. The simple implementation can treat cache-backed instances as durable; non-cacheable in-memory-only instances must not produce a reference.
- Update `RunResult`/`RunExecutionError` to carry structured output results rather than a generic nested `TaskRunResult` only.
- Add tests for successful target-less output returning a `CachedArtifactReference`, and for materialization failure preserving the reference when semantic commit succeeded.

### Rust changes

- Mirror the Python result model in `src/rust/crates/chunky/src/Scheduling/task_runner.rs` and `task_control.rs`.
- Replace or supplement `output_fingerprints` and `materialized_targets` with structured `OutputResult` and per-target materialization outcomes.
- Ensure target-less outputs skip materialization but still emit the requested output result and cached reference.
- Add parity tests.

## R3. Progress/output availability snapshots

### Intended design

The public progress surface is still a complete snapshot, not a low-level event stream. It should include committed requested-output availability entries so a web client can store retrieval references before terminal completion if desired.

### Python changes

- Add `OutputAvailabilitySnapshot` to `src/python/chunky/Scheduling/progress_models.py`.
- Add `available_outputs: tuple[OutputAvailabilitySnapshot, ...]` to `RunProgressSnapshot`.
- Add a `RunProgressTracker.record_available_output(artifact_id, cached_reference)` method.
- Call that method only after durable commit/cache-hit validation for a requested output.
- Ensure late subscribers receive the current snapshot including already available output references.

### Rust changes

- Add the equivalent `OutputAvailabilitySnapshot` and `available_outputs` fields in `src/rust/crates/chunky/src/Scheduling/progress_models.rs`.
- Add tracker mutation and snapshot rebuild support.
- Update subscription tests to verify current-snapshot delivery includes already available outputs.

## R4. Direct cache IO as shared upload/retrieval foundation

### Intended design

`ArtifactCacheIO` is the common primitive for upload-first flows, duplicate-upload identity, cached-root rebinding, and later output retrieval. The public identity is always `artifact_id + fingerprint`.

### Python changes

- `ArtifactCacheIO.publish(...)` currently returns a reference even when `cache_policy.do_not_cache=True` without writing the cache entry. That conflicts with the durable-reference meaning. Change one of these approaches:
  - reject `do_not_cache=True` for direct publish; or
  - return a non-durable fingerprint-only diagnostic type, not `CachedArtifactReference`.
- Add an `exists(reference)` or `try_open(reference)` seam only if implementing the reserved future preflight now. Otherwise leave it out but keep identity-compatible tests around `open` failures.
- Ensure `publish` and provided-root publication share the same helper path so metadata validation/reservation behavior cannot diverge.

### Rust changes

- Add an optional publish policy parameter if direct publish needs to honor policy. If not, document and enforce that direct publish always means durable publish.
- Keep `open(reference)` as the validation/rebinding primitive.

## R5. Persisted-task orchestration boundary

### Intended design

Durable persisted-task execution is a future orchestration layer over the same `RunRequest` and execution core. It is not a third base run API.

Persisted task records should store:

- stable persisted-task ID;
- normalized `RunRequest` shape;
- durable root references;
- planning-visible pipeline/manifest compatibility identity;
- attempt history/diagnostics as task-manager metadata.

They must not store live scheduler state, partial stage state, cache reservations, resource leases, or in-flight progress snapshots.

### Python changes

- Do not expand the current live `TaskState` enum for compatibility-waiting. If a persisted task manager is added, keep cannot-start-yet state in that layer.
- If adding a persisted task manager, start each execution attempt by reconstructing a normal `RunRequest` and calling the same execution core with a fresh live `task_id`.
- Add optional `persisted_task_id` plumbing to progress snapshots and terminal results only when the persisted layer exists.

### Rust changes

- Same boundary as Python: keep live `RunHandle` and `TaskState` process-local.
- Model persisted-task identity separately from live run ID if/when persisted orchestration is implemented.

## R6. Graceful shutdown semantics

### Intended design

Graceful global shutdown should stop admitting new live work, preserve restartable persisted-task records for durable tasks, and terminate in-memory-only `run_away(...)` work without a restart guarantee. A shutdown-interrupted durable attempt should not be reported as ordinary caller cancellation or generic failure.

### Python changes

- Add a cancellation/shutdown origin that distinguishes process shutdown from caller cancellation in final reporting.
- Add a terminal status or outcome reason equivalent to `shutdown_interrupted` for attempts stopped specifically for durable resubmission.
- Ensure in-memory `run_away(...)` does not imply restartability.

### Rust changes

- Mirror the Python outcome distinction in `RunStatus` or equivalent terminal result model.
- Keep persisted-task resubmission outside the base run API.

## R7. Tests and parity checks

Add cross-language tests for:

1. target-less output validation success/failure;
2. target-less output returns committed `CachedArtifactReference`;
3. `do_not_cache=True` conflicts with target-less output;
4. fan-out materialization preserves target order and reports per-target outcomes;
5. materialization failure does not remove the committed reference;
6. cached-root reference identity is `artifact_id + fingerprint`, not a bare fingerprint;
7. direct cache publish/open and provided-root publication produce compatible references;
8. progress snapshots include already available requested outputs for late subscribers;
9. persisted-task compatibility-waiting is not represented as a live `TaskState`.
