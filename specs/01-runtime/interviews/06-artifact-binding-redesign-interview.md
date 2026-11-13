# Artifact and Binding Redesign Interview — Q1–Q139

> **Historical interview record.** The post-Q139 reconciliation in
> `../../Chunky-artifact-binding-redesign-Q1-Q139.md` and active normative
> surfaces supersede the locator/request conclusions recorded here.

## Status

This document records the complete numbered design interview that led to the 2026-08-13 artifact/binding redesign. It is historical decision context, not the primary normative specification. Where a later answer superseded an earlier one, that is stated explicitly.

Final normative behavior is defined by the artifact, provenance, planning, cache, storage, run, stage, and lifecycle specifications.

## Q1 — Content identity equality

**Question:** Should universal content identity mean exact byte equality?

**Answer:** Yes. Equal content means byte-for-byte identical finite logical bytes. Schema, codec, role and provenance do not participate.

## Q2 — Provenance determinism

**Question:** May one provenance identity resolve to more than one content result?

**Answer:** No. One provenance identity must resolve deterministically to exactly one result. Multiple provenance identities may converge on identical content.

## Q3 — Multi-file artifact shape

**Question:** Should a split recording be represented as one compound content object?

**Answer:** No. The physical files remain independent valid artifacts/content objects; the plural semantic value is represented by an ordered binding.

## Q4 — a-ref semantics

**Question:** Should a-ref represent schema/interpretation rather than storage/content identity?

**Answer:** Yes. a-ref is schema/declaration context. Raw content is independently addressable.

## Q5 — Compound identity

**Question:** Should a plural binding get a synthetic parent/Merkle content identity?

**Answer:** No. This proposal was rejected. The members remain individual content objects and the binding carries ordering.

## Q6 — Universal artifact as sequence

**Question:** Is the universal artifact itself a 1..N sequence object?

**Answer:** No. Universal content identity remains one finite byte sequence. Plurality belongs to bindings.

## Q7 — Cardinality model

**Question:** What cardinality model should bindings use?

**Answer:** Use ONE and ONE_OR_MORE plus an optional flag: required ONE=1, optional ONE=0..1, required ONE_OR_MORE=1..N, optional ONE_OR_MORE=0..N.

## Q8 — What counts as a member

**Question:** May arbitrary storage shards become binding members?

**Answer:** No. Each binding member must itself be an independently valid declaration-compatible representation. Storage multipart/sharding remains below the artifact layer.

## Q9 — Sequence index in a-ref

**Question:** Should sequence/member index be encoded in a-ref?

**Answer:** No. It does not belong in a-ref.

## Q10 — Sequence as binding concept

**Question:** Should ordering/membership exist only in the binding context?

**Answer:** Yes. The same content can appear in different bindings and positions; the sequence is not a new artifact identity.

## Q11 — Digest vs fingerprint terminology

**Question:** Should content and provenance use distinct names?

**Answer:** Yes. Reserve content_digest for exact-byte identity and provenance_fingerprint for derivation/execution identity.

## Q12 — Exact a-ref version

**Question:** Should an exact a-ref identify an immutable declaration/schema version?

**Answer:** Yes. Persisted exact references must not silently move to a newer declaration.

## Q13 — Plural outputs

**Question:** Should stage outputs support ONE_OR_MORE as well as inputs?

**Answer:** Yes. Output bindings use the same cardinality model and may also be used for incremental streaming.

## Q14 — Incremental producer-to-consumer execution

**Question:** Should downstream stages be able to consume committed upstream members before upstream completion?

**Answer:** Yes. Incremental publication is first-class and separate from cardinality.

## Q15 — a-instance identity

**Question:** What should a-instance identify?

**Answer:** Initial discussion leaned toward provenance for produced values and content for supplied values. This was later superseded by Q108: a-instance always becomes exact schema + content digest.

## Q16 — Exact a-ref vs selector

**Question:** Should exact declaration identity be separated from unresolved planning selection?

**Answer:** Yes. Exact a-ref is immutable schema; request/planner selection is a separate concept.

## Q17 — a-task plural resolution

**Question:** Should one a-task identify the whole requested output binding?

**Answer:** Yes. One a-task covers the complete requested output, which may expose zero/one/many members.

