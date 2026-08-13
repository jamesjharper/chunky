# Run Request Building

## Purpose

This document defines the caller-facing contract for constructing run requests:
the local and durable request forms, accepted artifact addressing, input and
output semantics, request-level cache and run policy, builder validation and
ownership, task artifact retention, and the durable request's serialized
representation.

- `LocalRunRequest` is process-local and may own external artifact handles.
- `DurableRunRequest` is data-only and is used for durable submission and
  replay.

This document does not define cache mechanics, target-handle mechanics,
planning, execution, or persisted task lifecycle. Examples use Python-like
syntax to illustrate the language-neutral contract; bindings may use idiomatic
naming and ownership constructs while preserving the described behavior.

## Artifact addressing

Request builders accept the locator families defined by
[Artifact Model](../02-artifacts/01-artifact-model-surface.md):

- `a-ref` identifies a semantic artifact request;
- `a-instance` identifies one concrete semantic artifact instance.

The builder interfaces should accept non-canonical forms such as `source/audio`
instead of its canonical form, `a-ref:/global/source/audio/any`. Builders parse
accepted non-canonical forms into typed canonical values, and duplicate
detection uses equality of those parsed values. This is syntactic parsing, not
manifest lookup or artifact resolution.

## Local vs. durable requests

Local and durable requests differ in how they represent artifacts and in the
circumstances under which their execution can survive an interruption.

**Durable request:**

- contains only serializable data;
- resolves required inputs through the runtime's cache and artifact services;
- publishes requested outputs to durable artifact persistence;
- can run unattended; and
- can be paused, restarted, or recovered after a process restart.

**Local request:**

- may consume non-cached artifacts through external source handles; and
- may materialize outputs directly to external in-memory objects or files.

> A local run can be paused or resumed while its external resources remain
> valid. After a process restart, it can only be rerun by rebuilding the same
> request with equivalent inputs and outputs. Reusing a persistent cache from
> the initial run allows already committed work to be recovered.

### Durable request limitations

Durable requests MUST represent every external input as a serializable `a-ref`
and upload-fingerprint binding and MUST request targetless outputs. They do not
require every uploaded root to remain committed when the request is accepted.
This ensures that:

- the request can be serialized without encoding external input or output
  handles; and
- execution can resume or restart from cache-satisfied intermediates without
  the caller resupplying an unneeded root or collecting outputs synchronously.

If resumed execution must run a stage whose uploaded root has been deleted, the
task fails with an unavailable required input.

### Local request limitations

Local requests may contain non-serializable external artifact handles. Therefore:

- they cannot be serialized, transferred, or durably submitted as-is;
- source and target handles must remain valid until their I/O completes; and
- after a process failure, external handles cannot be recovered and the caller must
  rebuild the request. Only work committed to a persistent cache can be reused.


There is no whole-request local-to-durable conversion operation. Callers publish
external inputs independently through
[Durable Artifact Upload](03-durable-artifacts-upload-surface.md), receive
upload fingerprints, and bind those fingerprints to `a-ref` values in a durable
request.

## Request: Inputs

`inputs` supplies caller-provided artifact roots for a run. A request may
contain zero or more input roots.
The caller can specify the following input semantics:

### 1. Durable request inputs

A durable request input binds an `a-ref` to an uploaded cache fingerprint. The
fingerprint supplies identity; it does not assert that the uploaded bytes are
still resident when the request is built or accepted.

```python
local_or_durable_builder.include_uploaded_artifact(
    artifact_ref="source/audio/none",
    fingerprint="2f9d...",
)
```

The runtime checks presence only if cache evaluation determines that a stage
must consume the binding. A deleted root fingerprint remains acceptable when
cached downstream artifacts satisfy the request without executing that stage.

### 2. External request inputs (local only)

Callers may provide an externally sourced artifact through an artifact source
handle. This pattern allows a caller to specify an explicit input source, such
as a file on disk or an in-memory object.

```python
local_builder_only.include_input(
    "source/audio",
    SourceAudioArtifact.from_file_path("input.wav"),
)
```

### Expected behavior

#### Valid input types

Inputs may be source, settings, or explicitly supplied intermediate artifacts.
Caller-supplied settings are ordinary input artifacts.

