# Rust integration tests

This folder contains Rust integration tests covering the crate's public API:

- `execution_cache_contract.rs` ports `test_execution_cache_contract.py`.
- `planner_evaluation.rs` ports the planner-evaluation scenarios covering optional pruning, supplied intermediates, required-input failure, producer ambiguity/defaults, phase selection, output-subset re-resolution, settings propagation, and manifest cycle validation.
- `planner_refactor.rs` ports the focused refactor tests around distinct inherited audio settings creating distinct `audio.prepare` invocations.
- `fixtures/graph-cases.canonical.yaml` is copied from the Python fixture folder for parity and future canonical-case expansion.

The Rust crate lives under `src/rust`, and these integration tests exercise its
public API directly.
