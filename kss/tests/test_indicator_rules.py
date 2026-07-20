"""通用规则引擎单测."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.indicators.primitives import (
    FAMILY_BOLL_ATR,
    FAMILY_MA_CROSS,
    FAMILY_RSI_THRESHOLD,
    FAMILY_SR_LEVEL,
)
from kss.indicators.rules import IndicatorSpec, compute_positions, warm_period


def _synth_df(n: int = 150, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.15, 1.0, size=n))
    high = close + rng.uniform(0.2, 1.5, size=n)
    low = close - rng.uniform(0.2, 1.5, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close}
    )


def _golden_cross_df(n: int = 100) -> pd.DataFrame:
    # 平台期须盖过 warm-up(=40)，金叉发生在平台结束后才会被状态机检出
    # （边沿触发：若跨越发生在 warm-up 窗口内，状态机重开检测时已处于"已上穿"态，
    # 不会再触发，见 mi_signal 同款状态机设计）。
    flat = np.full(45, 100.0)
    ramp = 100.0 + np.cumsum(np.full(n - 45, 0.8))
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


def _breakout_df(n: int = 100) -> pd.DataFrame:
    flat = 100.0 + np.random.default_rng(9).normal(0, 0.3, size=60)
    surge = 100.0 + np.cumsum(np.full(n - 60, 1.2))
    close = np.concatenate([flat, surge])
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


def test_indicator_spec_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="未知基元族"):
        IndicatorSpec("nope", {})


def test_warm_period_covers_longest_window() -> None:
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 60, "kind": "sma"})
    assert warm_period(spec) >= 65


def test_positions_flat_before_warmup() -> None:
    df = _synth_df(120, seed=1)
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    feat = compute_positions(df, spec)
    warm = warm_period(spec)
    assert (feat["position"].iloc[:warm] == 0).all()


def test_ma_cross_golden_cross_enters_position() -> None:
    df = _golden_cross_df()
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    feat = compute_positions(df, spec)
    assert feat["position"].iloc[-1] > 0


def test_rsi_threshold_entry_on_upcross() -> None:
    # 构造一段深跌后反弹的序列，确保 RSI 从低位上穿 entry_level。
    down = 100.0 - np.cumsum(np.full(40, 1.0))
    up = down[-1] + np.cumsum(np.full(40, 1.0))
    close = np.concatenate([np.full(20, 100.0), down, up])
    dates = pd.bdate_range("2024-01-01", periods=len(close))
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )
    spec = IndicatorSpec(
        FAMILY_RSI_THRESHOLD, {"period": 14, "entry_level": 30.0, "exit_level": 70.0}
    )
    feat = compute_positions(df, spec)
    assert feat["position"].sum() > 0


def test_boll_atr_entry_on_breakout() -> None:
    df = _breakout_df()
    spec = IndicatorSpec(
        FAMILY_BOLL_ATR, {"period": 20, "atr_period": 14, "atr_mult": 2.0, "atr_window": 10}
    )
    feat = compute_positions(df, spec)
    assert feat["position"].sum() > 0


@pytest.mark.parametrize(
    "family,params",
    [
        (FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"}),
        (FAMILY_RSI_THRESHOLD, {"period": 14, "entry_level": 30.0, "exit_level": 70.0}),
        (
            FAMILY_BOLL_ATR,
            {"period": 20, "atr_period": 14, "atr_mult": 2.0, "atr_window": 10},
        ),
        (
            FAMILY_SR_LEVEL,
            {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "bounce", "multi_timeframe": False},
        ),
        (
            FAMILY_SR_LEVEL,
            {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "breakout", "multi_timeframe": False},
        ),
    ],
)
def test_no_lookahead(family: str, params: dict) -> None:
    """截断未来行数不改变过去的仓位——signal 只依赖历史数据。"""
    df = _synth_df(150, seed=5)
    spec = IndicatorSpec(family, params)
    feat_full = compute_positions(df, spec)
    truncated = df.iloc[:100].copy()
    feat_trunc = compute_positions(truncated, spec)
    pd.testing.assert_series_equal(
        feat_full["position"].iloc[:100].reset_index(drop=True),
        feat_trunc["position"].reset_index(drop=True),
    )


def test_compute_positions_deterministic() -> None:
    df = _synth_df(120, seed=7)
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    a = compute_positions(df, spec)
    b = compute_positions(df, spec)
    pd.testing.assert_series_equal(a["position"], b["position"])


def test_short_sample_does_not_raise() -> None:
    df = _synth_df(30, seed=8)
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    feat = compute_positions(df, spec)
    assert (feat["position"] == 0).all()