> Note: Settings are strictly parameters that affect artifact output shapes.
> Changing settings invalidates their derived artifacts. Settings therefore
> should not contain credentials, cache backend configuration, runtime-wide
> capacity, or other operational values that do not affect the resulting
> artifacts. These runtime settings are runtime configuration rather than
> request inputs. See
> [RuntimeSettingsBuilder](./01-runtime-builder-surface.md).

#### Input consumption behavior

Providing an input does not guarantee its consumption.

**Why:**

*The planner* works backward from the requested outputs through a graph of
configured stages. As each stage is encountered, the planner resolves inputs by
looking for a candidate producer of the respective artifact. A supplied input
is a terminal candidate; that is, it prevents the planner from needing to
produce that artifact when a requirement in the request-specific graph selects
it. However, because input artifacts are included in the task graph on a
discovery basis, supplying an input does not guarantee that the runtime will
execute a stage with it.

If the planner cannot resolve a required input from a supplied root, an
applicable default, a compatible cache entry, or a producer path, planning
fails. Unselected optional inputs do not make the run fail.

Availability is evaluated after stage-output cache resolution. A supplied root
that would feed only skipped stages is not a required input and need not remain
in cache. If a stage must run, every selected input for that stage must resolve
to readable bytes before execution.

*Stage implementations* determine which inputs are relevant by evaluating their
artifact requirements. They should declare every required input and every
optional input that can improve the requested output so that the planner can
select and bind available inputs consistently.


## Request: Outputs

`outputs` tells the runtime which semantic artifacts the caller wants back and
how each should be materialized.

The caller can specify the following output semantics:

### 1. Durable requested outputs

Durable artifacts can be materialized directly from the cache later by querying
the caching interface. This pattern supports "set-and-forget" integrations,
such as running ASR as a background process, or web services in which upload
and download form a multistep asynchronous process.

All requested outputs are cache-durable by default unless disabled through
configuration. **This is the only retrieval pattern supported by durable
requests.**

```python
local_or_durable_builder.expect_output("render/webvtt_transcript")
```

### 2. A single output target (local only)

Callers may request materialization for any artifact known to the manifest and
materializable to the given target handle. This pattern allows a caller to
specify an explicit external output target, such as a file on disk or an
in-memory object.

```python
local_builder_only.expect_output_target(
    "render/webvtt_transcript",
    TargetVttArtifact.to_file_path("output.vtt"),
)
```

### 3. Multiple output targets (local only)

One artifact may be materialized to multiple caller targets in the same run.
Repeated calls for the same `a-ref` create an ordered fan-out.

```python
some_in_memory_target = InMemoryTargetArtifact()

local_builder_only = (
    local_builder_only
    .expect_output_target(
        "render/webvtt_transcript",
        TargetVttArtifact.to_file_path("output.vtt"),
    )
    .expect_output_target("render/webvtt_transcript", some_in_memory_target)
)
```

### Expected behavior

#### Fan-out rules

- multiple target handles are represented as an ordered list under one artifact
  key;
- list order is semantically meaningful and must be preserved for
  materialization, diagnostics, and cancellation reporting;
- if one target materialization fails, the run fails as a whole while reporting
  which targets succeeded and which failed. Successful target writes are left
  in place; rollback is not promised or attempted.

#### Internal artifact planning behavior

An output can exist at any point in the planned `TaskRunGraph`; however, output
targets are never treated as artifact intermediaries.

*Example:* If an artifact is written to disk and is later an input dependency
for another stage, the later stage does not subsequently read the file from
disk. Instead, an internal `IntermediateArtifactHandle` enables artifact
handover. It is kept in memory between stages and freed only after all dependent
stages are complete.

> *Design comment:* The `IntermediateArtifactHandle` should be the first
> materialization target so that work in progress is not lost if a subsequent
> target fails.

#### Notes on cache hits and materialization

`ChunkyRuntime` optimistically reuses cached artifacts when available. This
means that if a stage's output requirements are already satisfied, the stage is
omitted from execution. In such cases, the runtime MUST still honor the output
materialization requirements associated with the omitted stage.

Two specific cases may require separate handling to ensure materialization:

1. A stage has been omitted, but another stage that has not been omitted requires
   the materialized artifact. In this case, the runtime should use the existing
   cached artifact handle to materialize efficiently from the in-memory cached
   instance. This process is likely identical or comparable to the standard
   process.