## Q18 — a-instance resolver variants

**Question:** Should a-instance explicitly distinguish CONTENT and PROVENANCE resolvers?

**Answer:** Initially yes. Superseded by Q107/Q108 after provenance moved to binding level; final a-instance has content digest only.

## Q19 — Plural member provenance

**Question:** Should every produced member have provenance derived from binding provenance + ordinal?

**Answer:** Initially yes. Superseded by Q107: provenance identifies the whole binding and members do not need public provenance fingerprints.

## Q20 — Incremental policy precedence

**Question:** How should incremental publication preference be resolved?

**Answer:** Stage/output capability constrains support, stage configuration supplies a default, and RunPolicy may request DEFAULT/AT_COMPLETION/INCREMENTAL. Unsupported forced incremental is a compatibility failure.

## Q21 — Live consumer execution model

**Question:** Should plural streaming create one long-lived consumer invocation rather than implicit per-member fan-out?

**Answer:** Yes. The stage receives a live ordered async iterator.

## Q22 — Committed members survive producer failure

**Question:** Do members committed before a later producer failure remain valid?

**Answer:** Yes. Their content remains valid/addressable; only the containing binding is incomplete/failed.

## Q23 — Plural request structure

**Question:** Should request inputs group members structurally under one artifact request?

**Answer:** Yes. Array/list order defines member order; sequence index is not embedded in locators.

## Q24 — Externally supplied provenance

**Question:** Should externally supplied content have deterministic provenance for downstream planning?

**Answer:** Yes conceptually, derived from supplied content; later simplified to one external binding provenance derived from exact schema + ordered content digests.

## Q25 — Content digest encoding contract

**Question:** Should content identity include length plus digest?

**Answer:** No. Final decision is algorithm-qualified cryptographic digest only. Byte length is mandatory metadata but not identity. Initial algorithm BLAKE3-256.

## Q26 — Duplicate members

**Question:** Are duplicates allowed in an ordered binding?

**Answer:** Yes. Bindings are lists, not sets; duplicates and order are semantic.

## Q27 — Publication mode and semantics

**Question:** May AT_COMPLETION vs INCREMENTAL affect semantic planning/provenance?

**Answer:** No. Publication mode is operational. An incompatible selected producer causes an explicit compatibility failure rather than semantic replanning.

## Q28 — a-task incremental results

**Question:** Should a-task expose currently committed members before terminal binding state?

**Answer:** Yes. Task output observation exposes an evolving committed prefix while the binding is producing.

## Q29 — Phase-less final artifact cleanup

**Question:** Should completed/AT_MOST selectors be separated from exact artifact identity?

**Answer:** Yes. This was an intermediate step; later Q77 removed phase from artifact locators entirely.

## Q30 — a-task original selector/request

**Question:** Should a-task preserve the original caller request rather than planner-selected exact output?

**Answer:** Yes. Final form stores the original versionless ArtifactRequest.

## Q31 — Binding provenance fingerprint

**Question:** Should there be a provenance identity for the deterministic ordered binding?

**Answer:** Yes. This became the central execution-cache identity.

## Q32 — Schema-independent durable ingest

**Question:** Should durable upload store bytes without requiring a-ref?

**Answer:** Yes. Upload is bytes -> content digest -> immutable content. Schema is bound later by task/input context.

## Q33 — a-ref fixes declaration codec/version

**Question:** Should exact a-ref identify an immutable declaration including codec/validation interpretation?

**Answer:** Yes. Changing incompatible interpretation requires a new declaration version. Cardinality was later explicitly removed from declaration identity.

## Q34 — Incremental support granularity

**Question:** Should incremental support be per output capability rather than stage-wide?

**Answer:** Yes. A stage may have both streamable and completion-only outputs.

## Q35 — ONE may publish early

**Question:** Can a ONE output publish its final member before the stage completes?

**Answer:** Yes. Incremental publication is independent of cardinality.

## Q36 — Optional outputs

**Question:** Does optionality apply symmetrically to outputs?

**Answer:** Yes. Output cardinality uses the same ONE/ONE_OR_MORE + optional algebra as inputs.

## Q37 — Cardinality belongs to edges

