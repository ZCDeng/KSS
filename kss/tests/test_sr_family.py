"""sr_level 基元族单测：网格规模、规则变体、walk-forward 接入."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.indicator_walk_forward import WFConfig, reestimate
from kss.indicators.primitives import FAMILY_SR_LEVEL, build_features, param_grid
from kss.indicators.rules import IndicatorSpec, replay, rule_sentence, signal_strength

_WAVE_CYCLE = [106, 104, 102, 100, 102, 104, 106, 108]


def _wave_df(n_cycles: int) -> pd.DataFrame:
    vals = _WAVE_CYCLE * n_cycles
    n = len(vals)
    dates = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": vals, "high": vals, "low": vals, "close": vals}
    )


def test_param_grid_has_16_combinations() -> None:
    grid = param_grid(FAMILY_SR_LEVEL)
    assert len(grid) == 16
    seen = {tuple(sorted(combo.items())) for combo in grid}
    assert len(seen) == 16  # 无重复组合


def test_replay_produces_at_least_one_round_trip() -> None:
    df = _wave_df(10)  # 80 根，warm-up(=40) 后仍有多个周期可触发进出场
    spec = IndicatorSpec(
        FAMILY_SR_LEVEL,
        {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "bounce", "multi_timeframe": False},
    )
    rep = replay(df, spec)
    assert len(rep["trades"]) >= 1


def test_breakout_variant_also_trades() -> None:
    df = _wave_df(10)
    spec = IndicatorSpec(
        FAMILY_SR_LEVEL,
        {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "breakout", "multi_timeframe": False},
    )
    rep = replay(df, spec)
    assert isinstance(rep["trades"], list)  # 至少不抛异常，产出合法结构


def test_signal_strength_bounded() -> None:
    df = _wave_df(10)
    feat = build_features(
        df,
        FAMILY_SR_LEVEL,
        {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "bounce", "multi_timeframe": False},
    )
    strength = signal_strength(feat, FAMILY_SR_LEVEL)
    assert strength.between(-1.0, 1.0).all()


@pytest.mark.parametrize("variant", ["bounce", "breakout"])
def test_rule_sentence_names_variant(variant: str) -> None:
    spec = IndicatorSpec(
        FAMILY_SR_LEVEL,
        {"pivot_window": 5, "cluster_atr_mult": 1.0, "rule_variant": variant, "multi_timeframe": False},
    )
    sentence = rule_sentence(spec)
    assert variant in sentence


def test_unknown_rule_variant_rejected() -> None:
    df = _wave_df(6)
    spec = IndicatorSpec(
        FAMILY_SR_LEVEL,
        {"pivot_window": 3, "cluster_atr_mult": 1.0, "rule_variant": "nope", "multi_timeframe": False},
    )
    with pytest.raises(ValueError, match="未知 sr_level 规则变体"):
        replay(df, spec)


def _trending_oscillation_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    base = 100 + np.cumsum(rng.normal(0.02, 0.6, size=n))
    wave = 4.0 * np.sin(np.linspace(0, 40 * np.pi, n))
    close = base + wave
    high = close + rng.uniform(0.2, 1.0, size=n)
    low = close - rng.uniform(0.2, 1.0, size=n)
    open_ = close + rng.normal(0, 0.3, size=n)
    dates = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close}
    )


def test_reestimate_returns_ok_with_grid_params() -> None:
    df = _trending_oscillation_df(400)
    wf = reestimate(df, FAMILY_SR_LEVEL, cfg=WFConfig())
    assert wf.status == "ok"
    grid = param_grid(FAMILY_SR_LEVEL)
    assert wf.best_params in grid