2. A stage has been omitted, and there are no other requirements for this
   artifact, most likely because the dependent stage also has cached outputs. In
   this case, the runtime must load the artifact from the cache solely to
   materialize it to the configured output. Materialization order must match the
   order that would apply if the artifact were processed without a cache.

## Request: Cache policy

Both request forms may provide one or more `ArtifactCachePolicy` values and an
optional `TaskCachePolicy`. Artifact policies apply to individual artifacts;
the task policy supplies task-wide defaults. Together they control cache reuse,
publication, and retention.

```text
enum ArtifactCacheBehavior
  INHERITED
  CACHE
  NO_CACHE
  OVERWRITE
  DRY_RUN

ArtifactCachePolicy
  artifact: a-ref
  behavior: ArtifactCacheBehavior = INHERITED
  desired_ttl?: positive-duration

TaskCachePolicy
  behavior: ArtifactCacheBehavior = INHERITED
  max_ttl?: positive-duration
```

### `ArtifactCacheBehavior`

`ArtifactCacheBehavior` defines five ways in which the runtime may interact
with the shared artifact cache:

| Behavior     | Read an existing shared entry | Publish a newly produced entry |
|--------------|------------------------------:|-------------------------------:|
| `INHERITED`  | inherit                       | inherit                        |
| `CACHE`      | yes                           | yes                            |
| `NO_CACHE`   | no                            | no                             |
| `OVERWRITE`  | no                            | yes                            |
| `DRY_RUN`    | yes                           | no                             |

**`ArtifactCacheBehavior::INHERITED`**

Defers to the next applicable configuration layer:

`artifact policy` → `task policy` → `artifact registration default`

**`ArtifactCacheBehavior::CACHE`**

Reuses a valid cached entry when present and publishes a newly produced entry
on a miss.

**`ArtifactCacheBehavior::NO_CACHE`**

Skips both cache lookup and publication.

**`ArtifactCacheBehavior::OVERWRITE`**

Ignores any existing cached entry and publishes a newly produced replacement.

**`ArtifactCacheBehavior::DRY_RUN`**

Reuses a valid cached entry when present but does not publish a newly produced
entry on a miss.

### Example

```python
request = (
    durable_builder
    .include_uploaded_artifact("source/audio/none", "2f9d...")
    .expect_output("render/webvtt_transcript")
    .with_task_cache_policy(
        behavior=ArtifactCacheBehavior.INHERITED,
        max_ttl=timedelta(days=1),
    )
    .with_artifact_cache_policy(
        "audio/prepared",
        ArtifactCachePolicy(behavior=ArtifactCacheBehavior.OVERWRITE),
    )
    .with_artifact_cache_policy(
        "render/webvtt_transcript",
        ArtifactCachePolicy(
            behavior=ArtifactCacheBehavior.CACHE,
            desired_ttl=timedelta(days=1),
        ),
    )
    .build()
)
```

### Policy resolution

#### TTL resolution

TTL is resolved in this order:

- the artifact policy's `desired_ttl`, when present; otherwise
- the greater of the task policy's `max_ttl` and the producing stage's default
  TTL, ignoring either value when absent.

An artifact's TTL SHOULD be refreshed to the resolved value whenever a task
successfully reads it or commits it as an output.

The resolved TTL SHOULD be passed to the cache provider for each refresh. TTL
is best-effort: the provider decides how expiry is represented and enforced and
MAY ignore the request when unsupported. Providers may expose additional hooks
for cleanup.

#### Cache behavior resolution

Cache behavior is resolved in this order:

1. the artifact policy, unless its behavior is `INHERITED` or unstated;
2. the task policy, unless its behavior is `INHERITED` or unstated; then
3. the producing stage's default behavior.

`INHERITED` delegates to the next level; it does not itself select cache read
or publication behavior.

Cache hit, miss, refresh, reservation, publication, retention, and garbage
collection behavior is defined by
[Cache Management](../03-bindings/02-cache-management.md).

## Request: Run policy

Both request forms may contain one optional `RunPolicy`. It controls scheduler
admission for the task's planned stage invocations, primarily to prevent
resource-intensive work, such as local ASR inference, from oversubscribing
available hardware.

Run policy affects execution scheduling only. It does not change graph
planning, producer selection, artifact identity, cache behavior, requested
outputs, or materialization obligations.

### Resource constraints

Each stage invocation declares the resource classes it requires through
`StageConcurrencyPolicy`. The initial classes are:

- `vram`: GPU memory, such as model weights and inference buffers held by a
  local ASR model;
