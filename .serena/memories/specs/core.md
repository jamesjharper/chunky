# Specs Core

- Specs are the durable behavior contract and should be checked before changing planner/cache/stage semantics.
- Important spec paths:
  - `specs/00-overview/01-run-contract-overview.md`: public local/durable run overview.
  - `specs/01-runtime/`: request building, upload, run, retrieval, failure and time-format surfaces.
  - `specs/02-artifacts/`: exact content/schema/request/instance identity and binding I/O.
  - `specs/03-bindings/`: binding coordination, cache, Content Store and durable persistence.
  - `specs/04-fingerprinting/01-fingerprints.md`: content digests and binding provenance.
  - `specs/05-stages/stages-spec.md`: stage capabilities/registration/execution contract.
  - `specs/06-planning/01-task-planning.md`: graph creation, producer selection and provenance planning.
  - `specs/07-scheduling/`: persisted task lifecycle, eventing and task control.
  - `specs/99-stage-implementations/`: expected stage and concrete stage implementation specs.
- Artifact-binding redesign vocabulary: `ContentDigest` identifies exact bytes; `ProvenanceFingerprint -> CompletedBinding` identifies a successfully closed ordered binding; `a-ref` is exact schema; `ArtifactRequest` is versionless caller intent; `a-instance` is exact schema + content digest; `a-task` resolves to a task binding snapshot.
- Cardinality (`ONE`/`ONE_OR_MORE` + optionality) and publication mode belong to stage edges, not artifact declarations. Processing order is producer-selection metadata and never appears in artifact locators.
- Historical interview/refactor notes explain decisions but are not the primary normative contract. Prefer active module specs over `interviews/`, `older-versions/`, copies and redesign interview records.
- Keep Python and Rust tests aligned with specs when behavior is clarified or changed.
