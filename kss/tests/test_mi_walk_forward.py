"""MI walk-forward 单测."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.backtest.mi_walk_forward import WFConfig, reestimate


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
    r = reestimate(_df(50), "mi_cross_up_0", "a_cross_dn_mi", "none")
    assert r.status == "skipped"


def test_ok_on_long_sample() -> None:
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    r = reestimate(
        _df(350, seed=1),
        "mi_cross_up_0",
        "a_cross_dn_mi",
        "none",
        cfg=cfg,
    )
    assert r.status == "ok"
    assert r.best_n in cfg.n_grid
    assert r.param_history
    assert r.replay.get("action")


def test_deterministic() -> None:
    cfg = WFConfig(train_window=100, retrain_freq=50, holdout_bars=30, min_trades=1)
    df = _df(300, seed=9)
    a = reestimate(df, "mi_cross_up_0", "mi_cross_dn_0", "none", cfg=cfg)
    b = reestimate(df, "mi_cross_up_0", "mi_cross_dn_0", "none", cfg=cfg)
    assert a.best_n == b.best_n
    assert a.best_thr == b.best_thr
