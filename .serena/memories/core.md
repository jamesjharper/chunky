# Core

- Repository root: `/Users/harperj/Desktop/projects/personal/chunky`.
- Shape: spec-first prototype with parallel Python and Rust implementations of the same artifact/stage/planning/scheduling/cache concepts.
- Top-level source map:
  - `specs/`: durable design/spec contract source. Start here for behavior intent before changing planner/cache/run semantics. See `mem:specs/core`.
  - `src/python/chunky/`: Python implementation and current Python test target. See `mem:python/core`.
  - `src/rust/crates/chunky/`: Rust library crate named `chunky_example`. See `mem:rust/core`.
  - `src/rust/crates/chunky_demo/`, `src/rust/crates/chunky_bench/`: Rust binary/demo and benchmark consumers of `chunky_example`.
  - `src/python/chunky_demo/`, `src/python/chunky_bench/`: Python demo/benchmark entry modules.
- No README, Makefile, pyproject, pytest.ini, or Python packaging metadata currently exists at repo root; do not assume package-manager commands are configured.
- Cargo workspace exists at both `Cargo.toml` and `src/rust/Cargo.toml`; root workspace includes `src/rust/crates/chunky`, `chunky_demo`, and `chunky_bench`.
- Codebase graph project is indexed as `Users-harperj-Desktop-projects-personal-chunky`; prefer codebase-memory-mcp graph tools for code discovery.
- Read for setup/tooling details: `mem:tech_stack`.
- Read before running/changing checks: `mem:suggested_commands` and `mem:task_completion`.
- Read for style/API patterns: `mem:conventions`.