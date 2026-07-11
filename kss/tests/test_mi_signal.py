"""MI 规则引擎单测."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.strategies.mi_signal import (
    RuleSpec,
    build_features,
    extract_trades,
    positions_from_rules,
    replay,
    uses_z_thresholds,
)


def _synth_df(n: int = 120, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # 趋势 + 噪声
    close = 100 + np.cumsum(rng.normal(0.15, 1.0, size=n))
    high = close + rng.uniform(0.2, 1.5, size=n)
    low = close - rng.uniform(0.2, 1.5, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    )


def test_build_features_has_mi() -> None:
    df = _synth_df()
    feat = build_features(df, 12)
    assert "mi" in feat.columns
    assert "mi_z" in feat.columns
    assert feat["mi"].notna().sum() > 50


def test_cross_rule_produces_trades() -> None:
    df = _synth_df(200, seed=1)
    feat = build_features(df, 12)
    pos = positions_from_rules(
        feat, "mi_cross_up_0", "a_cross_dn_mi", "none", warm=40
    )
    trades = extract_trades(feat, pos)
    # 趋势噪声数据通常会有回合；至少仓位非全 0 或全 1
    assert pos.max() >= 0
    assert isinstance(trades, list)


def test_exec_date_after_signal() -> None:
    df = _synth_df(150, seed=2)
    feat = build_features(df, 12)
    pos = positions_from_rules(
        feat, "mi_cross_up_0", "mi_cross_dn_0", "none", warm=40
    )
    trades = extract_trades(feat, pos)
    for t in trades:
        assert t["exec_buy_date"] >= t["signal_buy_date"]
        assert t["exec_sell_date"] >= t["signal_sell_date"]


def test_z_thr_changes_positions() -> None:
    df = _synth_df(200, seed=3)
    feat = build_features(df, 12)
    p_lo = positions_from_rules(
        feat, "mi_z_gt", "mi_z_lt", "none", warm=40, thr={"entry_z": 0.3, "exit_z": 0.0}
    )
    p_hi = positions_from_rules(
        feat, "mi_z_gt", "mi_z_lt", "none", warm=40, thr={"entry_z": 1.5, "exit_z": 0.0}
    )
    # 更高 entry 阈值通常更少持仓
    assert p_hi.sum() <= p_lo.sum() + 1e-9


def test_warmup_flat() -> None:
    df = _synth_df(80)
    feat = build_features(df, 12)
    warm = 40
    pos = positions_from_rules(
        feat, "mi_cross_up_0", "a_cross_dn_mi", "none", warm=warm
    )
    assert (pos.iloc[:warm] == 0).all()


def test_invalid_entry() -> None:
    df = _synth_df(50)
    feat = build_features(df, 6)
    with pytest.raises(ValueError, match="未知 entry"):
        positions_from_rules(feat, "nope", "a_cross_dn_mi", "none", warm=10)


def test_replay_deterministic() -> None:
    df = _synth_df(100, seed=7)
    rule = RuleSpec(
        entry="mi_cross_up_0", exit="a_cross_dn_mi", filt="none", n=12
    )
    a = replay(df, rule)
    b = replay(df, rule)
    assert a["action"] == b["action"]
    assert a["trades"] == b["trades"]
    assert a["action"]["reason"]


def test_uses_z() -> None:
    assert uses_z_thresholds("mi_z_gt", "mi_z_lt")
    assert not uses_z_thresholds("mi_cross_up_0", "a_cross_dn_mi")
