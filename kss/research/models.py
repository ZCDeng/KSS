"""Core data contracts for KSS Deep Research.

This layer is intentionally provider-neutral. AgentRuntime may produce task
results, but only these contracts plus the audit service decide whether a
research goal is complete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

GoalStatus = Literal[
    "draft",
    "queued",
    "running",
    "waiting_user",
    "paused",
    "needs_refresh",
    "insufficient_evidence",
    "blocked",
    "budget_limited",
    "failed",
    "completed",
    "cancelled",
]
TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "succeeded",
    "incomplete",
    "failed",
    "interrupted",
    "cancelled",
    "blocked",
    "waiting_user",
]
AttemptStatus = Literal["running", "succeeded", "incomplete", "failed", "interrupted", "cancelled"]
ClaimStatus = Literal["proposed", "supported", "contradicted", "retracted"]

TERMINAL_GOAL_STATUSES = {
    "needs_refresh",
    "insufficient_evidence",
    "blocked",
    "budget_limited",
    "failed",
    "completed",
    "cancelled",
}
TERMINAL_TASK_STATUSES = {"succeeded", "incomplete", "failed", "interrupted", "cancelled", "blocked"}
TERMINAL_ATTEMPT_STATUSES = {"succeeded", "incomplete", "failed", "interrupted", "cancelled"}


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    goal_id: str
    label: str
    required: bool = True
    min_verified_evidence: int = 1
    allowed_tiers: list[str] = field(default_factory=lambda: ["official_or_primary", "reputable_secondary"])
    freshness_days: int | None = None
    validator: str | None = None
    status: str = "pending"

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    goal_id: str
    source_tool: str
    source_tier: str
    criterion_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    run_id: str | None = None
    tool_call_id: str | None = None
    provider: str | None = None
    uri: str | None = None
    artifact_id: str | None = None
    data_as_of: str | None = None
    method: str | None = None
    scope: str | None = None
    hash: str | None = None
    caveat: str | None = None
    verified: bool = False
    check_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Claim:
    claim_id: str
    goal_id: str
    content: str
    status: ClaimStatus = "proposed"
    task_id: str | None = None
    criterion_id: str | None = None
    confidence: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    contradiction_ids: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    goal_id: str
    profile_id: str
    kind: str
    title: str
    status: TaskStatus
    required: bool
    sequence_index: int
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    title: str
    required: bool = True
    depends_on: list[str] = field(default_factory=list)
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchAgentSpec:
    agent_id: str
    role: str
    instructions: str
    provider_route: str | None = None
    model_override: str | None = None
    tool_whitelist: list[str] = field(default_factory=list)
    skill_whitelist: list[str] = field(default_factory=list)
    max_steps: int = 8
    max_tokens: int = 25_000
    timeout_seconds: int = 240
    can_submit_claims: bool = True
    can_verify_evidence: bool = False
    write_allowlist: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    title: str
    criteria: list[dict[str, Any]]
    tasks: list[TaskSpec]
    agents: list[ResearchAgentSpec] = field(default_factory=list)
    pilot_max_concurrency: int = 2
    anchors: list[str] = field(default_factory=list)
    budget: dict[str, int] = field(default_factory=lambda: {
        "max_nodes": 24,
        "max_seconds": 3600,
        "max_provider_tokens": 200_000,
    })

    def to_wire(self) -> dict[str, Any]:
        data = asdict(self)
        data["tasks"] = [asdict(t) for t in self.tasks]
        data["agents"] = [asdict(agent) for agent in self.agents]
        return data
