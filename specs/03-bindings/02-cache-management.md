# Cache Management

## Purpose

The execution cache coordinates and reuses deterministic **artifact bindings**.
It is distinct from the Content Store and from durable artifact persistence.

The cache answers:

> Has this exact semantic production already completed successfully, and if not,
> who is allowed to produce it now?

## Cache identity

A cache entry is keyed by `ProvenanceFingerprint`.

```text
CacheEntryKey = ProvenanceFingerprint
```

A committed value is:

```text
CompletedBinding
  provenance_fingerprint
  artifact_schema_id
  scoped_request
  ordered content_ids
  created_at
  operational metadata...
```

The member list is semantic and ordered. Duplicates are preserved.

A cache key never includes task ID, attempt ID, file path, storage path, or
processing order as a proxy for producer identity. Its provenance payload does
include the actual selected producer behavior, effective hierarchical bindings,
exact schemas, and resolved output set. Therefore two scoped invocations remain
distinct when their effective bindings differ even if their output bytes are
equal.

Artifact cache policy targets one complete canonical `ScopedArtifactRequest`.
Policy resolution is independent of input shadowing. `REFRESH` requests
recomputation/verification and never means a hierarchy override or successive
same-family replacement.

## Completed-binding invariant

Only a binding that closed successfully may be published as a cache hit.

An active incremental production may already have committed content members,
but until the binding closes successfully there is no `CompletedBinding(P)`.
If the producer fails or is cancelled, committed members remain valid content
but the binding is a cache miss on a later identical request.

Explicit resumable execution may be added later; it is not part of the MVP.

## Resolution outcomes

Conceptually:

```text
CacheHit
  completed_binding

CacheProductionReservation
  provenance_fingerprint
  publication/verification plan

CacheWait
  provenance_fingerprint
  active_production
```

Normal resolution is atomic:

```text
1. find CompletedBinding(P)
2. if valid and policy permits reuse:
     reserve referenced content as needed
     return CacheHit
3. else if active production for P exists:
     return CacheWait / attach as a demander
4. else:
     acquire exclusive active-production claim for P
     return CacheProductionReservation
```

There may be at most one active producer for a provenance fingerprint.

## Reservation and wait contract

Resolution, active-production claim acquisition and waiter attachment are one
atomic coordination boundary. A caller must never observe both production
ownership and a competing owner for the same provenance fingerprint.

A production reservation is an exclusive, leased right to attempt that binding
production. It is not a successful cache entry and does not make incrementally
committed members reusable. The owner either:

- publishes one matching `CompletedBinding` after successful closure;
- releases/aborts the claim after failure or cancellation; or
- loses a stale lease that recovery safely replaces.

A cache wait is cancellation-aware and rechecks authoritative state when woken.
Wakeup may mean completed hit, released/failed producer, invalidated record or
lease recovery; it is not itself proof of success. Waiters retry resolution and
at most one of them acquires replacement production ownership.

Hit validation and acquisition of all referenced content reservations are
atomic from the consumer's point of view. If any member cannot be protected,
the result is not a partial hit.

## Shared active production

Active production belongs logically to the binding-production claim, not to the
first task that requested it.

```text
ActiveBindingProduction(P)
  demanders: task/invocation references
  production_mode
```

Several tasks may share one producer. Removing one demander does not cancel the
producer while another demander still requires it.

When no demander remains, the scheduler may cancel the producer according to
normal task-control rules.

A late incremental demander cannot upgrade an already-running completion-only
production. It shares the result and waits for completion. A completion-only
consumer may attach to an incremental producer and simply withhold visibility
until closure.

## Cache policy

The public behavior vocabulary is:

```text
enum ArtifactCacheBehavior
  INHERITED
  CACHE
  NO_CACHE
  REFRESH
  READ_ONLY
```

### INHERITED

Delegates to the next applicable policy layer:

```text
artifact policy -> task policy -> stage/declaration default
```

### CACHE

- reuse a valid `CompletedBinding` when present;
- otherwise produce and publish one after successful closure.

### NO_CACHE

- do not use a shared completed-binding hit;
- do not publish a new reusable completed-binding record.

The runtime may still use the Content Store for durable/output publication and
exact-byte deduplication.

### REFRESH

- ignore a completed binding for execution selection;
- recompute the same deterministic production;
- verify/repair against the existing immutable provenance result;
- publish the result when no valid completed record exists.

`REFRESH` never grants permission for one provenance fingerprint to map to a
different valid binding. A mismatch is nondeterminism/integrity failure.

