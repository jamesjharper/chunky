# Rust Core

- Root workspace from repo root includes `src/rust/crates/chunky`, `src/rust/crates/chunky_demo`, `src/rust/crates/chunky_bench`.
- Main library crate path: `src/rust/crates/chunky`; package name is `chunky_example`.
- Library modules mirror Python domains:
  - `Artifacts/`: binding models, bindings, codecs/examples, handles, models, fingerprints, phase catalog.
  - `Stages/`: stage manifest and stage models.
  - `Planning/`: execution planner and manifest graph.
  - `Scheduling/`: progress models, task control, task runner.
  - `CacheProviders/`: cache models.
  - `pipeline.rs`, `example_features.rs`, `demo_support.rs`, `benchmark_support.rs`.
- Rust integration tests live in `src/rust/crates/chunky/tests`: cache contract, planner evaluation, planner refactor, plus `support/`.
- Run root `cargo check`/`cargo test`; no separate command is needed for nested `src/rust/Cargo.toml` unless intentionally scoping to that workspace file.