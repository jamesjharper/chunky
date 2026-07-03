# Chunky Run Contract — Graph Creation and Scheduling

## Purpose

This document defines how a `RunRequest` is resolved into a graph of stage invocations and how that graph is scheduled.

## Task Graph Planning

A run request resolves to one shared dependency DAG.

Planning starts from requested outputs and walks backwards through the registry to supplied inputs and synthesizable settings.

Planning fails before execution if:

- a requested output has no valid producer or supplied input;
- required settings cannot be supplied or defaulted;
- artifact schemas are incompatible;
- constraints cannot be satisfied;
- producer ambiguity cannot be resolved.

Supplied intermediate artifacts are valid graph roots. They win over producer paths by default.

## Shared Multi-Output Graphs

A request with multiple outputs must not create isolated duplicate plans when outputs share dependencies.

Example:

```text
outputs:
  webvtt_transcript
  transcript::speaker_role_change_stream
```

The planner creates one graph. Shared upstream artifacts are produced once, cached once, and reused by all dependent outputs.

## Logical Requirements and Concrete Instances

A logical artifact requirement describes what a stage needs.

```text
artifact_id: audio::prepared
constraints:
  sample_rate_hz: 16000
  channels: 1
  container: wav
```

A concrete artifact instance is the actual resolved node in the task graph.

```text
artifact_id: audio::prepared
instance_id: audio.prepared.<short_fingerprint>
schema_id: chunky.audio.v1
properties:
  sample_rate_hz: 16000
  channels: 1
  container: wav
```

Multiple concrete instances may exist for the same logical artifact ID in a single task graph.

For example:

```text
source::audio
audio.prepared.<fingerprint>
audio.prepared.<fingerprint>
```

The full fingerprint is the authority. Human-readable instance IDs are for diagnostics.

## Artifact Constraints

The first version supports exact equality constraints only.

Example:

```text
sample_rate_hz: 16000
channels: 1
container: wav
```

Unknown metadata does not satisfy a constraint.

Source artifacts should populate required metadata during creation or validation. Planning should then be deterministic and should not depend on arbitrary runtime probing.

## Audio Preparation

Source `audio` is immutable.

Audio conversion, normalization, loudness adjustment, compression, noise suppression, or other preparation must be represented as explicit stage-produced concrete audio instances.

Downstream stages should declare logical audio requirements. They should not know which preprocessing stage will satisfy them.

Planning behavior:

- if source audio already satisfies the requirement, bind it directly;
- otherwise, insert a preparation stage that can produce a matching concrete audio instance;
- record the decision in the manifest.

## Cache-Aware Execution

Cache lookup occurs during execution immediately before a stage would run.

Planning may annotate cacheable nodes, but planning should not rely on cache state as a stable decision.

If all requested outputs for a stage invocation are cache hits, the stage is skipped and the relevant artifact availability events are emitted.

Cache hits do not acquire compute resource locks.

## Resource Scheduling

Stages declare default resource requirements. Concrete invocations may resolve different requirements based on settings.

Resource classes:

```text
cpu_memory_bound
cpu_compute_bound
gpu_memory_bound
gpu_compute_bound
unbound
```

A stage may require multiple constrained resources. `unbound` means no constrained resource lock and should not be mixed with constrained resources.

Concurrency controls:

```text
stage_concurrency_limit
resource_concurrency_limit
```

Scheduling order:

```text
1. find invocations whose required artifacts are available
2. filter to invocations whose resource locks and concurrency limits are available
3. sort eligible work by task creation time, queue sequence, then stage priority
4. start eligible work
```

Older tasks are preferred among eligible work. A blocked older task must not prevent unrelated eligible work from running.

## Resource Protection

`bound_protected` applies globally by resource class, not by stage ID.

For example, two GPU-memory-bound stages should not run together when the configured GPU memory policy says that resource class is protected, even if they are different stage types.

Stage-specific concurrency limits are separate from resource-class limits.

## Scheduler Responsibilities

The scheduler is responsible for:

- tracking available artifacts;
- tracking ready stage invocations;
- checking resource locks and concurrency limits;
- preferring older eligible tasks;
- avoiding starvation of unblocked work;
- emitting stage and artifact lifecycle events;
- respecting failure and cancellation policy.

---

## Learned Facts To Actualize Later

These facts were learned while consolidating `03-stage-implementations/02-expected_stages.md` and should be reconciled in this document during its own design pass.

1. `audio::prepared` should replace consumer-specific ready artifact IDs such as transcription-ready or speaker-detection-ready audio. It is a generic intermediate artifact whose concrete instances are distinguished by canonical constraints, resolved audio transform settings, source fingerprint, and provider versions.
2. Audio-consuming stages should declare logical `audio::prepared` requirements derived from their own audio settings artifacts. The planner may bind `source::audio` directly if it already satisfies the requirement, or insert/reuse audio preparation otherwise.
3. Direct source binding for an `audio::prepared` logical requirement should not create a trivial cached `audio::prepared` artifact, but the graph and manifest must still record the availability edge so dependent stages wait for the resolved audio resource.
4. Identical canonical `audio::prepared` requirements should share one preparation invocation or one direct source binding.
5. The `audio::prepared` fingerprint should include only audio-preparation-relevant inputs, not the full consuming-stage settings artifact.
6. Cache availability must not affect graph shape. Cache hits should affect execution skipping only.
7. Supplied artifacts win over producer paths by default, with an explicit recompute/force-producer override available.
8. Phase-less intermediate requests for declared phased families should drive planner terminal remapping. Non-terminal outputs remain concrete phase-numbered artifacts unless they satisfy a phase-less requirement in the planned graph.
9. Planned stage invocations should have explicit requested output subsets. Cache-hit skipping should require only those planned outputs to be available.
