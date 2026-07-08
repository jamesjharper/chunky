# Artifact Terminology

This document consolidates the domain terminology defined in the `specs/01-artifacts` folder. It is a reference glossary for the artifact model and artifact IO specifications.

## Core Terms

### Artifact
A typed value that enters, moves through, or leaves the pipeline. Artifacts include source inputs, settings, intermediate results, final outputs, diagnostics, and output requests.

### Artifact Model
The semantic model that lets the pipeline reason consistently about artifact identity, compatibility, reuse, and materialization across stages.

### Artifact Role
The single role assigned to an artifact. The defined roles are `source`, `settings`, `intermediate`, `target`, and `diagnostic`.

### Source Artifact
An externally supplied input artifact. Source artifacts are always fingerprinted and may optionally be retained in durable storage for resume or recovery.

### Settings Artifact
A resolved configuration artifact. Effective settings are produced by combining declaration defaults, user-supplied settings, and inherited settings.

### Intermediate Artifact
An artifact produced by one stage and consumed by another. Intermediate artifacts are cached by default and usually derive their fingerprint from production pedigree rather than content hashing.

### Target Artifact
A requested final output artifact. From the producing stage's perspective, targets are write-only.

### Diagnostic Artifact
A requested inspection or observability artifact, such as logs, performance outputs, or stats. Diagnostics may be queried by task ID, but non-deterministic diagnostics are not reusable across later tasks.

## Identity and Declaration Terms

### Artifact ID
A stable semantic identifier that describes what an artifact means, rather than where it is stored or how it was produced.

### Identity Format
The canonical artifact ID format:

```text
{domain}//{content_kind}{#phase}
```

### Domain
The first part of an artifact ID. It names the semantic subsystem or concern area that owns the artifact.

### Content Kind
The second part of an artifact ID. It names the specific semantic value within a domain.

### Phase
An optional numeric suffix on an intermediate artifact family. A phase identifies concrete processing steps within a phased intermediate family.

### Artifact Key
The structured manifest identity of an artifact declaration. It contains `domain`, `content_kind`, and optional `phase`.

### Family
The non-phased identity of an artifact key: `{domain}//{content_kind}`. Multiple concrete phases can belong to the same family.

### Phased Family
An intermediate artifact family that explicitly publishes one or more phase-specific declarations.

### Phase Selector
The use of `artifact_id.phase` in an input requirement to request the closest available phase that does not exceed the requested phase number.

### Completed Value
The phase-less value of a phased family after graph evaluation has selected the concrete phase to use.

### Artifact Declaration
The semantic contract for an artifact schema. A declaration owns the artifact's identity, role, version, codec, cacheability, and optional default provider.

### Declaration Ownership
The rule that the stage or shared package defining an artifact schema is the authoritative owner of that artifact declaration.

### Default Provider
An optional declaration component that supplies a default settings value.

### Codec
The serialization contract for an artifact at cache, process, and materialization boundaries. A codec defines `codec_id`, `codec_version`, `mime_type`, `extension`, `serialize`, and `deserialize`.

### Extension
Storage metadata exposed by a codec, including its leading dot such as `.json` or `.vtt`. It does not imply that a consumer receives a file path.

### Cacheable
The declaration property indicating whether an artifact may be reused from durable cache.

## Planning Terms

### Artifact Capability
A static stage advertisement that references a declared artifact the stage can input or output. Capabilities must be known during backward traversal of the task graph.

### Input Capability
A capability stating that a stage can consume a referenced artifact.

### Output Capability
A capability stating that a stage can produce a referenced artifact.

### Artifact Requirement
A dynamic stage input requirement computed from planning context. It references an artifact declaration and records whether that input is required.

### Stage Node Context
The read-only planning context used while building the task graph. It is used to inspect expected stage outputs and resolved or inherited settings.

### Backward Traversal
The graph-construction process that works backward from requested outputs to determine required stages and artifacts.

### Terminal Remapping
Planner-owned resolution that maps a requirement or requested output to the concrete artifact instance that should satisfy it. Stages do not perform this remapping themselves.

### Production Pedigree
The planner-provided provenance used to fingerprint stage-produced outputs and intermediates.

## Runtime and IO Terms

### Artifact Handle
The execution-time binding for one concrete artifact instance after fingerprint resolution and source or target resolution.

### Resolved Artifact ID
The fully evaluated artifact identity on a handle, after phase selection and terminal remapping are complete.

### Artifact Instance
The available semantic artifact associated with one resolved artifact handle.

### Artifact Source Handle
The runtime object representing one selected readable source for a concrete artifact value.

### Artifact Target Handle
The runtime object representing one selected writable destination for a concrete artifact value.

### Source
The readable binding on an artifact handle. Inputs have one resolved source, and cache-hit outputs may also expose a source.

### Target
A writable destination bound to an artifact handle. Produced outputs may bind one or more targets before stage execution.

### Materialization
The act of writing an available semantic artifact to caller-provided output destinations such as files or sinks.

### Input Binding
An artifact handle with a resolved source and usually no targets.

### Output Binding
An artifact handle that either represents a cache hit or carries pre-resolved targets for production.

### Materialization Binding
A binding that reuses an existing semantic source while adding caller-requested output targets.

### Cache Hit
An output case where the artifact is already available in cache, so the producing stage can be skipped.

### Read Reservation
A cache-owned reservation that keeps a cache source safely readable while a task is still consuming it.

### Write Reservation
A cache-owned reservation that protects a cache target while a stage is preparing or committing output.

### Content Fingerprint
The source-handle contribution that represents semantic content equality for supplied inputs. It is not always the final artifact fingerprint.

### Artifact Fingerprint
The final identity hash for a concrete artifact instance. Supplied inputs derive it from content, while produced artifacts may derive it from pedigree.

### Bootstrap
The point where a handle becomes fully assembled with declaration, fingerprint, and resolved source or targets.

## Reader and Writer Terms

### Artifact Reader
The runtime reader bound to a complete input artifact handle. It reads a typed value from the handle's source and may memoize it for the invocation.

### Artifact Writer
The runtime writer bound to a complete output artifact handle. It prepares all semantic targets, commits them in order, and aborts prepared targets on failure.

### Prepare
The target-handle lifecycle step that stages a value without publishing it as complete.

### Commit
The target-handle lifecycle step that publishes the prepared value and releases any reservation.

### Abort
The target-handle lifecycle step that discards prepared output and releases any reservation when publication does not complete.

### Semantic Target
A target that makes the artifact itself available to the system, such as the durable cache target or in-memory publication target.

### Materialization Target
A caller-provided target written after the semantic artifact has successfully become available.

### In-Memory Publication Target
An output target that makes a produced artifact available directly to downstream stages during the current task.

### Intermediate Artifact Handle
An example combined target/source handle used for in-memory publication of intermediates. It is written as a target, then read later as a source after commit.
