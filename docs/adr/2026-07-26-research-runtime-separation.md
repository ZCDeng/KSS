# ADR: Research overlay stays above AgentRuntime

## Status

Superseded-in-part by plan 2026-08-14-002: the **kernel owner** is DeepSeek Harness, not Python AgentRuntime. Research overlay (Profile / DAG / audit) still sits above the kernel.


Accepted.

## Context

KSS already has a stateful `AgentRuntime` for single conversational runs: provider streaming, messages, tools, context, Skill, memory and abort. The research stack needs longer-lived business goals, evidence standards, resumable task graphs and delivery audit. Those concepts span multiple agent runs and must not be conflated with one assistant response.

## Decision

Add `kss/research/` as an overlay above `kss/agent/`.

The fixed responsibility chain is:

`goal_id -> task_id -> attempt_id -> agent run_id -> tool_call_id -> evidence_id/artifact_id`

`AgentRuntime` remains the execution kernel for a node. It does not know weekly-report structure, cannot mark a research goal complete, and cannot treat model prose as verified evidence. The research layer owns goals, criteria, claims, evidence, task graph state, artifacts and completion audits.

## Consequences

- Research workflows can survive sidecar restarts without changing the chat protocol semantics.
- Models can propose claims, but only the research ledger and audit service can satisfy completion criteria.
- Future profiles can reuse the same runtime without embedding report-specific logic into the agent core.

## Rejected

- Put weekly-report logic inside `AgentRuntime` — rejected because it would couple generic chat execution to one delivery format.
- Let model output set goal completion — rejected because it bypasses strict evidence and artifact gates.

