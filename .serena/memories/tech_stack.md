# Tech Stack

- Languages: Python 3.12-compatible source under `src/python`; Rust 2021 workspace under `src/rust`.
- Rust workspace:
  - `chunky_example`: library crate implementing artifacts, stages, planning, scheduling, cache provider models, demo/benchmark support.
  - `chunky_demo`: binary depending on local `chunky_example`.
  - `chunky_bench`: binary depending on local `chunky_example`.
- Rust notable dependencies: `tokio`, `anyhow`, `thiserror`, `serde`, `serde_json`, `serde_yaml`, `rusqlite` with bundled SQLite, `blake3`, `chrono`, `uuid`, `parking_lot`, `futures`, `tempfile`, `walkdir`, `nix`; tests use `pretty_assertions`.
- Python uses stdlib-heavy modules plus optional `yaml`/PyYAML for canonical graph case loading; tests skip YAML-backed cases if unavailable.
- Python import layout is source-tree based, not installed-package based. Use `PYTHONPATH=src/python` for direct execution/tests unless packaging is later added.
- No configured Python formatter/linter/type-checker was found in repo metadata during onboarding.