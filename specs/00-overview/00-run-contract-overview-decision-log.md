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

### D1. Authoritative contract scope
- Status: accepted
- Question: Should `specs/00-overview/00-run-contract-overview.md` be the authoritative language-neutral public contract for runs, with Python and Rust bindings treated as projections of the same `RunRequest`/run model rather than separate behavioral contracts?
- Recommended answer: Yes. Keep this document as the single semantic contract, and let language-specific APIs be thin idiomatic wrappers over it.
- Decision: Accepted.
- Why this matters:
  - It makes cross-language behavior a compatibility requirement rather than an implementation coincidence.
  - It constrains binding APIs to compile down to one shared request and execution model.
  - It gives downstream specs one top-level contract to refine rather than multiple binding-specific sources of truth.

### D2. Overview document role
- Status: accepted
- Question: Should this overview remain a stable public contract document, while lower-level specs own most internal mechanism details, or should the overview itself also be normative about internal component responsibilities like planner ownership, runner admission timing, and materializer sequencing?
- Recommended answer: Keep the overview normative about a small set of internal responsibility boundaries that are necessary to preserve the public contract, but push the detailed mechanics into the lower-level specs.
- Decision: Accepted.
- Why this matters:
  - It makes the overview the anchor for the rest of the design without forcing every low-level mechanism into one document.
  - It gives downstream specs clear constraints on which responsibilities are fixed versus refinable.
  - It reduces the risk that detailed specs drift into incompatible interpretations of the public run contract.
- Notes:
  - User clarification: this document should help anchor the other specs in how it brings everything together.

### D3. Public execution surfaces
- Status: accepted
- Question: Should the first public run contract continue to expose exactly two execution surfaces, `run(...)` and `run_away(...)`, with all other modes deferred?
- Recommended answer: Yes. Keep the contract narrow around awaited execution and detached in-process execution, and leave durable background jobs, per-output await, and resumable handles out of scope.
- Decision: Accepted.
- Why this matters:
  - It fixes the external execution model before discussing handle semantics, progress, and failure delivery.
  - It keeps the first public contract small enough to make cross-language parity realistic.
  - It preserves space for future execution surfaces without weakening the current guarantees.

### D4. Shared execution core
- Status: accepted
- Question: Should `run(...)` and `run_away(...)` be required to share the same planning/execution/materialization core, differing only in how completion and control are exposed to the caller?
- Recommended answer: Yes. One execution core, two exposure modes.
- Decision: Accepted.
- Why this matters:
  - It prevents semantic drift between direct and detached execution.
  - It localizes correctness to one planning and execution path.
  - It makes detached execution a wrapper contract rather than a second runtime model.

### D5. Detached handle durability boundary
- Status: accepted
- Question: Should the `run_away(...)` handle contract stay intentionally minimal: cancellation, progress inspection, and awaited completion only, with no durability, persistence, or cross-process recovery semantics?
- Recommended answer: Yes. Keep the handle as an in-memory control surface only.
- Decision: Accepted.
- Why this matters:
  - It keeps the current contract honest about what state actually survives process exit.
  - It avoids prematurely introducing job persistence responsibilities into the run handle model.
  - It preserves a clean boundary between detached execution and any future persisted-task layer.
- Notes:
  - User clarification: a later spec should describe persisted task configuration records that can be retried after restart by reconstructing the original task configuration and rediscovering completed work through cache.
  - User clarification: persisted job information should contain task metadata/configuration, not persisted execution status.
  - User clarification: if the pipeline configuration has changed and no longer matches the serialized task configuration, the task should remain stored but be treated as unable to start until a matching pipeline configuration is available again.

### D6. Forward compatibility for persisted task re-submission
- Status: accepted
- Question: Should this overview explicitly reserve compatibility for a future persisted-task layer by stating that restart-time “resumption” is defined as re-submission of an equivalent saved task configuration plus normal cache rediscovery, not persisted execution-state continuation?
- Recommended answer: Yes. Add that as a forward-compatibility boundary in the overview, while keeping the actual persisted-task design out of the current run-handle contract.
- Decision: Accepted.
- Why this matters:
  - It protects an intended future extension without weakening the current in-process handle contract.
  - It clarifies that any future restart behavior is cache-driven replay, not restoration of live scheduler state.
  - It creates an explicit compatibility boundary around pipeline-configuration matching for re-startable saved tasks.

