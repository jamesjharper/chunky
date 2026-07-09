# Pipeline Spec Decision Log

Source spec: `specs/04-pipeline/pipeline-spec.md`

Status: in discovery

## Reframed Baseline

- Builder model: features are expected to be self-contained installers that emit fully constructed instances of whatever they contribute.
- Common case: most features contribute artifact declarations and stage registrations only.
- Specialist case: a minority of features may install core runtime services such as the cache provider or other pipeline services.
- Simplicity rule: there is currently no provision for implicit dependency auto-installation; required features must be installed explicitly by the caller.
- Dependency model: features do not declare explicit dependencies on other features. The real failure modes are missing artifact declarations and missing producer paths.

## Decisions

### D1. Configuration installation model
- Status: accepted
- Question: Should runtime collaborators such as cache, fingerprinting, planner, runner, and bindings-provider configuration be modeled primarily as installable `PipelineFeature`s, or should direct builder/constructor injection remain a first-class peer path?
- Recommended answer: Make installable `PipelineFeature`s the canonical model, and keep direct injection only as a narrow escape hatch for tests, bootstrapping internals, or transitional compatibility.
- Decision: Accepted.
- Why this matters:
  - It determines whether the builder stays a thin composition surface or grows bespoke knobs.
  - It affects feature dependency handling and cross-language parity.
  - It constrains how much configuration becomes part of manifest-adjacent diagnostics versus opaque runtime wiring.
- Notes:
  - Current Python code primarily uses direct constructor/builder injection for runtime collaborators.
  - The spec currently says both "primary installation mechanism" and "may be installed through features or supplied directly", so this needs a sharper decision.
  - Reframed understanding: features are self-contained and emit fully constructed contributions rather than contributing fragments to be assembled later by a DI container.

### D2. Runtime collaborator identity boundary
- Status: accepted
- Question: When a feature installs runtime collaborators such as the cache, fingerprint provider, planner factory, runner factory, or bindings-provider factory, which of those choices should contribute to manifest identity versus remaining external configured pipeline state?
- Recommended answer: Only semantic planning-visible configuration should contribute to manifest identity. Storage/runtime mechanics such as cache backend choice and runner implementation should remain outside manifest identity unless they can change planned graph shape, artifact identity, or semantic outputs.
- Decision: Accepted.
- Why this matters:
  - It defines what a manifest ID proves.
  - It constrains cache key safety across pipeline configurations.
  - It separates semantic reproducibility from operational deployment details.
- Notes:
  - `specs/05-stages/99-terminology.md` defines manifest identity as derived from declarations, stage metadata, feature records, and configured producer choices.
  - `specs/04-pipeline/pipeline-spec.md` currently says runtime collaborators are configured pipeline state even when not part of the stage manifest itself.

### D3. Default settings binding strategy
- Status: accepted
- Question: Should the pipeline bind all declared default settings eagerly before planning, or should defaults be resolved lazily only for settings that become relevant to the request-specific plan?
- Recommended answer: Resolve defaults lazily for only the settings that become relevant during planning, while preserving the semantic rule that resolved defaults behave exactly like supplied graph roots.
- Decision: Accepted with note that this may conflict with the current design/implementation.
- Why this matters:
  - It affects startup cost and side effects from default-source providers.
  - It determines whether irrelevant settings can influence planning or diagnostics.
  - It shapes the planner/pipeline boundary for settings discovery.
- Notes:
  - Current Python code eagerly binds every declared default setting in `_bind_default_settings(...)`.
  - The code comment already hints this may be only a focused example and that a fuller planner might resolve only relevant defaults.

### D4. Lazy default resolution ownership
- Status: accepted
- Question: If default settings are resolved lazily, should the planner own relevance discovery and request defaults on demand, or should the pipeline perform a preplanning relevance pass and bind defaults before calling the planner proper?
- Recommended answer: Let the planner own relevance discovery and request defaults on demand; keep the pipeline responsible only for the execution lifecycle and binding mechanics once a setting has been deemed relevant.
- Decision: Accepted.
- Why this matters:
  - It defines a clean planner/pipeline boundary.
  - It determines whether planning stays self-contained and deterministic.
  - It affects whether planning may require a two-phase handshake with the pipeline.
