# Stage Terminology

This document consolidates the domain terminology defined in the `specs/04-stages` folder. It is a reference glossary for the stage model, stage registration, planning, and runtime execution specifications.

## Core Terms

### Stage
A deterministic unit of pipeline behavior that reads already-bound semantic artifacts and writes already-bound semantic artifacts.

### Stage Model
The semantic model that separates bootstrap-time registration, request-specific planning, and runtime stage execution.

### Stage Executor
The runtime implementation that performs one resolved stage invocation through `execute(inputs, outputs, context)`.

### Stage Registration
The complete bootstrap-time contract for one stage implementation. It includes identity, versions, capabilities, planning hooks, resource policy resolution, and the executor factory.

### Stage Manifest Builder
The mutable bootstrap-time builder that collects features, artifact declarations, stage registrations, provider bindings, and defaults before freezing the manifest.

### Stage Manifest
The validated and frozen result of bootstrap. It is the immutable planning registry used for graph creation.

### Planned Stage Invocation
A request-specific planned use of one registered stage, including bound inputs, requested outputs, resource policy, and expected fingerprints.

## Identity and Registration Terms

### Stage ID
The stable semantic identifier for a stage registration, used for producer selection, provenance, scheduling, progress, diagnostics, and explicit producer preferences.

### Display Name
A human-readable stage name used in diagnostics and progress reporting. It is not part of semantic identity.

### Registration Version
Version metadata for the planning and manifest-visible contract of a stage registration. It changes when registration semantics change, even if produced bytes do not.

### Behavior Version
Version metadata for execution behavior that can change semantic outputs for the same inputs and effective settings.

### Executor Factory
The bootstrap-time binding that creates or returns an invocation-safe executor instance for a planned invocation.

### Owned Artifact Declaration
An artifact declaration authoritatively installed by a stage registration or feature package.

### Input Capability
A static declaration that a stage may consume a referenced artifact in some valid invocation.

### Output Capability
A static declaration that a stage may produce a referenced artifact in some valid invocation.

### Static Capability Envelope
The broad set of input or output artifacts a registration may support before request-specific planning narrows them.

### Concurrency Policy
Registration metadata that describes concurrency limits, affinity, or safety rules relevant to stage selection or scheduling.

### Producer Uniqueness
The requirement that an exact output artifact have at most one registered producer in a frozen manifest unless ambiguity is removed before planning.

## Planning Terms

### Stage Planning Context
The read-only deterministic planning context passed to registration hooks during graph creation.

### Resolved Stage Requirements
The concrete set of artifact requirements computed for one request-specific invocation.

### Requirement Resolution
The planning step that narrows static input capabilities into the concrete required and optional inputs for one invocation.

### Output-Capability Resolution
The planning step that narrows a stage's broad output capability envelope to the outputs it can validly produce for the current request and settings.

### Resource-Policy Resolution
The planning step that determines the scheduler-facing resource requirements for one planned invocation.

### Requested Output Subset
The exact subset of outputs fixed for one planned invocation. The executor receives writers only for that subset.

### Candidate Graph
The static or reused artifact relationship graph the planner uses to discover producer paths from requested outputs.

### Request-Specific Resolution
The full graph-creation process that selects producers, resolves settings, calls planning hooks, binds artifact paths, and creates planned invocations.

### Invocation Sharing
The planner optimization that reuses one invocation across graph paths when all identity-affecting values are equivalent.

### Invocation Duplication
The planner behavior that creates separate invocations when stage identity, versions, settings, constraints, inputs, or requested outputs differ.

### Registry Override
A per-run override that derives a new manifest snapshot without mutating the pipeline's baseline frozen manifest.

## Runtime Terms

### Input Artifacts
The stage-facing collection of readers for the concrete input artifacts bound to one invocation.

### Output Artifacts
The stage-facing collection of writers for the exact output subset bound to one invocation.

### Stage Execution Context
The narrow runtime context passed to stage code, containing correlation identities, cancellation, progress, logging, and optional scratch workspace.

### Scratch Workspace
An optional invocation-owned temporary workspace created and cleaned up by the runner. Its contents are implementation details unless published as declared artifacts.

### Scheduler Invocation Record
The richer internal runtime record used by the scheduler, distinct from the narrower stage-facing execution context.

### Cache Hit
The runtime case where a planned output is already available, allowing stage execution to be skipped.

### Cache Wait
A scheduler-level blocked condition where a task must wait for another task's output writer reservation to commit or abort before resolving outputs again.

### Resource Admission
The scheduler step that waits for the planned invocation's resolved resource policy to be admitted for execution.

### Materialization
The runner-owned act of writing an available semantic artifact to caller-provided targets after the semantic artifact already exists.

### Output Validation
The post-execution runner check that every bound output was written correctly and matches its planned declaration and fingerprint.

## Bootstrap and Manifest Terms

### Pipeline Feature
A coherent bootstrap installation unit that can add artifact declarations, stage registrations, provider bindings, defaults, and dependencies.

### Feature Installer
Bootstrap code that installs one pipeline feature into the stage manifest builder.

### Feature Dependency
A bootstrap-time prerequisite relationship between features or shared declarations.

### Frozen Manifest
The immutable runtime manifest produced after bootstrap validation and manifest identity calculation complete.

### Manifest Identity
The deterministic identity of a frozen manifest, derived from its declarations, stage metadata, feature records, and configured producer choices.

### Serializable Manifest Description
A portable diagnostic representation of the runtime manifest that excludes executable objects, credentials, and process-local references.

### Baseline Manifest
The pipeline-owned frozen stage manifest used as the default input to planning for all runs.

### Derived Manifest
A request-specific frozen manifest snapshot produced from the baseline manifest plus registry overrides.

## Contract and Invariant Terms

### Deterministic Planning Hook
A side-effect-free registration hook whose result depends only on deterministic planning inputs, not cache state, live resources, time, or randomness.

### Executor Lifetime
The scope in which an executor instance is created, reused, or shared safely across invocations.

### Invocation-Safe Executor
An executor instance that is safe to use for the invocation and, when explicitly declared, safe to share under concurrent execution.

### Stage Failure
The execution outcome where a stage reports inability to complete by raising an exception.

### Cancellation
The cooperative stop signal observed by the executor through the stage execution context and handled by scheduler waits outside execution.

### Language-Neutral Contract
The cross-language requirement that all bindings preserve the same semantic separation between feature installation, registration, planning, and execution.

### Recommended Invariant
A normative rule that preserves the correctness of the stage model across bootstrap, planning, scheduling, and execution.
