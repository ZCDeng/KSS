"""紫苏叶注册表的证据缺失与时间口径回归测试."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from kss.supply_chain.registry import ChainRegistry
from kss.supply_chain.scoring import _coverage_gap_score


def _write_registry(path: Path, stock_overrides: dict | None = None) -> ChainRegistry:
    stock = {
        "name": "样例公司",
        "demand_chains": ["半导体"],
        "chain_layer": 4,
        "chain_role": "equipment",
        "n_competitors_global": 3,
        "n_competitors_domestic": 1,
        "substitutability": "medium",
        "expansion_cycle_years": 3,
        "demand_locked": True,
        "analyst_notes": "test",
    }
    stock.update(stock_overrides or {})
    raw = {
        "version": 2,
        "updated": "2026-01-01",
        "structural_updated": "2026-02-01",
        "analyst_updated": "2026-03-01",
        "scoring_weights": {
            "layer": 0.25,
            "moat": 0.35,
            "lock": 0.25,
            "coverage_gap": 0.15,
        },
        "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
        "ranking_multiplier": 0.3,
        "demand_chains": {"半导体": {}},
        "stocks": {"688012.SH": stock},
    }
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return ChainRegistry.from_yaml(path)


def test_missing_analyst_count_stays_unknown(tmp_path: Path) -> None:
    """缺失覆盖数据不是“零覆盖”，不能自动获得错误发现溢价."""
    reg = _write_registry(tmp_path / "sc.yaml")

    info = reg.get("688012.SH")

    assert info is not None
    assert info.analyst_count is None
    assert _coverage_gap_score(info.analyst_count) == pytest.approx(0.0)


def test_explicit_zero_analyst_count_remains_valid_evidence(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path / "sc.yaml", {"analyst_count": 0})

    info = reg.get("688012.SH")

    assert info is not None
    assert info.analyst_count == 0
    assert _coverage_gap_score(info.analyst_count) == pytest.approx(1.0)


def test_registry_parses_evidence_card_and_separate_dates(tmp_path: Path) -> None:
    reg = _write_registry(
        tmp_path / "sc.yaml",
        {
            "analyst_count": 3,
            "main_business_confirmed": True,
            "import_substitution_valid": True,
            "liquidity_eligible": False,
            "valuation_unpriced": None,
            "structural_as_of": "2026-02-10",
            "evidence_as_of": "2026-02-15",
            "evidence_sources": ["annual-report:2025", "industry-source:2026-02"],
            "evidence_history": [
                {
                    "as_of": "2025-08-31",
                    "source_url": "https://example.com/annual-2025",
                    "support": ["main_business"],
                },
                {
                    "as_of": "2026-02-15",
                    "source_url": "https://example.com/industry-2026-02",
                    "support": ["import_substitution"],
                },
            ],
        },
    )

    info = reg.get("688012.SH")

    assert info is not None
    assert info.main_business_confirmed is True
    assert info.import_substitution_valid is True
    assert info.liquidity_eligible is False
    assert info.valuation_unpriced is None
    assert info.structural_as_of == "2026-02-10"
    assert info.evidence_as_of == "2026-02-15"
    assert info.evidence_sources == ("annual-report:2025", "industry-source:2026-02")
    assert info.evidence_history[0]["as_of"] == "2025-08-31"
    assert info.evidence_history[1]["source_url"] == "https://example.com/industry-2026-02"
    assert reg.config.structural_updated == "2026-02-01"
    assert reg.config.analyst_updated == "2026-03-01"


def test_per_stock_structural_date_overrides_global_date(tmp_path: Path) -> None:
    reg = _write_registry(
        tmp_path / "sc.yaml",
        {
            "analyst_count": 3,
            "structural_as_of": "2026-03-01",
            "main_business_confirmed": True,
            "import_substitution_valid": True,
            "liquidity_eligible": True,
            "valuation_unpriced": True,
            "evidence_as_of": "2026-03-01",
            "evidence_sources": ["official-report"],
        },
    )

    assessment = reg.assess("688012.SH", as_of=date(2026, 3, 15))

    assert assessment is not None
    assert "结构标注日期晚于审计基准日" not in assessment.review_flags
    assert "结构标注已过期" not in assessment.review_flags


def test_legacy_updated_is_structural_date_fallback(tmp_path: Path) -> None:
    path = tmp_path / "sc.yaml"
    reg = _write_registry(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("structural_updated")
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    legacy = ChainRegistry.from_yaml(path)

    assert legacy.config.structural_updated == "2026-01-01"