- Notes:
  - The spec currently places default binding in the pipeline execution lifecycle.
  - A lazy model is simplest if the planner can ask for settings only when a branch of the graph actually needs them.

### D5. Default-source determinism boundary
- Status: accepted
- Question: May a settings default-source provider depend only on the supplied override and artifact declaration, or may it also inspect broader request/planning context to compute defaults?
- Recommended answer: Restrict default-source providers to deterministic inputs local to the setting declaration and optional supplied override; do not let them inspect broader request/planning context.
- Decision: Accepted.
- Why this matters:
  - It preserves deterministic planning and explainable fingerprints.
  - It keeps default resolution composable and cache-safe.
  - It prevents hidden cross-setting or request-shape couplings from leaking into planning.
- Notes:
  - The current Python seam `resolve_source(source, declaration=...)` already points toward a narrow, local contract.
  - Allowing broader planning context would effectively create hidden planning hooks inside settings resolution.

### D6. Planner default-resolution interface
- Status: accepted
- Question: In the lazy model, should the planner receive unresolved setting declarations plus a resolver callback/interface it can invoke on demand, or should the pipeline still hand the planner a partially bound settings view abstraction?
- Recommended answer: Give the planner an explicit resolver interface/callback for on-demand settings resolution rather than a partially bound hidden view.
- Decision: Accepted.
- Why this matters:
  - It makes the lazy contract explicit instead of smuggling planning dependencies through prebound state.
  - It keeps the planner testable and language-neutral.
  - It provides a clear seam for diagnostics about why a default was requested and resolved.
- Notes:
  - Current Python construction hands the planner fully bound supplied/default artifact instances.
  - A lazy design will need a new interface boundary somewhere between planning and binding.

### D7. Cache policy ownership
- Status: accepted
- Question: Should `cache_policy` remain a caller-supplied per-run field on `RunRequest`, or should cache behavior stay in configured pipeline/runtime policy except for narrowly scoped control surfaces?
- Recommended answer: Keep `cache_policy` as an explicit per-run `RunRequest` field, but constrain it to semantic cache-use intent rather than backend-specific operational knobs.
- Decision: Accepted, with added clarification that `cache_policy` is per-artifact policy.
- Why this matters:
  - It decides whether callers can request behaviors like bypassing reuse or forcing refresh for a single run.
  - It affects API parity across languages and testing ergonomics.
  - It defines whether cache behavior is part of the public run contract or an implementation detail.
- Notes:
  - `specs/04-pipeline/pipeline-spec.md` includes `cache_policy` in the current `RunRequest` shape.
  - The current Python `RunRequest` implementation in [`src/python/chunky/pipeline.py`](/Users/harperj/Desktop/projects/personal/chunky/src/python/chunky/pipeline.py:34) does not currently expose `cache_policy`.
  - User clarification: `cache_policy` may indicate that a specific artifact should not be cached, that an existing cached value should be ignored for the run, or that a desired TTL should be assigned to that artifact in the cache.
  - User clarification: if an artifact is already cached with a prior TTL, the original TTL persists rather than being overwritten by a later request.

### D8. Cache policy scope
- Status: accepted
- Question: May `cache_policy` target any semantic artifact in the request/plan, including intermediates, or should callers be limited to specifying policy only for caller-visible roots and requested outputs?
- Recommended answer: Allow `cache_policy` to target any semantic artifact by `ArtifactKey`, including intermediates, because the caller may need to influence reuse/materialization behavior for non-terminal artifacts that are operationally important but not directly requested as outputs.
- Decision: Accepted.
- Why this matters:
  - It determines whether cache control is expressive enough for debugging and benchmarking.
  - It affects whether planner/runner internals stay controllable without inventing extra API surfaces.
  - It constrains validation rules for unknown or unreachable artifact IDs.
