# Handover Prompt: Run Surface Interview

> **Historical.** Superseded by the active run, durable-task, and download
> surfaces keyed by canonical `ScopedArtifactRequest`.

Continue the design interview for
`specs/01-runtime/04-run-surface.md`.

## Required working protocol

- Read and follow `AGENTS.md` and `specs/SPECS_RULE.md` first.
- Activate the repository with Serena and read the Serena instructions manual
  before project work.
- Prefer codebase-memory-mcp graph tools for code discovery.
- Read the complete interview record:
  `specs/01-runtime/interviews/04-run-surface-interview.md`.
- Also read:
  - `specs/01-runtime/04-run-surface.md`;
  - `specs/01-runtime/02-run-request-building-surface.md`;
  - `specs/01-runtime/03-durable-artifacts-upload-surface.md`;
  - `specs/07-scheduling/eventing-and-task-control-model-spec.md`;
  - `specs/07-scheduling/02-persisted-task-lifecycle.md`; and
  - `specs/01-runtime/interviews/03-run-request-building-interview.md`.
- The older POC is under `src/python/chunky/Runtime`; inspect it when useful,
  but do not treat it as authoritative.
- Ask exactly one design question at a time and wait for the user's answer.
- Give a recommended answer with every question.
- Look up discoverable facts rather than asking the user.
- Record each confirmed, rejected, refined, or superseded decision in
  `04-run-surface-interview.md` as the interview proceeds.
- Keep the interview focused on the run surface and its immediate cancellation,
  awaiting, state, result, submission, and lifetime boundaries.
- Do not modify `specs/01-runtime/04-run-surface.md`, create the planned failure
  contract, change surrounding authoritative specs, or implement code until the
  user explicitly confirms shared understanding.
- Preserve unrelated existing worktree changes.

## Current state

The target run-surface specification has not been modified. The working
interview record contains 87 confirmed or superseded decisions.

Major settled directions include:

- Local `run`, local handle awaiting, polling, and durable observation use a
  shared data-only `RunState` family.
- `get_state()` returns `InProgress(progress)` or a terminal variant; waits
  return the same type and guarantee a terminal variant.
- Wait cancellation, timeout, or disconnect never implicitly cancels a task.
- Cancellation is explicit, non-blocking, idempotent, has no caller reason, and
  returns a simple boolean.
- Local callers needing cancellation authority use `start()` and retain a
  cloneable/shareable `RunHandle`.
- `RunHandle` exposes `task_id` and sorted `output_artifacts` containing stable
  early `a-task` locators; it exposes no direct `a-instance` accessor.
- Early `a-task` locators may contain unresolved requested phase selectors.
  Query results separately expose the requested reference, planner-resolved
  Completed Value, and available instance.
- Durable control is service-by-task-ID:
  `get_state`, `await_to_completion`, and `cancel`.
- Durable receipts are data-only and contain `task_id`, sorted
  `output_artifacts`, effective active expiry, and terminal-retention
  information.
- Durable submission returns `DurableSubmissionReceipt` after atomic
  acceptance. Rejection before acceptance is a call-level `Failure`.
- Every successfully accepted durable submission creates a new task. There is
  no idempotency key and no automatic task-level deduplication. Cache policy
  controls artifact reuse.
- Durable requests are immutable, reusable, and cloneable.
- Durable tasks have separate active/recovery and terminal-record lifetimes.
  Active expiry produces `Expired(RunResult)` after cleanup; terminal history
  later expires into a lookup error.
- Durable restart creates a fresh attempt and fresh progress. `task_id` remains
  stable; required `attempt_id` changes.
- Durable service shutdown interrupts only the attempt, not the durable task.
  `Interrupted` is a local-only orderly-shutdown terminal outcome.
- The initial surface has no local or durable subscription API. Callers poll
  `get_state()` or await completion.
- `RunResult.status`, error IDs, embedded manifests, and `persisted_task_id` are
  removed.
- `RunResult` contains task and attempt identity, a sorted complete output-state
  list, and at most one aggregated failure envelope.
- Progress and terminal results share one `OutputState` shape. Progress contains
  every requested output and permits `PENDING` and `RUNNING`; terminal results
  prohibit those nonterminal statuses.
- Output semantic status and local target-delivery status are independent.
- Requested output identity and planner-resolved Completed Value are separate
  fields.
- All Chunky-originated errors use one versioned data-only failure envelope.
  Its planned owner is
  `specs/00-overview/02-failure-contract.md`, but that document must not be
  created until final user confirmation.

The interview record is the source of truth for the exact decisions and
supersession history; do not rely only on this summary.

## Resume point

Question 88 was asked but not answered:

> Should a durable task's `active_expires_at` continue to elapse while the
> service is stopped or unavailable? On recovery, a task already past that
> deadline would enter expiry handling without starting a new execution
> attempt.

Recommended answer: yes, because an absolute active lifetime should not silently
extend during service downtime.

Ask this question first in the new session. After it is resolved, approximately
8–10 material branches were expected to remain:

- timestamp and retention-field precision;
- final `OutputState` and target-state invariants;
- `RunState` serialization/versioning;
- unknown versus expired lookup-error distinctions;
- final cross-spec ownership and consistency review; and
- an explicit user confirmation that shared understanding has been reached.
