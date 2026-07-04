# Python Core

- Root package: `src/python/chunky` with tests in `src/python/chunky_tests`.
- Main domain modules:
  - `Artifacts/`: artifact keys/declarations/instances/handles/bindings/fingerprints/phase catalog.
  - `Stages/`: stage models and manifest/builder/registration logic.
  - `Planning/`: execution planner, manifest graph, task execution graph, task request graph expansion and producer resolution.
  - `Scheduling/`: progress models, task control, task runner.
  - `CacheProviders/`: cache models plus `file_cache_provider/` implementation using filesystem/metadata/maintenance/provider modules.
  - `ExampleFeatures/`: concrete example stage implementations used by demos/tests/spec alignment.
  - `pipeline.py`: public run/pipeline orchestration entry logic.
- Test modules mirror important contracts: `test_execution_cache_contract.py`, `test_planner_evaluation.py`, `test_planner_refactor.py`.
- Tests are stdlib `unittest`, often async via `IsolatedAsyncioTestCase`; run with `PYTHONPATH=src/python` rather than relying on installation.
- Python demo/bench modules live outside the root package at `src/python/chunky_demo` and `src/python/chunky_bench`.