**Question:** Should cardinality live exclusively on stage edges rather than a-ref declarations?

**Answer:** Yes. a-ref describes one artifact schema; cardinality/optionality/publication capability belong to input/output edges.

## Q38 — CompletedBinding record

**Question:** Should a successfully closed binding have an immutable completion record keyed by binding provenance?

**Answer:** Yes. Active/failed production state is separate; only successful closure creates the immutable reusable binding.

## Q39 — Binding-level cache hits

**Question:** Should execution-cache hits be decided at binding provenance level?

**Answer:** Yes. Provenance -> completed ordered binding is the reusable execution cache; content digest -> bytes is physical content.

## Q40 — Failed binding retry behavior

**Question:** Should an incomplete streamed binding be a cache miss on retry for now?

**Answer:** Yes. Existing committed members may deduplicate, but the producer starts normally. Explicit resumability is deferred.

## Q41 — Incremental member finality

**Question:** Must an incrementally published member be irrevocably final?

**Answer:** Strong yes. Incremental means final committed output, never provisional progress.

## Q42 — Live upstream failure propagation

**Question:** Should a consumer see committed prefix then an iterator error on upstream failure?

**Answer:** Yes. Successful closure alone yields EOF. Cancellation must also be observable through the normal StageExecutionContext cancellation mechanism.

## Q43 — a-instance as validated boxing

**Question:** Does a-instance prove the content already passed schema validation?

**Answer:** Initially yes. Superseded by Q55 after clarification: final design says no; a-instance supplies schema context and decoding may fail lazily.

## Q44 — Cancellation-aware live waits

**Question:** Should waiting for the next live binding member wake on context cancellation?

**Answer:** Yes. Blocking artifact operations observe cancellation, while stage code still explicitly checks raise_if_cancelled at work boundaries.

## Q45 — Upstream cancellation vs failure

**Question:** Should upstream cancellation be distinguishable from upstream execution failure?

**Answer:** Yes. Successful close, failure and cancellation are distinct terminal causes; task cancellation takes precedence when active locally.

## Q46 — Downstream cancellation propagation

**Question:** Should cancelling one consuming stage automatically cancel its upstream producer?

**Answer:** No. Scheduler demand ownership decides. Shared upstream work continues while another consumer/request still needs it.

## Q47 — Binding-level cache policy

**Question:** Should cache policy apply atomically to the whole output binding?

**Answer:** Yes. Cache behavior addresses the binding provenance/result, not individual ordinals.

## Q48 — Partial task history

**Question:** Should committed members of a failed binding remain visible through task history but not count as a cache hit?

**Answer:** Yes.

## Q49 — Memoized schema validation

**Question:** Should successful schema validation be durably memoized?

**Answer:** After clarification, the user said this is unnecessary because consumption may discover/report invalid content. Superseded by Q55: no required validation index.

## Q50 — Exact a-ref structural fields

**Question:** Should exact a-ref contain declaration identity only?

**Answer:** Yes in principle. The provisional phase field discussed here was later removed entirely by Q77.

## Q51 — Shared active binding production

**Question:** Can multiple tasks share one actively producing deterministic binding?

**Answer:** Yes. One active producer can serve multiple demanders.

## Q52 — RunPolicy scope

**Question:** Should RunPolicy broaden from scheduler-only to general non-semantic execution policy?

**Answer:** Yes. It may include publication mode while retaining the invariant that it cannot change semantic work/identity.

## Q53 — a-ref grammar with version/phase

**Question:** Should exact a-ref use a readable version/phase grammar?

**Answer:** Version suffix @N was accepted. The phase portion was later removed entirely by Q77.

## Q54 — Representation Store becomes Content Store

**Question:** Should the byte store become the universal Content Store keyed by content digest?

**Answer:** Yes. Cache and durable persistence become higher-level metadata/retention layers over exact content.

## Q55 — a-instance without validation certificate

**Question:** Should a-instance mean content interpreted under a schema without guaranteeing prior validation?

**Answer:** Yes. This supersedes Q43/Q49.

## Q56 — Phase in artifact family path

**Question:** Should concrete phase live in the artifact family path?

**Answer:** Temporarily yes. Superseded by Q77, which removes phase/processing order from locators entirely.