- `ram`: host memory;
- `gpu`: GPU compute capacity;
- `cpu`: CPU compute capacity.

A stage may declare more than one class. Before starting the invocation, the
scheduler must acquire capacity for every declared class and release it when
the invocation stops. An unconstrained stage does not acquire a resource-class
slot.

For the initial MVP, each class is exclusive by default: at most one invocation
that claims the class may run in the task at once. Stages that claim different
classes may run together. A stage claiming both `vram` and `gpu` occupies one
slot in each class.

### Policy model

```text
enum ResourceConstraint
  VRAM
  RAM
  GPU
  CPU

RunPolicy
  prevent_concurrency: bool = False
  constraint_concurrency_limits: map<ResourceConstraint, positive-int>
```

### Example

```python
(
    local_or_durable_builder
    .with_run_policy(
        RunPolicy(
            constraint_concurrency_limits={
                ResourceConstraint.VRAM: 1,
                ResourceConstraint.CPU: 2,
            },
        )
    )
)
```

This request serializes `vram` stages while allowing up to two `cpu` stages to
run concurrently, subject to lower runtime-wide limits. A stage claiming both
classes must acquire capacity from each before it starts.

### Scheduling behavior

`constraint_concurrency_limits` overrides the default limit for individual
resource classes. A limit of `1` serializes invocations that claim the class; a
higher limit permits that many concurrent invocations.

`prevent_concurrency=True` is the simple safe mode. It allows no more than one
stage invocation from the task to run at a time, including unconstrained stages
and stages that use different resource classes. When enabled, it takes
precedence over per-class limits.

### Effective limits and validation

Run policy may lower runtime-wide scheduler capacity but must not raise it.
Runtime configuration remains responsible for physical capacity shared by
concurrent tasks, provider quotas, device affinity, and other long-lived
operational limits. Where both layers specify a limit, the lower value applies.

- policy keys must be known `ResourceConstraint` values;
- policy limits must be positive integers;
- `prevent_concurrency` and per-class limits may be specified together; and
- the resolved policy is fixed when the execution plan is created and must not
  change while the task runs.

Detailed admission and task-control behavior is defined by
[Eventing and Task Control](../07-scheduling/eventing-and-task-control-model-spec.md).

### Open design questions

> Note: This concept is still in its infancy. The current implementation is a
> stopgap solution for a working proof of concept and may become considerably
> more complex over time. This capability could be useful in
> resource-constrained environments. For the kinds of tasks this software will
> perform, most systems would be considered resource-constrained.

- The stage contract currently has both `ResourcePolicy` and
  `StageConcurrencyPolicy`. Their responsibilities must be consolidated or
  distinguished before the scheduling API is finalized.
- The initial model uses whole slots. It does not express quantities such as
  `16 GiB` of VRAM or resource pools spanning multiple devices.
- Fairness, priority, and arbitration of conflicting run-level limits across
  tasks remain unspecified.
- Remote-provider rate limits and stage-specific concurrency keys may need
  first-class constraint types instead of mapping to CPU, GPU, RAM, or VRAM.

## Local request objects

### `LocalRequest`

`LocalRunRequest` is an immutable process-local submission description.
Conceptually:

```text
LocalRunRequest
  inputs: map<canonical-a-ref, ArtifactSourceHandle>
  outputs: map<canonical-a-ref, OutputRequest>

  durable_inputs: list<canonical-a-instance>
  durable_outputs: list<canonical-a-ref>

  run_policy?: RunPolicy
  task_cache_policy?: TaskCachePolicy
  cache_policy: map<canonical-a-ref, ArtifactCachePolicy>

OutputRequest
  targets: ordered-list<ArtifactTargetHandle>
```

### `LocalRequestBuilder`

`LocalRunRequestBuilder` constructs a local request.

```python
builder = LocalRunRequestBuilder()
```

Construction is explicit through the operations below. Builders do not flatten
nested mappings or convert a request into a separate manifest-resolved form.

Its language-neutral interface is:

```text
LocalRunRequestBuilder
  include_input(a-ref, ArtifactSourceHandle) -> self
  include_artifact_instance(a-instance) -> self

  expect_output_target(a-ref, ArtifactTargetHandle) -> self
  expect_output(a-ref) -> self

  with_run_policy(RunPolicy) -> self
  with_task_cache_policy(TaskCachePolicy) -> self
  with_artifact_cache_policy(a-ref, ArtifactCachePolicy) -> self

  build() -> LocalRunRequest
  close() -> void
```

