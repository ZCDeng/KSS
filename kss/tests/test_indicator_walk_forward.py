"""通用 walk-forward 回测单测."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.backtest.indicator_walk_forward import WFConfig, reestimate
from kss.indicators.primitives import FAMILY_MA_CROSS


def _df(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50 + np.cumsum(rng.normal(0.05, 0.8, size=n))
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close + rng.normal(0, 0.2, n),
            "high": close + 1,
            "low": close - 1,
            "close": close,
        }
    )


def test_skip_short_sample() -> None:
    r = reestimate(_df(50), FAMILY_MA_CROSS)
    assert r.status == "skipped"


def test_ok_on_long_sample() -> None:
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    r = reestimate(_df(350, seed=1), FAMILY_MA_CROSS, cfg=cfg)
    assert r.status == "ok"
    assert r.best_params
    assert r.replay["family"] == FAMILY_MA_CROSS
    assert "action" in r.replay


def test_param_history_recorded() -> None:
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    r = reestimate(_df(350, seed=2), FAMILY_MA_CROSS, cfg=cfg)
    assert r.status == "ok"
    assert len(r.param_history) >= 1
    for entry in r.param_history:
        assert {"asof", "params", "score"}.issubset(entry.keys())


def test_reestimate_deterministic() -> None:
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    df = _df(350, seed=3)
    a = reestimate(df, FAMILY_MA_CROSS, cfg=cfg)
    b = reestimate(df, FAMILY_MA_CROSS, cfg=cfg)
    assert a.best_params == b.best_params
    assert a.param_history == b.param_history