### D7. Output-driven request contract
- Status: accepted
- Question: Should the public run contract remain output-driven, meaning every `RunRequest` must declare requested outputs and `run_policy` must never reduce or suppress them?
- Recommended answer: Yes. Keep outputs mandatory and authoritative; policy may influence how work is planned or executed, but not what the caller is entitled to receive.
- Decision: Accepted.
- Why this matters:
  - It fixes the semantic center of a run request before discussing planning or execution flexibility.
  - It prevents run policy from becoming a hidden way to weaken caller-visible obligations.
  - It keeps success and failure semantics grounded in explicit requested artifacts.

### D8. Output eligibility scope
- Status: accepted
- Question: Should any semantic artifact that can be materialized be requestable as an output, including intermediates and diagnostics, rather than limiting outputs to a curated “final results” set?
- Recommended answer: Yes. If an artifact is semantically real and materializable, it should be requestable.
- Decision: Accepted.
- Why this matters:
  - It keeps the run contract artifact-centric rather than hard-coding a narrow notion of end-user outputs.
  - It allows callers to request debugging, inspection, and intermediate products without adding separate API surfaces.
  - It keeps output selection orthogonal to the planner’s internal graph structure.
- Notes:
  - User clarification: requestability as an output does not change the artifact’s declared role.

### D9. Artifact role immutability
- Status: accepted
- Question: Should artifact role remain an immutable declaration-level property, so requesting an intermediate or diagnostic artifact as an output does not promote it into a target-role artifact or otherwise change planning semantics?
- Recommended answer: Yes. Output requestability should affect caller entitlement and materialization only, not artifact role.
- Decision: Accepted.
- Why this matters:
  - It keeps the artifact model stable and context-independent.
  - It prevents request shape from mutating declaration semantics in hidden ways.
  - It cleanly separates artifact identity/role from caller-visible output selection.

### D10. Supplied intermediate roots
- Status: accepted
- Question: Should `RunRequest.inputs` continue to allow caller-supplied intermediate artifacts as first-class graph roots, alongside sources and settings?
- Recommended answer: Yes. Keep explicitly supplied intermediates as valid roots.
- Decision: Accepted.
- Why this matters:
  - It allows callers to splice into the graph at semantic intermediate boundaries.
  - It supports debugging, experimentation, and partial reuse workflows without separate APIs.
  - It affects planner root handling and producer-path precedence rules.

### D11. Supplied root precedence
- Status: accepted
- Question: When a caller supplies an artifact that could also be produced by the pipeline, should the supplied artifact win over producer paths by default?
- Recommended answer: Yes. A supplied artifact should satisfy that requirement by default, unless some future explicit recompute/force-producer policy says otherwise.
- Decision: Accepted.
- Why this matters:
  - It makes caller intent deterministic when explicit supplied artifacts are present.
  - It avoids surprising recomputation of artifacts the caller deliberately provided.
  - It establishes a stable planner precedence rule for both source and intermediate graph roots.

### D12. Unified artifact addressing
- Status: accepted
- Question: Should the overview keep one unified artifact-addressing model across `inputs`, `outputs`, and `cache_policy`, using normalized concrete artifact keys from either direct keys or nested string mappings?
- Recommended answer: Yes. One addressing model everywhere.
- Decision: Accepted.
- Why this matters:
  - It keeps the public request model internally consistent.
  - It reduces binding-specific edge cases around naming and normalization.
  - It makes validation and diagnostics easier to explain and implement consistently.

### D13. Duplicate normalized artifact IDs
- Status: accepted
- Question: After normalization, should duplicate concrete artifact IDs anywhere in request addressing be a hard validation error rather than “last one wins” or merge behavior?
- Recommended answer: Yes. Duplicate normalized artifact IDs should hard-fail validation.
- Decision: Accepted.
- Why this matters:
  - It keeps request interpretation deterministic and non-lossy.
  - It catches caller mistakes early instead of silently overriding data.
  - It preserves cross-language consistency for nested and direct addressing forms.

### D14. Aggregated structural validation
- Status: accepted
- Question: Should structural validation errors across `inputs`, `outputs`, and `cache_policy` be accumulated and reported together, rather than failing on the first one encountered?
- Recommended answer: Yes. Aggregate deterministic shape/normalization errors into one validation result.
- Decision: Accepted.
- Why this matters:
  - It makes request validation a more useful and predictable contract surface.
  - It avoids iterative fix-one-error-at-a-time loops for purely structural problems.
  - It keeps validation behavior deterministic and easier to align across bindings.

