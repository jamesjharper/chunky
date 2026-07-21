# AGENTS.md

1. When implementing, or contributing to specs, first read `specs/SPECS_RULE.md`

2. This repository is a work-in-progress codebase.

Refactoring policy:
- Do not preserve backwards compatibility unless the current task explicitly requires it.
- Remove legacy code paths, compatibility wrappers, and transitional APIs as you refactor.
- Prefer simplifying ownership and data flow over keeping old extension points alive.
- When replacing an abstraction, delete the superseded implementation in the same change when practical.

Implementation bias:
- Treat this codebase as pre-stable.
- Prefer direct, coherent designs over incremental compatibility layers.
- Fail fast on conflicting configuration instead of silently merging legacy behavior.
