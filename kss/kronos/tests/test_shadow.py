"""U5 影子打分测试（R8-R11, AE2）."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.backtest.significance import Significance
from kss.kronos.shadow import (
    build_shadow_returns,
    score_shadow,
    update_blacklist_state,
    week_metrics_ok,
    weekly_validation_metrics,
)


class TestScoreShadow:
    def test_covers_ae2_high_sharpe_fails_mined_gate(self) -> None:
        """Covers AE2: 原始 Sharpe 高但 mined DSR 未过闸门 → 未毕业、维持只读.

        同一序列在宽松族(prior)可能过闸门，在 mined(n_trials=100) 被拒——
        证明是 mined 惩罚在拒绝，而非序列本身没信号（significance.py 设计意图）。
        """
        rng = np.random.default_rng(0)
        # 60 obs，中等强度正收益：raw Sharpe 高，但 mined 惩罚足以拒。
        r = pd.Series(rng.normal(0.0015, 0.01, 60))
        v = score_shadow(r, forward_window=60)
        assert v.sharpe > 1.0  # 原始 Sharpe 确实高
        assert v.gate_passed is False  # mined 闸门未过
        assert v.graduated is False  # 维持只读
        # 对照：同序列在 prior 族（n_trials=1）更可能过闸门
        prior = Significance.is_deployable(r, strategy_family="prior", return_details=True)
        assert prior["dsr"] >= v.dsr  # mined 的 DSR 不高于 prior

    def test_happy_strong_long_series_graduates(self) -> None:
        """happy: 极强信号 + 满窗口 → 过闸门且毕业."""
        # 近常数正收益：t-stat 巨大、DSR→1，即使 mined 也过。
        rng = np.random.default_rng(1)
        r = pd.Series(0.01 + rng.normal(0, 0.0005, 80))
        v = score_shadow(r, forward_window=60)
        assert v.gate_passed is True
        assert v.window_full is True
        assert v.graduated is True

    def test_edge_window_not_full_no_graduate(self) -> None:
        """edge: 窗口未满即使过闸门也不毕业."""
        rng = np.random.default_rng(2)
        r = pd.Series(0.01 + rng.normal(0, 0.0005, 30))  # 强信号但仅 30 < 60
        v = score_shadow(r, forward_window=60)
        assert v.window_full is False
        assert v.graduated is False


def _pred_row(symbol, base_date, target_date, base_close, point_close, lower, upper, horizon=1):
    point_ret = point_close / base_close - 1.0
    return {
        "ts_code": symbol, "base_date": base_date, "target_date": target_date,
        "horizon": horizon, "point_close": point_close, "lower_close": lower,
        "upper_close": upper, "point_ret": point_ret, "base_close": base_close,
    }


def test_build_shadow_returns_signal_times_realized() -> None:
    """方向信号 × 真实收益聚合为日度序列."""
    preds = pd.DataFrame([
        _pred_row("A.SZ", "2025-04-01", "2025-04-02", 10.0, 10.5, 10.0, 11.0),  # 预测涨
        _pred_row("B.SZ", "2025-04-01", "2025-04-02", 20.0, 19.0, 18.0, 20.0),  # 预测跌
    ])
    realized = {
        "A.SZ": pd.DataFrame({"trade_date": ["2025-04-02"], "close": [11.0]}),  # 真涨 +10%
        "B.SZ": pd.DataFrame({"trade_date": ["2025-04-02"], "close": [18.0]}),  # 真跌 -10%
    }
    s = build_shadow_returns(preds, realized, horizon=1)
    # A: signal+1 × +0.10 ; B: signal-1 × -0.10 → 两者都 +0.10，截面均值 +0.10
    assert len(s) == 1
    assert abs(s.iloc[0] - 0.10) < 1e-9


class TestWeeklyMetrics:
    def test_covers_r11_brier_dir_coverage(self) -> None:
        """Covers R11: 周校验产出 Brier/方向/覆盖率三项并可记录."""
        preds = pd.DataFrame([
            _pred_row("A.SZ", "2025-04-01", "2025-04-02", 10.0, 10.5, 9.5, 11.5),
            _pred_row("B.SZ", "2025-04-01", "2025-04-02", 20.0, 19.0, 18.0, 21.0),
        ])
        realized = {
            "A.SZ": pd.DataFrame({"trade_date": ["2025-04-02"], "close": [10.6]}),  # 真涨，方向对，落带内
            "B.SZ": pd.DataFrame({"trade_date": ["2025-04-02"], "close": [19.5]}),  # 真跌，方向对，落带内
        }
        m = weekly_validation_metrics(preds, realized)
        assert m["n"] == 2
        assert 0.0 <= m["brier"] <= 1.0
        assert m["dir_rate"] == 1.0
        assert m["coverage"] == 1.0

    def test_no_verifiable_samples_returns_nan(self) -> None:
        """无可验证样本 → 各项 NaN、n=0（不给误导数字）."""
        preds = pd.DataFrame([
            _pred_row("A.SZ", "2025-04-01", "2025-04-02", 10.0, 10.5, 9.5, 11.5),
        ])
        m = weekly_validation_metrics(preds, {})  # 无真实数据
        assert m["n"] == 0
        assert np.isnan(m["brier"])


class TestBlacklist:
    def test_two_consecutive_fails_blacklists(self) -> None:
        """连续两周不达标 → 拉黑."""
        c, bl = update_blacklist_state(0, week_ok=False)
        assert (c, bl) == (1, False)
        c, bl = update_blacklist_state(c, week_ok=False)
        assert (c, bl) == (2, True)

    def test_pass_resets_counter(self) -> None:
        c, bl = update_blacklist_state(1, week_ok=True)
        assert (c, bl) == (0, False)

    def test_week_metrics_ok(self) -> None:
        assert week_metrics_ok({"n": 5, "dir_rate": 0.6, "brier": 0.2}) is True
        assert week_metrics_ok({"n": 5, "dir_rate": 0.4, "brier": 0.2}) is False
        assert week_metrics_ok({"n": 0}) is False