### D15. Settings as normal artifacts
- Status: accepted
- Question: Should settings remain ordinary semantic artifacts that stages consume through normal bindings and that participate in fingerprinting like any other effective input, rather than being treated as a separate hidden configuration channel?
- Recommended answer: Yes. Keep settings as normal artifacts.
- Decision: Accepted.
- Why this matters:
  - It keeps planning, caching, and execution on one artifact model.
  - It makes effective inputs and fingerprints explainable.
  - It avoids introducing a second, less visible configuration path with special semantics.

### D16. Supplied settings precedence
- Status: accepted
- Question: When a caller supplies a settings artifact explicitly, should that binding always take precedence over any declaration default?
- Recommended answer: Yes. Supplied settings bind first; defaults exist only to fill relevant gaps.
- Decision: Accepted.
- Why this matters:
  - It keeps caller intent authoritative for explicit settings.
  - It makes default resolution a gap-filling mechanism rather than a competing source of truth.
  - It creates a clear prerequisite for lazy relevance-driven default binding.

### D17. Lazy default resolution
- Status: accepted
- Question: Should declaration defaults be resolved lazily only for settings that the request-specific plan actually makes relevant, rather than eagerly binding every declared default before planning?
- Recommended answer: Yes. Resolve defaults lazily and only when relevant.
- Decision: Accepted.
- Why this matters:
  - It avoids work and side effects for irrelevant settings.
  - It keeps planning focused on the graph actually required by the request.
  - It makes default binding semantics depend on relevance rather than declaration existence alone.

### D18. Planner-owned default relevance
- Status: accepted
- Question: Should the planner own relevance discovery and request defaults on demand, rather than the pipeline doing a preplanning relevance pass?
- Recommended answer: Yes. The planner should own relevance discovery.
- Decision: Accepted.
- Why this matters:
  - It preserves a clean planner/pipeline boundary.
  - It keeps request-specific graph discovery in one place.
  - It avoids splitting planning semantics across multiple lifecycle phases.

### D19. Default provider determinism boundary
- Status: accepted
- Question: Should a default-source provider be restricted to deterministic inputs local to the setting declaration and optional caller override, and be forbidden from inspecting broader request shape, planner state, cache state, wall-clock time, or resource availability?
- Recommended answer: Yes. Keep default providers narrow, local, and deterministic.
- Decision: Accepted.
- Why this matters:
  - It preserves explainable fingerprints and cache safety.
  - It prevents default resolution from becoming a hidden planning hook.
  - It keeps settings defaults composable and predictable across bindings and runtimes.

### D20. Resolved defaults as normal roots
- Status: accepted
- Question: After a default setting is resolved, should it behave exactly like a caller-supplied graph root for the rest of planning and execution?
- Recommended answer: Yes. Once resolved, a defaulted setting should be semantically indistinguishable from a supplied root except for diagnostics about where it came from.
- Decision: Accepted.
- Why this matters:
  - It keeps downstream planning and execution semantics uniform.
  - It avoids creating a second-class category of bound settings artifacts.
  - It preserves a clean distinction between provenance diagnostics and execution behavior.

### D21. Immutable stage registration boundary
- Status: accepted
- Question: Should a stage continue to be registered through immutable metadata, with execution performed through a separate narrow executor contract rather than by the registry object itself?
- Recommended answer: Yes. Keep immutable registration metadata separate from executor behavior.
- Decision: Accepted.
- Why this matters:
  - It keeps the manifest a stable planning-time surface.
  - It separates planning-visible stage identity from runtime execution mechanics.
  - It avoids blurring registry, executor, and orchestration responsibilities.

### D22. Runner-owned orchestration responsibilities
- Status: accepted
- Question: Should stages remain forbidden from doing cache lookup, cache reservation, manifest mutation, or direct caller-target materialization, with those responsibilities owned by the runner/pipeline machinery instead?
- Recommended answer: Yes. Stages should operate only on already-bound semantic inputs and outputs.
- Decision: Accepted.
- Why this matters:
  - It preserves a clear orchestration boundary around cache, planning, and materialization.
  - It keeps stage behavior focused on deterministic semantic transformation.
  - It makes system-wide guarantees enforceable in one place instead of relying on stage discipline.

### D23. Binding transport boundary
- Status: accepted
- Question: Should bindings remain the physical transport abstraction, allowing direct in-process typed-object passing between stages without a serialize/deserialize round trip, while still requiring canonical codec serialization at cache and materialization boundaries?
- Recommended answer: Yes. Allow efficient in-process transport, but keep canonical serialized form at persistence/export boundaries.
- Decision: Accepted.
- Why this matters:
  - It preserves efficient in-process execution.
  - It keeps cache and materialization behavior canonical and portable.
  - It cleanly separates transport optimization from semantic artifact identity.

