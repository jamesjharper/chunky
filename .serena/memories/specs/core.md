# Specs Core

- Specs are the durable behavior contract and should be checked before changing planner/cache/stage semantics.
- Important spec paths:
  - `specs/00-run-contract-overview.md`: public run contract examples and entrypoint vocabulary (`chunky_run`, `chunky_run_away`).
  - `specs/01-artifacts/artifacts-spec.md`: artifact model semantics.
  - `specs/02-stages/stages-spec.md`: stage capabilities/registration contract.
  - `specs/03-planning/`: graph creation/scheduling/planning notes; contains historical/copy/gpt draft files, so prefer latest named spec files over drafts.
  - `specs/04-scheduling/eventing-and-task-control-model-spec.md`: progress/eventing/task-control behavior.
  - `specs/05-cache-providers/cache-management-spec.md`: cache provider contract.
  - `specs/99-stage-implementations/`: expected stage and concrete stage implementation specs.
- Keep Python and Rust tests aligned with specs when behavior is clarified or changed.