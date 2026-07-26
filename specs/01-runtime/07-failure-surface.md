# Failure Contract

<------------>
some how incorporate


Both builders accumulate component-level structural issues and return them
together from `build()`.
> Issues whose prerequisites failed are omitted to avoid misleading cascades.

`from_object(...)` separately validates the serialized envelope shape, field
shapes, and version before returning a builder.
> If deserialization fails, `from_object(...)` immediately returns one
> `RequestValidationFailure` containing all independently detectable
> deserialization issues.

This failure surface owns the root envelope, versioning, serialization, and
binding invariants.
Request building owns its `REQUEST_VALIDATION` member and the following issue
contexts:

```text
Failure
  kind: REQUEST_VALIDATION
  version: 1
  issues: ordered-list<RequestValidationIssue>

RequestValidationIssue
  code: stable machine-readable category string
  message: human-readable explanation string
  context_type:
    UnableToDeserializeRequest
    | UnableToBuildRunPolicyRequest
    | UnableToBuildCachePolicyRequest
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
deserialization succeeds, errors in contained cached inputs, outputs, or
policies use the corresponding component context below and are returned by
`build()`.

**`include_input` (`UnableToBuildInputsRequest`):**
- *`invalid_artifact_reference`*: the `a-ref` cannot be parsed; and
- *`duplicate_input`*: another input resolves to the same parsed artifact
  reference.

**`include_cached_input` (`UnableToBuildDurableInputsRequest`):**
- *`invalid_artifact_instance`*: the locator cannot be parsed as an
  `a-instance`; and
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
- *`targetless_output_requires_cache_write`*: request-level policy resolution
  gives a targetless output `NO_CACHE` or `DRY_RUN`.

For `build`, `no_outputs` uses `UnableToBuildOutputsRequest` for a local builder
and `UnableToBuildDurableOutputsRequest` for a durable builder.
`targetless_output_requires_cache_write` uses
`UnableToBuildDurableOutputsRequest`.

**`close`:**
no request-validation errors.

`from_object` is durable-only; `include_input`, `expect_output_target`, and
`close` are local-only. Other operations apply to both builders.

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
[Artifact Model](../02-artifacts/01-artifact-model-surface.md).
<------------>

## Purpose

This specification defines the universal data-only failure envelope used by
Chunky.

Every failure produced by Chunky conforms to this contract, including request
validation, durable submission rejection, service lookup and availability
failures, planning and recovery incompatibility, and terminal run failure.

Bindings may expose the envelope as an exception, error return, or result
variant. They MUST preserve the same lossless data. Cancellation and transport
errors originating in the caller's language runtime or transport library may
remain native when Chunky did not produce them.

## Envelope

The language-neutral shape is:

```text
Failure
  kind: stable-category
  message: human-readable-string
  version: positive-integer
  issues: ordered nonempty list<FailureIssue>

FailureIssue
  code: stable-machine-readable-code
  message: human-readable-string
  context_type: stable-context-category
  context: object selected by context_type
```

Version `1` is the initial envelope version.

`kind` identifies the broad domain member, such as `REQUEST_VALIDATION`,
`SUBMISSION_REJECTED`, `TASK_LOOKUP`, `SERVICE_UNAVAILABLE`, or `RUN_FAILURE`.
The specification that produces a failure owns its allowed `kind`, issue
`code`, `context_type`, and typed `context` payloads.

The root `message` summarizes the failure for a person. Issue messages explain
individual causes. Messages are diagnostic text and MUST NOT be parsed for
control flow; `kind`, `version`, `code`, and `context_type` are the stable
machine-readable fields.

## Issues and context

One envelope aggregates all independently reportable issues known at the
failure boundary. Its `issues` list is deterministic:

- validation issues follow the owning contract's canonical declaration order;
- graph, stage, artifact, output, and target issues use stable canonical
  identity order defined by their owner; and
- a causal issue precedes issues that merely report consequences of it.

An owner MUST omit misleading cascade issues whose prerequisites failed. It
MUST NOT use nondeterministic scheduler completion order as public issue order.

`context_type` selects the exact schema of `context`. Context carries any
stage, artifact, requested-output, target, request-field, service, or task
association needed to interpret the issue. Public contracts MUST NOT add
parallel error-ID correlation fields to output or target records.

Context MUST be data-only and limited to information appropriate for the
failure boundary. It MUST NOT contain:

- live handles, callbacks, clients, builders, or runtime objects;
- exceptions or language-specific stack objects;
- credentials, secrets, or authorization tokens;
- raw external source or target contents; or
- an unrestricted copy of a rejected request.

An owner may define explicitly bounded diagnostic excerpts when required, but
their redaction and size limits are part of that owner's public contract.

## Serialization

The envelope has a lossless canonical object representation. A serializer MUST:

- emit all required fields;
- preserve issue order;
- emit only fields defined by the selected envelope version, `kind`, and
  `context_type`; and
- represent every context using data values that can cross process and language
  boundaries.

A deserializer MUST reject:

- missing required fields;
- a nonpositive or unsupported version;
- unknown root fields;
- an unknown `kind`, issue `code`, or `context_type` for the selected version
  and consuming surface;
- context that does not match its selected `context_type`; and
- violations of configured document, collection, string, or nesting limits.

This contract defines the object model, not one transport encoding. A transport
may use JSON, MessagePack, or another encoding if it preserves the canonical
data and validation behavior.

## Binding behavior

Bindings MAY define typed exception or result classes for particular failure
kinds. Every such wrapper MUST expose the complete `Failure` value.

Bindings MUST NOT:

- replace machine-readable codes with exception text;
- discard independently reported issues;
- reorder issues;
- require exception inspection to access structured context; or
- serialize language-specific exception details as part of the canonical
  envelope.

Task-outcome failures appear in the task's domain state rather than being
reclassified as call-level or transport failures. For example:

- durable submission rejection before acceptance is a call-level `Failure`;
- a known accepted run that fails is `RunState.Failed(RunResult)` with one
  aggregated `Failure`; and
- an unknown task ID is a call-level `TASK_LOOKUP` failure.

## Ownership

This document owns:

- the root `Failure` and `FailureIssue` structure;
- envelope versioning and serialization invariants;
- deterministic aggregation requirements; and
- binding preservation requirements.

Module specifications own only the members they produce:

- allowed failure `kind` values;
- allowed issue `code` values;
- allowed `context_type` values and exact context schemas; and
- module-specific ordering and redaction rules.

Module specifications MUST reference this contract instead of redefining the
root envelope.

## Invariants

1. Every Chunky-originated failure has one versioned, lossless, data-only
   envelope.
2. Every envelope contains at least one issue.
3. Stable categorical fields, not messages, drive program behavior.
4. Typed issue context is the sole detailed cause-correlation mechanism.
5. Issue order is deterministic and independent of execution races.
6. No canonical failure contains process-local state or secrets.