#### Design notes

Bindings may implement fluent methods by mutating and returning the builder or
by returning a new builder value. Either approach must preserve the same
ownership and duplicate-declaration rules.

`with_run_policy` and `with_task_cache_policy` may each be called at most once.
`with_artifact_cache_policy` may be called once for each parsed artifact
reference. Repeated declarations are structural errors rather than
last-write-wins updates.

`build()` performs structural validation and constructs the immutable request.
It does not look up artifact declarations or resolve references against a
manifest.

### Publishing Inputs for a Durable Request

Publishing an external input is an independent durable artifact upload. Each
successful upload returns one cache fingerprint; the caller then constructs the
`DurableRunRequest` by binding that fingerprint to an `a-ref`.
Local requests are never
converted wholesale and local output targets are never carried into durable
requests.

The request remains structurally valid if the root upload is later deleted.
Execution requires its bytes only when downstream cache hits are insufficient
and a selected stage must consume the root.


## Durable request objects

### `DurableRequest`

`DurableRunRequest` is a serialisable durable computation contract. It
contains no external handles, open streams, file descriptors, callbacks,
credentials, backend paths, provider identifiers, runtime-manifest identifier, or delivery destinations.

Its data model is:

```text
DurableRunRequest
  version: 1
  durable_inputs: map<canonical-a-ref, fingerprint>
  durable_outputs: set<canonical-a-ref>
  artifact_retention_ttl?: positive-duration
  run_policy?: RunPolicy
  task_cache_policy?: TaskCachePolicy
  cache_policy: map<canonical-a-ref, ArtifactCachePolicy>
```

The address and identity kinds are intentional:

- every `durable_inputs` entry binds a semantic `a-ref` to an upload
  fingerprint without promising current cache residency;
- every element of `durable_outputs` is a semantic `a-ref` and may retain an
  unresolved phase such as `any`;
- `artifact_retention_ttl`, when present, is the requested lifetime of the
  task's durable artifact claims after the task terminalizes; and
- every cache-policy key is a canonical `a-ref`.

### `DurableRunRequestBuilder`

`DurableRunRequestBuilder` is also standalone and manifest-independent:

```text
DurableRunRequestBuilder
  static from_object(NonCanonicalDurableRunRequest) -> self
  include_uploaded_artifact(a-ref, fingerprint) -> self
  expect_output(a-ref) -> self

  with_artifact_retention_ttl(positive-duration) -> self
  with_run_policy(RunPolicy) -> self
  with_task_cache_policy(TaskCachePolicy) -> self
  with_artifact_cache_policy(a-ref, ArtifactCachePolicy) -> self

  build() -> DurableRunRequest
```

- **`include_uploaded_artifact`** records an `a-ref` and fingerprint but does
  not perform I/O to verify that the cache entry currently exists.
- **`expect_output`** declares one durable semantic output without a local
  target. Repeating either declaration for the same parsed reference is
  invalid.
- **`with_artifact_retention_ttl`** sets the lifetime of the task's durable
  artifact claims after terminalization. If omitted, submission applies the
  artifact-persistence service default.
- **`with_run_policy`** and **`with_task_cache_policy`** may each be called at
  most once.
- **`with_artifact_cache_policy`** may be called once for each parsed artifact
  reference. Repeated declarations are structural errors.

The builder supplies the current schema name and version automatically. Callers
cannot override either field through normal construction. The builder does not
access artifact persistence to prove that an instance exists and does not
inspect a manifest to prove that an output can be produced.

**Example:**

```python
durable_request = (
    DurableRunRequestBuilder()
    .include_uploaded_artifact("source/audio/none", audio_fingerprint)
    .expect_output("render/webvtt_transcript")
    .with_artifact_retention_ttl(timedelta(days=1))
    .with_artifact_cache_policy(
        "render/webvtt_transcript",
        ArtifactCachePolicy(
            behavior=ArtifactCacheBehavior.OVERWRITE,
            desired_ttl=timedelta(days=1),
        ),
    )
    .build()
)

receipt = await durable_task_service.submit(durable_request)
```

#### Example Upload and Start

Upload publishes opaque bytes and returns their cache fingerprint. The short
upload-grace TTL protects the root while the caller constructs and submits the
request. A task claim replaces that grace only when execution actually requires
the root.

