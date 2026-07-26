# ADR: Research completion and formal publishing require strict audit gates

## Status

Accepted.

## Context

The weekly report profile is a heavy analytical deliverable. It must distinguish a useful draft from a completed, publishable artifact. Model confidence, compressed context, Skill text and long-term memory cannot prove a market claim.

## Decision

Only `ResearchAuditService` semantics in `kss/research/service.py` can transition a goal to `completed`.

Completion requires:

- every required criterion has enough verified, fresh evidence from allowed source tiers;
- required tasks have succeeded;
- critical contradictions are resolved;
- a formal HTML artifact exists;
- compiler audit passes numeric binding, metric recomputation, stable anchors, CSP, escaped text, evidence manifest and object hashes.

Audit failure may still produce an exportable draft, but formal publishing is blocked until the latest audit passes. Draft export and formal publish are separate protocol operations.

## Consequences

- Incomplete research is visible and useful without being mislabeled as complete.
- Financial numbers must be ledger-bound or explicitly allowlisted.
- Formal outputs have stable hashes and publication records.

## Rejected

- Treat generated prose as sufficient completion evidence — rejected because model text can omit or hallucinate source support.
- Allow formal publish with warnings — rejected because warnings are acceptable for drafts, not completed analytical deliverables.

