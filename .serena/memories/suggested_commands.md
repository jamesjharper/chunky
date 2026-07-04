# Suggested Commands

- Rust check from repo root: `cargo check`.
- Rust full test suite from repo root: `cargo test`.
- Python unittest suite from repo root: `PYTHONPATH=src/python python3 -m unittest discover -s src/python/chunky_tests`.
- Python compile check from repo root: `PYTHONPATH=src/python python3 -m py_compile $(find src/python -name '*.py')`.
- Python module entrypoint, if needed: `PYTHONPATH=src/python python3 -m chunky`.
- Rust demo binary, if needed: `cargo run -p chunky_demo`.
- Rust benchmark/demo support binary, if needed: `cargo run -p chunky_bench`.
- Darwin environment observed during onboarding: `python3` at `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`; `.venv/bin/python` is not present by default.
- There is no repo-local Makefile/Justfile/pyproject command alias; prefer explicit commands above.