## Q57 — Declaration version syntax

**Question:** Should exact declaration version use @N?

**Answer:** Yes. @version qualifies the whole artifact schema path.

## Q58 — Streaming consumer early outputs

**Question:** May a downstream streaming consumer publish its own final members before upstream closes?

**Answer:** Yes. Those members survive later upstream failure. Normal scheduler/resource gating still applies before the consumer runs.

## Q59 — Shared production ownership

**Question:** Should active shared production belong to the binding claim rather than the first task?

**Answer:** Yes. Tasks hold demand references; the first task is not special.

## Q60 — Content vs cache lifetime

**Question:** Should exact content lifetime be independent of execution-cache TTL?

**Answer:** Yes. Cache eviction releases only cache retention; content remains while any other claim/reference/reservation protects it.

## Q61 — p100 phase syntax

**Question:** Should concrete phases use a self-identifying p100-style suffix consistently across locators?

**Answer:** Initially yes. Superseded by Q77: processing order is removed from all locators.

## Q62 — Uniform input binding reader

**Question:** Should inputs.get_artifact always return an ordered binding reader?

**Answer:** Yes, including ONE inputs.

## Q63 — Streaming consumer readiness

**Question:** Should a consumer become dependency-ready only when it can make observable progress?

**Answer:** Yes: first committed consumable member, or successful empty closure where zero is permitted; resource admission follows afterward.

## Q64 — Conflicting publication modes on shared production

**Question:** How should AT_COMPLETION and INCREMENTAL demand coexist?

**Answer:** Choose model A: shared production can serve both; visibility may be more restrictive per consumer. Later Q122 clarifies a late incremental caller cannot upgrade an already-running completion-only producer.

## Q65 — Plural output writer

**Question:** Should plural outputs use an explicit runtime-owned binding writer with write_next?

**Answer:** Yes. Runtime assigns ordinals.

## Q66 — Cardinality enforcement

**Question:** Should cardinality violations fail the stage?

**Answer:** Yes. write_next should fail immediately for known upper-bound violations; successful-return finalization checks minimum cardinality.

## Q67 — Optional input absence

**Question:** Should absent optional input be an empty binding instead of None?

**Answer:** Yes.

## Q68 — Implicit output close

**Question:** Should output bindings close automatically on successful execute return?

**Answer:** Yes. Explicit executor close is unnecessary; runtime finalizes/validates.

## Q69 — Streaming resource lease lifetime

**Question:** Should scheduler resource leases remain held for the whole long-lived invocation for MVP?

**Answer:** Yes. Dynamic yield/reacquire is deferred.

## Q70 — Separate a-select scheme

**Question:** Should planning selectors get their own a-select syntax?

**Answer:** User worried this overcomplicated the model. This proposal was superseded by Q74 and ultimately Q77/Q81.

## Q71 — a-instance resolver-kind syntax

**Question:** Should a-instance include /content/ or /provenance/ resolver kind?

**Answer:** Unsettled at this point; superseded by Q108: final a-instance is always schema + content digest.

## Q72 — a-task selector-body syntax

**Question:** Should a-task serialize selector semantics?

**Answer:** Superseded. Final a-task serializes the original versionless ArtifactRequest, optionally including explicit producer constraint.

## Q73 — Self-typed content/provenance identifiers

**Question:** Should content/provenance values carry their own portable type/version prefixes?

**Answer:** The direction was refined by HTTP design: content digest is typed by algorithm and provenance by version; final textual payload uses URL-safe Base64 without padding.

## Q74 — Drop a-select and simplify phase semantics

**Question:** Should one artifact path be interpreted differently by typed context rather than introducing a-select?

**Answer:** User remained concerned and observed ordinary stage phase dependencies are always AT_MOST and exact phase creates producer coupling. This led directly to removing phase from locators in Q77.

## Q75 — Planner concrete binding

**Question:** Does the planner still need a concrete resolved binding object?

**Answer:** Yes. Keep an internal PlannedArtifactBinding containing exact schema, provenance, cardinality and selected source/producer.

## Q76 — Separate content/provenance HTTP routes

**Question:** Should raw web retrieval distinguish content and provenance routes?

**Answer:** Initially yes. The content route remains; public provenance route was later dropped for MVP in Q128.

