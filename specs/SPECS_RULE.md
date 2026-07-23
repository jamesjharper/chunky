# Specification rules

## Write specifications for both humans and agents.

Keep them clear, logically ordered, concise, and maintainable. State each requirement once, where readers would expect it. Avoid unnecessary implementation detail, repetition, and mixing requirements with rationale or examples without distinction.

Prefer prose and lists. Avoid tables unless they are small, simple, and make a comparison materially easier to understand. Use headings or nested lists for long text, many entries, or content organized by operation or entity.

Treat the specification as the authoritative expression of human design intent at its chosen level of abstraction. Missing detail does not automatically grant permission to invent new design intent. It may be intentionally delegated, defined elsewhere, irrelevant at this level, or unresolved.

## Decision authority follows module boundaries:

* Humans should remain involved in decisions that affect module interfaces, cross-module relationships, or externally observable behaviour.
* Agents should independently make ordinary implementation decisions hidden within an established module boundary.

A module interface includes anything callers, operators, or other modules must know: public APIs and data formats, responsibilities, observable behaviour, invariants, errors, lifecycle and concurrency rules, compatibility, visible dependencies, configuration, and contractual performance, security, durability, or operational characteristics.

Within that contract, use engineering judgement freely for private types and functions, internal algorithms and data structures, local organisation, refactoring, validation, testing, caching, optimisation, and implementation decomposition.

Prefer deep modules: keep interfaces simple and stable, and pull complexity into the implementation rather than exposing configuration, special cases, coordination, or internal concepts to callers.

## Escalate or explicitly surface a decision when it could materially affect:

* public behaviour or interfaces;
* compatibility or architectural boundaries;
* security, privacy, or data integrity;
* failure, concurrency, lifecycle, or operational semantics;
* cross-module dependencies;
* significant performance characteristics; or
* a commitment that would be costly to reverse.

When uncertain, ask:

> Who else must know about this decision?

If only the module implementation must know, decide independently. If callers, operators, other modules, or future implementations must know, treat it as a surface-level design decision and involve the human.

When work can continue without resolving a surface decision, preserve flexibility and continue rather than forcing a premature choice.


