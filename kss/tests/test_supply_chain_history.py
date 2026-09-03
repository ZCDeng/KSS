"""紫苏叶 point-in-time 历史快照测试."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kss.supply_chain.history import (
    build_supply_chain_snapshot,
    snapshot_supply_chain_history,
    write_supply_chain_snapshot,
)


def _raw_config(**stock_overrides: object) -> dict[str, object]:
    stock = {
        "name": "中微公司",
        "demand_chains": ["半导体"],
        "chain_layer": 4,
        "chain_role": "equipment",
        "n_competitors_global": 3,
        "n_competitors_domestic": 1,
        "substitutability": "medium",
        "expansion_cycle_years": 3.0,
        "demand_locked": True,
        "analyst_count": 5,
        "analyst_notes": "test",
        "main_business_confirmed": True,
        "import_substitution_valid": True,
        "liquidity_eligible": True,
        "valuation_unpriced": True,
        "structural_as_of": "2026-08-31",
        "evidence_as_of": "2026-08-31",
        "evidence_sources": ["annual-report-2025"],
        "evidence_history": [
            {
                "as_of": "2026-06-30",
                "published_at": "2026-08-31",
                "retrieved_at": "2026-09-03T13:00:00Z",
                "source_kind": "official_periodic_report",
                "source_url": "https://example.com/2026-h1.pdf",
                "verdict": "support",
            }
        ],
    }
    stock.update(stock_overrides)
    return {
        "version": 1,
        "updated": "2026-08-31",
        "analyst_updated": "2026-08-31",
        "scoring_weights": {"layer": 0.25, "moat": 0.35, "lock": 0.25, "coverage_gap": 0.15},
        "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
        "ranking_multiplier": 0.3,
        "demand_chains": {"半导体": {"layers": []}},
        "stocks": {"688012.SH": stock},
    }


def _write_config(tmp_path: Path, raw: dict[str, object]) -> Path:
    path = tmp_path / "supply_chain.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def test_snapshot_records_complete_point_in_time_payload(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _raw_config())
    path = snapshot_supply_chain_history(
        config=config_path,
        output_root=tmp_path / "pit",
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["observed_at"] == "2026-09-03T05:00:00Z"
    assert payload["as_of"] == "2026-09-03"
    assert payload["source"]["config_path"] == str(config_path.resolve())
    assert payload["source"]["config_sha256"]
    assert payload["metadata"]["structural_updated"] == "2026-08-31"
    assert payload["metadata"]["analyst_updated"] == "2026-08-31"
    assert payload["tiers"]["main"][0]["ts_code"] == "688012.SH"
    stock = payload["stocks"]["688012.SH"]
    assert stock["raw_stock"]["evidence_sources"] == ["annual-report-2025"]
    assert stock["raw_evidence_fields"]["evidence_as_of"] == "2026-08-31"
    assert stock["raw_evidence_fields"]["structural_as_of"] == "2026-08-31"
    assert stock["raw_evidence_fields"]["evidence_history"][0]["as_of"] == "2026-06-30"
    assert stock["assessment"]["status"] == "qualified"


def test_repeated_same_observed_at_same_content_is_idempotent(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _raw_config())
    first = snapshot_supply_chain_history(
        config=config_path,
        output_root=tmp_path / "pit",
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )
    second = snapshot_supply_chain_history(
        config=config_path,
        output_root=tmp_path / "pit",
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )

    assert second == first


def test_same_observed_at_different_content_is_rejected(tmp_path: Path) -> None:
    first = build_supply_chain_snapshot(
        _raw_config(),
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )
    second = build_supply_chain_snapshot(
        _raw_config(analyst_notes="changed"),
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )
    path = write_supply_chain_snapshot(first, output_root=tmp_path / "pit")

    with pytest.raises(FileExistsError, match="内容不同"):
        write_supply_chain_snapshot(second, output_root=path.parent)


def test_as_of_cannot_be_after_observed_shanghai_day() -> None:
    with pytest.raises(ValueError, match="上海自然日"):
        build_supply_chain_snapshot(
            _raw_config(),
            as_of="2026-09-04",
            observed_at="2026-09-03T15:59:59Z",
        )


def test_future_evidence_does_not_qualify() -> None:
    snapshot = build_supply_chain_snapshot(
        _raw_config(evidence_as_of="2026-09-04"),
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00Z",
    )

    assessment = snapshot["stocks"]["688012.SH"]["assessment"]
    assert assessment["status"] == "needs_review"
    assert "证据日期晚于审计基准日" in assessment["reviewFlags"]


def test_builder_accepts_raw_bytes_and_source_metadata() -> None:
    data = yaml.safe_dump(_raw_config(), allow_unicode=True).encode("utf-8")
    snapshot = build_supply_chain_snapshot(
        data,
        as_of="2026-09-03",
        observed_at="2026-09-03T05:00:00+00:00",
        source_ref="HEAD~1:kss/config/supply_chain.yaml",
        source_observed_at="2026-09-02T23:30:00+08:00",
    )

    assert snapshot["source"]["config_path"] is None
    assert snapshot["source"]["source_ref"] == "HEAD~1:kss/config/supply_chain.yaml"
    assert snapshot["source"]["source_observed_at"] == "2026-09-02T15:30:00Z"
