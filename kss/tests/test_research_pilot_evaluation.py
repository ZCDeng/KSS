from kss.research.pilot_evaluation import PilotRunMetrics, evaluate_pilot


def _runs(*, real_provider: bool) -> list[PilotRunMetrics]:
    runs = []
    for index in range(3):
        runs.append(
            PilotRunMetrics(
                execution_mode="single",
                run_id=f"single-{index}",
                real_provider=real_provider,
                criterion_coverage=0.95,
                completion_rate=0.95,
                contradictions_detected=2,
                wall_seconds=100,
                provider_tokens=10_000,
            )
        )
        runs.append(
            PilotRunMetrics(
                execution_mode="multi_agent_pilot",
                run_id=f"pilot-{index}",
                real_provider=real_provider,
                criterion_coverage=0.96,
                completion_rate=0.95,
                contradictions_detected=3,
                wall_seconds=75,
                provider_tokens=16_000,
            )
        )
    return runs


def test_mock_pilot_can_only_be_marked_architecturally_feasible():
    evaluation = evaluate_pilot(_runs(real_provider=False))

    assert evaluation.passed is True
    assert evaluation.status == "mock_feasible"
    assert evaluation.real_provider_verified is False


def test_real_provider_pilot_can_become_default_candidate():
    evaluation = evaluate_pilot(_runs(real_provider=True))

    assert evaluation.passed is True
    assert evaluation.status == "eligible_for_default_candidate"
    assert evaluation.metrics["wall_reduction"] == 0.25
    assert evaluation.metrics["token_ratio"] == 1.6


def test_pilot_safety_error_blocks_promotion():
    runs = _runs(real_provider=True)
    broken = runs[-1]
    runs[-1] = PilotRunMetrics(
        **{
            **broken.__dict__,
            "unbound_financial_numbers": 1,
        }
    )

    evaluation = evaluate_pilot(runs)

    assert evaluation.passed is False
    assert evaluation.status == "rejected"
    assert "safety_errors_zero" in evaluation.findings
