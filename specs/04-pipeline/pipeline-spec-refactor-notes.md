# Pipeline Spec Refactor Notes

Purpose: track known divergences between the intended pipeline design and the current implementation.

This file is intentionally separate from `pipeline-spec.md`, which remains normative.

## Known Gaps

### R1. Lazy default settings resolution
- Decision IDs: D3, D4, D5, D6.
- Intended design:
  - Resolve default settings lazily, only when planning determines that a setting is relevant.
  - Let the planner own relevance discovery.
  - Use an explicit on-demand settings resolver interface.
- Implementation status:
  - Applied in Python and Rust: default binding is no longer performed by the pipeline before planning.
  - The planner now accepts an explicit default-settings resolver seam and invokes it as part of planning.
  - Remaining refinement: the executable resolver still resolves the default-capable settings set in one planner-owned step rather than a fully per-setting async callback from each planning hook.

### R2. Feature-first runtime configuration
- Decision IDs: D1, D21, D22, D23.
- Intended design:
  - Public runtime configuration should come through installable features.
  - Specialist runtime services occupy explicit service slots.
  - Conflicting service-slot installation should hard-fail.
- Implementation status:
  - Partially applied: builder lifecycle and duplicate feature enforcement are now stricter.
  - Remaining refinement: direct runtime collaborator injection remains available as an implementation/test escape hatch.

### R3. Builder lifecycle and duplicate installation enforcement
- Decision IDs: D17, D18.
- Intended design:
  - The builder is single-use.
  - Any mutation or second `build()` after a successful build hard-fails.
  - Duplicate feature installation hard-fails by default.
- Implementation status:
  - Applied in Python and Rust for duplicate feature installation.
  - Applied for Python builder post-build mutation/build checks; Rust's consuming builder API already prevents normal post-build reuse.

### R4. Cache policy request surface
- Decision IDs: D7, D8, D9, D10, D11, D12, D13.
- Intended design:
  - `RunRequest.cache_policy` is a normalized per-artifact mapping.
  - Directives are `ignore_existing_cache`, `do_not_cache`, and `desired_ttl`.
  - Unknown artifact IDs fail validation; known unreachable artifact IDs produce diagnostics.
- Implementation status:
  - Applied in Python and Rust: `RunRequest` exposes per-artifact cache policy.
  - `ignore_existing_cache` and `do_not_cache` are wired into stage-output binding.
  - `desired_ttl` is represented but not yet consumed by the cache backend contract.

### R5. Output fan-out materialization
- Decision IDs: D24, D25, D26, D27, D28, D29, D32.
- Intended design:
  - Any materializable semantic artifact may be requested as an output.
  - One artifact may materialize to an ordered list of targets.
  - Target failure fails the run, reports partial target success, and does not roll back successful writes.
- Implementation status:
  - Applied in Python and Rust: requested outputs may fan out to an ordered list of targets.
  - Materialization preserves target order and records successful target counts.

### R6. Failure and partial-outcome surfaces
- Decision IDs: D35, D36, D37, D38.
- Intended design:
  - `run_away(...)` exposes terminal failed state even before invocation progress exists.
  - Failed results surface structured diagnostics and successful partial outcomes.
  - `run(...)` raises on failure, but the exception carries equivalent structured information.
- Implementation status:
  - Applied in Python: direct run failures raise an exception carrying the structured `RunResult`; detached runs surface the same failed result.
  - Partially applied in Rust: result models can carry failed output, but the spawned run path still maps generic errors without downcasting structured partial failures.

### R7. Manifest validation boundary
- Decision IDs: D20, D39, D40.
- Intended design:
  - `StageManifest` creation rejects static structural invalidity.
  - Request-dependent missing producer paths remain planner failures.
- Current implementation gap:
  - Manifest creation validation should be reviewed against the stronger boundary.
