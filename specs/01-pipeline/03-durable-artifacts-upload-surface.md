# Durable Request Preparation

## Purpose

Durable preparation is the explicit asynchronous conversion of a local
`LocalRunRequest` into a `DurableRunRequest`. This document defines its
behavior, not a concrete service class or transport API.

## Eligibility

Durable preparation accepts only requests whose outputs have no local target
handles. It must not silently detach or discard delivery targets.

After taking ownership, the service preflights before reading any source:

- every output is known to the current manifest and declared cacheable;
- output cache modes permit publication;
- the configured provider satisfies durable-submission requirements; and
- the request is otherwise eligible for durable preparation.

Eligibility failure consumes the request but performs no source upload.

## Preparation

Durable preparation commits every supplied input through the ordinary artifact and
cache-I/O contracts and replaces it with its committed `a-instance` locator.
This includes inputs that local execution might not select; complete publication
is what makes the resulting request independent of the submitting process.

The operation does not define a separate streaming-ingest API or bounded-memory
guarantee. Handles and providers may optimize transfer internally while
preserving the same semantic validation and commit contract.

As each input commits, its owned source handle is closed and released. The
resulting `DurableRunRequest` retains the requested semantic outputs and
request policies, but never local targets or delivery instructions.

## Ownership and failure

The service consumes the complete local request when preparation begins. The
original request remains consumed on success, failure, or cancellation.
Bindings without enforceable moves must reject attempted reuse.

On failure or cancellation, the service closes all remaining owned handles and
aborts incomplete writes. Inputs committed before the failure remain ordinary
unreferenced cache entries and are not rolled back.

## Cancellation and progress

Cancellation and structured progress cover preflight, input reads, encoding,
cache waits and writes, and cleanup. Progress describes the preparation work and
does not imply durable task acceptance.

## Result

Success returns a structurally valid `DurableRunRequest`. Durable task
identity and output `a-task` locators are allocated only by subsequent durable
submission.

Callers that already possess committed input locators may construct the
durable request directly without running this preparation.

## Invariants

1. Durable preparation is explicit and never part of `build()` or local execution.
2. It rejects local output targets rather than omitting them.
3. Every supplied input is committed before success.
4. The request is consumed for every terminal outcome.
5. Committed content survives later preparation failure as normal cache content.
