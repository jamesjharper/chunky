# Chunky Run Contract - Scheduling

## Purpose

This document defines how an execution plan is scheduled after graph creation has resolved the stages, artifacts, settings, fingerprints, and requested outputs for a `RunRequest`.

Scheduling is runtime orchestration. It must not change graph shape, producer selection, artifact identity, phase remapping, or stage requirement resolution. Those decisions are made during graph creation.

The scheduler is responsible for:

- tracking artifact availability;
- honoring cache reservations before they are used;
- deciding which stage invocations are ready to run;
- enforcing resource and concurrency constraints;
- starting, skipping, cancelling, and failing stage invocations;
- updating the execution manifest over time;
- emitting task, stage, cache, and artifact events.

## 1. Scheduler Inputs
The scheduler consumes several domain objects produced or frozen before execution starts.

### 1.1 Stage Manifest
The stage manifest is the frozen stage registry snapshot used during graph creation.

The scheduler uses the stage manifest for execution metadata only:

- stage identity;
- stage behavior version;
- declared resource defaults;
- stage-level concurrency limits;
- stage enablement settings after they have been resolved into the execution plan;
- invocation entry points or adapters.

The scheduler must not call stage requirement or output resolution in a way that changes the graph. If a concrete invocation needs runtime metadata from the stage definition, it reads it from the frozen stage manifest and the resolved invocation context.

### 1.2 Evaluated Graph

The evaluated graph is the dependency DAG of concrete stage invocations and artifact edges before runtime cache decisions are applied.

The scheduler uses the evaluated graph to understand:

- which artifact edges are required by each stage invocation;
- which artifact edges each stage invocation is expected to produce;
- which downstream invocations are unblocked by each artifact;
- which invocations are independent and may run concurrently;
- which invocations are descendants of a failed, cancelled, or unavailable artifact.

The evaluated graph is read-only during scheduling. Runtime state is stored in the execution manifest and artifact manifest, not by mutating the evaluated graph.

### 1.3 Execution Plan

The execution plan is the evaluated graph after supplied inputs, disabled stages, and cache hits have been considered.

For each stage invocation, it records:

- the stage identity and concrete invocation identity;
- the requested output subset for this run;
- whether the invocation is expected to run or no-op;
- the input artifact bindings selected for the invocation;
- the output artifact bindings expected from the invocation;
- the expected artifact fingerprints;
- dependency links to upstream and downstream invocations.

The execution plan is the scheduler's source of truth for what work exists. The execution manifest is the mutable runtime state of that work.

### 1.4 Artifact Manifest

The artifact manifest records every artifact edge that matters to execution.

Each artifact entry should include:

- artifact ID;
- artifact role: `source`, `settings`, `intermediate`, `target`, or `diagnostic`;
- schema identity and version;
- expected fingerprint when one is known;
- planned producer invocation, if any;
- supplied provider or cache provider, if already available;
- requiredness for the current plan;
- terminal remapping metadata, when a concrete phased output is stored as a phase-less final artifact;
- current availability state.

Recommended artifact states:

```text
pending
available_supplied
available_cache_reserved
materializing
available_produced
available_materialized
failed
unavailable_optional
```

The scheduler uses artifact entries differently by role:

- `source` artifacts are graph roots supplied by the caller or bound directly from an external provider. They must be validated and fingerprinted before dependent stages can run.
- `settings` artifacts hold resolved effective settings. They are inputs to stage fingerprints and execution contexts. They should be available before any invocation that consumes them starts.
- `intermediate` artifacts carry data between stages. Their availability controls dependency readiness and cache reuse.
- `target` artifacts are requested terminal semantic outputs. They may still need materialization to a file, memory object, stream, or sink after the semantic artifact is available.
- `diagnostic` artifacts are declared inspection outputs. Deterministic diagnostics may be cached; non-deterministic diagnostics should be materialized as run-specific outputs and should not affect semantic cache hits unless explicitly required.

Optional artifacts that cannot be supplied, produced, or found in cache should be recorded as `unavailable_optional`. Required artifacts in the same situation fail or block the dependent invocation.

### 1.5 Run Policy

The scheduler also receives run-level policy:

- failure mode: `fail_fast` or `continue_independent`;
- cancellation policy;
- cache policy;
- resource limits;
- fairness policy across tasks;
- event sink configuration;
- target materialization policy.

## 2. Invocation Context Model

Each concrete stage invocation should receive a resolved context object rather than re-reading global planning state.

