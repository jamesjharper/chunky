# Runtime External Design Principles

## Purpose

These principles govern the runtime's externally observable surfaces. Detailed
surface specifications remain authoritative for exact types and behavior.

## Principles

### 1. One execution model

Local and durable execution are different ways to supply, retain, and observe a
run, not different meanings of a run. After mode-specific binding, both paths
must use the same planning and execution semantics.

The runtime is the configured collection of services that provides those
semantics. A run is one execution requested from that runtime; it is not a
second kind of runtime or a separately configured execution service.

### 2. Durable processing is data-only

Every external model participating in durable processing MUST be data-only.
Durable requests, states, results, progress, receipts, policies, and failures
must contain no live handles, callbacks, clients, credentials, or runtime
objects.

Process-local capabilities belong only in explicitly local models. Crossing
into durable processing requires an explicit conversion.

### 3. Semantic intent, delivery, and operation are separate

A request describes desired work. Delivery targets describe where local results
go. Cache and run policies describe execution and retention.

Delivery attachments must not enter durable semantic requests. Operational
policy must not change semantic work or graph shape. Equal requests do not imply
the same task identity.

### 4. Ownership and effects are explicit

Operations that consume requests or external handles must define ownership and
cleanup for success, failure, and cancellation. They must not silently retain,
duplicate, detach, or discard caller resources.

Durable preparation is an explicit asynchronous operation. It must reject local
delivery targets rather than dropping them.

### 5. Validate at the earliest informed boundary

- Builders perform pure structural validation without I/O.
- Durable preparation establishes eligibility before reading a source.
- Planning validates requirements that depend on the frozen manifest.
- Active runtime settings are validated before execution begins.

Pure validation reports all independently detectable issues. Effectful work
fails fast unless a more specific contract requires aggregation. Misleading
cascade errors are omitted.

### 6. Reject ambiguity

Conflicts, duplicates, unsupported values, and lossy conversions must fail
instead of using last-write-wins behavior, permissive merging, silent fallback,
or implicit conversion.

Convenience APIs must not hide semantic changes, publication, data loss, or a
durability transition.

### 7. Portable contracts are strict and versioned

Durable requests, run states, results, and failures use strict, canonical,
versioned representations with stable identities, timestamps, and observable
ordering.

Bindings may expose idiomatic types without changing canonical data, variants,
invariants, or failure information. Evolution requires an explicitly supported
schema version.

### 8. Observable behavior is deterministic

Serialization, issue aggregation, and public collection ordering must not
depend on scheduler completion, hash iteration, process timing, or other
internal nondeterminism.

Declared or canonical identity order governs public collections. Causes precede
consequences where applicable.

### 9. Artifact identity and publication are honest

The surface distinguishes universal `ContentId`, versionless
`ArtifactRequest`, hierarchy-qualified `ScopedArtifactRequest`, exact internal
`ArtifactSchemaId`, task-scoped output bindings, and committed members. Public
requests and task results remain semantic/content-oriented; exact schema,
producer, provenance, and cache coordination remain internal.
A member must not be exposed as readable before its content commit succeeds,
and an output binding must not be reported complete before successful binding
closure.

Selected caller inputs are authoritative. Unused inputs remain lazy. Supplying
an input must not implicitly publish it.

### 10. Observation and lifecycle are coherent

Polling and awaiting, and local and durable observation where applicable, use
the same run-state and result families. Progress is a coherent snapshot, not an
event log.

Completion, failure, cancellation, expiry, and recovery must lead to one
well-defined terminal result. Internal retries, attempts, scheduling, and
dead-letter routing remain private unless needed to interpret public state.

### 11. Public service APIs are flat

Public service APIs SHOULD remain relatively flat. Callers reach operations
through a small number of top-level service objects rather than nested service
hierarchies.

This keeps the surface clear and simplifies bindings across DLL and
shared-object boundaries. Bindings may provide idiomatic facades without
changing the underlying service contracts.

### 12. All external APIs share one failure surface

All external APIs MUST communicate errors through the consistent envelope
defined by the [Failure Contract](./07-failure-surface.md). They must not invent
parallel error shapes or discard structured failure information across
bindings.

Modules define only their allowed failure kinds, issue codes, typed contexts,
ordering, and redaction rules. Failures must remain data-only, deterministic,
serializable, safe, and actionable.

### 13. Public modules are deep

Public APIs expose only the concepts callers need to construct, submit, and
observe work. Binding, provider coordination, planning convergence, scheduling,
and cleanup stay behind those surfaces.

Features may package common configuration, but composition remains explicit and
exclusive ownership conflicts fail clearly.

### 14. Missing detail is not a contract

Placeholders, interview proposals, internal mechanisms, and open questions do
not create external guarantees. New guarantees require an explicit
specification decision.
