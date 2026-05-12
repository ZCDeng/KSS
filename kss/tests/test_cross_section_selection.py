"""WF IC selector + walk_forward feature_selector 路径集成测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import CostModel
from kss.backtest.engine import BacktestEngine
from kss.features.cross_section_selection import (
    make_combined_selector,
    make_ic_topk_selector,
)


def _make_panel(
    n_dates: int = 80, n_stocks: int = 15, seed: int = 0,
) -> pd.DataFrame:
    """合成 panel：f1 强 informative, f2~f4 噪声."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        for s in range(n_stocks):
            f1 = rng.standard_normal()
            f2 = rng.standard_normal()
            f3 = rng.standard_normal()
            f4 = rng.standard_normal()
            r5 = 0.4 * f1 + rng.standard_normal() * 0.4
            r_next = 0.1 * f1 + rng.standard_normal() * 0.4
            rows.append({
                "trade_date": d, "symbol": f"S{s:03d}",
                "f1": f1, "f2": f2, "f3": f3, "f4": f4,
                "future_return_5d": r5,
                "next_day_return": r_next,
            })
    return pd.DataFrame(rows)


# ====================================================================== #
# make_ic_topk_selector
# ====================================================================== #


class TestMakeICTopKSelector:

    def test_picks_strongest_factor(self) -> None:
        panel = _make_panel(seed=0)
        selector = make_ic_topk_selector(
            ["f1", "f2", "f3", "f4"],
            horizon="future_return_5d", top_k=1,
        )
        # f1 是唯一 informative → 必入选
        train_df = panel.iloc[: 30 * 15]  # 30 天
        selected = selector(train_df)
        assert "f1" in selected
        assert len(selected) == 1

    def test_top_k_size(self) -> None:
        panel = _make_panel()
        selector = make_ic_topk_selector(
            ["f1", "f2", "f3", "f4"], top_k=3,
        )
        selected = selector(panel)
        assert len(selected) == 3

    def test_min_t_stat_filter(self) -> None:
        """min_t_stat 过滤后无因子入选时退化到 top_k."""
        panel = _make_panel(seed=2)
        # 设置极高门槛
        selector = make_ic_topk_selector(
            ["f2", "f3", "f4"],  # 都是噪声，不太可能过 t=3 门槛
            top_k=2, min_t_stat=3.0,
        )
        selected = selector(panel)
        # 退化到 top_k=2，应有 2 个
        assert len(selected) == 2

    def test_empty_panel_does_not_crash(self) -> None:
        """空 panel 时 selector 不应抛错；返回任何值，下游 dropna 兜底."""
        empty = pd.DataFrame({
            "trade_date": [], "symbol": [],
            "f1": [], "future_return_5d": [],
        })
        selector = make_ic_topk_selector(["f1"], top_k=1)
        # 不抛错即可——具体返回值 [] 或 ["f1"]（IC=NaN）都属于合理边界处理
        result = selector(empty)
        assert isinstance(result, list)


# ====================================================================== #
# make_combined_selector
# ====================================================================== #


class TestMakeCombinedSelector:

    def test_union(self) -> None:
        panel = _make_panel()
        sel_a = make_ic_topk_selector(["f1", "f2"], top_k=1)
        sel_b = make_ic_topk_selector(["f3", "f4"], top_k=1)
        combined = make_combined_selector([sel_a, sel_b], mode="union")
        out = combined(panel)
        assert len(out) == 2  # 两个 selector 各选 1，union 通常 2 个

    def test_intersection_empty_when_disjoint(self) -> None:
        panel = _make_panel()
        sel_a = make_ic_topk_selector(["f1", "f2"], top_k=1)
        sel_b = make_ic_topk_selector(["f3", "f4"], top_k=1)
        combined = make_combined_selector([sel_a, sel_b], mode="intersection")
        out = combined(panel)
        assert out == []

    def test_empty_selectors_raises(self) -> None:
        with pytest.raises(ValueError, match="selectors 不能为空"):
            make_combined_selector([])


# ====================================================================== #
# walk_forward feature_selector 路径
# ====================================================================== #