Conceptually:

```text
StageInvocationContext
  task_id
  invocation_id
  stage_id
  stage_behavior_version
  requested_outputs: set<ArtifactId>
  inputs: map<InputRole, ArtifactBinding>
  optional_inputs: map<InputRole, ArtifactBinding?>
  outputs: map<ArtifactId, OutputBinding>
  settings: map<ArtifactId, ResolvedSettingsArtifact>
  effective_settings_fingerprint
  input_fingerprints: map<ArtifactId, Fingerprint>
  resource_policy: ResolvedResourcePolicy
  cache_policy: CachePolicy
  cancellation_token
  event_sink
  scratch_workspace?
```

`settings` contains the resolved effective settings values for the invocation, including defaults, user-configured settings, and any path-pushed inherited overrides, with pushed overrides taking precedence.

`ArtifactBinding` should include enough information for the stage to read the artifact without knowing whether it was supplied, cache-reserved, or produced earlier in the same run.

Conceptually:

```text
ArtifactBinding
  artifact_id
  role
  schema_identity
  fingerprint
  provider
  location_or_handle
  provenance
```

`OutputBinding` should tell the stage where to write or return each planned output:

```text
OutputBinding
  artifact_id
  role
  schema_identity
  expected_fingerprint
  cacheable
  materialization_target?
  produced_from_declared_output?
```

The context is derived from the execution plan and artifact manifest. It must not let a stage alter the plan shape. A stage may emit declared incidental outputs, but only validated declared artifacts may be recorded or cached.


<!-- example-implementation:start -->
## Example implementation: artifact bindings and fixed scheduler loop

The scheduler presents transport-neutral readers and writers to stages. The `ArtifactReader` prefers direct memory when a prior stage produced the value in the same run. If the value is not in memory, the reader deserializes from the supplied file or cache using the declaration codec.

```python
class ArtifactReader:
    """Physical transport abstraction presented to a stage as an input binding."""

    def __init__(self, declaration: ArtifactDeclaration, instance: ArtifactInstance, cache: FileArtifactCache):
        self.declaration = declaration
        self.instance = instance
        self.cache = cache
        self.read_mode_used: str | None = None

    @property
    def fingerprint(self) -> str:
        return self.instance.fingerprint

    @property
    def provider(self) -> str:
        return self.instance.provider

    def read(self) -> Any:
        if self.instance.value is not None:
            self.read_mode_used = "memory/no-serde"
            return self.instance.value

        if self.instance.provider == "supplied-file" and self.instance.handle is not None:
            self.read_mode_used = "file/deserialized"
            return self.declaration.codec.deserialize(self.instance.handle.read_bytes())

        if self.instance.provider == "cache":
            self.read_mode_used = "cache/deserialized"
            return self.cache.read(self.declaration, self.instance)

        raise ValueError(f"cannot read artifact {self.instance.artifact_id} from {self.instance.provider}")
```

The writer validates the value through the codec, writes it to cache when cacheable, and still keeps the object in memory for downstream stages in the same process.

```python
class ArtifactWriter:
    """Output binding presented to a stage. It records memory value and durable cache."""

    def __init__(
        self,
        declaration: ArtifactDeclaration,
        fingerprint: str,
        cache: FileArtifactCache,
        produced: dict[str, ArtifactInstance],
    ):
        self.declaration = declaration
        self.fingerprint = fingerprint
        self.cache = cache
        self.produced = produced

    def write(self, value: Any) -> ArtifactInstance:
        raw = self.declaration.codec.serialize(value)
        self.declaration.codec.deserialize(raw)

        handle = None
        if self.declaration.cacheable:
            handle = self.cache.write(self.declaration, self.fingerprint, value)

        instance = ArtifactInstance(
            artifact_id=self.declaration.artifact_id,
            fingerprint=self.fingerprint,
            value=value,
            provider="produced-memory",
            handle=handle,
        )
        self.produced[self.declaration.artifact_id] = instance
        return instance
```

The runner below is intentionally not a real resource scheduler. It demonstrates the execution contract: bind supplied roots, skip stages when all requested outputs are cache hits, otherwise run with reader/writer bindings, then materialize requested targets.