## Q77 — Remove phase from locators

**Question:** Should phase be removed from a-ref, a-instance and a-task entirely?

**Answer:** Yes. This is the final locator model.

## Q78 — Rename phase

**Question:** What should the ordering concept be called?

**Answer:** processing_order.

## Q79 — Producer in provenance

**Question:** Should concrete provenance identify the actual selected producer rather than processing order?

**Answer:** Yes. Record stable producer ID and behavior version; processing_order is selection metadata only.

## Q80 — PlannedArtifactBinding after simplification

**Question:** Should PlannedArtifactBinding remain an internal planner concept?

**Answer:** Yes.

## Q81 — Exact producer debugging request

**Question:** For rare exact/debug requests, should caller select a producer by producer_id instead of phase?

**Answer:** Yes. Producer constraint is separate request metadata.

## Q82 — a-task request orientation

**Question:** Should a-task preserve only caller request, with no declaration version, while local runs use their own handles?

**Answer:** Yes.

## Q83 — Producer ID identity and URL form

**Question:** Should producer_id use stable stage identity and be human-readable/URL-safe?

**Answer:** Yes. Use a stable path-safe producer ID, exposed in request syntax such as transcript/token_stream/producer/punctuation_repair.

## Q84 — Unversioned caller requests

**Question:** Should callers request an artifact family without declaration version and let the runtime resolve exact a-ref?

**Answer:** Yes.

## Q85 — Role of a-task

**Question:** Should a-task be primarily durable/public requested-output identity rather than general local runtime output identity?

**Answer:** Yes.

## Q86 — HTTP task-artifact route

**Question:** Should HTTP use task ID + requested artifact key directly?

**Answer:** Yes, option B: GET /task/{task_id}/artifact/{artifact_request}.

## Q87 — Local output handles

**Question:** Should start_task expose process-local output handles while run_task remains run-to-completion?

**Answer:** Yes. Serialized RunState stays data-only.

## Q88 — processing_order ties

**Question:** May multiple producers share the same processing_order?

**Answer:** Yes. processing_order is ordering only; normal producer-selection resolves ties.

## Q89 — URL-safe producer ID

**Question:** Should producer_id itself be one stable URL-safe path segment?

**Answer:** Yes. It is the tightened/renamed stable stage identity, distinct from display_name.

## Q90 — a-task mirrors route

**Question:** Should canonical a-task structurally mirror task ID + artifact request route?

**Answer:** Yes.

## Q91 — Task binding response

**Question:** Should resolving a task artifact return an ordered binding description rather than one a-instance?

**Answer:** Yes. This is the final task-output observation model.

## Q92 — Plural HTTP downloads

**Question:** Should plural output members be downloaded independently rather than automatic concatenation/archive?

**Answer:** Yes.

## Q93 — Local/durable binding model alignment

**Question:** Should local RunHandle and data-only RunResult expose the same logical output-binding model with different capabilities?

**Answer:** Yes.

## Q94 — Task artifact lookup HTTP method

**Question:** Should task-artifact binding lookup use GET rather than POST?

**Answer:** Yes; POST was a slip.

## Q95 — Binding vs member status

**Question:** Should status live on the binding rather than each committed member?

**Answer:** Yes in direction, then refined by Q103: members are committed prefix; binding status carries production/completion/failure.

## Q96 — Failure list for missing members

**Question:** Can the runtime report individual failed/unwritten members?

**Answer:** No. It knows declared cardinality class but not future N for ONE_OR_MORE. It reports committed members plus binding-level failure/incompleteness.

## Q97 — a-instance raw-download role

**Question:** Should a-instance remain schema-contextual but stop being required for raw download?

**Answer:** Yes.

## Q98 — Digest URL encoding

**Question:** Should digest/fingerprint payloads use compact URL-safe Base64 rather than hex?

**Answer:** Yes. Use unpadded Base64URL; path-length concerns are negligible for 256-bit values.

## Q99 — Produced provenance payload

**Question:** Should produced provenance include selected producer and input bindings, and how should settings participate?

**Answer:** Producer and inputs yes. User noted settings require special planner merging, leading to Q105: keep final effective settings as a distinct provenance contribution.