```python
upload = await runtime.artifacts.upload(
    path="input.wav",
    desired_grace_ttl=timedelta(hours=2),
    desired_retention_ttl=timedelta(hours=6),
)

request = (
    DurableRunRequestBuilder()
    .include_uploaded_artifact("source/audio/none", upload.fingerprint)
    .expect_output("render/webvtt_transcript")
    .with_artifact_retention_ttl(timedelta(days=1))
    .build()
)

result = await runtime.run(request)
```

### `NonCanonicalDurableRunRequest`

`NonCanonicalDurableRunRequest` is a non-canonical, serializable, fault-tolerant
contract. It has the same elements as `DurableRunRequest` but does not guarantee
that locators are in canonical form. This object provides a flexible
serialisation contract for externally sourced tasks such as web requests.

```text
NonCanonicalDurableRunRequest
  version: 1
  durable_inputs: map<a-ref, fingerprint>
  durable_outputs: set<a-ref>
  artifact_retention_ttl?: positive-duration
  run_policy?: RunPolicy
  task_cache_policy?: NonCanonicalTaskCachePolicy
  cache_policy: map<a-ref, NonCanonicalArtifactCachePolicy>

NonCanonicalArtifactCachePolicy
  artifact: a-ref
  behavior: ArtifactCacheBehavior = INHERITED
  desired_ttl?: positive-duration

NonCanonicalTaskCachePolicy
  behavior: ArtifactCacheBehavior = INHERITED
  max_ttl?: positive-duration
```

#### JSON encoding example

```json
{
  "version": 1,
  "inputs": [
    "source/audio/none/fingerprint/2f9d...",
    "render/webvtt_settings/none/fingerprint/81aa..."
  ],
  "outputs": [
    "render/webvtt_transcript/any"
  ],
  "cache_policy": {
    "render/webvtt_transcript": {
      "behavior": "OVERWRITE",
      "desired_ttl_seconds": 86400
    }
  }
}
```

**Example usage:**

```python
external_request = json.loads(request_json_string)
canonical_request = DurableRunRequestBuilder.from_object(external_request).build()
```

## Build validation and ownership

Both builders accumulate component-level structural issues and return them
together from `build()`.
> Issues whose prerequisites failed are omitted to avoid misleading cascades.

`from_object(...)` separately validates the serialized envelope shape, field shapes, and version before returning a builder. 
> If deserialization fails, `from_object(...)` immediately returns one `RequestValidationFailure` containing all independently detectable deserialization issues;.

The platform-universal envelope is:

```text
RequestValidationFailure
  kind: REQUEST_VALIDATION
  message: human-readable summary string
  version: 1
  issues: ordered-list<RequestValidationIssue>

RequestValidationIssue
  code: stable machine-readable category string
  message: human-readable explanation string
  context_type:
    UnableToDeserializeRequest
    | UnableToBuildRunPolicyRequest
    | UnableToBuildCachePolicyRequest
    | UnableToBuildArtifactRetentionRequest
    | UnableToBuildDurableInputsRequest
    | UnableToBuildDurableOutputsRequest
    | UnableToBuildInputsRequest
    | UnableToBuildOutputsRequest
  context: object selected by context_type

UnableToDeserializeRequest
  violation: violation_message
  field_path: json_path_string

UnableToBuildRunPolicyRequest
  field?: prevent_concurrency | constraint_concurrency_limits
  resource_constraint?: canonical ResourceConstraint or original supplied string

UnableToBuildCachePolicyRequest
  scope: TASK | ARTIFACT
  artifact_reference?: canonical a-ref or original supplied string
  field?: behavior | desired_ttl | max_ttl

UnableToBuildArtifactRetentionRequest
  field: artifact_retention_ttl

UnableToBuildDurableInputsRequest
  instance_reference?: original supplied string

UnableToBuildDurableOutputsRequest
  artifact_reference?: original supplied string

UnableToBuildInputsRequest
  artifact_reference?: original supplied string

UnableToBuildOutputsRequest
  artifact_reference?: original supplied string
  target_index?: nonnegative-index
```

`context_type` identifies the request component that could not be built. `UnableToBuildCachePolicyRequest`
is shared by task and artifact cache policies; `scope` distinguishes them.
`UnableToDeserializeRequest` is reserved for envelope or field-shape failures
returned directly by `from_object(...)`, before a request component can be
interpreted. A successfully returned builder never contains this context type.

