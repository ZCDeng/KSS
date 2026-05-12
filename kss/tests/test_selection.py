"""FeatureSelector 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.features.selection import FeatureSelector


def _make_panel(
    n_dates: int = 60, n_stocks: int = 10, seed: int = 0
) -> pd.DataFrame:
    """构造 informative + noise 混合 panel."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        for s in range(n_stocks):
            good1 = rng.standard_normal()
            good2 = rng.standard_normal()
            noise1 = rng.standard_normal()
            noise2 = rng.standard_normal()
            noise3 = rng.standard_normal()
            label = 0.5 * good1 + 0.3 * good2 + rng.standard_normal() * 0.1
            rows.append({
                "trade_date": d, "symbol": f"S{s}",
                "good1": good1, "good2": good2,
                "noise1": noise1, "noise2": noise2, "noise3": noise3,
                "future_return_5d": label,
            })
    return pd.DataFrame(rows)


class TestFeatureSelector:

    def test_informative_features_picked(self) -> None:
        """gain_pct=0.95 → good1/good2 都应入选；同时排除 noise."""
        panel = _make_panel(seed=0)
        cands = ["good1", "good2", "noise1", "noise2", "noise3"]
        selected = FeatureSelector.stable_features(
            panel, cands, "future_return_5d",
            n_folds=3, gain_pct=0.95,
        )
        # good1 / good2 必须入选（高 gain_pct 下）
        assert "good1" in selected
        assert "good2" in selected
        # 选中数 ≤ 候选数
        assert len(selected) <= len(cands)
        # 至少应该比所有 noise 少（即不会全选）
        assert len(selected) <= 4

    def test_top_k_returns_exact_count(self) -> None:
        panel = _make_panel()
        cands = ["good1", "good2", "noise1", "noise2", "noise3"]
        selected = FeatureSelector.stable_features(
            panel, cands, "future_return_5d", n_folds=3, top_k=2,
        )
        assert len(selected) == 2
        # 排名前 2 必含 good1 / good2
        assert set(selected) == {"good1", "good2"}

    def test_missing_columns_raises(self) -> None:
        panel = _make_panel()
        with pytest.raises(ValueError, match="缺少列"):
            FeatureSelector.stable_features(
                panel, ["good1", "no_such_col"], "future_return_5d",
            )

    def test_invalid_n_folds(self) -> None:
        panel = _make_panel()
        with pytest.raises(ValueError, match="n_folds"):
            FeatureSelector.stable_features(
                panel, ["good1"], "future_return_5d", n_folds=1,
            )
