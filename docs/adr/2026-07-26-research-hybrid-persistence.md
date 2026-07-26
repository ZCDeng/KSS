# ADR: Research state uses SQLite plus append-only audit mirrors and content-addressed artifacts

## Status

Accepted.

## Context

Research goals need efficient listing, resumable scheduling, idempotent protocol operations, durable events and large immutable deliverables. Putting full HTML, screenshots or ledgers into SQLite would make state queries heavy. Keeping only JSONL would make indexed recovery and idempotency fragile.

## Decision

Use a hybrid persistence model:

- SQLite `kss.db` migration v3 is the source of truth for goals, criteria, claims, evidence, checks, tasks, dependencies, attempts, artifacts, audits, publications and durable events.
- `storage/agent/research/goals/<goal_id>/events.jsonl` is an append-only audit mirror of `research_events`.
- Large artifact bytes live in `storage/agent/research/objects/sha256/<prefix>/<hash>`.
- Artifact writes go through staging, fsync, SHA-256 calculation, atomic object move and database registration.
- State changes and durable events commit in the same SQLite transaction when they affect workflow state.

## Consequences

- Goal hydration, event replay and task recovery remain indexed and bounded.
- Deliverables are immutable and hash-verifiable.
- If JSONL mirroring lags, SQLite remains authoritative and the mirror can be repaired.
- On startup, staging is cleaned and unreferenced objects are isolated instead of promoted.

## Rejected

- SQLite-only artifact storage — rejected because full reports and previews should stay content-addressed and independently hashable.
- JSONL-only workflow state — rejected because leases, idempotency and list queries need indexed transactional state.

