# Conventions

- Architecture vocabulary is shared across Python and Rust: Artifacts, Stages, Planning, Scheduling, CacheProviders, ExampleFeatures/demo/benchmark support.
- Python package directories intentionally use domain-style capitalization in several places (`Artifacts`, `Stages`, `Planning`, `Scheduling`, `CacheProviders`, `ExampleFeatures`); preserve existing import/module casing.
- Python data models heavily use `@dataclass(frozen=True)` for immutable value objects and plain mutable `@dataclass` only for builder/graph-discovery state.
- Python typing style uses modern annotations: `T | None`, `TypeAlias`, `Protocol`, `Generic[T]`, `Mapping`, `Iterable`, `tuple[...]`; keep signatures typed.
- Async boundaries are central: planner/cache/stage execution code uses `async def`; tests use `unittest.IsolatedAsyncioTestCase` rather than pytest fixtures.
- Builder pattern: manifests and registrations validate/freeze, detect duplicates/conflicts, and return typed model objects. Preserve validation errors instead of silently resolving conflicts.
- Cache contract distinguishes hit/write reservation/wait states and treats non-cacheable outputs separately from cacheable outputs.
- Planning contract distinguishes required vs optional inputs, supplied artifacts vs producer paths, default/user/inherited settings, phase matching, and terminal remapping.
- Rust module names mirror the Python domain layout, including capitalized directories such as `Artifacts`, `Planning`, `Scheduling`, `Stages`, `CacheProviders`; preserve current layout unless doing a deliberate cross-language refactor.
- Rust uses `Result`-returning APIs with `anyhow`/`thiserror`, async execution with `tokio`, and serde-derived model structs/enums.