- Notes:
  - The user’s wording refers to “a specific artifact”, which suggests artifact-key granularity rather than only whole-run or output-only policy.

### D9. Cache policy validation strictness
- Status: accepted
- Question: If `cache_policy` names an artifact that is unknown to the manifest or known but unreachable in the resolved request-specific plan, should the run fail validation or should that entry be ignored with diagnostics?
- Recommended answer: Fail for unknown artifact IDs, but ignore unreachable-yet-known artifacts with a surfaced diagnostic.
- Decision: Accepted.
- Why this matters:
  - It distinguishes caller typos from harmless over-specification.
  - It keeps the contract strict where it catches real mistakes.
  - It avoids making callers perfectly predict planner reachability just to express optional cache intent.
- Notes:
  - Allowing policy over any semantic artifact implies some policies may not apply on a given run if the plan never touches that artifact.

### D10. Cache policy directive precedence
- Status: accepted
- Question: For one artifact in one run, what precedence should apply among directives like "ignore existing cache", "do not write this artifact to cache", and "desired TTL", especially when they interact with already-cached entries whose original TTL must persist?
- Recommended answer: Treat these directives as orthogonal with deterministic effects:
  - `ignore existing cache` affects read/reuse only for the current run;
  - `do_not_cache` affects write/publish-to-cache only for the current run;
  - `desired_ttl` applies only when creating a new cache entry, and never mutates an existing entry's TTL.
- Decision: Accepted.
- Why this matters:
  - It avoids ambiguous combined policy semantics.
  - It gives the runner/cache layer a clear contract for read path versus write path.
  - It preserves the user's stated invariant that existing TTL values are not overwritten.
- Notes:
  - This recommendation assumes these controls are not mutually exclusive but instead operate on separate phases of cache interaction.

### D11. Cache policy API shape
- Status: accepted
- Question: Should `RunRequest.cache_policy` be a single mapping from `ArtifactKey` to a structured per-artifact policy object, or should these controls be exposed as separate parallel collections/flags?
- Recommended answer: Use a single mapping from `ArtifactKey` to a structured per-artifact cache policy object.
- Decision: Accepted.
- Why this matters:
  - It keeps per-artifact policy merge/validation localized.
  - It avoids mismatched parallel structures for ignore/write/TTL controls.
  - It scales cleanly if more cache directives are added later.
- Notes:
  - The decisions so far already imply artifact-key granularity and orthogonal directives, which fit naturally into one structured value per artifact.

### D12. Cache policy addressing shape
- Status: accepted
- Question: Should `cache_policy` use the same nested string-mapping normalization model as `inputs` and `outputs`, or should it require explicit `ArtifactKey` identifiers only?
- Recommended answer: Support the same normalization model as `inputs` and `outputs`, so callers may use either direct `ArtifactKey`s or nested string mappings.
- Decision: Accepted.
- Why this matters:
  - It keeps the request contract internally consistent.
  - It reduces friction when callers want to apply policy alongside similarly-addressed inputs/outputs.
  - It avoids introducing an unnecessary special case in request validation.
- Notes:
  - The pipeline spec already defines deterministic flattening rules for request artifact addressing.

### D13. Cache policy targeting precision
- Status: accepted
- Question: Should `cache_policy` target only exact concrete artifact IDs, or should the contract allow broader wildcard/prefix/family targeting such as an entire domain or artifact family?
- Recommended answer: Restrict `cache_policy` to exact concrete artifact IDs only.
- Decision: Accepted.
- Why this matters:
  - It keeps validation deterministic and unambiguous.
  - It avoids hidden expansion rules that depend on manifest shape or future stage additions.
  - It preserves a clean separation between semantic artifact identity and convenience targeting syntax that can be added later if truly needed.
- Notes:
  - The existing request normalization model is already based on concrete artifact addresses, not pattern expansion.