### D24. Codec extension semantics
- Status: accepted
- Question: Should codec `extension` remain storage/materialization metadata only, and not be part of the stage semantic dependency contract?
- Recommended answer: Yes. `mime_type` and the artifact schema/codec matter semantically; file extension should stay non-semantic metadata.
- Decision: Accepted.
- Why this matters:
  - It prevents file naming details from affecting planning or compatibility.
  - It keeps semantic identity separate from storage/export presentation.
  - It reduces accidental cache or planner invalidation from non-semantic changes.

### D25. Shared per-request dependency graph
- Status: accepted
- Question: Should each `RunRequest` resolve into one shared dependency DAG across all requested outputs, rather than separate per-output plans?
- Recommended answer: Yes. One request, one shared graph.
- Decision: Accepted.
- Why this matters:
  - It enables shared upstream work and consistent multi-output reasoning.
  - It keeps planning and scheduling aligned with artifact reuse.
  - It defines the structural unit of execution for a run request.

### D26. Cache-independence of graph shape
- Status: accepted
- Question: Should cache state be forbidden from changing graph shape, with cache hits affecting execution skipping only after planning has already produced the request-specific DAG?
- Recommended answer: Yes. Cache influences execution, not planning topology.
- Decision: Accepted.
- Why this matters:
  - It preserves reproducible planning for the same request and pipeline configuration.
  - It keeps diagnostics about missing producers and chosen paths independent of transient cache contents.
  - It cleanly separates planning semantics from execution-time reuse decisions.

### D27. Pre-execution planning failure boundary
- Status: accepted
- Question: Should request-specific planning failures such as missing producer paths, unresolved required settings, schema incompatibility, unsatisfied constraints, or unresolved ambiguity remain pre-execution failures rather than being deferred to runtime?
- Recommended answer: Yes. These are planning failures and should stop the run before execution starts.
- Decision: Accepted.
- Why this matters:
  - It draws a clean line between unrunnable requests and execution-time failures.
  - It keeps execution focused on realizable planned work.
  - It improves diagnostics by failing before any runtime side effects occur.

### D28. Explicit requested outputs per invocation
- Status: accepted
- Question: Should a planned stage invocation be scoped to the explicit subset of outputs the request-specific graph actually needs from that invocation, rather than implicitly meaning “produce every output this stage knows how to emit”?
- Recommended answer: Yes. Planned invocations should carry explicit requested output subsets.
- Decision: Accepted.
- Why this matters:
  - It keeps planning precise and request-specific.
  - It avoids accidental behavior changes when stages gain additional output capabilities.
  - It gives cache-hit and execution-skipping rules a precise unit of responsibility.

### D29. Public per-artifact cache policy
- Status: accepted
- Question: Should per-artifact cache behavior remain an explicit `RunRequest.cache_policy` field in the public contract, instead of being only pipeline-level configuration?
- Recommended answer: Yes. Keep per-run, per-artifact cache intent in the request contract.
- Decision: Accepted.
- Why this matters:
  - It gives callers a precise way to influence cache behavior for a specific run.
  - It keeps semantic cache intent separate from backend/runtime configuration.
  - It avoids requiring pipeline reconfiguration for operationally targeted cache decisions.

### D30. Cache policy targeting scope
- Status: accepted
- Question: Should `cache_policy` be allowed to target any exact concrete semantic artifact ID in the request/plan, including intermediates, rather than only requested outputs or supplied roots?
- Recommended answer: Yes. Allow any exact concrete artifact ID.
- Decision: Accepted.
- Why this matters:
  - It makes per-artifact cache control expressive enough for intermediate reuse/debugging cases.
  - It keeps cache intent aligned with the artifact-centric model.
  - It avoids forcing callers to expose or request artifacts solely to influence their cache treatment.

### D31. Known-unreachable cache policy handling
- Status: accepted
- Question: If `cache_policy` names an artifact ID that is known to the manifest but unreachable in the resolved request-specific graph, should that entry be ignored with diagnostics rather than failing the run?
- Recommended answer: Yes. Unknown IDs should fail validation, but known-yet-unreachable IDs should be ignored with surfaced diagnostics.
- Decision: Accepted.
- Why this matters:
  - It distinguishes caller mistakes from harmless over-specification.
  - It keeps broad cache-policy scope usable without requiring perfect reachability prediction from callers.
  - It preserves strict validation for genuinely invalid artifact identifiers.

