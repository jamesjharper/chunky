# Task Completion

- For Rust changes, run from repo root: `cargo check` and `cargo test`.
- For Python changes, run from repo root: `PYTHONPATH=src/python python3 -m py_compile $(find src/python -name '*.py')` and `PYTHONPATH=src/python python3 -m unittest discover -s src/python/chunky_tests`.
- For cross-language behavior changes, run both Python and Rust suites because implementations mirror the same contracts.
- If specs under `specs/` changed, inspect affected Python/Rust tests and add or update canonical/contract cases where behavior changes.
- Onboarding verification observed: `cargo check`, `cargo test`, Python compile check, and Python unittest discovery all pass with the commands above.
- No repo-configured formatter/linter/type checker exists; do not claim lint/format completion unless a tool is added or run explicitly.