### D14. `run_away(...)` durability scope
- Status: accepted
- Question: Is `run_away(...)` intentionally only an in-process detached execution surface, with no durability or resumability across process boundaries?
- Recommended answer: Yes. Keep `run_away(...)` strictly in-process and non-durable in this contract.
- Decision: Accepted, with clarification that recovery after restart may occur naturally through cache reuse when the same job is started again, but not through persisted run-handle/job state.
- Why this matters:
  - It defines what the returned run handle can and cannot promise.
  - It avoids accidentally implying a job system or persistent orchestration layer.
  - It aligns with `specs/00-run-contract-overview.md`, which lists durable cross-process background jobs as a non-goal for the first version.
- Notes:
  - The current Python implementation uses `asyncio.create_task(...)` and returns an in-memory handle, which is already process-local.
  - User clarification: after a restart, re-running the exact same job is expected to continue naturally to the extent cache reuse permits, but the pipeline does not persist task state or progress itself.

### D15. Restart recovery contract strength
- Status: accepted
- Question: Should "same job after restart continues via cache reuse" be an explicit contract guarantee for semantically identical reruns, or merely an expected consequence of normal cache behavior without a special resumability promise?
- Recommended answer: Make it an expected consequence of normal cache behavior, not a separate resumability guarantee.
- Decision: Accepted.
- Why this matters:
  - It keeps the contract honest about what is actually persisted.
  - It avoids overpromising exact continuation semantics when partial writes, TTL, or `cache_policy` may change the observed behavior.
  - It still preserves the important expectation that deterministic cache reuse can skip completed work on rerun.
- Notes:
  - This draws a clean line between "resume from persisted execution state" and "recompute only missing uncached artifacts".

### D16. Per-run manifest override exposure
- Status: accepted
- Question: Should the first public `RunRequest` contract expose an explicit per-run manifest override/registry override field, or should manifest overrides remain out of scope for now and be introduced later only when fully designed?
- Recommended answer: Keep per-run manifest overrides out of the first public `RunRequest` contract and introduce them later once the derived-manifest model is fully specified.
- Decision: Accepted. User additionally indicated this likely should never become a feature.
- Why this matters:
  - It determines whether the current run contract stays narrow and stable.
  - It affects manifest identity, cache safety, and planner construction.
  - It avoids baking in an override API before producer-choice and manifest-derivation semantics are nailed down.
- Notes:
  - `specs/05-stages/99-terminology.md` defines `Registry Override` and `Derived Manifest`, but `specs/04-pipeline/pipeline-spec.md` only treats this as a future invariant.

### D17. Builder post-build behavior
- Status: accepted
- Question: After `build()` is called, should the builder become single-use and reject any further `install(...)` or `build()` calls, or should repeated `build()` calls be allowed to produce equivalent configured pipelines?
- Recommended answer: Make the builder single-use and hard-fail on any further mutation or `build()` calls after the first successful build.
- Decision: Accepted.
- Why this matters:
  - It makes the bootstrap/freeze boundary crisp.
  - It prevents accidental divergence between caller expectations and actual manifest state.
  - It keeps the builder contract simple across languages.
- Notes:
  - The spec already says the builder must not be used after `build()` has emitted the frozen manifest, but it does not yet say whether that is a soft convention or a hard API rule.

### D18. Duplicate feature installation semantics
- Status: accepted
- Question: If the same feature is installed twice, should that always be a hard error, or should duplicate installation be allowed when the feature is semantically idempotent?
- Recommended answer: Treat duplicate installation as a hard error by default.
- Decision: Accepted.
- Why this matters:
  - It keeps bootstrap deterministic and easy to reason about.
  - It avoids hidden order-sensitive merges between features that appear identical.
  - It forces explicit composition decisions instead of relying on silent de-duplication.
- Notes:
  - If idempotence is truly needed later, it can be designed explicitly rather than inferred from object equality or type identity.