### READ_ONLY

- reuse a valid completed binding when present;
- on miss, execute as required by the run but do not publish a reusable cache
  binding for that production.

## Partial output hits

A stage's resolved output set is part of its semantic production context.
Therefore cache optimization must not alter the output set presented to the
executor.

If an invocation resolves outputs `[A, B]`, with A hit and B miss:

- the executor still receives writers for `[A, B]`;
- A is bound as a verification output against its existing completed binding;
- B is bound as a normal publishing output; and
- any mismatch while recomputing A is a nondeterminism/integrity failure.

The runtime must not silently invoke the stage for `[B]` only unless that was the
original resolved output set and therefore has its own provenance.

## Publication

Members commit independently to the Content Store as the stage writes them.
Cache publication for provenance happens only when the whole binding closes
successfully.

Conceptually:

```text
produce P
  -> commit C0
  -> commit C1
  -> ...
  -> successful binding close
  -> atomically publish CompletedBinding(P, [C0,C1,...])
```

If a matching valid `CompletedBinding(P)` already exists during refresh or
recovery, the newly recomputed ordered member list must exactly match it.

## Read protection

A cache hit must acquire whatever read/reference reservations are required to
keep each referenced `ContentId` readable for the consuming invocation.
Reservation acquisition is atomic with hit validation from the caller's point
of view.

Closing the cache-bound source/binding releases those reservations when no
remaining consumer requires them.

## Cache retention

Cache TTL applies to `CompletedBinding` execution-reuse records. It does not
directly delete content bytes and never shortens an independent durable claim or
active reader.

When a completed-binding record is evicted, its cache retention/reference to
member content is released. Content becomes GC-eligible only when no other
retention reason remains.

Successful cache use or commit may refresh the resolved cache TTL according to
provider policy.

## Corruption and repair

A completed binding is not a hit when:

- its record is malformed;
- its exact artifact schema is incompatible with the expected plan;
- a referenced content object is missing or corrupt; or
- the provenance record conflicts with immutable metadata.

Providers may quarantine invalid records. `REFRESH` may regenerate missing or
corrupt exact content when deterministic provenance permits it.

A valid provenance record is never silently rebound to different content.

## Failure and recovery

Stage failure or cancellation releases the active-production claim and wakes
waiters. Any members already committed to the Content Store remain valid, but
no completed-binding record is created.

If preparation/publication fails before the completed record becomes visible,
the record is not a hit, provisional metadata is removed or quarantined, and
waiters retry resolution. If the completed record committed before a later
local materialization or optional cache-maintenance action failed, the cache
record remains valid.

Recovery reconciles:

- stale active-production leases and abandoned wait registrations;
- completed records whose referenced content is missing/corrupt;
- provisional records left by interrupted publication; and
- cache-retention references no longer backed by a live record.

Recovery is idempotent and concurrency-safe. It may restore the exact bytes for
an immutable valid binding, quarantine it, or remove the invalid cache record;
it never changes the ordered result assigned to a provenance fingerprint.

## Cancellation

Waiting for another active producer is cancellation-aware. Cancelling one wait
or one consumer never implicitly cancels another task's shared production.

Stage/task control owns producer cancellation based on surviving demand.

## Diagnostics

Operational records may retain producing task/invocation, timestamps, waiters,
quarantine reason, provider generation and other diagnostics. Those fields do
not change provenance identity.

Diagnostics should be sufficient to explain whether a request reused, waited,
produced, refreshed, verified, declined publication, lost a lease or rejected a
corrupt/nondeterministic result. Useful events include:

```text
cache_hit
cache_miss
cache_wait_started / cache_wait_finished
cache_production_claimed / cache_production_released
cache_binding_published / cache_binding_evicted
cache_verification_succeeded / cache_verification_failed
cache_record_quarantined / cache_recovery_repaired
```

Events identify the provenance fingerprint and may include task/invocation and
provider diagnostics, but never introduce a second cache identity.

## Invariants

1. Cache entries are keyed by binding provenance, not `ContentId`.
2. Only successfully closed bindings are cache hits.
3. One provenance fingerprint has at most one active producer.
4. One provenance fingerprint maps to at most one valid completed binding.
5. Incrementally committed members survive failed production but do not make a
   cache hit.
6. Cache policy applies to whole bindings, not individual ordinals.
7. Partial cache hits never silently change the executor's resolved output set.
8. Cache eviction does not define physical content lifetime.
9. Wait wakeup always rechecks authoritative cache state.
10. Failed publication cannot leave a visible partial completed-binding record.
