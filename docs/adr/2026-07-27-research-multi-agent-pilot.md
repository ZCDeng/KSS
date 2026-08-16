# ADR: Multi-agent research is a role-bound DAG pilot

## Status

Accepted.

## Context

The research stack already has durable tasks, attempts, leases, evidence and deterministic audits. A separate Swarm runtime would duplicate those controls and risk making agent conversation appear authoritative.

## Decision

Model multi-agent work as role-bound research tasks.

- Each task names a `ResearchAgentSpec` with model route, instructions, tool/Skill whitelist, optional R7 write allowlist, and budget.
- Node execution binds to a Harness research agent (`agentPreset=research`) with a dedicated attempt workspace. Overlay Profile, DAG and audit still own goal completion; model text cannot mark a goal complete.
- A node is write-capable iff its bound research preset's R7 allowlist is non-empty (bash, filesystem, or KSS live writes). Empty-allowlist nodes in one topological layer may run concurrently, maximum two. Write-capable nodes in the same layer are serial.
- `execution_slot` remains the cross-process mutex for the production research slot. It is not the R11 same-layer write classifier.
- Agents exchange immutable artifact summaries, claims and evidence IDs, not full histories or direct messages.
- Agents cannot validate their own evidence, change protected criteria, expand budgets or publish.
- Child agents inherit the parent allowlist and workspace cwd and cannot escalate.
- Resume of an interrupted Harness turn reuses the same `agentPreset` and workspace and does not replay already-applied writes. A new cwd or whitelist is a new attempt. Scheduled origin never attaches a desktop answerer.
- The pilot is disabled by default until fixed-fixture and real-provider comparisons pass its quality, cost, latency and recovery gates.

## Consequences

- Existing research recovery and audit semantics remain authoritative.
- Concurrency can be evaluated without committing ordinary chat to a Swarm model.
- Research writes are owned by the U3 pre-execute allowlist, not by a Python `reject_write` / `research_read_only` gate.

## Rejected

- Introduce Vibe Swarm as a second scheduler — rejected because it would split recovery, budget and evidence ownership.
- Enable multi-agent research by default after mock tests — rejected because provider variability and cost need real BYOK evidence.
- Allow two write-capable nodes in one layer — rejected because they would race the same research workspace contract.