```python
class MockSchedule:
    """The deliberately dumb schedule requested: Stage1, then Stage2."""

    def __init__(self, stages: list[Stage]):
        self.stages = stages
        self.index = 0

    def get_next_stage(self) -> Stage | None:
        if self.index >= len(self.stages):
            return None
        stage = self.stages[self.index]
        self.index += 1
        return stage


class Runner:
    def __init__(self, schedule: MockSchedule, manifest: StageManifest, cache: FileArtifactCache):
        self.schedule = schedule
        self.manifest = manifest
        self.declarations = manifest.declarations
        self.cache = cache
        self.artifacts: dict[str, ArtifactInstance] = {}
        self.events: list[str] = []

    def bind_supplied(self, supplied_inputs: dict[str, SuppliedArtifact]) -> None:
        for artifact_id, supplied in supplied_inputs.items():
            declaration = self.declarations[artifact_id]
            fp = supplied_fingerprint(declaration, supplied)
            if supplied.value is not None:
                instance = ArtifactInstance(artifact_id, fp, supplied.value, "supplied-memory")
            else:
                instance = ArtifactInstance(artifact_id, fp, None, "supplied-file", supplied.file_path)
            self.artifacts[artifact_id] = instance
            self.events.append(f"supplied {artifact_id} via {instance.provider} fp={fp}")

    def run(self, supplied_inputs: dict[str, SuppliedArtifact], targets: dict[str, TargetArtifact]) -> RunResult:
        self.bind_supplied(supplied_inputs)

        while True:
            stage = self.schedule.get_next_stage()
            if stage is None:
                break

            input_instances = {req.artifact_id: self.artifacts[req.artifact_id] for req in stage.inputs}
            output_fps: dict[str, str] = {}
            all_outputs_cached = True

            for output_id in stage.outputs:
                declaration = self.declarations[output_id]
                fp = produced_fingerprint(declaration, stage, input_instances)
                output_fps[output_id] = fp
                if not (declaration.cacheable and self.cache.has(declaration, fp)):
                    all_outputs_cached = False

            if all_outputs_cached:
                for output_id, fp in output_fps.items():
                    declaration = self.declarations[output_id]
                    self.artifacts[output_id] = ArtifactInstance(
                        artifact_id=output_id,
                        fingerprint=fp,
                        value=None,
                        provider="cache",
                        handle=self.cache.data_path(declaration, fp),
                    )
                self.events.append(f"skipped {stage.stage_id}; outputs loaded from cache")
                continue

            produced: dict[str, ArtifactInstance] = {}
            readers = {
                artifact_id: ArtifactReader(self.declarations[artifact_id], instance, self.cache)
                for artifact_id, instance in input_instances.items()
            }
            writers = {
                output_id: ArtifactWriter(self.declarations[output_id], fp, self.cache, produced)
                for output_id, fp in output_fps.items()
            }
            context = StageInvocationContext(
                stage_id=stage.stage_id,
                behavior_version=stage.behavior_version,
                inputs=readers,
                outputs=writers,
                requested_outputs=set(stage.outputs),
            )

            self.events.append(f"running {stage.stage_id}")
            stage.run(context)
            self.artifacts.update(produced)

        for artifact_id, target in targets.items():
            declaration = self.declarations[artifact_id]
            instance = self.artifacts[artifact_id]
            reader = ArtifactReader(declaration, instance, self.cache)
            value = reader.read()
            target.file_path.parent.mkdir(parents=True, exist_ok=True)
            target.file_path.write_bytes(declaration.codec.serialize(value))
            self.events.append(f"materialized {artifact_id} -> {target.file_path}")

        return RunResult(artifacts=self.artifacts, events=self.events)
```
<!-- example-implementation:end -->


## 3. Stage State

The execution manifest stores mutable stage state.

Each stage invocation entry should include:

- task ID;
- invocation ID;
- stage ID;
- stage behavior version;
- stage invocation fingerprint;
- requested output subset;
- required input artifact IDs;
- optional input artifact IDs;
- output artifact IDs;
- upstream invocation IDs;
- downstream invocation IDs;
- resolved resource policy;
- current state;
- skip reason, if skipped;
- error, if failed;
- start and finish timestamps.

Recommended stage states:

```text
waiting_for_artifacts
waiting_for_global_cache
waiting_for_resources
running
skipped_cache
skipped_disabled
completed
failed
cancelled
blocked
```

An invocation is ready for resource admission when all required input artifacts are available and the invocation is not skipped, failed, cancelled, or blocked.

Disabled stages should be represented explicitly. If a disabled stage has required requested outputs and no supplied or cached substitute exists, the plan should fail or the invocation should become blocked. If its outputs are not required, or all required outputs are otherwise available, the invocation may be marked `skipped_disabled`.

