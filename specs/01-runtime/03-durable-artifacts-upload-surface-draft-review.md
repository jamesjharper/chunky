# Durable artifacts upload surface drift review

## Scope and authority used for this comparison

Reviewed:

- `03-durable-artifacts-upload-surface.md`
- `03-durable-artifacts-upload-surface.wit`
- `artifact-binding-redesign-handover-prompt.md`
- `02-artifacts/01-artifact-model-surface.md`
- `Chunky-artifact-binding-redesign-Q1-Q139.md`

> [!NOTE]
> This review predates the later clarification that the old caller-provided
> `fingerprint` and `expected_content_digest` were intentionally separate. The
> active upload specs now preserve those concepts as a policy-controlled
> caller-provided `ContentId` and an independent integrity digest. Findings below
> that call for collapsing them into one verified `ContentId` are historical.

The Q1-Q139 document declares itself historical rather than normative at lines
5-7. Its post-Q139 reconciliation says that it governs where it conflicts with
the earlier interview answers (lines 845-849). Consequently, old terminology in
the historical Q1-Q139 answers is not counted as active drift. The reconciliation,
the artifact model, and the handover's established decisions are used as the
redesign baseline.

## Executive finding

The upload WIT is mostly on the redesigned surface. The upload Markdown is not:
it still expresses the old two-identity model in which a trusted or computed
`fingerprint` is the cache identity and a separate `ContentDigest` is an optional
integrity assertion. The redesign collapses those concepts at this boundary into
one verified exact-byte `ContentId`.

The upload Markdown also still makes task handoff depend on a public `a-ref` and
retains old artifact-oriented operation/result names. These are active contract
drifts, not merely editorial differences.

## Drift summary

| Severity | Area | Upload Markdown | Redesign/WIT baseline |
| --- | --- | --- | --- |
| High | Content identity | Separate `fingerprint` identity and `ContentDigest` integrity assertion | One verified `ContentId` is identity, physical key, and public transfer address |
| High | Caller-assigned identity | Trusted arbitrary fingerprint policy | Caller may assert only a `ContentId`, which is verified against the bytes |
| High | Task handoff | Bind fingerprint to `a-ref`; conditionally check only artifacts needed after cache resolution | Supply scoped bindings of `ContentId` members; acceptance atomically verifies/protects every supplied member or rejects the task |
| High | Public locator terminology | Active `a-ref` handoff wording | Public `a-ref` is retired; exact schema identity is internal/persisted `ArtifactSchemaId` |
| Medium | Markdown/WIT API | `publish_artifact_*`, `begin_artifact_upload`, `DurableArtifactUploadResult.fingerprint` | `publish-content-*`, `begin-content-upload`, `durable-content-upload-result.content-id` |
| Medium | Part acknowledgement | Markdown returns optional `AcceptedUploadPart.content_digest` | WIT returns only `accepted-upload-part.part-number` |
| Medium | `ContentId` record shape | Artifact model diagram shows only `value` | Handover and WIT say algorithm plus digest payload |
| Medium | Algorithm extensibility | Upload Markdown allows provider/binding-selected algorithms | Artifact model fixes current identity to BLAKE3-256; future algorithms require a distinct spec-defined identifier |
| Low | Negative legacy wording | Invariant says ingress does not accept `a-ref` | Semantically correct, but still teaches a retired public name |

## Detailed drift

### 1. The upload Markdown still has two identities for the same bytes

The upload Markdown says:

- upload returns the `fingerprint` by which the object is later used (line 5);
- `ContentDigest` is an integrity assertion (lines 68-85);
- fingerprint is the committed cache identity and is distinct from integrity
  digests (lines 106-112);
- the common result is `DurableArtifactUploadResult { fingerprint, ... }`
  (lines 114-130); and
- direct and multipart operations accept both `fingerprint?` and
  `expected_content_digest?` (lines 205-213, 308-317, 341-351, 450-460, and
  730-761).

The redesign says instead:

- `ContentId` is the universal identity, physical key, and public upload/download
  address for one exact byte sequence (Q1-Q139 lines 851-853; artifact model
  lines 36-50);
- upload returns `ContentId` (handover lines 898-904 and 1156-1162); and
- the caller may assert only a `ContentId`, which must be verified against the
  supplied bytes (handover lines 900-909).

The WIT follows the redesign: `durable-content-upload-result` contains
`content-id` (WIT lines 39-44), and each applicable operation accepts
`expected-content-id` rather than a separate identity plus digest assertion
(lines 54-78 and 119-131).

This is the central semantic drift. It affects publication, deduplication,
integrity verification, result recovery, error handling, examples, and every
invariant that currently distinguishes fingerprint from digest.

### 2. Trusted arbitrary fingerprints are explicitly superseded

