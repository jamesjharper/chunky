# Spec Refactor Notes

These notes describe the source changes needed to bring the current Python and Rust implementations into line with the reconciled run-contract specs.

## 1. Add concrete cached-instance references

Add a public `CachedArtifactReference` / `CachedArtifactRef` model in the artifact or binding model layer:

```text
CachedArtifactReference
  artifact_id: ArtifactKey
  fingerprint: str
```

Required source updates:

- Python: `src/python/chunky/Artifacts/artifact_models.py` or `src/python/chunky/Bindings/cache_models.py`.
- Rust: `src/rust/crates/chunky/src/Artifacts/artifact_models.rs` or `Bindings/cache_models.rs`.
- Export it from the package/crate public API.
- Ensure validation always checks both `artifact_id` and `fingerprint`; do not treat fingerprints as untyped global IDs.

## 2. Update `RunRequest.inputs` to accept cached roots

`RunRequest.inputs` currently accepts only `ArtifactSourceHandle` values. It now needs to accept either a source handle or a cached-instance reference.

Required source updates:

- Python: `src/python/chunky/pipeline.py`
  - update `RunRequest.inputs` typing;
  - update `_normalize_request` and `_collect_flattened_request_artifacts` so nested request normalization still returns one semantic artifact key per input;
  - reject cached references whose embedded `artifact_id` does not match the normalized input key.
- Rust: mirror the same shape in `src/rust/crates/chunky/src/pipeline.rs` and request model structs.
- Add validation tests for duplicate normalized IDs, unknown IDs, mismatched cached-reference artifact IDs, and malformed cached-reference inputs.

## 3. Add direct artifact-cache IO

Add a public cache IO facade separate from `run(...)` and `run_away(...)`.

Conceptual API:

```text
publish(artifact_id/declaration, source_handle, policy?) -> CachedArtifactReference
open(CachedArtifactReference) -> ArtifactInstance
```

Required source updates:

- Python:
  - add an `ArtifactCacheIO` facade or methods on `ChunkyPipeline`;
  - implement publish/open through `ArtifactBindingsProvider` and `ArtifactCache` rather than exposing cache file paths;
  - export the facade from `chunky.__init__` if it is intended as public API.
- Rust:
  - add equivalent trait/struct APIs around the cache provider;
  - keep backend storage paths private.
- Tests should prove direct writes create entries indistinguishable from run-created cache entries.

## 4. Publish cacheable provided roots by default

Binding a provided source/settings/intermediate root should publish that root into cache by default when the artifact declaration is cacheable.

Required source updates:

- Python: `src/python/chunky/Bindings/bindings.py`
  - extend `bind_supplied(...)` or add `publish_supplied_root(...)`;
  - compute the supplied-artifact fingerprint once and reuse it for both the returned `ArtifactInstance` and cache publication;
  - use cache reservation, prepare, commit, abort, and metadata validation rather than writing directly to backend storage;
  - preserve the caller-supplied source handle as the current run's root source after publication.
- Rust: mirror the same behavior in `Bindings/bindings.rs`.
- Pipeline code must pass per-artifact `cache_policy` into supplied-root binding.
- `do_not_cache=True` must skip provided-root publication but still allow the current run to use the root.
- `desired_ttl` applies only when creating a new root cache entry, not when an entry already exists.

## 5. Add cache open-by-reference behavior

A cached root reference should bind only if the cache already contains a committed entry matching the reference.

Required source updates:

- Extend `ArtifactCache` with an `open` / `resolve_existing` operation, or add a strict mode around `resolve(...)` that returns a hit or a typed missing/invalid error without acquiring a write reservation.
- `open(reference)` must validate metadata and acquire a read reservation before returning a source handle.
- Add tests for valid hit, missing entry, mismatched metadata, corrupt data, and reservation release on planning failure.

## 6. Early-bind cached settings roots

Cached settings roots need to be read before graph planning when they are explicitly supplied by reference.

Required source updates:

- Python: `src/python/chunky/pipeline.py`, `Planning/execution_planner.py`, and `Planning/TaskRequestGraph/request_models.py`.
- Rust: corresponding `pipeline.rs`, `Planning/execution_planner.rs`, and `task_request_graph/request_models.rs`.
- During request binding, detect settings artifacts supplied as `CachedArtifactReference`, open them through the cache, read their value, and populate the same request settings view used for ordinary supplied settings.
- Ensure cache source handles opened for settings are closed if planning fails.

## 7. Make default settings truly lazy

The current planner/pipeline seam still has an eager `resolve_defaults(...)` shape. Replace it with on-demand default resolution for a specific relevant settings artifact.

Required source updates:

- Python:
  - replace `_PlannerDefaultSettingsResolver.resolve_defaults(...)` with `resolve_default_setting(artifact_id)`;
  - update `TaskRunPlanner.plan(...)` so it no longer reads every declaration default before graph construction;
  - update `PlannerSettingsResolver` / `PlannerRequestSettings` so missing defaults are requested only when a stage planning context needs them.
- Rust: apply the same protocol and planner changes.
- Add tests proving an irrelevant default provider is not invoked, and a relevant default is invoked exactly when needed.

## 8. Wire cache policy through all relevant binding paths

`cache_policy` currently reaches stage output binding but not supplied-root binding.

Required source updates:

- Pass normalized `cache_policy` into `_bind_supplied_inputs(...)`.
- Treat unknown policy IDs as validation errors before binding.
- Keep known-but-unreachable policy IDs diagnostic-only after planning.
- Apply `do_not_cache` to both produced artifacts and provided-root publication.
- Keep `ignore_existing_cache` scoped to read/reuse for produced outputs or explicit cache-open behavior where applicable; it should not suppress the caller-supplied source selected for the current run.

## 9. Persisted-task compatibility flags

No persisted-task runtime is required yet, but the source should preserve the future eligibility boundary.

Required source updates:

- Return enough provenance from binding/direct cache IO to know which provided roots were durably cached.
- Mark a saved-task configuration as not restartable when any provided root had `do_not_cache=True` or otherwise failed durable publication.
- Do not persist live run handles, scheduler state, progress state, or cache reservations as resumable job state.
- Validate future restart attempts against planning-visible manifest identity, not runtime backend wiring.

## 10. Tests to add or update

Minimum test coverage needed:

- cached-root reference validation: success, unknown artifact, mismatched artifact ID, missing fingerprint, metadata mismatch;
- provided-root cache publication for source, settings, and supplied intermediate artifacts;
- `do_not_cache` skips provided-root publication and marks future restart ineligible;
- direct cache IO publish/open uses the same fingerprint and metadata as run-time publication;
- cached settings roots are available to settings-dependent planning hooks;
- irrelevant defaults are not resolved;
- planning failure closes supplied and cached-root source handles;
- materialization failure still leaves committed semantic cache entries intact.