### D32. Orthogonal cache policy directives
- Status: accepted
- Question: Should `ignore_existing_cache`, `do_not_cache`, and `desired_ttl` remain orthogonal directives with distinct effects on read, write, and new-entry creation respectively?
- Recommended answer: Yes.
  - `ignore_existing_cache` affects reuse for this run.
  - `do_not_cache` affects publication for this run.
  - `desired_ttl` applies only when creating a new entry.
- Decision: Accepted.
- Why this matters:
  - It prevents ambiguous precedence and interaction rules.
  - It keeps read-path and write-path cache behavior conceptually separate.
  - It makes combined per-artifact cache intent explainable.

### D33. Existing entry TTL immutability
- Status: accepted
- Question: If an artifact already exists in cache, should a later run’s `desired_ttl` be forbidden from mutating that existing entry’s TTL?
- Recommended answer: Yes. `desired_ttl` should influence only newly created cache entries.
- Decision: Accepted.
- Why this matters:
  - It keeps cache policy non-destructive toward existing published state.
  - It avoids hidden retroactive mutation from later runs.
  - It preserves a clear meaning for TTL on already-committed cache entries.

### D34. Ordered output fan-out
- Status: accepted
- Question: Should one semantic artifact continue to support ordered fan-out to multiple caller targets within the same run?
- Recommended answer: Yes. Keep ordered output fan-out as part of the contract.
- Decision: Accepted.
- Why this matters:
  - It makes multi-target delivery a first-class materialization obligation.
  - It supports workflows that need the same semantic artifact in multiple destinations/forms.
  - It affects error reporting and partial-outcome semantics for target writes.

### D35. Target order preservation
- Status: accepted
- Question: Should target order in an output fan-out list be a contractual guarantee that materialization preserves, rather than a best-effort implementation detail?
- Recommended answer: Yes. Preserve caller-declared target order.
- Decision: Accepted.
- Why this matters:
  - It makes materialization side effects predictable.
  - It affects how partial success is interpreted when later target writes fail.
  - It keeps caller intent explicit when targets have observable ordering consequences.

### D36. Multi-target materialization failure semantics
- Status: accepted
- Question: If one target write fails in an ordered fan-out after earlier target writes have succeeded, should the run fail overall while still reporting which target writes succeeded and which failed?
- Recommended answer: Yes. Materialization failure should fail the run, but successful earlier side effects must be reported, not hidden.
- Decision: Accepted.
- Why this matters:
  - It defines the overall run-status rule for caller-visible materialization side effects.
  - It separates semantic artifact production success from caller-target delivery success.
  - It creates the basis for partial-outcome reporting on failed runs.
- Notes:
  - This aligns with accepted pipeline decision D27 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D37. Materialization rollback behavior
- Status: accepted
- Question: If a run fails during multi-target materialization after some targets were already written, should successful target side effects remain in place rather than promising rollback?
- Recommended answer: Yes. Do not promise rollback; report the successful and failed target writes.
- Decision: Accepted.
- Why this matters:
  - It keeps materialization semantics honest for non-transactional destinations.
  - It avoids imposing compensating-delete requirements on target handles.
  - It clarifies that failed runs may still leave visible successful side effects behind.
- Notes:
  - This aligns with accepted pipeline decision D28 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D38. Cancellation during materialization
- Status: accepted
- Question: If cancellation arrives after a semantic artifact is available but before all requested caller targets have been materialized, should the runner stop remaining target writes as soon as practical rather than finishing the rest of the fan-out?
- Recommended answer: Yes. Honor cancellation promptly and stop remaining target writes as soon as practical.
- Decision: Accepted.
- Why this matters:
  - It keeps cancellation meaningful for caller-visible side effects.
  - It avoids surprising extra work after the caller has asked the run to stop.
  - It fits the already-accepted non-transactional materialization model.
- Notes:
  - This aligns with accepted pipeline decision D29 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D39. Post-plan progress registration boundary
- Status: accepted
- Question: Should progress registration still begin only after planning succeeds and concrete invocations exist, with no pre-plan progress stream in this first contract?
- Recommended answer: Yes. Keep progress registration post-plan only for now.
- Decision: Accepted.
- Why this matters:
  - It keeps the progress model aligned with concrete planned work.
  - It avoids inventing pre-plan lifecycle progress surfaces before they are fully designed.
  - It preserves consistency with the accepted pipeline-level decision.
