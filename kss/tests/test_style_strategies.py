"""风格对照策略 U1 单测."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta
from kss.strategies.styles import (
    STYLE_ORDER,
    build_all_style_strategies,
    build_style_strategy,
    get_style_meta,
)


def _panel(n_sym: int = 10, n_days: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    rows: list[dict] = []
    for d in dates:
        for i in range(n_sym):
            rows.append(
                {
                    "trade_date": d,
                    "symbol": f"{600000 + i}.SH",
                    "volatility_20d": 0.01 * (i + 1),
                    "pb": 0.5 + 0.2 * i,
                    "ret_5d": -0.05 + 0.01 * i,
                    "sector_momentum_score": float(n_sym - i),
                    "next_open_ret": 0.001 * (i % 3 - 1),
                }
            )
    return pd.DataFrame(rows)


def test_style_order_and_meta_complete() -> None:
    assert len(STYLE_ORDER) == 4
    for sid in STYLE_ORDER:
        meta = get_style_meta(sid)
        assert meta.style_id == sid
        assert meta.source_tags
        assert meta.name


def test_build_all_four_strategies() -> None:
    strats = build_all_style_strategies(top_n=5)
    assert [s.meta.style_id for s in strats] == list(STYLE_ORDER)


def test_low_vol_prefers_lowest_volatility() -> None:
    s = build_style_strategy("style_low_vol", top_n=3)
    df = _panel()
    date = df["trade_date"].max()
    sig = s.generate_signals(df, date=date)
    assert len(sig) == 3
    assert list(sig["symbol"]) == ["600000.SH", "600001.SH", "600002.SH"]
    assert sig.iloc[0]["factor_value"] < sig.iloc[-1]["factor_value"]
    assert "低波" in sig.iloc[0]["selection_reason"]
    assert sig.iloc[0]["style_id"] == "style_low_vol"


def test_value_prefers_low_pb() -> None:
    s = build_style_strategy("style_value", top_n=2)
    sig = s.generate_signals(_panel(), date=_panel()["trade_date"].max())
    assert list(sig["symbol"]) == ["600000.SH", "600001.SH"]


def test_short_reversal_prefers_weak_recent() -> None:
    s = build_style_strategy("style_short_reversal", top_n=2)
    sig = s.generate_signals(_panel(), date=_panel()["trade_date"].max())
    assert list(sig["symbol"]) == ["600000.SH", "600001.SH"]


def test_sector_rotation_prefers_high_score() -> None:
    s = build_style_strategy("style_sector_rotation", top_n=2)
    sig = s.generate_signals(_panel(), date=_panel()["trade_date"].max())
    assert list(sig["symbol"]) == ["600000.SH", "600001.SH"]
    assert sig.iloc[0]["factor_value"] >= sig.iloc[1]["factor_value"]


def test_all_nan_factor_raises() -> None:
    s = build_style_strategy("style_value", top_n=5)
    df = _panel()
    df["pb"] = np.nan
    with pytest.raises(ValueError, match="无可用因子"):
        s.generate_signals(df, date=df["trade_date"].max())


def test_top_n_clamped_to_available() -> None:
    s = build_style_strategy("style_low_vol", top_n=50)
    df = _panel(n_sym=4)
    sig = s.generate_signals(df, date=df["trade_date"].max())
    assert len(sig) == 4
    assert pytest.approx(sig["planned_weight"].sum()) == 1.0


def test_missing_factor_column_raises_keyerror() -> None:
    s = build_style_strategy("style_sector_rotation", top_n=3)
    df = _panel().drop(columns=["sector_momentum_score"])
    with pytest.raises(KeyError, match="sector_momentum_score"):
        s.generate_signals(df, date=df["trade_date"].max())


def test_backtest_returns_series() -> None:
    s = build_style_strategy("style_low_vol", top_n=3)
    out = s.backtest(_panel(n_days=8))
    assert not out.empty
    assert "portfolio_return" in out.columns
    assert out["style_id"].iloc[0] == "style_low_vol"


def test_evaluate_gate_on_synthetic_returns() -> None:
    s = build_style_strategy("style_value", top_n=5)
    # 强正收益序列更容易 passed；此处只断言结构
    rets = pd.Series(np.random.default_rng(0).normal(0.002, 0.01, size=80))
    gate = s.evaluate_gate(rets)
    assert gate.label in ("passed", "research_blocked")
    assert isinstance(gate.deployable, bool)
    assert isinstance(gate.failures, list)


def test_unknown_style_id() -> None:
    with pytest.raises(KeyError):
        build_style_strategy("style_nope")


def test_custom_meta_reason() -> None:
    meta = StyleMeta(
        style_id="style_x",
        name="X",
        factor_col="pb",
        direction="asc",
        source_tags=("t",),
        reason_template="{style_name}:{rank_position}",
    )
    s = FactorRankStyleStrategy(meta, top_n=1)
    sig = s.generate_signals(_panel(), date=_panel()["trade_date"].max())
    assert sig.iloc[0]["selection_reason"] == "X:1"
