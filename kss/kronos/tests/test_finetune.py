"""U3 微调窗口构造测试（纯逻辑；真实微调见 scripts/kronos_finetune.py 冒烟）."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.kronos.finetune import build_training_windows


def _frame(symbol: str, n: int, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(3)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "ts_code": symbol,
        "trade_date": dates,
        "open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
        "vol": rng.uniform(1e5, 5e5, n),
        "amount": rng.uniform(1e5, 5e5, n),
    })


def test_shapes_lookback_plus_predict_plus_one() -> None:
    """窗口长度 = lookback + predict + 1；特征 6 列、stamp 5 列."""
    frames = {"A.SZ": _frame("A.SZ", 200)}
    x, x_stamp = build_training_windows(
        frames, cutoff="2024-12-31", lookback_window=90, predict_window=10,
    )
    window = 90 + 10 + 1
    assert x.shape[1:] == (window, 6)
    assert x_stamp.shape[1:] == (window, 5)
    assert len(x) == len(x_stamp)


def test_cutoff_excludes_future_bars() -> None:
    """截断日 D 之后的 bar 不进训练：早 cutoff → 样本数更少."""
    frames = {"A.SZ": _frame("A.SZ", 200)}  # 2024-01-02 起 200 bday，window=66
    common = dict(lookback_window=60, predict_window=5)
    x_early, _ = build_training_windows(frames, cutoff="2024-06-03", **common)
    x_late, _ = build_training_windows(frames, cutoff="2024-09-02", **common)
    assert 0 < len(x_early) < len(x_late)


def test_normalization_bounded_by_clip() -> None:
    """归一化 + clip：所有值落在 [-clip, clip]."""
    frames = {"A.SZ": _frame("A.SZ", 150)}
    x, _ = build_training_windows(
        frames, cutoff="2024-12-31", lookback_window=60, predict_window=5, clip=5.0,
    )
    assert np.isfinite(x).all()
    assert x.min() >= -5.0 - 1e-6 and x.max() <= 5.0 + 1e-6


def test_insufficient_history_yields_no_windows() -> None:
    """全部标的历史短于 window → 抛 ValueError（无可用窗口）."""
    frames = {"A.SZ": _frame("A.SZ", 30)}
    with pytest.raises(ValueError, match="没有可用训练窗口"):
        build_training_windows(
            frames, cutoff="2024-12-31", lookback_window=90, predict_window=10,
        )


def test_max_samples_caps_count() -> None:
    frames = {"A.SZ": _frame("A.SZ", 300)}
    x, _ = build_training_windows(
        frames, cutoff="2025-12-31", lookback_window=60, predict_window=5, max_samples=20,
    )
    assert len(x) == 20
