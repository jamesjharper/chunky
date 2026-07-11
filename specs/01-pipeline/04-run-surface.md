# Run Surface

## Purpose

This document defines the public execution surface for a configured pipeline and explains how callers consume results.

It covers:

- `run(...)` and `run_away(...)`;
- the execution lifecycle for one accepted `RunRequest`;
- task-control integration;
- progress snapshots and detached handles;
- result consumption, including `a-task` and `a-instance` output references.

The detailed task lifecycle, cancellation semantics, and progress model are defined in [../07-scheduling/eventing-and-task-control-model-spec.md](../07-scheduling/eventing-and-task-control-model-spec.md). This document describes how the pipeline surface composes that model into the run API.

## Public Run Surfaces

The configured pipeline exposes two run-oriented APIs that share the same execution core.

### `run(...)`

`run(...)` executes a task and awaits completion directly.

```python
async def run(
    self,
    request: RunRequest,
    *,
    cancellation: CancellationSource | None = None,
) -> TaskRunResult:
    ...
```

On success, it returns a completed `TaskRunResult`. On failure, it raises a structured exception carrying diagnostics and any successful partial outcomes that are available.

### `run_away(...)`

`run_away(...)` starts the same task in the current process and returns a `RunHandle`.

```python
def run_away(self, request: RunRequest) -> RunHandle[TaskRunResult]:
    ...
```

The handle exposes:

- generated `task_id`;
- cancellation access;
- progress inspection and streaming;
- awaitable typed terminal completion.

`run_away(...)` is strictly in-process and non-durable. It does not promise resumable job state across process restarts. Re-running a semantically identical job after restart may skip completed work through normal cache reuse, but that is a consequence of cache behavior rather than a separate resumability guarantee.

Language-neutral meaning:

```text
run(request) == run_away(request).await_to_completion()
```

## Execution Lifecycle

The pipeline exposes one execution seam that turns an accepted `RunRequest` plus run-scoped control surfaces into one concrete task execution.

Conceptual lifecycle:

1. Normalize and structurally validate request `inputs`, `outputs`, and `cache_policy`.
2. Derive task-scoped runtime collaborators, including cache view, bindings provider, planner, runner, cancellation, and progress surfaces, from the frozen runtime services.
3. Bind caller-supplied and cached-root inputs into supplied artifact instances.
4. Publish cacheable provided roots to cache unless their exact artifact policy has `do_not_cache=True`.
5. Validate target-less requested outputs against cacheability and per-artifact cache policy.
6. Provide planning with supplied instances, requested outputs, run policy, cache policy, and an explicit settings-default resolver interface.
7. Let the planner discover relevant settings and request default resolution on demand.
8. Plan the task graph from the frozen manifest and request-specific context.
9. Register planned invocations with the progress tracker after planning succeeds.
10. Execute the plan through the configured runner.
11. Materialize requested caller targets from available semantic artifact instances.
12. Return success, or surface structured failure diagnostics and partial outcomes.

If planning fails after supplied artifacts have already been bound, the pipeline must close every bound supplied instance it holds for that run before surfacing the error.

## Run Handle

`RunHandle` is the in-process control surface for a detached execution.

Conceptually:

```python
class RunHandle(Generic[T]):
    task_id: str

    def get_progress(self) -> "RunProgressSnapshot": ...
    def get_result(self) -> "RunResult[T] | None": ...

    def cancel(self, reason: str | None = None) -> bool: ...
    async def await_to_completion(self) -> "RunResult[T]": ...
    def subscribe_progress_events(
        self,
        handler: "RunProgressHandler",
    ) -> "ProgressSubscription": ...

    def __await__(self): ...
```

Bindings may rename these methods to match local conventions.

Handle semantics:

- `get_progress()` returns a consistent complete snapshot from the task-owned progress tracker;
- `get_result()` returns `None` until the task reaches a terminal state;
- `await_to_completion()` waits for any terminal state and returns the same `RunResult` stored by `get_result()`;
- awaiting the handle delegates to `await_to_completion()` and does not implicitly cancel the task if the caller's await is cancelled;
- `cancel()` requests cancellation and returns immediately;
- `subscribe_progress_events()` registers a callback, immediately schedules the current complete snapshot, and then receives future changed snapshots.

Dropping a handle does not cancel the task.

## Task-Control Integration

Each accepted request becomes one task managed by the task-control layer.

Task control is responsible for:

- allocating and exposing a task ID;
- owning task lifecycle state;
- exposing synchronous and asynchronous completion;
- accepting cancellation requests;
- providing progress snapshots;
- collecting terminal output results, committed retrieval references, target materialization results, and errors;
- notifying progress subscribers whenever the public snapshot changes.

The pipeline surface uses that layer rather than re-defining its own task model. In particular:

- `run(...)` blocks on the same task controller used by `run_away(...)`;
- `RunHandle.task_id` is the public correlation ID for progress, diagnostics, and cancellation;
- task-level state remains intentionally coarse;
- waiting conditions such as cache contention or resource admission are surfaced through progress snapshots rather than new top-level task states.

## Consuming Results

Terminal results must let callers distinguish semantic completion from materialization side effects and later retrieval metadata.

Callers should expect result payloads to include:

- terminal run status;
- diagnostics and structured failure information when applicable;
- per-requested-output materialization outcomes;
- `a-task` locators for every requested output after planning finalization;
- externally retrievable `a-instance` locators for requested outputs once durable cache commit completes.

The distinction matters:

- `a-task` identifies the task-scoped requested-output binding;
- `a-instance` identifies a concrete committed cache instance that can be rebound or retrieved later.

Clients should store returned locators rather than reconstructing them from graph metadata.

### Direct `run(...)` Consumption

```python
result = await pipeline.run(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript": [],
        },
    )
)

output = result.outputs[ArtifactKey.from_a_ref("render/webvtt_transcript")]
print(output.task_locator)
print(output.instance_locator)
```

A target-less output is valid only when durable publication is possible. `output.instance_locator` is externally useful only after the output has committed durably and is retrievable.

### Detached `run_away(...)` Consumption

```python
handle = pipeline.run_away(
    RunRequest(
        inputs={
            "source/audio": SourceAudioArtifact.from_file_path("input.wav"),
        },
        outputs={
            "render/webvtt_transcript": [
                TargetVttArtifact.to_file_path("output.vtt"),
            ],
        },
    )
)

print(handle.task_id)
result = await handle.await_to_completion()
```

Progress subscribers may observe when requested outputs become durably available. When that happens, events and the terminal result may include the externally retrievable `a-instance` locator in addition to the already-known `a-task`.

## Progress and Failure Reporting

The pipeline must expose one stable public view of run progress without leaking scheduler internals as public API commitments.

Task state is intentionally coarse:

```text
created -> running -> completed
created -> failed
created -> cancelling -> cancelled
running -> cancelling -> cancelled
running -> cancelling -> failed
```

More detailed activity such as pending work, blocked work, cache waits, or actively running stages belongs in `RunProgressSnapshot`, not the top-level task state enum.

Failure is fail-fast in the initial public contract. When a task-fatal failure occurs, the task-control layer coordinates shutdown, preserves partial outcomes already made durable, and surfaces a terminal failed result through the same run surfaces.

## Responsibility Boundaries

The pipeline surface is responsible for:

- accepting a `RunRequest`;
- normalizing and validating the request shape;
- binding supplied roots and cached-root locators;
- coordinating planner, runner, cache, task-control, and materialization collaborators;
- returning or exposing results, progress, and cancellation through one consistent public API.

The pipeline surface is not responsible for:

- mutating the frozen manifest during a run;
- selecting cache backend operational details as part of request semantics;
- redefining scheduler state as task-state transitions;
- bypassing task control for detached execution;
- exposing raw cache internals instead of semantic `a-ref`, `a-task`, and `a-instance` surfaces.