### D19. Feature dependency installation behavior
- Status: accepted
- Question: When composition leaves the manifest or plan missing required semantic pieces, should the system auto-install anything implicitly or should it fail explicitly?
- Recommended answer: Fail explicitly; do not auto-install anything implicitly.
- Decision: Accepted, with reframing that features do not have explicit feature-to-feature dependencies.
- Why this matters:
  - It keeps the feature model simple and non-magical.
  - It locates failures in the actual semantic surfaces: manifest validation and planning.
  - It avoids inventing a separate feature dependency system.
- Notes:
  - User clarification: features cannot have explicit dependencies on other features.
  - User clarification: the expected failures are missing `ArtifactDeclaration`s or missing producer paths for required artifacts.

### D21. Feature contribution style
- Status: accepted
- Question: Are features compositional installers that contribute fully constructed instances of their own registrations/services, rather than declarative fragments to be assembled by a general DI container?
- Recommended answer: Yes.
- Decision: Accepted.
- Why this matters:
  - It keeps the builder thin.
  - It avoids evolving toward a reflective service container.
  - It clarifies that most features are package-like installers rather than abstract config records.
- Notes:
  - User clarification: typically a feature just emits stage registrations and artifact declarations.
  - User clarification: a small number of specialist features may install cache providers or other core services.

### D22. Core service slot conflicts
- Status: accepted
- Question: If two specialist features both install the same core service slot, such as the cache provider or planner factory, should that be a hard error or should later installation override earlier installation?
- Recommended answer: Hard error.
- Decision: Accepted.
- Why this matters:
  - It prevents builder-order-dependent semantics.
  - It keeps self-contained features understandable in isolation.
  - It avoids hidden replacement behavior for critical runtime services.
- Notes:
  - This follows naturally from the self-contained feature model and the preference for explicit, simple bootstrap rules.

### D23. Builder configuration path
- Status: accepted
- Question: Should the builder expose dedicated top-level setters for core runtime services, or should even those specialist configurations still be installed only through features?
- Recommended answer: Keep a single configuration path and require specialist runtime configuration to come through features as well.
- Decision: Accepted.
- Why this matters:
  - It determines whether the builder stays conceptually uniform.
  - It affects whether there are two competing bootstrap idioms.
  - It constrains how much special-case API surface the builder accumulates.
- Notes:
  - This question is stronger after the builder reframing because specialist service installation is now clearly a minority case.

### D24. Output request eligibility
- Status: accepted
- Question: May callers request materialization targets for any semantic artifact, including intermediates, or are `outputs` limited to artifacts that the manifest treats as caller-facing final outputs/diagnostics?
- Recommended answer: Allow callers to request any semantic artifact as an output target, provided the artifact declaration is materializable to the requested target type.
- Decision: Accepted.
- Why this matters:
  - It determines whether the public run surface is strictly "final outputs" or a more general artifact materialization API.
  - It affects debugging, inspection, and benchmarking workflows.
  - It clarifies whether "intermediate" is a planning concern only or also a caller-visible retrieval surface.
- Notes:
  - The run-contract overview says callers request one or more outputs, but the artifact model treats intermediate results and diagnostics as artifacts too.
  - This interacts with cache policy only indirectly, but it sharpens the overall public artifact contract.

### D25. Output target fan-out
- Status: accepted
- Question: Should one requested artifact be allowed to materialize to multiple caller targets in the same run, or should each requested artifact map to exactly one target handle?
- Recommended answer: Allow one artifact to materialize to multiple caller targets in the same run.
- Decision: Accepted.
- Why this matters:
  - It determines whether materialization is modeled as a reusable post-artifact fan-out step.
  - It affects API convenience for common cases like writing the same artifact to file plus in-memory capture.
  - It clarifies whether output request uniqueness is about artifact production or about target handles.
- Notes:
  - The artifact already exists semantically before materialization, so fan-out can be treated as multiple target writes from one available artifact rather than multiple requested outputs in planning.

