# Artifact Storage Boundaries

## Purpose

Chunky separates exact physical content from the semantic reasons that content
is reusable or retained.

```text
Execution Cache --------+
                        |
                        +----> Content Store
                        |
Durable Persistence ----+
```

The three layers remain logically distinct even when one backend implements all
of them.

## Content Store

The Content Store owns immutable exact bytes addressed by `ContentId`, which is
also the public upload/download address.
It provides transactional ingestion, read reservations, integrity, recovery,
range streaming and physical garbage collection.

It does not understand:

- artifact schemas;
- artifact requests or task-output bindings;
- binding provenance;
- cache policy; or
- durable task claims.

## Execution Cache

The cache answers:

> Has this deterministic binding production already completed successfully?

Its semantic key is `ProvenanceFingerprint` and its reusable value is a
`CompletedBinding`:

```text
P -> exact ArtifactSchemaId + ordered [ContentId, ...]
```

The cache owns hit/miss/refresh/read-only behavior, active producer
coordination, cache retention and completed-binding metadata. It references
content objects but does not define their byte identity.

## Durable persistence

Durable persistence answers:

> Which uploaded contents and requested task-output members are retained and
> observable through this service?

It owns upload-grace retention, accepted-task input protection, requested
output retention, task-artifact binding snapshots and durable read access.

Durability is independent of execution-cache eviction.

## Identity boundaries

```text
Content identity:
    ContentId

Execution reuse identity:
    ProvenanceFingerprint -> CompletedBinding

Resolved interpretation identity:
    ResolvedArtifactInterpretation = ArtifactSchemaId + ContentId

Task requested-output key:
    task_id + original ScopedArtifactRequest -> TaskArtifactBinding
```

These identities must not be collapsed into one key.

## Lifetime example

```text
CompletedBinding P -----+
                        |
Task output member -----+----> ContentId C ----> exact bytes
                        |
Another task input -----+
```

Evicting `P` removes only execution reuse. The bytes remain while another
retention reason protects `C`.

Likewise, task-output expiry may release its protection while a cache result or
another task still references the same content.

## Read/write coordination

### Cache production

```text
1. acquire exclusive active-production claim for provenance P
2. execute stage and commit members into Content Store
3. successfully close binding
4. atomically publish CompletedBinding(P)
5. release active-production claim
```

A failed/incomplete binding never creates a completed cache hit.

### Durable task input

At durable task acceptance, every supplied `ContentId` must be present,
readable, authorized, and claimable. Acceptance atomically records/protects all
members, deletes or converts corresponding upload-grace handoff claims, and
creates the task. Any failed check leaves no task and no partial claims or
unprotected gap. Input protection remains through active, recoverable, and
`STOPPING` states and ends only at terminalization.

### Task output

Each committed requested-output member may become durable immediately. The
binding status separately records whether the whole ordered binding eventually
closed successfully.

Only explicitly requested outputs, including explicitly requested
intermediates, receive the MVP task-output retention guarantee. Incidental
cached intermediates do not become public or retained merely because a cache
record exists. Requested-output retention never implicitly pins supplied inputs
after terminalization.

## Invariants

1. Content identity is exact bytes only.
2. Cache identity is deterministic binding provenance.
3. Durable retention is independent of cache retention.
4. One physical content object may satisfy many semantic/cache/durable records.
5. Deleting one semantic record never deletes bytes still protected elsewhere.