The upload Markdown permits a caller-provided fingerprint to become identity
without hashing or byte comparison under policy (lines 108-112). It exposes
fingerprint options throughout direct and multipart publication (lines 209,
315, 349, 458, and 740) and defines `ARTIFACT_UPLOAD_FINGERPRINT_INVALID` and
`ARTIFACT_UPLOAD_FINGERPRINT_NOT_TRUSTED` (lines 261-266 and 676-681). Invariants
also preserve this policy (line 1653).

The handover explicitly requires replacing "old semantic fingerprints and
trusted caller-assigned cache identity with verified content identity" (lines
908-909), and excludes trusted arbitrary fingerprint inputs from WIT (lines
917-924). The WIT correctly has no fingerprint field or fingerprint policy.

All trusted/arbitrary fingerprint behavior in the Markdown is therefore stale,
not an optional extension of `ContentId`.

### 3. `ContentDigest` and `content_digest` remain public names in Markdown

The upload Markdown still declares `ContentDigest` (line 80) and uses
`expected_content_digest` or `content_digest` in direct publication, multipart
begin, part upload, HTTP bindings, examples, errors, and invariants. These old
names occur on 29 distinct lines in the file.

The handover requires `content-digest` records and fields to be renamed to
`content-id` while retaining the algorithm and canonical digest payload
(lines 915-920). Q1-Q139 makes the rename explicit (lines 851-853 and 909-910).
The WIT has completed this rename.

Not every lowercase use of **digest** is drift. It remains correct to describe:

- the BLAKE3 digest payload inside `ContentId`;
- digest computation over exact bytes;
- Base64URL encoding of the digest payload; and
- a digest mismatch discovered while verifying an asserted `ContentId`.

The drift is the old public type/field model (`ContentDigest`,
`expected_content_digest`, `content_digest`) and the claim that such a digest is
separate from content identity. Generic lowercase digest prose does not need a
mechanical global rename.

### 4. Algorithm ownership differs

The upload Markdown calls BLAKE3 merely the "initial" algorithm and allows other
algorithms when provider or binding policy supports them (line 85). That lets a
storage or transport implementation change public identity semantics.

The artifact model instead requires BLAKE3-256, fixes `blake3` permanently to
its 32-byte output, makes algorithm meaning part of the storage contract, and
requires a distinct identifier plus specification update for any future
algorithm (lines 44-50). The WIT's closed current enum containing only `blake3`
(lines 25-27) matches that baseline.

The Markdown should not delegate content-identity algorithms to provider or
binding policy.

### 5. The active task-handoff text still depends on `a-ref`

The upload Markdown says the caller binds the returned fingerprint to an
`a-ref` in a `DurableRunRequest`, after which acceptance deletes the grace claim
(lines 162-165). This combines three retired concepts: fingerprint as content
identity, public `a-ref`, and a flat fingerprint-to-schema binding.

The redesign says:

- public `a-ref` is retired (Q1-Q139 lines 854-858; handover lines 1163-1168);
- a supplied input is a `SuppliedArtifactBinding` containing one
  `ScopedArtifactRequest` and an ordered non-empty sequence of `ContentId`
  members (artifact model lines 179-192); and
- exact `ArtifactSchemaId + ContentId` interpretation remains internal or
  persisted rather than being caller-facing (artifact model lines 216-238).

The WIT correctly exposes no schema, artifact request, instance, or `a-ref`.

The Q1-Q139 file contains many earlier `a-ref` answers, but they are historical,
not unresolved drift: the reconciliation explicitly lists the affected answers
as superseded at lines 887-897.

### 6. Grace-claim/task-acceptance semantics conflict with the redesign

The upload Markdown says presence checks and task-input claims apply only to
artifacts needed by stages that will execute; an absent unneeded supplied root
does not fail acceptance, planning, or the task (lines 171-184). Its invariant
also says a structurally valid proposal deletes grace for every **present**
uploaded artifact, even if the task later fails (line 1659).

The redesigned rule is stronger and earlier: durable acceptance rejects any
missing, unreadable, unauthorized, or unclaimable supplied content and atomically
verifies/protects the supplied members, removes or converts grace claims, and
creates the task (Q1-Q139 lines 879-882; handover lines 1185-1188). Failure must
perform no partial mutation.

This is observable lifecycle drift. It is not resolved merely by renaming
fingerprints to content IDs.

### 7. Markdown and WIT public operation/type names have diverged

The Markdown uses artifact-oriented names throughout:

- `DurableArtifactUploadResult`;
- `publish_artifact_bytes`, `publish_artifact_source`, and
  `publish_artifact_file`;
- `begin_artifact_upload`, `upload_artifact_part`, `get_artifact_upload`,
  `commit_artifact_upload`, and `abort_artifact_upload`;
