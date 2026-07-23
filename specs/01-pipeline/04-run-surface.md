# Run Surface

## Purpose

This document defines how callers execute a
[`LocalRunRequest`](03-run-request-building.md) or submit a
[`DurableRunRequest`](03-run-request-building.md), and what each public
entrypoint returns.

The two request forms intentionally use different surfaces:

- the configured pipeline executes local requests and returns process-local
  task-control objects; and
- the durable task service accepts durable requests and returns a data-only
  submission receipt.

Choosing to wait or not wait never changes which durability contract is used.
Detailed task states, awaiting, cancellation, progress, and terminal-result
semantics belong to
[Eventing and Task Control](../07-scheduling/eventing-and-task-control-model-spec.md).

## Local execution

`pipeline.run(request)` consumes a `LocalRunRequest`, executes it in the current
process, and returns its terminal `RunResult`.

`pipeline.start(request)` consumes the same request and immediately returns a
process-local `RunHandle`:

```text
pipeline.run(request) == pipeline.start(request).await_to_completion()
```

`start` is detached only from the caller's wait. It is not durable, does not
survive loss of the owning process, and cannot be reattached from serialized
state. Normal cache reuse after a restart is not task resumption.

The local request is consumed as execution begins. Reuse must fail clearly in bindings that cannot enforce move semantics. 
Execution owns cleanup of every input and target handle, including handles that planning leaves unused.

### Expected usage

Use `run` when the caller needs the terminal result before continuing:

```python
request = (
    LocalRunRequestBuilder()
    .include_input("source/audio", SourceAudioArtifact.from_file_path("input.wav"))
    .expect_output_target("render/webvtt_transcript", WebVttTarget.to_file_path("output.vtt"),)
    .build()
)

result = await pipeline.run(request)
```

Use `start` when the caller needs to continue locally while retaining a
process-local reference to the same execution:

```python
handle = pipeline.start(request)
print(handle.task_id)
```

The pipeline must remain in scope for the local task. `start` is detached only
from the caller's immediate wait; it is not a durable background-job API. 

Both `pipeline.start(request)` and `pipeline.run(request)` are mutable operations over the pipeline instance. Pipeline, or its internal services must guarantee thread safety over mutable internals. 

### Returned objects

`pipeline.run(...)` returns the scheduling-owned `RunResult`. At this boundary,
callers can rely on it to contain:

```text
RunResult
  task_id
  status
  outputs: map<requested-artifact-id, OutputResult>
  errors: ordered collection<RunError>
```

`outputs` contains exactly one `OutputResult` for every requested semantic
output, including targetless outputs. It excludes intermediate artifacts and
incidental stage outputs. Each output result exposes:

```text
OutputResult
  artifact_id
  task_locator
  status
  fingerprint?
  instance_locator?
  targets: ordered collection<TargetMaterializationResult>
  error_ids: ordered collection<error-id>

TargetMaterializationResult
  target_index
  status
  error_ids: ordered collection<error-id>

RunError
  error_id
  code
  message
  stage_id?
  artifact_id?
```

- `task_locator` is the local task's provider-lifetime `a-task` locator for the
  requested output.
- `instance_locator` is present only when a matching committed, readable cache
  instance exists. Callers must not construct one from the fingerprint.
- `targets` reports local target delivery in declaration order and is empty for
  a targetless output.
- `target_index` identifies the target by its zero-based declaration index.
- Nested `error_ids` correlate an output or target outcome with entries in
  `RunResult.errors`.

This is the run surface's required composition of the result, not a second
definition of task-control types. Eventing and Task Control owns the complete
schemas and the meanings of their status values.

`pipeline.start(...)` returns a process-local `RunHandle` for the same task. The
handle exposes the allocated `task_id` and the observation and control
operations defined by Eventing and Task Control. It is neither serializable nor
reattachable, and dropping it does not make the task durable.

Bindings may reject a call before allocating a task when an argument cannot be
converted to the required local request contract. In that case there is no
`RunHandle`, task ID, or `RunResult`. After task allocation, planning,
execution, artifact, and materialization failures are represented by the
task-control result rather than by a different pipeline return type.

### Local result production

Cancellation and structured progress cover binding, planning, cache waits and
I/O, scheduling, stage execution, and target materialization. The run surface
delegates task state, progress snapshots, cancellation, and terminal result
semantics to the task-control contract.

Local source handles are read only when selected by backward planning. An
explicitly supplied input selected by the plan is authoritative over defaults,
compatible cache entries, or a producing stage. Unselected inputs remain unread
and are closed during cleanup.

Cacheable produced outputs resolve from or commit to cache before local target
materialization. Non-cacheable or `BYPASS` outputs use the direct local binding.

For multiple local targets, delivery follows declaration order and stops on the
first failure. Each target is transactional independently; completed cache
commits and earlier target commits are not rolled back, and later targets are
reported as unattempted.

Each requested output result identifies the semantic artifact and fingerprint,
reports target delivery status, and includes cache locators only when matching
cache records actually exist.

## Durable submission

`durable_task_service.submit(durable_request)` is a distinct public surface.
It consumes a `DurableRunRequest` and returns a serializable receipt after the
acceptance boundary defined by
[Persisted Task Lifecycle](../07-scheduling/02-persisted-task-lifecycle.md).

### Expected usage

A caller may build a durable request directly from committed `a-instance`
inputs, or obtain one through the durable request preparation flow. Submission
does not execute through `pipeline.run(...)` or `pipeline.start(...)`:

```python
request = (
    DurableRunRequestBuilder()
    .include_cached_input(audio_instance_locator)
    .expect_output("render/webvtt_transcript")
    .build()
)

receipt = await durable_task_service.submit(request)
persist(receipt)
```

The caller should persist the returned receipt, or at minimum its task ID, when
it needs to observe or recover the task from another process. Subsequent
status, cancellation, completion, and output-resolution operations address the
durable service by that identity; their eventing and waiting behavior is not
defined here.

### Returned object

The returned durable submission receipt is a data-only interoperable contract:

```text
DurableSubmissionReceipt
  task_id: durable-task-id
  output_task_locators: sorted list<canonical-a-task>
```

- `task_id` is the explicit durable task identity allocated at acceptance.
- `output_task_locators` contains one stable `a-task` locator for every
  requested output.
- Each locator already embeds its requested artifact reference, so the receipt
  does not repeat those references as map keys.
- The list is sorted deterministically by canonical locator representation so
  equivalent accepted requests do not depend on builder declaration order.

Returning the receipt confirms durable acceptance, not terminal completion. The
submission surface does not return the local `RunHandle` or the local
`RunResult`. A client library may wrap the receipt in a process-local
convenience object, but that wrapper is not part of the serializable contract
and must not be required to resume interaction with the durable task.

## Responsibility boundaries

The run surface owns:

- selection of the local pipeline or durable service entrypoint;
- consumption of the corresponding request form; and
- the caller-visible meaning of the returned `RunResult`, `RunHandle`, or
  durable submission receipt.

It does not define:

- request construction or canonical encoding;
- local/durable binding convergence;
- durable-preparation uploads;
- cache implementation mechanics;
- task states, awaiting, cancellation, progress, or scheduler policy; or
- persisted task storage and recovery.

Those contracts live in their respective pipeline, artifact, cache, planning,
and scheduling specifications.
