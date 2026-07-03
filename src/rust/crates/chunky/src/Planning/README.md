# Planning

This directory contains the request-graph and execution-planning layers used by
the Rust crate.

The implementation depends on the surrounding crate for artifact, fingerprint,
stage, scheduling, and execution-graph types, especially:

```text
artifact_models
artifact_binding_models
fingerprints
stage_models
stage_manifest
```

Key planner behaviors:

- request-scoped defaults and user settings are loaded before graph expansion;
- effective settings precedence is `defaults < user < inherited`;
- inherited override settings are matched by `ArtifactKey::without_override()`;
- supplied artifacts are preferred before producer-path planning;
- optional artifacts use speculative branches and are omitted when no valid path exists;
- ambiguous producer candidates are resolved through speculative branch expansion;
- invocation sharing is keyed by stage id, inherited settings, and effective settings fingerprint;
- explicitly requested phased outputs require exact phase matching;
- the request graph is finalized before execution-graph or cache decisions.