### D26. Output fan-out request shape
- Status: accepted
- Question: If one artifact may materialize to multiple targets, should `RunRequest.outputs` represent that as a list of target handles under one artifact key, or as another keyed mapping of named targets under one artifact key?
- Recommended answer: Use a list of target handles under one artifact key.
- Decision: Accepted.
- Why this matters:
  - It determines whether target fan-out is ordered collection semantics or named-subtarget semantics.
  - It keeps artifact identity separate from caller-local labeling concerns.
  - It avoids inventing a second layer of names that do not correspond to semantic artifact identity.
- Notes:
  - The current nested mapping form is used to normalize artifact addresses, not to describe multiple targets for one already-resolved artifact.

### D27. Multi-target materialization failure semantics
- Status: accepted
- Question: If one target materialization fails for an artifact that is being written to multiple caller targets, should the run fail as a whole even if some target writes succeeded, or should partial materialization success be allowed and reported explicitly?
- Recommended answer: Fail the run as a whole, while reporting which targets succeeded and which failed.
- Decision: Accepted.
- Why this matters:
  - It defines whether the run contract treats requested outputs as an all-or-nothing obligation.
  - It affects cleanup, retry, and caller trust in `run(...)` success.
  - It separates artifact production success from caller-target materialization success.
- Notes:
  - The semantic artifact may already exist successfully even if one or more target writes fail.
  - This question is specifically about overall run status, not whether successfully written targets should be rolled back.

### D28. Materialization rollback behavior
- Status: accepted
- Question: If a run fails during multi-target materialization after some targets were already written successfully, should the runner attempt best-effort rollback/cleanup of the successful target writes, or should those successful writes remain in place and just be reported?
- Recommended answer: Leave successful target writes in place and report them; do not promise rollback.
- Decision: Accepted.
- Why this matters:
  - It defines whether target handles must support compensating delete semantics.
  - It avoids pretending output materialization is transactional when many targets will not be.
  - It shapes caller expectations after a failed run with partial visible side effects.
- Notes:
  - This is about caller target side effects only, not cache publication semantics.

### D29. Cancellation during materialization
- Status: accepted
- Question: If cancellation arrives after a semantic artifact is already available but before all requested caller targets have been materialized, should the runner stop materializing remaining targets immediately, or should it finish the in-progress artifact's target fan-out before honoring cancellation?
- Recommended answer: Honor cancellation promptly and stop remaining target writes as soon as practical.
- Decision: Accepted.
- Why this matters:
  - It defines whether cancellation is primarily about compute avoidance or about all visible side effects.
  - It affects how much extra work a caller may still observe after cancellation.
  - It needs to align with the non-transactional target semantics already chosen.
- Notes:
  - This question is about post-production materialization, not stage execution or cache writes.

### D30. `run_policy` scope
- Status: accepted
- Question: Should `run_policy` be limited to planning/producer-selection behavior, or may it also suppress otherwise-requested outputs or alter caller-visible materialization obligations?
- Recommended answer: Limit `run_policy` to planning and execution strategy only; it must not suppress requested outputs or weaken materialization obligations.
- Decision: Accepted.
- Why this matters:
  - It keeps the request contract easy to reason about: requested outputs stay authoritative.
  - It separates "what the caller wants" from "how the system should try to achieve it."
  - It prevents policy flags from creating surprising partial-success semantics.
- Notes:
  - The current spec describes `run_policy` as an optional planning-time policy input.

### D31. Request validation error aggregation
- Status: accepted
- Question: When request normalization/validation finds structural problems across `inputs`, `outputs`, or `cache_policy`, should the pipeline fail fast on the first error or accumulate all structural validation errors and report them together?
- Recommended answer: Accumulate structural validation errors and report them together.
- Decision: Accepted.
- Why this matters:
  - It improves caller ergonomics for complex requests.
  - It keeps request-shape validation distinct from later planning/runtime failures.
  - It reduces fix-one-error-per-run iteration when several malformed entries are present.
- Notes:
  - This question is about deterministic request-shape validation only, not arbitrary downstream planning failures.