class TestWalkForwardWithSelector:

    def test_smoke_runs(self) -> None:
        panel = _make_panel()
        engine = BacktestEngine(CostModel())
        selector = make_ic_topk_selector(
            ["f1", "f2", "f3", "f4"], top_k=2,
        )
        res = engine.walk_forward(
            panel, ["f1", "f2", "f3", "f4"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            feature_selector=selector,
        )
        assert res is not None
        assert not res.empty
        assert "panel" in res.attrs

    def test_selector_returns_invalid_factor_raises(self) -> None:
        """selector 返回非 candidate 因子应抛错."""
        panel = _make_panel()
        engine = BacktestEngine(CostModel())

        def bad_selector(train_df: pd.DataFrame) -> list[str]:
            return ["nonexistent_factor"]

        with pytest.raises(ValueError, match="非候选列"):
            engine.walk_forward(
                panel, ["f1", "f2"], "future_return_5d",
                train_window=20, retrain_freq=5, top_pct=0.3,
                min_train=15, min_test=5, min_stocks=3,
                feature_selector=bad_selector,
            )

    def test_selector_returns_empty_skips_window(self) -> None:
        """selector 返回 [] 时本窗跳过，不影响其他窗."""
        panel = _make_panel()
        engine = BacktestEngine(CostModel())

        call_count = {"n": 0}

        def alternating_selector(train_df: pd.DataFrame) -> list[str]:
            call_count["n"] += 1
            # 隔一窗返回空 → 验证跳过
            if call_count["n"] % 2 == 0:
                return []
            return ["f1"]

        res = engine.walk_forward(
            panel, ["f1", "f2", "f3", "f4"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            feature_selector=alternating_selector,
        )
        # 至少 selector 被调用过
        assert call_count["n"] > 0
        # 仍能产生回测结果（非空窗仍跑出来）
        assert res is not None

    def test_anti_lookahead_first_window_only_sees_train_data(self) -> None:
        """第一次 retrain 时 selector 只能看到训练窗口内的数据.

        构造：前半段 f1 informative；后半段 f2 informative.
        第一次 selector 应选 f1（训练窗口在前半段）.
        """
        rng = np.random.default_rng(0)
        n_dates, n_stocks = 100, 15
        rows = []
        for i, d in enumerate(pd.date_range("2024-01-01", periods=n_dates)):
            for s in range(n_stocks):
                f1 = rng.standard_normal()
                f2 = rng.standard_normal()
                if i < n_dates // 2:
                    r = 0.5 * f1 + rng.standard_normal() * 0.3  # f1 informative
                else:
                    r = 0.5 * f2 + rng.standard_normal() * 0.3  # f2 informative
                rows.append({
                    "trade_date": d, "symbol": f"S{s:03d}",
                    "f1": f1, "f2": f2,
                    "future_return_5d": r,
                    "next_day_return": r * 0.3,
                })
        df = pd.DataFrame(rows)

        engine = BacktestEngine(CostModel())
        first_selection: list[list[str]] = []

        def spy_selector(train_df: pd.DataFrame) -> list[str]:
            sel = make_ic_topk_selector(["f1", "f2"], top_k=1)(train_df)
            first_selection.append(sel)
            return sel

        engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=30, retrain_freq=10, top_pct=0.3,
            min_train=20, min_test=5, min_stocks=3,
            feature_selector=spy_selector,
        )
        # 第一次调用：训练窗口在前半段（f1 informative）
        assert first_selection, "selector 至少应被调用一次"
        assert "f1" in first_selection[0], (
            f"第一次 retrain 应选 f1（前半段 informative），实际: {first_selection[0]}"
        )

    def test_backward_compat_no_selector_unchanged(self) -> None:
        """不传 feature_selector 时行为与传 feature_cols 一致."""
        panel = _make_panel()
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            panel, ["f1", "f2", "f3", "f4"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
        )
        assert res is not None
        # 该路径与既有测试用例同结构（test_backtest 已覆盖）
        assert "trade_date" in res.columns
