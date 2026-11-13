# Handover: Continue the Run Request Building Interview

> Consolidation status (2026-07-21): confirmed results through decision 90 are
> captured in the authoritative specifications. The instructions below apply
> when resuming the remaining open interview and must not be used to undo that
> consolidation.

Continue interviewing the user about the unresolved branches recorded in
`03-run-request-building-interview.md` until shared understanding is explicitly
confirmed.

## Non-negotiable interaction rules

- Read `AGENTS.md` and `specs/SPECS_RULE.md` first.
- Read `03-run-request-building-interview.md` in full; it is the authoritative
  record of this interview's confirmed, revised, superseded, deferred, and open
  decisions.
- Inspect `03-run-request-building.md`, `backup.md`, neighboring specifications,
  and `src/python/chunky/Runtime` as facts require.
- Prefer codebase-memory MCP graph tools for code discovery.
- Ask exactly one decision question at a time and wait for the user's answer.
- Give a recommended answer with every question.
- Look up discoverable facts instead of asking the user.
- Record each confirmed decision in the interview record immediately.
- Do not edit authoritative specifications or implementation until the user
  explicitly confirms shared understanding.
- Keep the work focused on the request-building boundary; route dependent
  contracts to their owning specs instead of fully designing every module here.

## Where the design currently landed

The interview record contains 90 numbered decisions. Central conclusions:

- `LocalRunRequest` is the process-local request and may own source/target
  handles.
- `DurableRunRequest` is only for durable/replayable submission and contains
  committed input locators plus durable semantic output requests.
- Local requests do not pass through durable requests; both bind into a private
  shared execution shape.
- `OutputDeliveryPlan` is not a specified concept.
- There are separate standalone, manifest-independent builders. Builders do no
  I/O and perform structural validation only.
- Durable request preparation is an explicit, consuming, cancellable,
  progress-reporting service capability in a runtime subdocument. It rejects local output
  targets and uploads every supplied input.
- `runtime.run` and `runtime.start` consume local requests. A distinct durable
  task service submits durable requests and returns a serializable receipt.
- Every runtime has a cache provider; the universal default is a
  runtime-scoped ephemeral workspace provider.
- Live source inputs are not implicitly published. Cacheable produced outputs
  commit before target materialization.
- Cache policy uses `DEFAULT`, `REFRESH`, `READ_ONLY`, and `BYPASS`, plus an
  optional positive-whole-seconds TTL hint.
- Persisted tasks restart planning/execution from scratch. Committed cache
  artifacts, not persisted progress or plans, carry reusable work.
- Cache metadata stores an untyped TTL-bound mapping:
  `(task_id, task_a_ref) -> (resolved_canonical_a_ref, fingerprint)`.
- `a-task` uses the stable requested reference, which may contain `any`; the
  mapped instance reference uses the fully resolved final-graph identity.
- Task-instance relations are non-pinning and only written for committed readable
  entries. Same-task entries may survive attempts; stale nonterminal relations
  may be replaced after replanning.
- Client/user isolation is intentionally deferred.

## Correction immediately before handover

Do not describe a cache provider as "supporting canonical tasks." Canonical task
semantics belong to the durable task service. The cache/storage layer merely
offers storage, lifecycle, and transfer behaviors used by higher layers.

The previous question about explicit provider capability advertisement was not
answered and should not be recorded as a decision.

## Next unresolved branch: cache ingestion and transfer

The user identified two important transfer cases:

1. An existing resource can be adopted or relocated into cache-owned storage,
   such as atomically renaming an existing file into a known cache directory.
2. Bytes must be streamed between storage systems, such as uploading a local
   file to S3 or downloading an S3 object into a local workspace.

Encoding complicates both cases. Zero-copy relocation is safe only when the
existing bytes already use the artifact declaration's required codec and carry
compatible metadata. Otherwise the system may need to decode/materialize and
encode, which may not be streamable.

The likely ownership split is:

- Artifact I/O defines source capabilities and encoded-resource transfer
  semantics.
- Cache Management defines adoption/copy reservations, atomic commit, rollback,
  cancellation, progress, and cleanup.
- Durable preparation invokes that cache-ingest abstraction but does not define its
  mechanics.

## Recommended next question

Ask only this first:

> Should Artifact/Cache I/O distinguish an optional **encoded-resource transfer**
> path from the ordinary typed-value path?
>
> Recommended answer: yes. A source handle may optionally expose an already
> encoded resource plus codec/metadata identity. When compatible, the cache may
> adopt/relocate it locally or stream-copy it remotely without decoding. When
> absent or incompatible, the ordinary codec-based typed-value path remains the
> fallback. The semantic result and validation rules stay identical.

Then wait. Subsequent questions should separately decide:

- whether relocation may consume/delete the source or must default to copying;
- same-filesystem atomic rename versus cross-device fallback;
- which layer selects adoption versus streaming;
- metadata/digest verification before commit;
- cancellation and rollback after partial transfer;
- whether download/materialization uses the symmetric abstraction;
- progress granularity;
- whether transfer optimization is required or optional;
- how the default workspace provider and remote providers advertise only transfer
  mechanics without acquiring task-level semantics.

## Remaining interview areas after transfer

Return to `03-run-request-building.md` and audit every retained field and builder
method after the document is narrowed. Likely remaining decisions include:

- exact canonical JSON schema after removing `compatibility`;
- exact standalone builder method names and immutable data shapes;
- structural versus runtime/planner error taxonomy for local requests;
- durable-preparation preflight error shape;
- request equality/hash semantics for process-local handles;
- cache-policy application to resolved phase selectors;
- schema-version evolution boundaries;
- final cross-spec routing audit;
- an explicit shared-understanding checkpoint before any authoritative edits.
