"""Deterministic promotion gate for the Research multi-agent pilot.

This module evaluates already-completed benchmark runs. It never starts model
runs and cannot enable the pilot; callers must explicitly persist or surface
the returned recommendation.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal

ExecutionMode = Literal["single", "multi_agent_pilot"]


@dataclass(frozen=True)
class PilotRunMetrics:
    execution_mode: ExecutionMode
    run_id: str
    real_provider: bool
    criterion_coverage: float
    completion_rate: float
    contradictions_detected: int
    wall_seconds: float
    provider_tokens: int
    unbound_financial_numbers: int = 0
    evidence_gate_bypasses: int = 0
    duplicate_success_nodes: int = 0
    duplicate_evidence: int = 0


@dataclass(frozen=True)
class PilotEvaluation:
    status: Literal[
        "insufficient_runs",
        "mock_feasible",
        "eligible_for_default_candidate",
        "rejected",
    ]
    passed: bool
    real_provider_verified: bool
    findings: tuple[str, ...]
    metrics: dict[str, float | int | bool]

    def to_wire(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "findings": list(self.findings),
        }


def evaluate_pilot(
    runs: Iterable[PilotRunMetrics],
    *,
    minimum_runs_per_mode: int = 3,
) -> PilotEvaluation:
    items = list(runs)
    single = [item for item in items if item.execution_mode == "single"]
    pilot = [
        item for item in items
        if item.execution_mode == "multi_agent_pilot"
    ]
    if len(single) < minimum_runs_per_mode or len(pilot) < minimum_runs_per_mode:
        return PilotEvaluation(
            status="insufficient_runs",
            passed=False,
            real_provider_verified=False,
            findings=("每种执行模式至少需要 3 次固定快照评测",),
            metrics={
                "single_runs": len(single),
                "pilot_runs": len(pilot),
            },
        )

    def median(values: Iterable[float | int]) -> float:
        return float(statistics.median(values))

    single_coverage = median(item.criterion_coverage for item in single)
    pilot_coverage = median(item.criterion_coverage for item in pilot)
    single_completion = median(item.completion_rate for item in single)
    pilot_completion = median(item.completion_rate for item in pilot)
    single_contradictions = median(
        item.contradictions_detected for item in single
    )
    pilot_contradictions = median(
        item.contradictions_detected for item in pilot
    )
    single_wall = median(item.wall_seconds for item in single)
    pilot_wall = median(item.wall_seconds for item in pilot)
    single_tokens = median(item.provider_tokens for item in single)
    pilot_tokens = median(item.provider_tokens for item in pilot)
    wall_reduction = (
        (single_wall - pilot_wall) / single_wall
        if single_wall > 0
        else 0.0
    )
    token_ratio = (
        pilot_tokens / single_tokens if single_tokens > 0 else float("inf")
    )
    safety_errors = sum(
        item.unbound_financial_numbers
        + item.evidence_gate_bypasses
        + item.duplicate_success_nodes
        + item.duplicate_evidence
        for item in pilot
    )
    real_provider_verified = all(item.real_provider for item in items)
    checks = {
        "safety_errors_zero": safety_errors == 0,
        "coverage_not_lower": pilot_coverage >= single_coverage,
        "completion_not_lower": pilot_completion >= single_completion,
        "contradictions_not_lower": (
            pilot_contradictions >= single_contradictions
        ),
        "wall_reduction_at_least_20pct": wall_reduction >= 0.20,
        "token_ratio_at_most_1_8": token_ratio <= 1.8,
    }
    findings = tuple(label for label, passed in checks.items() if not passed)
    metrics: dict[str, float | int | bool] = {
        "single_runs": len(single),
        "pilot_runs": len(pilot),
        "single_criterion_coverage_median": single_coverage,
        "pilot_criterion_coverage_median": pilot_coverage,
        "single_completion_rate_median": single_completion,
        "pilot_completion_rate_median": pilot_completion,
        "single_contradictions_median": single_contradictions,
        "pilot_contradictions_median": pilot_contradictions,
        "wall_reduction": wall_reduction,
        "token_ratio": token_ratio,
        "safety_errors": safety_errors,
        "real_provider_verified": real_provider_verified,
    }
    if findings:
        return PilotEvaluation(
            status="rejected",
            passed=False,
            real_provider_verified=real_provider_verified,
            findings=findings,
            metrics=metrics,
        )
    if not real_provider_verified:
        return PilotEvaluation(
            status="mock_feasible",
            passed=True,
            real_provider_verified=False,
            findings=("尚未完成真实 BYOK provider 对比，不能默认启用",),
            metrics=metrics,
        )
    return PilotEvaluation(
        status="eligible_for_default_candidate",
        passed=True,
        real_provider_verified=True,
        findings=(),
        metrics=metrics,
    )