- Notes:
  - This aligns with accepted pipeline decision D34 in `specs/01-pipeline/pipeline-spec-decision-log.md`, which rejected adding pre-plan progress for now.

### D40. Pre-plan failure surfacing for detached runs
- Status: accepted
- Question: Even without pre-plan progress events, should `run_away(...)` still complete with a terminal failed result carrying phase-specific diagnostics when failure happens during normalization, binding, or planning?
- Recommended answer: Yes. No pre-plan progress does not mean no observable pre-plan failure.
- Decision: Accepted.
- Why this matters:
  - It keeps detached execution diagnosable across the full lifecycle.
  - It distinguishes “no progress yet” from “no failure information.”
  - It aligns detached failure semantics with the accepted pipeline-level decision.
- Notes:
  - This aligns with accepted pipeline decision D35 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D41. Direct run failure surface
- Status: accepted
- Question: On failure, should direct `run(...)` continue to raise an exception rather than returning a typed failed result object?
- Recommended answer: Yes. Keep `run(...)` exception-based.
- Decision: Accepted.
- Why this matters:
  - It preserves an idiomatic direct-await surface.
  - It keeps `run(...)` and `run_away(...)` distinct by lifecycle/control surface rather than by core semantics.
  - It still allows one shared failure model underneath.
- Notes:
  - This aligns with accepted pipeline decision D37 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D42. Direct run exception richness
- Status: accepted
- Question: When `run(...)` raises on failure, should that exception be required to carry structured diagnostics and successful partial outcomes, rather than collapsing to an opaque error?
- Recommended answer: Yes. The exception should carry the same rich failure information model as detached completion, adapted to exception form.
- Decision: Accepted.
- Why this matters:
  - It keeps the direct-await path as informative as the detached path.
  - It preserves one shared failure-information model across both execution surfaces.
  - It avoids forcing callers onto `run_away(...)` just to get structured failure details.
- Notes:
  - This aligns with accepted pipeline decision D38 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D43. Bound supplied-instance cleanup on planning failure
- Status: accepted
- Question: If planning fails after caller-supplied inputs have already been bound into run-scoped instances, should the pipeline close every bound supplied instance it holds for that run?
- Recommended answer: Yes. Treat bound supplied instances as run-scoped owned resources and close them on planning failure.
- Decision: Accepted.
- Why this matters:
  - It makes resource ownership and cleanup predictable on pre-execution failure paths.
  - It reduces leak risk for bound file handles, temp resources, and similar run-scoped instances.
  - It keeps failure cleanup simple instead of depending on provenance-specific rules.
- Notes:
  - This aligns with accepted pipeline decision D33 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D44. Build-time manifest freeze and validation boundary
- Status: accepted
- Question: Should `build()` remain the hard freeze-and-validate boundary, meaning a configured pipeline owns one baseline immutable `StageManifest`, and structurally invalid topology must be rejected before any run can begin?
- Recommended answer: Yes. `build()` should freeze the baseline manifest and reject static topology errors up front.
- Decision: Accepted.
- Why this matters:
  - It establishes the stable bootstrap boundary the run contract depends on.
  - It separates invalid pipeline configuration from request-specific planning failure.
  - It prevents runtime execution from having to cope with globally broken topology.

### D45. Manifest identity boundary
- Status: accepted
- Question: Should runtime mechanics such as cache backend choice, runner implementation, lock strategy, and deployment wiring remain outside manifest identity unless they can change graph shape, artifact identity, or semantic outputs?
- Recommended answer: Yes. Only semantic, planning-visible configuration should contribute to manifest identity.
- Decision: Accepted.
- Why this matters:
  - It defines what a manifest identity or compatibility check actually proves.
  - It keeps operational deployment choices separate from semantic reproducibility.
  - It constrains future persisted-task restart compatibility to planning-visible configuration changes.

### D46. Spec versus implementation-gap tracking
- Status: accepted
- Question: Do you want the overview to explicitly call out where it intentionally diverges from the current implementation, or should implementation-gap tracking stay out of the overview and live only in separate refactor/debt notes?
- Recommended answer: Keep implementation-gap tracking out of the overview itself. The overview should stay normative; divergence notes should live in separate refactor-oriented files.
- Decision: Accepted.
- Why this matters:
  - It keeps the overview cleanly normative.
  - It avoids mixing migration commentary into the contract document.
  - It still leaves room for explicit implementation-gap tracking elsewhere.
