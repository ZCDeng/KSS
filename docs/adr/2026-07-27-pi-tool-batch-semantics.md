# ADR: Tool batches follow Pi ordering with KSS safety overrides

## Status

Accepted.

## Context

KSS currently executes tool calls sequentially. Pi supports parallel read work while preserving deterministic transcript order and forcing mixed batches containing a sequential tool to execute sequentially.

## Decision

Adopt Pi's batch semantics.

- Pure audited read tools may declare `parallel`.
- Legacy, stateful, non-idempotent, confirmation and write tools are `sequential`.
- Any sequential call makes the entire batch sequential.
- Start events follow assistant order, completion events reflect actual completion, and persisted results return to assistant order.
- A batch terminates the automatic model follow-up only when every finalized result requests termination.
- Abort discards late thread results and never represents an already-started write as cancelled.

## Consequences

- Independent reads can reduce latency without weakening the write gate.
- Every built-in tool needs an explicit safety classification before parallel execution.

## Rejected

- Parallelize every tool by default — rejected because existing tools were not written under that concurrency contract.
- Stop on the first terminating result — rejected because mixed batches would lose valid tool results and diverge from Pi.
