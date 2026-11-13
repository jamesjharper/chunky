# Failure Contract

## Purpose

This specification defines the universal, data-only failure envelope used by
Chunky.

Every failure produced by Chunky conforms to this contract, including request
validation, durable submission rejection, service lookup and availability
failures, planning and recovery incompatibility, and terminal run failure.

Bindings may expose the envelope as an exception, error return, or result
variant. They MUST preserve the same lossless data. Cancellation and transport
errors originating in the caller's language runtime or transport library may
remain native when Chunky did not produce them.

## Ownership

This document owns:

- the root `Failure`, `FailureIssue`, and `RetryDirective` structures;
- the global issue-code namespace and universal issue codes;
- retry-directive semantics;
- envelope versioning and serialization invariants;
- deterministic aggregation requirements; and
- binding preservation requirements.

Module specifications own the members they produce:

- allowed failure `kind` values;
- domain-specific issue `code` values that do not duplicate a universal or
  previously registered condition;
- allowed `context_type` values and exact context schemas;
- retryability and any minimum retry delay for each issue it may emit; and
- module-specific ordering and redaction rules.

Module specifications MUST reference this contract instead of redefining the
root envelope. The profiles below register the members currently represented
by the WIT interface. Their source specifications continue to own the
operations, validation boundaries, causes, and retry rules that produce them.

## Envelope

The language-neutral shape is:

```text
Failure
  kind: stable-category
  message: human-readable-string
  version: positive-integer
  issues: ordered nonempty list<FailureIssue>
  retry: RetryDirective

FailureIssue
  code: stable-machine-readable-code
  message: human-readable-string
  context_type: stable-context-category
  context: object selected by context_type

RetryDirective
  retryable: boolean
  retry_after_milliseconds?: positive-integer
```

Version `1` is the initial envelope version.

`kind` identifies the broad domain member. Version `1` currently permits
`REQUEST_VALIDATION` and `ARTIFACT_UPLOAD`, corresponding to the WIT
`request-validation` and `artifact-upload` cases. The specification that
produces a failure owns its allowed issue `code`, `context_type`, and typed
`context` payloads.

The root `message` summarizes the failure for a person. Issue messages explain
individual causes. Messages are diagnostic text and MUST NOT be parsed for
control flow; `kind`, `version`, `code`, `context_type`, and `retry` are the
stable machine-readable fields.

## Global issue-code namespace

All `FailureIssue.code` values share one global namespace. A code has one stable
meaning across every failure `kind`, binding, and implementation. The `kind`
field provides broad domain grouping but is not part of the code's identity.

A code may be reused by multiple modules only when the condition has the same
meaning everywhere. Shared conditions use the shortest stable owner in their
name; module-specific conditions use a registered domain prefix such as
`PRINCIPAL_`, `ARTIFACT_STORAGE_`, or `ARTIFACT_UPLOAD_`. Modules MUST NOT
introduce a second code for an existing condition merely to restate the calling
operation. When the condition or required caller response differs materially,
it receives a distinct, appropriately scoped code.

This contract owns the universal `OPERATION_CANCELLED` code, which may appear
under any failure kind and means that Chunky observed cancellation before the
call returned its ordinary result.

These codes describe why the call stopped, not whether repeating it is safe.
The producing operation defines their issue-level retry behavior and documents
any idempotency, status-recovery, or ambiguous-outcome requirements.
Cancellation or deadlines originating only in the caller's runtime or
transport remain native as described under **Purpose** and do not produce these
issue codes.

## Retry directive

`retry` describes whether the failed call may be attempted again. It is the
aggregate of the retry behavior of every emitted `FailureIssue` and applies to
the complete failure. Issue-level retry behavior is defined by the specification
that owns the issue and is not serialized as additional `FailureIssue` fields;
callers consume the aggregate root directive instead of combining per-issue
advice.

- `retryable: false` means the same call MUST NOT be retried without changing
  the relevant request, authorization, configuration, or operation state.