Context fields are included only when relevant. Declaration indexes use
zero-based caller declaration order. References are canonical when parsing
succeeded and otherwise preserve the original supplied string. Context never
contains an external artifact handle, target handle, raw request body, parsed
runtime object, or exception.

The complete failure MUST have a lossless, data-only representation usable both
as an in-process result and across transport boundaries such as a web-service
response. It MUST NOT contain or require access to builders, runtimes, binding
runtimes, exceptions, parsed artifact objects, handles, callbacks, or other
process-local state. Bindings may surface the failure as an exception or result
value, but must expose the same serializable data. This specification does not
prescribe a wire encoding.

For example:

```json
{
  "kind": "REQUEST_VALIDATION",
  "message": "Unable build invalid task request",
  "version": 1,
  "issues": [
    {
      "code": "mixed_output_form",
      "message": "Output already has a targetless declaration.",
      "context_type": "UnableToBuildOutputsRequest",
      "context": {
        "artifact_reference": "render/webvtt_transcript",
      }
    }
  ]
}
```

Issue order is deterministic. The following list is the complete set of
validation codes. `from_object(...)` returns deserialization issues immediately;
`build()` returns all accumulated component issues.

**`from_object` (`UnableToDeserializeRequest`):**
- *`invalid_request_shape`*: the request or a required field has the wrong
  structural type, or a required field is absent;
- *`unsupported_request_version`*: `version` is not supported.

These issues originate from and are returned by `from_object(...)`. If
deserialization succeeds, errors in contained artifact instances, outputs, or
policies use the corresponding component context below and are returned by
`build()`.

**`include_input` (`UnableToBuildInputsRequest`):**
- *`invalid_artifact_reference`*: the `a-ref` cannot be parsed; and
- *`duplicate_input`*: another input resolves to the same parsed artifact
  reference.

**`include_uploaded_artifact` (`UnableToBuildDurableInputsRequest`):**
- *`invalid_artifact_reference`*: the `a-ref` cannot be parsed;
- *`invalid_fingerprint`*: the fingerprint is structurally invalid; and
- *`duplicate_input`*: another input resolves to the same parsed artifact
  reference.

**`expect_output` (`UnableToBuildDurableOutputsRequest`):**
- *`invalid_artifact_reference`*: the `a-ref` cannot be parsed;
- *`mixed_output_form`*: the same parsed output has a targeted declaration.

**`expect_output_target` (`UnableToBuildOutputsRequest`):**
- *`invalid_artifact_reference`*: the `a-ref` cannot be parsed; and
- *`mixed_output_form`*: the same parsed output has a targetless declaration. Repeated targeted declarations are valid and create ordered fan-out.

**`with_run_policy` (`UnableToBuildRunPolicyRequest`):**
- *`duplicate_run_policy`*: a run policy was already declared;
- *`invalid_resource_constraint`*: a concurrency-limit key is not a known
  `ResourceConstraint`; and
- *`invalid_concurrency_limit`*: a concurrency limit is not a positive integer (language dependent).

**`with_artifact_retention_ttl` (`UnableToBuildArtifactRetentionRequest`):**
- *`duplicate_artifact_retention_ttl`*: a retention TTL was already declared;
  and
- *`invalid_ttl`*: the supplied TTL is not a positive duration.

**`with_task_cache_policy` (`UnableToBuildCachePolicyRequest`, `TASK`):**
- *`duplicate_task_cache_policy`*: a task cache policy was already declared;
- *`invalid_cache_behavior`*: the behavior is not a known
  `ArtifactCacheBehavior`;
- *`invalid_ttl`*: a supplied TTL is not a positive duration;  (language dependent).
- *`ttl_requires_writing_cache`*: request-level policy resolution attaches a
  TTL to `NO_CACHE` or `DRY_RUN`.

**`with_artifact_cache_policy` (`UnableToBuildCachePolicyRequest`, `ARTIFACT`):**
- *`invalid_artifact_reference`*: the supplied `a-ref` cannot be parsed;
- *`duplicate_artifact_cache_policy`*: a policy already exists for the same parsed artifact reference;
- *`invalid_cache_behavior`*: the behavior is not a known`ArtifactCacheBehavior`;
- `invalid_ttl`: a supplied TTL is not a positive duration; and
- `ttl_requires_writing_cache`: request-level policy resolution attaches a
  TTL to `NO_CACHE` or `DRY_RUN`.