### D32. Multi-target materialization ordering
- Status: accepted
- Question: If an artifact is materialized to a list of multiple targets, is that list order semantically meaningful and therefore preserved, or are target writes unordered and free to run in parallel?
- Recommended answer: Treat list order as semantically meaningful and preserve it.
- Decision: Accepted.
- Why this matters:
  - It determines whether the list is just a bag of targets or an ordered execution request.
  - It affects determinism, diagnostics, and failure/cancellation reporting.
  - It leaves room to add explicit parallel fan-out later without overloading list semantics.
- Notes:
  - Once `outputs` use a list for fan-out, the contract should say whether that ordering is observable.

### D33. Bound supplied-instance cleanup scope
- Status: accepted
- Question: If planning fails after supplied inputs have been bound, should the pipeline close every bound supplied instance it holds, regardless of origin, or only resources that were newly created/owned by the binding process?
- Recommended answer: Close every bound supplied instance the pipeline holds for that run.
- Decision: Accepted.
- Why this matters:
  - It defines ownership expectations for bound instances.
  - It keeps failure cleanup simple and leak-resistant.
  - It avoids subtle provenance tracking in the pipeline just to decide cleanup.
- Notes:
  - The current Python implementation already calls `close_instances(tuple(supplied.values()))` on planning failure.
  - This assumes a bound `ArtifactInstance` is a run-scoped owned object even when produced from caller-supplied handles.

### D34. Pre-plan progress visibility
- Status: accepted
- Question: Should callers observe progress states for pre-planning lifecycle phases such as request normalization, supplied-input binding, and planning itself, or should progress reporting begin only after a concrete invocation plan exists?
- Recommended answer: Expose coarse pre-plan lifecycle progress states as well; do not make progress visibility start only after planning succeeds.
- Decision: Rejected recommendation. Chosen behavior: no pre-plan progress for now.
- Why this matters:
  - It determines whether long planning/binding phases appear as "nothing is happening."
  - It affects how useful `run_away(...)` is for real workloads.
  - It keeps progress reporting aligned with the full execution lifecycle, not just runner execution.
- Notes:
  - The current spec explicitly registers planned invocations only after planning succeeds.
  - This question is about coarse run-level phases, not fake stage registrations before the plan exists.

### D35. Pre-plan failure surfacing
- Status: accepted
- Question: If the run fails during normalization, supplied-input binding, or planning before any invocation progress exists, should the run handle still expose a terminal failed state with phase-specific diagnostics, or should those failures remain exception-only behavior?
- Recommended answer: Expose a terminal failed state with phase-specific diagnostics.
- Decision: Accepted.
- Why this matters:
  - It keeps `run_away(...)` coherent even when progress has not started.
  - It distinguishes "no progress yet" from "no observable failure information."
  - It avoids making pre-plan failures harder to observe than execution failures.
- Notes:
  - This does not require pre-plan progress events; it only requires phase-aware terminal failure reporting.

### D36. Partial outcome reporting on failure
- Status: accepted
- Question: If a run fails after producing some semantic artifacts or materializing some caller targets, should the final result surface those successful partial outcomes explicitly, or should failed results return only error information?
- Recommended answer: Surface successful partial outcomes explicitly alongside the failure.
- Decision: Accepted.
- Why this matters:
  - It determines whether callers can safely reuse/report work completed before failure.
  - It aligns with the chosen non-transactional target materialization semantics.
  - It affects how useful failed `run_away(...)` results are for diagnostics and recovery.
- Notes:
  - This is about result reporting, not about turning failed runs into successful ones.

### D37. `run(...)` failure surface
- Status: accepted
- Question: On failure, should `run(...)` raise an exception, or should it return a typed failed result object analogous to the completion result from `run_away(...)`?
- Recommended answer: Keep `run(...)` exception-raising on failure, while `run_away(...)` continues to resolve to a typed terminal run result.
- Decision: Accepted.
- Why this matters:
  - It determines whether the two run surfaces differ only in lifecycle control or also in failure-shape ergonomics.
  - It affects idiomatic synchronous/awaited calling style.
  - It constrains how partial outcomes and diagnostics are surfaced on the direct await path.