## Q100 — REFRESH semantics

**Question:** Should OVERWRITE mean recompute-and-verify rather than replace deterministic identity?

**Answer:** Yes, and rename it REFRESH.

## Q101 — Partial output cache hit subset

**Question:** Should a stage run only for missing output writers?

**Answer:** User rejected assuming that is safe because excluding cached sibling A could change B. Refined by Q106/Q113: preserve full resolved output set and use verification writers.

## Q102 — Durable plural input resolvers

**Question:** Should durable plural inputs preserve ordered members and allow provenance indirection?

**Answer:** Yes to ordered bindings, but this exposed that provenance naturally belongs to the whole binding. Later Q131 removes public provenance input from MVP.

## Q103 — Committed-prefix member semantics

**Question:** Should members always mean successfully committed members so far, with completion/failure only on binding?

**Answer:** Yes.

## Q104 — Final digest encoding

**Question:** Should 256-bit digest/provenance values use unpadded Base64URL?

**Answer:** Yes.

## Q105 — Settings provenance contribution

**Question:** Should provenance retain one separate fingerprint of final effective settings?

**Answer:** Yes. Settings remain distinct because defaults/overrides are planner-resolved before complete requirements may be known.

## Q106 — Resolved output set in provenance

**Question:** Should the resolved output set contribute to production provenance and remain unchanged by partial cache hits?

**Answer:** Yes.

## Q107 — Binding-level provenance only

**Question:** Should provenance identify the entire ordered binding and eliminate per-member provenance?

**Answer:** Yes. This supersedes Q19 and greatly simplifies the model.

## Q108 — Final a-instance identity

**Question:** If provenance is binding-level, should a-instance always be exact a-ref + content digest?

**Answer:** Yes. This is the final a-instance model.

## Q109 — Durable input provenance shorthand

**Question:** Should a supplied binding be either explicit ordered contents or one binding provenance resolver?

**Answer:** At this point yes conceptually. Later Q131 removes provenance-based caller inputs from MVP; internal provenance resolution remains possible in future.

## Q110 — Completed-only provenance index

**Question:** Should persisted provenance index contain only successfully completed bindings?

**Answer:** Yes. Active/failed prefixes belong to task/execution state.

## Q111 — External binding provenance persistence

**Question:** Should explicit digest bindings get deterministic external provenance but not necessarily a persisted record?

**Answer:** Yes. Upload creates no provenance; task/planning derives it as needed, and persistence is optional.

## Q112 — Schema compatibility on provenance reuse

**Question:** If provenance is used as an input resolver, must its recorded schema match rather than silently reinterpret bytes?

**Answer:** Yes, option B. Cross-schema reinterpretation should use explicit content digests. Public provenance input is later deferred by Q131.

## Q113 — Verification writers for partial hits

**Question:** Should cached sibling outputs remain in the executor as verification writers on a partial cache miss?

**Answer:** Yes.

## Q114 — Cache behavior vocabulary

**Question:** Should cache modes be INHERITED/CACHE/NO_CACHE/REFRESH/READ_ONLY?

**Answer:** Yes. This replaces OVERWRITE/DRY_RUN terminology.

## Q115 — Universal identity vs access scope

**Question:** Does universal digest identity still allow service/authorization-scoped resolution?

**Answer:** Yes. Identity is global; presence/retrieval is service scoped. Authorization/tenant policy is future/non-normative for MVP.

## Q116 — Canonical a-instance syntax

**Question:** Should a-instance compactly embed exact schema + content algorithm/digest?

**Answer:** Yes, e.g. a-instance:source/audio@1/content/blake3/{base64url}.

## Q117 — Ephemeral external provenance

**Question:** Should externally derived provenance remain ephemeral unless explicitly persisted?

**Answer:** Yes. A provenance value can exist for downstream planning without a lookup record.

## Q118 — Completed provenance record shape

**Question:** Should a persisted completed provenance record contain exact schema + ordered content digests?

**Answer:** Yes.

## Q119 — Canonical provenance hash protocol

**Question:** Should provenance use domain-separated/versioned BLAKE3-256 over a canonical structured payload?

**Answer:** Yes. Do not depend on arbitrary language JSON serialization.

## Q120 — Upload result identity

