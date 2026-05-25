"""kss/macro/queries.py 单测 (plan 010 #19).

覆盖 RegimeInfo / ValuationInfo / RotationHint dataclass 查询函数.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kss.macro.queries import (
    RegimeInfo,
    RotationHint,
    ValuationInfo,
    _is_stale,
    lookup_regime,
    lookup_rotation,
    lookup_valuation,
)


@pytest.fixture
def fake_regime_parquet(tmp_path) -> Path:
    p = tmp_path / "regime.parquet"
    pd.DataFrame({
        "trade_date": ["20260520", "20260521", "20260522"],
        "stage": ["I", "I", "II"],
        "confidence": [0.65, 0.70, 0.75],
        "stage_raw": ["I", "I", "II"],
        "n_signals": [4, 4, 4],
    }).to_parquet(p, index=False)
    return p


@pytest.fixture
def fake_valuation_parquet(tmp_path) -> Path:
    p = tmp_path / "val.parquet"
    pd.DataFrame({
        "trade_date": ["20260520", "20260521", "20260522"],
        "pe": [14.0, 14.4, 14.5],
        "risk_free": [0.018, 0.018, 0.018],
        "n_years": [0.85, 0.90, 0.91],
    }).to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------------------- #
# lookup_regime
# ---------------------------------------------------------------------------- #


def test_lookup_regime_exact_match(fake_regime_parquet):
    info = lookup_regime("20260522", path=fake_regime_parquet)
    assert info == RegimeInfo(stage="II", confidence=0.75, as_of="20260522", stale=False)


def test_lookup_regime_fallback_to_latest(fake_regime_parquet):
    """trade_date 不在 parquet → 取 ≤ trade_date 的最新行."""
    info = lookup_regime("20260530", path=fake_regime_parquet)
    assert info is not None
    assert info.as_of == "20260522"
    assert info.stale is True   # 8 days > 3 days max_stale


def test_lookup_regime_returns_none_when_missing(tmp_path):
    info = lookup_regime("20260522", path=tmp_path / "nope.parquet")
    assert info is None


def test_lookup_regime_unknown_returns_none(tmp_path):
    p = tmp_path / "regime.parquet"
    pd.DataFrame({
        "trade_date": ["20260522"],
        "stage": ["Unknown"],
        "confidence": [0.0],
    }).to_parquet(p, index=False)
    info = lookup_regime("20260522", path=p)
    assert info is None


def test_lookup_regime_as_dict_round_trip(fake_regime_parquet):
    info = lookup_regime("20260522", path=fake_regime_parquet)
    assert info is not None
    d = info.as_dict()
    assert d["stage"] == "II"
    assert d["confidence"] == 0.75
    assert d["stale"] is False


# ---------------------------------------------------------------------------- #
# lookup_valuation
# ---------------------------------------------------------------------------- #


def test_lookup_valuation_returns_typed_info(fake_valuation_parquet):
    info = lookup_valuation("20260522", path=fake_valuation_parquet)
    assert isinstance(info, ValuationInfo)
    assert info.pe == 14.5
    assert info.n_years == pytest.approx(0.91)
    assert info.rule == "normal"
    assert info.percentile_5y is not None


def test_lookup_valuation_missing_returns_none(tmp_path):
    info = lookup_valuation("20260522", path=tmp_path / "nope.parquet")
    assert info is None


def test_lookup_valuation_nan_n_returns_none(tmp_path):
    p = tmp_path / "val.parquet"
    pd.DataFrame({
        "trade_date": ["20260522"],
        "pe": [14.5],
        "risk_free": [0.018],
        "n_years": [float("nan")],
    }).to_parquet(p, index=False)
    info = lookup_valuation("20260522", path=p)
    assert info is None


# ---------------------------------------------------------------------------- #
# lookup_rotation
# ---------------------------------------------------------------------------- #


def test_lookup_rotation_stage_II_returns_commodities():
    info = lookup_rotation("II")
    assert isinstance(info, RotationHint)
    assert "钢铁" in info.preferred or "煤炭" in info.preferred


def test_lookup_rotation_unknown_stage_returns_none():
    assert lookup_rotation("X") is None
    assert lookup_rotation(None) is None


# ---------------------------------------------------------------------------- #
# _is_stale
# ---------------------------------------------------------------------------- #


def test_is_stale_within_window():
    assert _is_stale("20260520", "20260522", max_days=3) is False


def test_is_stale_outside_window():
    assert _is_stale("20260515", "20260525", max_days=3) is True


def test_is_stale_invalid_input_returns_false():
    """Bad date format shouldn't crash; fall through to non-stale."""
    assert _is_stale("not-a-date", "20260522", max_days=3) is False
