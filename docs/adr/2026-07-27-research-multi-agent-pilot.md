# ADR: Multi-agent research is a role-bound DAG pilot

## Status

Accepted.

## Context

The research stack already has durable tasks, attempts, leases, evidence and deterministic audits. A separate Swarm runtime would duplicate those controls and risk making agent conversation appear authoritative.

## Decision

Model multi-agent work as role-bound research tasks.

- Each task names a `ResearchAgentSpec` with model route, instructions, tool/Skill whitelist and budget.
- Only independent read-only nodes in one topological layer may run concurrently, with a maximum of two.
- Agents exchange immutable artifact summaries, claims and evidence IDs, not full histories or direct messages.
- Agents cannot validate their own evidence, change protected criteria, expand budgets or publish.
- The pilot is disabled by default until fixed-fixture and real-provider comparisons pass its quality, cost, latency and recovery gates.

## Consequences

- Existing research recovery and audit semantics remain authoritative.
- Concurrency can be evaluated without committing ordinary chat to a Swarm model.

## Rejected

- Introduce Vibe Swarm as a second scheduler — rejected because it would split recovery, budget and evidence ownership.
- Enable multi-agent research by default after mock tests — rejected because provider variability and cost need real BYOK evidence.