- `ArtifactUploadPlan`, `ArtifactUploadStatus`, and `ArtifactUploadState`.

The WIT uses content-oriented equivalents at lines 39-44, 54-78, 83-142:

- `durable-content-upload-result`;
- `publish-content-*`;
- `begin-content-upload`, `upload-content-part`, `get-content-upload`,
  `commit-content-upload`, and `abort-content-upload`;
- `content-upload-plan`, `content-upload-status`, and `content-upload-state`.

This naming change is consistent with the handover's content-only boundary
(lines 898-900 and 1161-1162). The Markdown examples, conceptual signatures,
HTTP adapter examples, and prose references have not followed it.

The public HTTP paths also remain artifact-oriented (`/artifacts` and
`/artifact/upload/...`, for example lines 484 and 1011). None of the comparison
documents explicitly decides whether HTTP route compatibility is required, so
route renaming should be treated as a surface decision rather than inferred
from the WIT rename.

### 8. Multipart part acknowledgements disagree

The Markdown's `AcceptedUploadPart` includes optional `content_digest` and says
it is returned only when the request supplied `expected_content_digest`
(lines 831-858). The WIT's `accepted-upload-part` contains only `part-number`
(lines 104-106), although part upload accepts `expected-content-id`
(lines 127-132).

One of these surfaces must change. Under the current redesign, returning a
`ContentId` for an unpublished staging part could misleadingly suggest that the
part is a published content object. If the acknowledgement only needs to prove
acceptance, the WIT's part number is the cleaner surface; if callers need the
computed identifier, its publication/presence semantics must be specified.

### 9. Live-capture begin result names disagree

The Markdown returns `LiveCapturePlan` from `begin_live_capture` (lines
1235-1251). The WIT returns `accepted-live-capture-segment` (lines 151-171).
Their fields are substantially aligned, but the types communicate different
concepts: a session plan versus acknowledgement of an optionally supplied first
segment. This should be normalized when the Markdown is brought onto the WIT
surface.

### 10. The artifact model's `ContentId` diagram omits its algorithm

The artifact model shows:

```text
ContentId
  value: 32-byte digest
```

at lines 39-42, but later defines equality using both algorithm identifier and
digest value (lines 68-69). The handover says `ContentId` contains algorithm plus
digest (lines 1158-1160), and the WIT record has `algorithm` and `value` (lines
25-33).

This is drift in the redesign baseline itself. The artifact model diagram should
include the algorithm field (or explicitly state that the type is permanently
BLAKE3-only and therefore omits it). The current prose and WIT favor including
the field.

### 11. Retired names remain in invariants

The upload Markdown says "No ingress path accepts or resolves an `a-ref`" at
line 1648. Its meaning is aligned with the content-only redesign, but the
invariant still teaches a retired name. It would be clearer as a positive
boundary: ingress accepts no artifact request, scoped binding, schema, or exact
interpretation identifier.

Line 1694 also calls an open capture "not an `ArtifactInstance`". Public
`a-instance`/`ArtifactInstance` is retired by the same reconciliation (Q1-Q139
lines 856-858), so this should be rewritten as "not published content" or "not
an addressable content object."

## Areas that are already aligned

The following are not drift and should be preserved during cleanup:

- upload is schema-independent and does not interpret, transcode, or validate an
  artifact representation (upload Markdown lines 7 and 1648-1649; handover
  lines 898-900 and 1161-1162);
- provisional upload/capture state is not bindable content and becomes visible
  only after verified publication;
- direct, multipart, and live-capture paths converge on one publication result;
- create-if-absent deduplication, recovery, grace, retention, timeout, abort, and
  bounded-memory transfer concepts remain valid;
- multipart parts and live-capture segments are transfer units, not artifact
  binding members; and
- lowercase `digest` is still the correct name for the hash payload and hash
  computation inside `ContentId`.

## Recommended correction order

1. Rewrite the upload Markdown's identity section around the single
   `ContentId` model; delete the trusted fingerprint policy rather than retaining
   it as a compatibility path.
2. Rename Markdown public operations, types, fields, examples, and adapter code
   to match the WIT content-oriented surface.
3. Rewrite task handoff around `SuppliedArtifactBinding` and atomic verification,
   protection, grace conversion/removal, and task creation for every supplied
   member.
4. Resolve the two remaining cross-document interface choices: multipart
   acknowledgement shape and live-capture begin result name.
5. Correct the artifact model's `ContentId` record diagram to match its own
   equality prose and the WIT.
6. Sweep retired public `a-ref`, `ArtifactInstance`, `ContentDigest`,
   `content_digest`, and upload-semantic `fingerprint` names from active upload
   prose, while leaving the explicitly historical interview record intact.