**Question:** Should durable upload return content_digest instead of fingerprint?

**Answer:** Yes. Upload publishes content, not semantic artifact identity.

## Q121 — No complete boolean

**Question:** Can task-artifact binding status express completeness without a separate boolean?

**Answer:** Yes. AVAILABLE is the successful complete state; other states do not assert completeness.

## Q122 — Late incremental demander

**Question:** If active shared production started AT_COMPLETION, can a later INCREMENTAL caller upgrade it?

**Answer:** No. It joins the existing production and waits for completion. More restrictive visibility is always possible.

## Q123 — Expose provenance in task response

**Question:** Should task-artifact response expose binding provenance now?

**Answer:** No for MVP. It can be added later.

## Q124 — Distinct producer-constrained outputs

**Question:** Are same artifact requests with different explicit producer IDs distinct requested outputs?

**Answer:** Yes. The unconstrained request is also distinct.

## Q125 — One output capability per stage/schema

**Question:** May one stage expose the same artifact schema as multiple output capabilities?

**Answer:** No. At most one per stage/schema so producer_id + artifact is unambiguous.

## Q126 — Grace handoff timing

**Question:** Should upload grace remain until content is actually consumed later in execution?

**Answer:** User challenged this because a short grace cannot cover a long task. The delayed-consumption proposal was rejected and refined through Q129/Q134.

## Q127 — Accept missing content digests

**Question:** Should durable task acceptance allow absent supplied digests and hope cache/streaming resolves them later?

**Answer:** No for MVP. This would require client/task-ingress streaming/backpressure semantics and is deferred as v2.

## Q128 — Public provenance endpoint

**Question:** Should /artifact/provenance/... exist in MVP?

**Answer:** No. Drop it until a concrete public use case exists.

## Q129 — Grace to task protection

**Question:** Should task acceptance hand off grace to active task protection?

**Answer:** Yes in substance. The original mechanism is preserved: acceptance atomically establishes task protection and deletes the upload-grace claim, rather than treating grace as execution lifetime.

## Q130 — Missing supplied content submission failure

**Question:** Should missing explicit content reject durable submission?

**Answer:** Yes. No task is created.

## Q131 — Public provenance inputs

**Question:** Should provenance-based caller inputs be removed from MVP?

**Answer:** Yes. Durable inputs are explicit ordered content digests; provenance remains internal cache/planning identity.

## Q132 — Minimal task member representation

**Question:** Should task-artifact members be a simple ordered digest list?

**Answer:** Yes, but keep explicit ordinal as useful redundant/canonical metadata.

## Q133 — Defer client-to-task streaming

**Question:** Should streaming new logical input members into an already-running task be explicitly deferred?

**Answer:** Yes. MVP only supports stage-to-stage incremental streaming; task request inputs are finite and closed at acceptance.

## Q134 — Exact grace handoff mechanism

**Question:** What keeps accepted input content alive after the grace claim is removed?

**Answer:** Ordinary content retention TTL remains, and the active/recoverable task independently pins/protects its supplied content. Acceptance records that protection and deletes grace atomically.

## Q135 — Input retention after terminalization

**Question:** Should task-specific input protection end when the task terminalizes?

**Answer:** Yes. Inputs then survive only through ordinary retention or other independent references/claims/reservations; output artifact retention does not automatically pin inputs.

## Q136 — Output binding status vs task status

**Question:** Should requested-output binding status be independent of overall task status?

**Answer:** Yes. A binding that closed successfully remains AVAILABLE even if another branch later makes the task fail.

## Q137 — Final a-ref grammar

**Question:** Should exact a-ref be simply a-ref:{domain}/{content_kind}@{version}?

**Answer:** Yes.

## Q138 — Final caller ArtifactRequest grammar

**Question:** Should caller requests be versionless {domain}/{content_kind} with optional /producer/{producer_id}?

**Answer:** Yes. This is a distinct ArtifactRequest type, not an a-ref.

## Q139 — Input protection through STOPPING

**Question:** When active TTL expires, should task input protection remain until actual terminalization rather than disappearing at the deadline?

**Answer:** Yes. Expiry triggers STOPPING; protection remains through cleanup and is released only when the task terminalizes.
