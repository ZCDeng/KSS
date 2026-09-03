"""紫苏叶证据审计测试."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from kss.supply_chain.assessment import assess_perilla
from kss.supply_chain.registry import StockChainInfo


def _stock(**overrides: object) -> StockChainInfo:
    """构造默认合格的紫苏叶审计样本."""
    defaults = {
        "ts_code": "688012.SH",
        "name": "样例公司",
        "demand_chains": ("半导体",),
        "chain_layer": 4,
        "chain_role": "equipment",
        "n_competitors_global": 3,
        "n_competitors_domestic": 1,
        "substitutability": "medium",
        "expansion_cycle_years": 3.0,
        "demand_locked": True,
        "analyst_count": 3,
        "analyst_notes": "test",
        "main_business_confirmed": True,
        "import_substitution_valid": True,
        "liquidity_eligible": True,
        "valuation_unpriced": True,
        "evidence_as_of": "2026-06-20",
        "evidence_sources": ("annual-report:2025", "industry-source:2026-06"),
        "evidence_history": (
            {
                "as_of": "2026-06-30",
                "published_at": "2026-07-10",
                "retrieved_at": "2026-07-11T09:00:00+08:00",
                "source_kind": "official_periodic_report",
                "source_url": "https://example.com/2026-h1.pdf",
                "verdict": "support",
            },
        ),
    }
    defaults.update(overrides)
    return StockChainInfo(**defaults)  # type: ignore[arg-type]


def test_qualified_when_structure_and_evidence_complete() -> None:
    result = assess_perilla(
        _stock(),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "qualified"
    assert result.exclusion_reasons == ()
    assert result.review_flags == ()
    assert "主营业务已确认" in result.positive_signals


def test_needs_review_when_evidence_unknown_but_structure_passes() -> None:
    info = _stock(
        main_business_confirmed=None,
        import_substitution_valid=None,
        liquidity_eligible=None,
        valuation_unpriced=None,
        evidence_as_of="",
        evidence_sources=(),
        evidence_history=(),
    )

    result = assess_perilla(
        info,
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert result.exclusion_reasons == ()
    assert "待补主营业务收入证据" in result.review_flags
    assert "待补证据来源" in result.review_flags
    assert "待补PIT证据历史" in result.review_flags


def test_excluded_when_structure_hard_gate_fails() -> None:
    result = assess_perilla(
        _stock(chain_layer=3, demand_locked=False, substitutability="high"),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "excluded"
    assert "产业链层级不足 L4" in result.exclusion_reasons
    assert "需求未锁定" in result.exclusion_reasons
    assert "可替代性高" in result.exclusion_reasons


def test_excluded_when_explicit_evidence_false() -> None:
    result = assess_perilla(
        _stock(import_substitution_valid=False, liquidity_eligible=False),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "excluded"
    assert "进口替代逻辑无效" in result.exclusion_reasons
    assert "流动性不达标" in result.exclusion_reasons


def test_stale_evidence_and_structure_require_review() -> None:
    result = assess_perilla(
        _stock(evidence_as_of="2026-01-01"),
        structural_updated="2026-01-01",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "证据日期已过期" in result.review_flags
    assert "结构标注已过期" in result.review_flags


def test_as_dict_uses_frontend_keys() -> None:
    result = assess_perilla(
        replace(_stock(), evidence_sources=("source-a",)),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.as_dict()["status"] == "qualified"
    assert "exclusionReasons" in result.as_dict()


def test_future_dates_are_reviewed_as_lookahead_risk() -> None:
    result = assess_perilla(
        _stock(evidence_as_of="2026-07-20"),
        structural_updated="2026-07-20",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "证据日期晚于审计基准日" in result.review_flags
    assert "结构标注日期晚于审计基准日" in result.review_flags


def test_unknown_analyst_coverage_requires_review() -> None:
    result = assess_perilla(
        _stock(analyst_count=None),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "待补分析师覆盖数据" in result.review_flags


def test_undefined_demand_chain_requires_review() -> None:
    result = assess_perilla(
        _stock(demand_chains=("生物医药",)),
        structural_updated="2026-06-30",
        known_demand_chains={"半导体"},
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "需求链未在顶层定义: 生物医药" in result.review_flags


def test_negative_stale_threshold_is_invalid() -> None:
    with pytest.raises(ValueError, match="stale_after_days"):
        assess_perilla(_stock(), stale_after_days=-1)


def test_pit_history_rejects_lookahead_and_bad_chronology() -> None:
    result = assess_perilla(
        _stock(
            evidence_history=(
                {
                    "as_of": "2026-06-30",
                    "published_at": "2026-07-20",
                    "retrieved_at": "2026-07-19T09:00:00+08:00",
                    "source_kind": "official_periodic_report",
                    "source_url": "https://example.com/report.pdf",
                    "verdict": "support",
                },
            ),
        ),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "PIT证据时间顺序无效" in result.review_flags
    assert "PIT证据包含前视信息" in result.review_flags


def test_pit_conflict_is_kept_as_review_not_silently_promoted() -> None:
    history = dict(_stock().evidence_history[0])
    history["verdict"] = "conflict"

    result = assess_perilla(
        _stock(evidence_history=(history,)),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "PIT历史存在冲突证据" in result.review_flags


def test_pit_lookahead_uses_shanghai_day_for_utc_timestamp() -> None:
    history = dict(_stock().evidence_history[0])
    history.update(
        {
            "published_at": "2026-07-15",
            # UTC 7 月 15 日深夜已经是上海 7 月 16 日，不能归入 7 月 15 日快照。
            "retrieved_at": "2026-07-15T23:30:00Z",
        }
    )

    result = assess_perilla(
        _stock(evidence_history=(history,)),
        structural_updated="2026-06-30",
        as_of=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "PIT证据包含前视信息" in result.review_flags