**`build`:**
- *`no_outputs`*: no output was requested; and
- *`targetless_output_requires_cache_write`* (local only): request-level policy
  resolution gives a targetless local output `NO_CACHE` or `DRY_RUN`.

For `build`, `no_outputs` uses `UnableToBuildOutputsRequest` for a local builder
and `UnableToBuildDurableOutputsRequest` for a durable builder.
`targetless_output_requires_cache_write` uses
`UnableToBuildOutputsRequest`. Durable outputs are published through artifact
persistence independently of cache policy.

**`close`:**
no request-validation errors.

`from_object` and `with_artifact_retention_ttl` are durable-only;
`include_input`, `expect_output_target`, and `close` are local-only. Other
operations apply to both builders.

A value that cannot be converted to a binding method's parameter type may fail
at the call boundary before builder validation. Calls on a consumed builder are
also binding-level lifecycle errors; neither case produces a
`RequestValidationFailure`.

Unknown artifacts, target/declaration compatibility, declaration cacheability,
provider capability, phased-family matching, and graph support require a frozen
runtime and are not builder errors.

A phased artifact selector MUST NOT be converted into an exact match during
request construction. The builder has no family catalog with which to select a
concrete phase, so it preserves the parsed selector in the request. Exact-phase
validation, `AT_MOST` selection, and terminal phase-less remapping are owned by
planning, as defined in
[Phased intermediate references](../02-artifacts/01-artifact-model-surface.md#phased-intermediate-references).

### Ownership
Adding an external source or target handle transfers ownership to the builder. A
successful `build()` consumes the builder and transfers every handle into the
immutable `LocalRunRequest`. A failed build leaves the builder and its handles
valid so the caller may correct structural errors and retry.

`close()` consumes an unbuilt builder and closes or aborts every handle it still
owns. Dropping an unbuilt builder must provide equivalent cleanup in languages
with deterministic destruction. Bindings without enforceable move semantics
must reject mutation after successful build and reject reuse of a consumed
request.

Once local execution begins, the runtime owns request cleanup across binding,
planning, execution, cancellation, failure, and materialization.

### Operational validation boundary

Construction and deserialization establish structural validity. The consuming
runtime or durable task service separately validates facts that require current
configuration or I/O:

- referenced inputs exist, are readable, and match durable semantic and
  representation metadata;
- Local targets are compatible with current artifact declarations;
- the provider supports the durability required by the operation;
- output and policy references are interpreted against the current frozen
  manifest; and
- planning determines whether the requested graph can be satisfied.

Direct durable submission rejects malformed data, unusable input instances,
and insufficient provider durability before task creation. A structurally valid
direct request whose output is unknown, non-cacheable, or unsupported by the
current graph may be accepted and terminate as `UNPLANNABLE` without starting a
stage.

## Invariants

1. `LocalRunRequest` and `DurableRunRequest` have separate standalone builders.
2. Builders perform syntactic parsing and structural validation but no I/O,
   manifest inspection, or artifact resolution.
3. A request may have zero explicit inputs and must have at least one output.
4. External source and target handles transfer ownership into the builder,
   request, and execution lifecycle.
5. Local target order is significant; canonical collection order is not.
6. Supplied inputs are authoritative when selected and are never implicitly
   published by local execution.
7. `DurableRunRequest` contains only durable semantic data and no delivery
   attachments.
8. Canonical construction and deserialization share structural validation.
9. Cache policy cannot make a supplied input optional or change graph shape.
10. `RunPolicy` cannot change semantic work.
11. Durable inputs bind semantic `a-ref` locators to upload fingerprints;
    current cache presence is required only for inputs consumed by stages that
    execute. Durable outputs are targetless semantic `a-ref` locators.
12. External inputs are uploaded independently; there is no whole-request
    local-to-durable conversion operation.
13. Duplicate declarations are detected after canonical parsing and are never
    resolved using last-write-wins behavior.
14. Request construction preserves unresolved phased artifact selectors;
    planning alone resolves them against a frozen manifest.
15. A successful build consumes the builder, while a failed build preserves the
    builder and all handles it owns for correction or cleanup.
16. Task artifact retention is distinct from cache retention and begins when
    the durable task terminalizes.