- `retryable: true` means the owning operation permits another attempt. It does
  not guarantee success or replace operation-specific idempotency,
  status-recovery, or ambiguous-outcome rules.
- `retry_after_milliseconds`, when present, is the minimum duration the caller
  should wait after receiving the failure before retrying. The caller may wait
  longer because of local backoff, jitter, rate limits, or cancellation.

`retry_after_milliseconds` is present only when `retryable` is `true`. When it
is absent from a retryable failure, this contract prescribes no minimum delay
and the caller applies its own retry policy. Bindings may expose a countdown or
an absolute local retry time derived from this duration, but those derived
values are not part of the canonical failure envelope.

For example:

```json
{
  "retry": {
    "retryable": true,
    "retry_after_milliseconds": 1000
  }
}
```

The producer MUST derive the root directive from all emitted issues as follows:

- `retryable` is `false` if any issue is non-retryable, and is `true` only if
  every issue is retryable.
- When `retryable` is `false`, `retry_after_milliseconds` MUST be absent.
- When `retryable` is `true`, `retry_after_milliseconds` is the largest minimum
  retry delay specified by any emitted issue. It is absent only when none of the
  emitted issues specifies a minimum retry delay.

## Issue aggregation and context

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
- preserve the complete retry directive;
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
- context that does not match its selected `context_type`;
- a retry directive with the wrong field types, unknown fields, a nonpositive
  `retry_after_milliseconds`, or a delay when `retryable` is `false`; and
- violations of configured document, collection, string, or nesting limits.

This contract defines the object model, not one transport encoding. A transport
may use JSON, MessagePack, or another encoding if it preserves the canonical
data and validation behavior.

## Binding behavior

Bindings MAY define typed exception or result classes for particular failure
kinds. Every such wrapper MUST expose the complete `Failure` value.

A binding MAY expose `retry_after_milliseconds` through a native retry facility,
such as an HTTP `Retry-After` header, and MAY provide a locally decreasing
countdown. It MUST preserve the canonical retry directive and MUST NOT make a
non-retryable failure appear retryable. Converting milliseconds to a coarser
native duration MUST round up so the advertised minimum delay is not shortened.

Bindings MUST NOT:

- replace machine-readable codes with exception text;
- discard independently reported issues;
- reorder issues;
- require exception inspection to access structured context; or
- serialize language-specific exception details as part of the canonical
  envelope.

## Registered cross-cutting issue contexts

The WIT `failure-issue-context` variant has five outer cases: `general`,
`request-validation`, `principal`, `artifact-storage`, and `artifact-upload`.
The failure `kind` identifies the failed operation's domain; it does not require
every issue in that failure to use the matching context case. Each issue code
selects exactly one allowed outer case and one context alternative within it.
The inner alternative is the WIT representation of `context_type`, and its
payload is `context`; the string field and selected variant MUST agree.

`QuotaContext` is shared by principal, artifact-storage, and artifact-upload
issues:

```text
QuotaContext
  quota_scope: string
```

The registered cross-cutting alternatives and codes are:

- `general.operation`: `OPERATION_CANCELLED`, with no additional context;
- `general.transfer_source`: `TRANSFER_SOURCE_INVALID` or
  `TRANSFER_SOURCE_EXHAUSTED`, with no additional context;
- `principal.permission`:
  `PRINCIPAL_MISSING_PERMISSIONS_FOR_ARTIFACT_PUBLICATION` or
  `PRINCIPAL_MISSING_PERMISSIONS_FOR_LIVE_CAPTURE`, with no additional
  context;
- `principal.artifact_storage_quota`:
  `PRINCIPAL_ARTIFACT_STORAGE_QUOTA_EXCEEDED`, carrying `QuotaContext`;
- `artifact_storage.provider_capacity`:
  `ARTIFACT_STORAGE_PROVIDER_CAPACITY_EXCEEDED`, with no additional context;
- `artifact_storage.quota`: `ARTIFACT_STORAGE_QUOTA_EXCEEDED`, carrying
  `QuotaContext`; and
- `artifact_storage.storage_operation`: `ARTIFACT_STORAGE_FAILURE`, with no
  additional context.