- Notes:
  - This aligns with accepted pipeline decisions D42 and D43 in `specs/01-pipeline/pipeline-spec-decision-log.md`.

### D47. Default cache publication for provided artifact roots
- Status: accepted
- Question: Should the contract change so that any caller-provided artifact root that is cacheable is uploaded into the artifact cache by default at bind time, unless that exact artifact is opted out via per-artifact `cache_policy`?
- Recommended answer: Yes. Make cache publication of provided artifact roots the default, because it supports restartable saved tasks and future multi-file/web upload workflows without changing planning semantics.
- Decision: Accepted.
- Why this matters:
  - It makes restartable saved-task replay viable even when the original external inputs are no longer available.
  - It creates a natural model for future large-file and multi-file upload workflows without changing the run contract shape.
  - It brings caller-provided roots into the same cache-coordination model as stage-produced reusable artifacts.

### D48. Scope of default cache publication for provided roots
- Status: accepted
- Question: Should this default cache-publication rule apply to all caller-provided artifact roots that are semantically cacheable, including sources, settings, and explicitly supplied intermediates, rather than only source artifacts?
- Recommended answer: Yes. Apply it to all cacheable provided roots, with cacheability determined by the artifact declaration and per-artifact policy.
- Decision: Accepted.
- Why this matters:
  - It keeps restartability and future upload workflows consistent across root types.
  - It avoids special-casing source artifacts when supplied intermediates and settings may also need to survive process restarts.
  - It keeps the rule aligned with the artifact-centric model rather than role-specific exceptions.

### D49. Opt-out mechanism for provided-root cache publication
- Status: accepted
- Question: Should opting out of this default publication use the existing per-artifact `cache_policy.do_not_cache` control, rather than inventing a separate “do not upload provided root” flag?
- Recommended answer: Yes. Reuse `do_not_cache` so the policy model stays unified: if a provided artifact root is marked `do_not_cache`, it remains usable for the current run but is not published into durable cache.
- Decision: Accepted.
- Why this matters:
  - It keeps cache policy conceptually unified.
  - It avoids splitting root-publication behavior into a second policy surface.
  - It preserves a consistent meaning for per-artifact cache opt-out across supplied and produced artifacts.
- Notes:
  - User clarification: a saved task cannot be resumed if `do_not_cache` is enabled for any of its provided roots.

### D50. Persisted-task restart eligibility with uncached roots
- Status: accepted
- Question: For the future persisted-task model, if any provided root was marked `do_not_cache`, should restart be treated as a hard “cannot start” condition for that saved task rather than a best-effort attempt?
- Recommended answer: Yes. Treat it as a hard cannot-start condition, because the saved task no longer has a complete durable root set to reconstruct the original run configuration.
- Decision: Accepted.
- Why this matters:
  - It turns root-cache publication into a concrete restart eligibility rule.
  - It avoids ambiguous partial-restart semantics when a durable root set is incomplete.
  - It aligns saved-task restartability with the requirement to reconstruct the original run configuration from durable state.

### D51. Dedicated direct artifact-cache IO interface
- Status: accepted
- Question: Should Chunky define a dedicated public interface for direct artifact-cache reads and writes, separate from `run(...)`/`run_away(...)`, so callers can publish provided artifacts into cache and retrieve them later without going through a run request?
- Recommended answer: Yes. Define a dedicated cache-IO interface.
- Decision: Accepted.
- Why this matters:
  - It creates a clean architectural seam for upload-first and restartable-task workflows.
  - It avoids abusing run requests just to populate or inspect durable artifact roots.
  - It allows future web-service streaming upload designs without changing run execution semantics.

### D52. Semantic shape of the direct cache-IO interface
- Status: accepted
- Question: Should that direct cache-IO interface operate in terms of semantic artifact declarations/IDs and artifact source or target handles, rather than exposing raw cache paths or backend-native object keys?
- Recommended answer: Yes. Keep it artifact-semantic and handle-based.
- Decision: Accepted.
- Why this matters:
  - It preserves the language-neutral artifact contract.
  - It prevents cache backend details from leaking into caller code.
  - It keeps direct cache IO aligned with the rest of the artifact/binding model.

### D53. Direct cache-IO write correctness boundary
- Status: accepted
- Question: Should direct writes through the cache-IO interface be required to obey the same semantic fingerprinting, metadata validation, reservation, and atomic publish rules as run-time cache publication?
- Recommended answer: Yes. Direct cache writes should not be a weaker or bypass path.
- Decision: Accepted.
- Why this matters:
  - It keeps directly uploaded artifacts trustworthy as first-class cache entries.
  - It ensures planning and execution can rely on uploaded cache state the same way they rely on run-produced cache state.
  - It avoids creating a second class of cache entries with weaker correctness guarantees.

