"""五维 GO/NO-GO 门禁裁决单测."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.indicators.gate import _dim_tradeable, judge
from kss.indicators.primitives import FAMILY_MA_CROSS
from kss.indicators.rules import IndicatorSpec


def _noise_df(n: int = 200, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.0, 1.0, size=n))
    high = close + rng.uniform(0.2, 1.0, size=n)
    low = close - rng.uniform(0.2, 1.0, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close}
    )


def _strong_trend_df(n: int = 300, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.3, 1.0, size=n))
    high = close + rng.uniform(0.2, 1.0, size=n)
    low = close - rng.uniform(0.2, 1.0, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close}
    )


def _v_shape_df(n: int = 110) -> pd.DataFrame:
    """深跌后快速反弹再走平；纯确定性构造，无随机数。"""
    down = np.cumsum(np.full(40, -0.5))
    up = down[-1] + np.cumsum(np.full(40, 1.0))
    tail = np.full(n - 80, up[-1])
    close = 100 + np.concatenate([down, up, tail])
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


def test_noise_series_no_go_on_economic_dim() -> None:
    df = _noise_df()
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    verdict = judge(df, spec)
    assert verdict.go is False
    econ = next(d for d in verdict.dimensions if d.name == "经济意义")
    assert econ.passed is False


def test_strong_trend_goes_go() -> None:
    df = _strong_trend_df()
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    verdict = judge(df, spec)
    assert verdict.go is True
    assert all(d.passed for d in verdict.dimensions)


def test_robustness_fails_on_param_cliff() -> None:
    """构造只有最优参数吃到行情、其余网格组合全程未开仓的场景——稳健维应拦。"""
    df = _v_shape_df()
    best = {"fast": 5, "slow": 10, "kind": "sma"}
    grid = [
        best,
        {"fast": 50, "slow": 90, "kind": "sma"},
        {"fast": 60, "slow": 95, "kind": "sma"},
    ]
    spec = IndicatorSpec(FAMILY_MA_CROSS, best)
    verdict = judge(df, spec, grid=grid)
    robust = next(d for d in verdict.dimensions if d.name == "稳健")
    assert robust.passed is False
    assert verdict.go is False


def test_tradeable_fails_on_sparse_trades() -> None:
    """数年仅 1 笔交易——可交易维应拦，不管收益本身多好。"""
    n = 756  # 约 3 个交易年
    feat = pd.DataFrame({"close": np.arange(n, dtype=float)})
    trades = [
        {
            "signal_buy_date": "2021-01-04",
            "exec_buy_date": "2021-01-05",
            "signal_sell_date": "2023-11-01",
            "exec_sell_date": "2023-11-02",
            "trade_return": 0.30,
            "hold_days": 700,
        }
    ]
    verdict = _dim_tradeable(feat, trades)
    assert verdict.passed is False
    assert verdict.value["trade_count"] == 1


def test_tradeable_passes_with_reasonable_frequency() -> None:
    n = 252
    feat = pd.DataFrame({"close": np.arange(n, dtype=float)})
    trades = [
        {"trade_return": 0.02, "hold_days": 10},
        {"trade_return": 0.015, "hold_days": 8},
        {"trade_return": -0.005, "hold_days": 5},
        {"trade_return": 0.01, "hold_days": 12},
    ]
    verdict = _dim_tradeable(feat, trades)
    assert verdict.passed is True


def test_interpretable_and_operational_always_pass() -> None:
    df = _noise_df()
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    verdict = judge(df, spec)
    interp = next(d for d in verdict.dimensions if d.name == "可解释")
    ops = next(d for d in verdict.dimensions if d.name == "运维")
    assert interp.passed is True
    assert ops.passed is True
    assert "均线交叉" in interp.value["rule_sentence"]


def test_judge_deterministic() -> None:
    df = _strong_trend_df()
    spec = IndicatorSpec(FAMILY_MA_CROSS, {"fast": 5, "slow": 20, "kind": "sma"})
    a = judge(df, spec)
    b = judge(df, spec)
    assert a.go == b.go
    assert [d.value for d in a.dimensions] == [d.value for d in b.dimensions]
