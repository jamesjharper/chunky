# Spec Refactor Notes

These notes track source alignment after reconciling the run-contract specs and applying the cached-root/direct-cache-IO refactor.

## Completed in this refactor

### Cached artifact references

- Added `CachedArtifactReference` as the concrete cached-instance reference shape:

```text
CachedArtifactReference
  artifact_id: ArtifactKey
  fingerprint: str
```

- Python: added the model in `src/python/chunky/Artifacts/artifact_models.py` and exported it from `chunky.__init__`.
- Rust: added the equivalent model in `src/rust/crates/chunky/src/Artifacts/artifact_models.rs` and re-exported it through the existing artifact module surface.
- Validation treats the fingerprint as scoped by the artifact ID, not as an untyped global identifier.

### Cached roots in `RunRequest.inputs`

- Python: updated `RunRequest.inputs` to accept either an `ArtifactSourceHandle` or `CachedArtifactReference`.
- Python: request normalization now rejects cached references whose embedded `artifact_id` does not match the normalized request key.
- Python: cached references are opened during supplied-root binding and become normal supplied `ArtifactInstance` roots before planning.
- Rust: added `ProvidedArtifactRoot::{Source, Cached}` and updated pipeline binding to handle both source roots and cached roots.

### Direct artifact-cache IO

- Python: added `ArtifactCacheIO` as `pipeline.cache_io` with:

```text
publish(artifact_id, source_handle) -> CachedArtifactReference
open(CachedArtifactReference) -> ArtifactInstance
```

- Python: direct cache IO uses `ArtifactBindingsProvider` and `ArtifactCache` rather than exposing backend paths or object keys.
- Rust: added an equivalent `ArtifactCacheIO` facade on `ChunkyPipeline`.

### Supplied-root cache publication

- Python: `ArtifactBindingsProvider.bind_supplied(...)` now publishes cacheable supplied source/settings/intermediate roots by default.
- Python: supplied-root publication computes the supplied-artifact fingerprint once and reuses it for both the returned instance and the cache entry.
- Python: publication goes through cache `resolve`, writer reservation, `prepare`, `commit`, and `abort` rather than writing backend files directly.
- Python: the current run preserves the caller-provided source handle as the selected graph root after publication.
- Rust: mirrored the same behavior in `ArtifactBindingsProvider::bind_supplied_any_with_cache_policy(...)` and `publish_supplied_root(...)`.

### Cache policy wiring

- Python: `_bind_supplied_inputs(...)` now receives normalized `cache_policy`.
- Python/Rust: `do_not_cache=True` skips supplied-root publication but still lets the current run use the supplied root.
- Existing produced-output `cache_policy` behavior remains in the runner/stage-output binding path.

### Cache open-by-reference

- Python: added `ArtifactCache.open(...)` to the cache protocol.
- Python: `FileCacheProvider.open(...)` validates metadata, acquires a read reservation, and returns a cache hit without creating a write reservation on miss.
- Python: explicit cached-root references bind only to committed cache entries.
- Rust: added an `ArtifactCache::open(...)` trait seam. The Rust default implementation is functional but should still be tightened to avoid acquiring a transient write reservation on a miss.

### Tests and validation

- Added Python tests covering:
  - supplied cacheable root publication by default;
  - `do_not_cache` skipping supplied-root publication;
  - cached-reference open by exact `artifact_id + fingerprint`;
  - mismatched cached-reference artifact IDs failing validation.
- Existing Python planner/cache tests still pass.

## Remaining implementation work

### Make default settings truly lazy

The Python and Rust planner seams still retain an eager `resolve_defaults(...)` method. To fully match the spec, replace this with an on-demand method such as:

```text
resolve_default_setting(artifact_id) -> ArtifactInstance | None
```

Required follow-up changes:

- Update `TaskRunPlanner.plan(...)` so it does not resolve every declared default before graph construction.
- Update `PlannerRequestSettings` and `PlannerSettingsResolver` so a stage planning context requests only settings relevant to that stage/candidate path.
- Add tests proving irrelevant default providers are not invoked and relevant defaults are invoked exactly when first needed.

### Tighten Rust cache open-by-reference

The Rust `ArtifactCache::open(...)` seam exists, but the default implementation is intentionally conservative and still delegates to `resolve(...)`. The filesystem provider should get a strict `open_existing` metadata path matching the Python implementation:

```text
lookup + metadata validation + read reservation
or wait/missing error
```

It should not acquire and abort a write reservation when an explicit cached reference is missing.

### Persisted-task eligibility metadata

No persisted-task runtime was added. Future saved-task support still needs a small provenance record indicating whether every provided root was durably cached. A saved task should be marked non-restartable when any provided root uses `do_not_cache=True` or otherwise lacks a durable cached root.

### More coverage still needed

Add tests for:

- direct `ArtifactCacheIO.publish/open` equivalence with run-time supplied-root publication;
- cached settings roots used by settings-dependent planning hooks;
- missing cached reference;
- metadata mismatch for cached reference;
- corrupt cache data during cached-reference read;
- planning failure closes opened cached-root source handles;
- materialization failure leaves committed semantic cache entries intact.