## 4. Resource Scheduling

Stages declare default resource requirements in the stage manifest. Concrete invocations may resolve different resource requirements from their settings during planning.

Resource classes:

```text
cpu_memory_bound
cpu_compute_bound
gpu_memory_bound
gpu_compute_bound
unbound
```

A stage may require multiple constrained resources. `unbound` means no constrained resource lock and should not be combined with constrained resources for the same invocation.

Resource constraints do not change artifact dependencies or graph shape. They only control when ready invocations may start.

### 4.1 Resource Policies

The scheduler should support at least:

- per-resource-class concurrency limits;
- per-stage concurrency limits;
- per-provider or per-device limits when relevant;
- protected resource classes, such as exclusive GPU memory use;
- task-level cancellation and failure limits.

`bound_protected` applies globally by resource class, not by stage ID. For example, two GPU-memory-bound stages should not run together when the configured GPU memory policy says that resource class is protected, even if they are different stage types.

Stage-specific concurrency limits are separate from resource-class limits.

### 4.2 Admission Loop

Scheduling order:

```text
1. mark supplied and cache-reserved artifacts as available
2. find invocations whose required artifacts are available
3. remove invocations that are skipped, cancelled, failed, or blocked
4. filter to invocations whose resource locks and concurrency limits are available
5. sort eligible work by task creation time, queue sequence, then stage priority
6. reserve resource locks
7. start eligible work
```

Older tasks are preferred among eligible work. A blocked older task must not prevent unrelated eligible work from running.

Resource locks must be released when the invocation completes, fails, or is cancelled. Locks should not be held while an invocation is only waiting for artifacts.

## 5. Execution Flow

The scheduler lifecycle is:

```text
1. initialize execution manifest from the execution plan
2. validate and mark supplied source and settings artifacts available
3. load plan-time cache reservations and global cache wait records
4. mark skipped invocations whose requested outputs are supplied or cache-reserved
5. wait for globally reserved cache writers when the plan depends on their outputs
6. repeatedly start ready invocations that pass resource admission
7. validate produced artifacts
8. store cacheable produced artifacts and release write reservations
9. update artifact availability and unblock dependents
10. materialize requested targets
11. release usage reservations and resource locks
12. complete, fail, or cancel the task
```

When a stage finishes successfully:

- every requested output from the invocation must be present, valid, and fingerprinted;
- declared incidental outputs may be recorded if they are valid;
- required output artifacts become available;
- downstream invocations depending on those artifacts may become ready;
- target artifacts may enter materialization if their semantic artifact is available.

If a stage fails:

- the invocation becomes `failed`;
- produced artifacts from that invocation are not marked available unless they were completed and validated before failure and the failure policy allows retaining them;
- descendants that require failed artifacts become `blocked`;
- independent invocations may continue only when failure mode is `continue_independent`.

## 6. Eventing

The scheduler emits the canonical task event stream defined by the eventing contract.

At minimum, scheduling should emit events for:

- task start, completion, failure, and cancellation;
- stage state changes;
- stage progress and step progress;
- cache hits and misses;
- artifact availability;
- artifact materialization;
- artifact failure;
- scheduler waits caused by unavailable artifacts, unavailable resources, or global cache writers.

Events should include stable IDs where available:

```text
task_id
invocation_id
stage_id
artifact_id
fingerprint
state
reason
timestamp
```

Per-task event ordering is preserved within one process. Event handlers must not be allowed to fail the task by default.

Cache-hit skipping should emit both cache events and stage state events. This keeps the event stream understandable: the task can show that an invocation did not run because its requested outputs were already available.

## 7. Scheduling Invariants

The scheduler should preserve these invariants:

1. Scheduling never changes graph shape.
2. A stage starts only after all required input artifacts are available.
3. A stage is skipped for cache only when all requested outputs for that invocation are available.
4. Cache hits are reserved before downstream stages can consume them.
5. Resource locks are acquired before work starts and released when work ends.
6. Supplied artifacts are task-local roots, not cache hits for their normal producer stages.
7. Optional missing artifacts do not block execution.
8. Required missing artifacts fail or block the dependent path.
9. Stage execution receives a resolved context containing settings, inputs, outputs, fingerprints, cancellation, and event handles.
10. Events describe the same state transitions recorded in the execution and artifact manifests.