The artifact-upload source specification owns all cross-cutting codes in this
list except `OPERATION_CANCELLED`.

## Request-validation profile

Request builders produce `REQUEST_VALIDATION` failures through the universal
envelope.

The primary bounded contexts are:

```text
UnableToDeserializeRequest
  violation
  field_path

UnableToBuildRunPolicyRequest
  field?: prevent_concurrency | constraint_concurrency_limits | publication_mode
  resource_constraint?

UnableToBuildCachePolicyRequest
  scope: TASK | ARTIFACT
  scoped_request?
  field?: behavior | desired_ttl | max_ttl

UnableToBuildArtifactRetentionRequest
  field: artifact_retention_ttl

UnableToBuildDurableInputsRequest
  scoped_request?
  member_ordinal?
  content_id?

UnableToBuildDurableOutputsRequest
  scoped_request?

UnableToBuildInputsRequest
  scoped_request?

UnableToBuildOutputsRequest
  scoped_request?
  target_index?
```

Builders aggregate independently detectable structural issues and omit
misleading cascades.

Stable request-validation conditions include:

- `invalid_request_shape`;
- `unsupported_request_version`;
- `invalid_scoped_artifact_request`;
- `invalid_content_id`;
- `scoped_request_too_short`;
- `empty_supplied_binding`;
- `invalid_member_ordinal`;
- `duplicate_input_binding`;
- `duplicate_output_request`;
- `mixed_output_form`;
- `duplicate_run_policy`;
- `invalid_publication_mode`;
- `invalid_resource_constraint`;
- `invalid_concurrency_limit`;
- `duplicate_artifact_retention_ttl`;
- `duplicate_task_cache_policy`;
- `duplicate_artifact_cache_policy`;
- `invalid_cache_behavior`;
- `invalid_ttl`;
- `ttl_requires_writing_cache`;
- `no_outputs`; and
- `targetless_output_requires_cache_write` where applicable to local targetless
  output semantics.

`ttl_requires_writing_cache` applies when a cache TTL is attached to behavior
that never publishes shared cache state, such as `NO_CACHE` or `READ_ONLY` on a
miss according to the owning policy contract.

Unknown artifact families, exact schema resolution, scope applicability,
unscoped-output ambiguity, processing-order selection, output capability/cardinality compatibility,
provider capability, and durable content presence require a frozen runtime and
are not pure builder errors.

Durable task submission failures caused by supplied content being missing,
unreadable, unauthorized, expired, or unclaimable occur at atomic acceptance,
not builder validation. Stable codes are
`SUPPLIED_CONTENT_MISSING`, `SUPPLIED_CONTENT_UNREADABLE`,
`SUPPLIED_CONTENT_UNAUTHORIZED`, and `SUPPLIED_CONTENT_UNCLAIMABLE`; context
contains the canonical scoped request, member ordinal, and `ContentId`. These
failures create no task and no partial protection or grace-claim mutation.

## Artifact-upload profile

Durable content upload continues to use broad failure kind `ARTIFACT_UPLOAD` for
compatibility with the failure taxonomy. The operation publishes content rather
than a semantic artifact instance.

The bounded upload contexts include:

```text
ArtifactUploadSessionIdentity
  multipart_stream: opaque-string
  | live_capture: opaque-string

ArtifactUploadSessionContext
  session

ArtifactUploadRejectedOptionContext
  option_name

ArtifactUploadByteLengthContext
  expected_byte_length
  observed_byte_length

ArtifactUploadSizeLimitContext
  max_byte_length

ArtifactUploadCaptureProgressContext
  session?
  received_byte_length

ArtifactUploadInactivityTimeoutContext
  requested_inactivity_timeout_milliseconds?
  effective_inactivity_timeout_milliseconds

ArtifactUploadTerminalStateContext
  session?
  terminal_state
```

Stable upload conditions include:

- `ARTIFACT_UPLOAD_SIZE_LIMIT_EXCEEDED`;
- `ARTIFACT_UPLOAD_STORAGE_PROVIDER_CAPABILITY_UNSUPPORTED`;
- `ARTIFACT_UPLOAD_LENGTH_MISMATCH`;
- `ARTIFACT_UPLOAD_DIGEST_MISMATCH`;
- `ARTIFACT_UPLOAD_DIGEST_UNSUPPORTED`;
- `ARTIFACT_UPLOAD_CONTENT_ID_INVALID`;
- `ARTIFACT_UPLOAD_CONTENT_ID_NOT_TRUSTED`;
- `ARTIFACT_UPLOAD_FINALIZATION_AMBIGUOUS`;
- `ARTIFACT_UPLOAD_INPUT_FORM_UNSUPPORTED`;
- `ARTIFACT_UPLOAD_NOT_FOUND`;
- `ARTIFACT_UPLOAD_TIMED_OUT`;
- `ARTIFACT_UPLOAD_ABORTED`;
- `ARTIFACT_UPLOAD_STATE_INVALID`;
- `ARTIFACT_UPLOAD_SESSION_OWNERSHIP_INVALID`;
- `ARTIFACT_UPLOAD_QUOTA_EXCEEDED`;
- `ARTIFACT_UPLOAD_PART_INVALID`;
- `ARTIFACT_UPLOAD_MULTIPART_SIZE_LIMIT_EXCEEDED`;
- `ARTIFACT_UPLOAD_PART_LENGTH_MISMATCH`;
- `ARTIFACT_UPLOAD_PART_DIGEST_MISMATCH`;
- `ARTIFACT_UPLOAD_PART_CONFLICT`;
- `ARTIFACT_UPLOAD_PART_SET_INCOMPLETE`;
- `ARTIFACT_UPLOAD_OPTIONS_INVALID`;
- `ARTIFACT_UPLOAD_LIVE_CAPTURE_STATE_INVALID`;
- `ARTIFACT_UPLOAD_LIVE_CAPTURE_APPEND_ALREADY_IN_PROGRESS`;
- `ARTIFACT_UPLOAD_ALREADY_SEALED`; and
- `ARTIFACT_UPLOAD_INCOMPLETE`.

The removed caller-assigned upload fingerprint feature has no
`ARTIFACT_UPLOAD_FINGERPRINT_*` errors in the revised contract.

## Durable task acceptance and artifact retrieval

The owning run/persistence/download specifications may use failure kinds such as
`TASK_SUBMISSION`, `TASK_CONTROL`, `ARTIFACT_PERSISTENCE`, and
`ARTIFACT_DOWNLOAD` according to the implementation's registered taxonomy.
Their issue codes must remain globally unique and use the same root envelope.

Important redesigned conditions include:

- malformed `ContentId` or canonical `ScopedArtifactRequest`;
- supplied durable content missing, unreadable, unauthorized, expired, or
  unclaimable at atomic task acceptance;
- incompatible exact artifact schema or scoped binding;
- output publication/cardinality failure;
- content not retained;
- content corruption;
- invalid/unsupported byte range;
- provider unavailable/capability unsupported;
- `OPERATION_CANCELLED`; and
- `OPERATION_DEADLINE_EXCEEDED`.

A task-artifact binding that is `PENDING`, `PRODUCING`, `FAILED`, `CANCELLED`, or
`EXPIRED` is normally an expected binding state rather than a malformed call.
Raw content download uses operation failures when the requested `ContentId` cannot be
opened under the applicable durable/service retention scope.

## Invariants

1. Every Chunky-originated failure has one versioned, lossless, data-only
   envelope.
2. Every envelope contains at least one issue.
3. Stable categorical fields, not messages, drive program behavior.
4. Typed issue context is the sole detailed cause-correlation mechanism.
5. Issue order is deterministic and independent of execution races.
6. No canonical failure contains process-local state, raw artifact bytes, or
   secrets.
7. Retryability is explicit for every failure and applies to the complete failed
   call.
8. The redesigned artifact model never reuses the removed semantic upload
   fingerprint errors for `ContentId` or provenance.