### D54. Planning semantics for directly published cached roots
- Status: accepted
- Question: When a task configuration references a provided root that was already published through the direct cache-IO interface, should planning and binding treat that cached root exactly like the same artifact supplied normally, with no special planning semantics beyond provenance diagnostics?
- Recommended answer: Yes. A directly published cached root should behave like an ordinary supplied root once selected by the task configuration.
- Decision: Accepted.
- Why this matters:
  - It keeps upload-first workflows within the same execution model.
  - It ensures publishing roots into cache changes availability, not graph semantics.
  - It avoids inventing a second planner mode for cache-backed provided roots.
- Notes:
  - User clarification: task configuration needs a way to carry an identifier from cache-IO publication, such as the artifact fingerprint, so the cached root can be referenced later.
  - User clarification: cached settings roots may need to be read earlier so planning can consider them.

### D55. Cached-root reference shape in task configuration
- Status: accepted
- Question: When a task configuration wants to reference a provided root that was already published into cache, should it do so by pairing the semantic artifact ID with a concrete published fingerprint, rather than by fingerprint alone?
- Recommended answer: Yes. Use `artifact_id + fingerprint` as the reference shape.
- Decision: Accepted.
- Why this matters:
  - It preserves semantic typing and validation when rebinding cached roots.
  - It avoids treating fingerprints as untyped global identifiers.
  - It gives saved task configurations a durable way to refer to previously published roots.

### D56. Early binding of cached settings roots for planning
- Status: accepted
- Question: For settings artifacts referenced from cache by `artifact_id + fingerprint`, should the pipeline be allowed to bind and read those cached settings before normal graph planning proceeds, so their values can participate in planning just like ordinary supplied settings?
- Recommended answer: Yes. Cached settings roots should have an explicit early-binding path for planning.
- Decision: Accepted.
- Why this matters:
  - It preserves the planner’s ability to use supplied settings values even when those values are rebound from cache.
  - It avoids making cached settings a second-class supplied-root path.
  - It introduces only the narrow early-binding exception required for planning-relevant settings values.
- Notes:
  - User clarification: consider extending the artifact identity/reference format to carry the `artifact_id + fingerprint` concept.
  - User clarification: assume the evolving identity/reference format should adhere to URI standards, with a likely future move to an official URI form.

### D57. Cached-instance references as part of the official addressing model
- Status: accepted
- Question: Should the `artifact_id + fingerprint` cached-root reference become part of the official artifact identity/addressing model going forward, rather than staying as a separate task-configuration-only reference type?
- Recommended answer: Yes. Evolve the addressing model to include a URI-compatible artifact reference form that can represent both semantic artifact identity and a concrete cached instance identity.
- Decision: Accepted.
- Why this matters:
  - It gives saved-task configuration, direct cache IO, and future service APIs one shared reference model.
  - It avoids proliferating ad hoc reference shapes for cached roots.
  - It creates a clean path toward an official URI-based artifact reference format later.

### D58. Distinct semantic versus concrete artifact address kinds
- Status: accepted
- Question: Should the spec distinguish between two address kinds going forward: a semantic artifact identifier for “what artifact is needed” and a concrete cached-instance reference for “which published instance to bind,” rather than overloading one identifier to mean both?
- Recommended answer: Yes. Keep those as distinct but related address kinds.
- Decision: Accepted.
- Why this matters:
  - It keeps planning-oriented addressing separate from concrete instance rebinding.
  - It gives saved-task replay and direct cache IO an explicit instance-identity surface.
  - It makes a future URI design cleaner by avoiding one overloaded identifier with mixed semantics.

### D59. Default-versus-explicit use of semantic and concrete references
- Status: accepted
- Question: In the public contract, should plain semantic artifact IDs remain the default addressing form for normal planning and outputs, with concrete cached-instance references used only when a caller intentionally wants to bind a specific published root instance?
- Recommended answer: Yes. Semantic IDs should stay the default; concrete instance references should be an explicit pinning mechanism.
- Decision: Accepted.
- Why this matters:
  - It keeps ordinary run requests simple.
  - It preserves planner flexibility for normal semantic requests.
  - It gives saved-task replay and upload-first workflows a precise opt-in pinning mechanism without changing default request ergonomics.
