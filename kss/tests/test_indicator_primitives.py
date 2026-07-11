"""指标基元库单测."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.indicators.primitives import (
    FAMILIES,
    FAMILY_BOLL_ATR,
    FAMILY_MA_CROSS,
    FAMILY_RSI_THRESHOLD,
    build_features,
    default_params,
    param_grid,
)


def _synth_df(n: int = 120, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.15, 1.0, size=n))
    high = close + rng.uniform(0.2, 1.5, size=n)
    low = close - rng.uniform(0.2, 1.5, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close}
    )


def _golden_cross_df(n: int = 80) -> pd.DataFrame:
    """构造前段横盘、后段单调上涨的价格序列，保证快线在后段上穿慢线."""
    flat = np.full(30, 100.0)
    ramp = 100.0 + np.cumsum(np.full(n - 30, 0.8))
    close = np.concatenate([flat, ramp])
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_default_params_valid(family: str) -> None:
    params = default_params(family)
    assert isinstance(params, dict) and params


@pytest.mark.parametrize("family", FAMILIES)
def test_param_grid_nonempty(family: str) -> None:
    grid = param_grid(family)
    assert isinstance(grid, list) and len(grid) >= 3
    for combo in grid:
        assert isinstance(combo, dict)


def test_unknown_family_rejected_with_allowed_list() -> None:
    with pytest.raises(ValueError, match="未知基元族"):
        default_params("nope")
    with pytest.raises(ValueError, match="ma_cross"):
        default_params("nope")


def test_ma_cross_features_and_golden_cross() -> None:
    df = _golden_cross_df()
    feat = build_features(df, FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    assert {"ma_fast", "ma_slow", "ret"}.issubset(feat.columns)
    # 后段单调上涨：快线最终应高于慢线（金叉已发生）。
    assert feat["ma_fast"].iloc[-1] > feat["ma_slow"].iloc[-1]


def test_ma_cross_requires_fast_lt_slow() -> None:
    df = _synth_df(60)
    with pytest.raises(ValueError, match="fast < slow"):
        build_features(df, FAMILY_MA_CROSS, {"fast": 20, "slow": 5, "kind": "sma"})


def test_ma_cross_unknown_kind_rejected() -> None:
    df = _synth_df(60)
    with pytest.raises(ValueError, match="ma_cross kind"):
        build_features(df, FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "wma"})


def test_rsi_threshold_features() -> None:
    df = _synth_df(120, seed=2)
    feat = build_features(
        df, FAMILY_RSI_THRESHOLD, {"period": 14, "entry_level": 30.0, "exit_level": 70.0}
    )
    assert "rsi" in feat.columns
    valid = feat["rsi"].dropna()
    assert not valid.empty
    assert valid.between(0, 100).all()


def test_boll_atr_features() -> None:
    df = _synth_df(120, seed=3)
    feat = build_features(
        df,
        FAMILY_BOLL_ATR,
        {"period": 20, "atr_period": 14, "atr_mult": 2.0, "atr_window": 10},
    )
    assert {"boll_upper", "boll_lower", "boll_mid", "atr", "rolling_high"}.issubset(
        feat.columns
    )
    valid = feat.dropna(subset=["boll_upper", "boll_lower"])
    assert (valid["boll_upper"] >= valid["boll_lower"]).all()


def test_short_sample_does_not_raise() -> None:
    """样本过短（<80 bar）：特征列自然产出 NaN，不抛异常。"""
    df = _synth_df(30, seed=4)
    for family in FAMILIES:
        feat = build_features(df, family, default_params(family))
        assert len(feat) == 30