- Notes:
  - The current Python implementation of `run(...)` raises, while `run_away(...)` wraps completion into `RunResult`.

### D38. `run(...)` exception richness
- Status: accepted
- Question: When `run(...)` fails, should the raised exception carry structured diagnostics and partial outcomes analogous to the detached `RunResult`, or should that richer structured failure information remain exclusive to `run_away(...)`?
- Recommended answer: The raised exception from `run(...)` should carry structured diagnostics and partial outcomes.
- Decision: Accepted.
- Why this matters:
  - It keeps the direct-await path from being materially less informative than the detached path.
  - It preserves one underlying failure model even if the surface shape differs.
  - It avoids forcing callers onto `run_away(...)` just to get useful structured failure information.
- Notes:
  - This keeps `run(...)` exception-based while still aligning with D35 and D36 on partial outcome reporting.

### D20. Missing dependency error detail
- Status: accepted
- Question: When composition is invalid, should a missing `ArtifactDeclaration` be surfaced during manifest creation/validation, while a missing producer path for a required artifact is surfaced during planning?
- Recommended answer: Yes.
- Decision: Accepted with stronger clarification: by virtue of the current `StageManifest` object structure, this validation needs to happen during `StageManifest` creation.
- Why this matters:
  - It sets a clean boundary between configuration invalidity and request-specific planning failure.
  - It catches undeclared artifacts as early as possible.
  - It avoids conflating manifest problems with planner reachability problems.
- Notes:
  - This follows from the no-explicit-feature-dependency model.
  - User clarification: the current `StageManifest` structure pushes this validation into manifest creation time.

### D39. Static vs request-dependent producer-path validation
- Status: accepted
- Question: Should producer-path viability be validated as far as possible during `StageManifest` creation, while still leaving request-dependent missing producer-path failures to planning?
- Recommended answer: Yes. Validate static structural impossibilities at manifest creation, but keep request-dependent reachability failures in planning.
- Decision: Accepted.
- Why this matters:
  - It maximizes early detection for globally broken registrations.
  - It preserves planning as the place where request-specific graph reachability is resolved.
  - It avoids forcing manifest creation to solve every possible request upfront.
- Notes:
  - This fits the current `StageManifest`-centric validation boundary.

### D40. `StageManifest` creation as strong validation boundary
- Status: accepted
- Question: Should the pipeline spec explicitly state that `StageManifest` creation is a strong validation boundary that rejects structurally invalid stage/artifact topology before any run can begin?
- Recommended answer: Yes.
- Decision: Accepted.
- Why this matters:
  - It makes the architecture legible.
  - It clarifies where invalid topology is rejected.
  - It aligns the spec with the chosen manifest-centric validation model.
- Notes:
  - This builds directly on D20 and D39.

### D42. Spec vs implementation divergence tracking
- Status: accepted
- Question: Should the spec explicitly call out where it intentionally diverges from the current Python implementation, especially around eager default binding versus intended lazy planner-driven resolution?
- Recommended answer: Yes, but keep that divergence tracking out of the spec itself and in a separate refactor-oriented file.
- Decision: Accepted with the constraint that implementation-gap notes should not be recorded directly in the spec.
- Why this matters:
  - It keeps the spec normative rather than mixing in transition commentary.
  - It still preserves visibility into known implementation gaps.
  - It gives later refactoring a concrete checklist without polluting the design document.
- Notes:
  - User clarification: record implementation divergence in a separate file for later refactoring.

### D43. Refactor file shape
- Status: accepted
- Question: Should the separate refactor file be a simple implementation-gap tracker keyed by decision ID, rather than a second prose design document?
- Recommended answer: Yes.
- Decision: Accepted.
- Why this matters:
  - It keeps one source of truth for design decisions.
  - It makes refactoring work trackable without creating a competing spec.
  - It keeps the migration notes concise and actionable.
- Notes:
  - This refines D42.
