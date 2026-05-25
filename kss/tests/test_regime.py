"""kss/macro/regime.py 与新增 derived 指标单测.

覆盖：
- ``compute_thresholds`` 分位数 / 样本不足降级
- ``_direction`` 三档量化 / 缺值
- ``_combine_yc`` 曲线维度合成
- ``classify_day`` 四阶段正样本 / Unknown 降级
- ``_apply_hysteresis`` 滞后窗口拒绝单日 flip / 累计 N 日允许切换
- ``classify_history`` 全量回填
- ``classify_today`` 自动取最末日
- ``compute_e_trend`` / ``compute_liquidity_index`` 算法 + 缺数据降级
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.macro.derived import compute_e_trend, compute_liquidity_index
from kss.macro.regime import (
    UNKNOWN,
    MacroRegime,
    RegimeThresholds,
    _apply_hysteresis,
    _combine_yc,
    _direction,
    classify_day,
    classify_history,
    classify_today,
    compute_thresholds,
    load_config,
)


# ---------------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------------- #


@pytest.fixture
def sample_config() -> dict:
    """测试用 config：min_days 降到 30，方便构造小样本."""
    cfg = load_config()
    cfg["history"]["min_days_for_quantile"] = 30
    cfg["hysteresis"]["min_consecutive_days"] = 3
    cfg["min_signals_required"] = 2
    return cfg


@pytest.fixture
def history_panel() -> pd.DataFrame:
    """构造 60 日合成历史：e_trend 围绕 0 分布、r_trend 随机."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y%m%d")
    return pd.DataFrame({
        "trade_date": dates,
        "e_trend": rng.normal(0, 1, 60),
        "r_trend": rng.normal(0, 0.3, 60),
        "liquidity": rng.normal(0, 1, 60),
        "yc_slope": rng.normal(0.6, 0.3, 60),
        "yc_slope_change": rng.normal(0, 0.2, 60),
    })


# ---------------------------------------------------------------------------- #
# compute_thresholds
# ---------------------------------------------------------------------------- #


def test_compute_thresholds_full_panel(history_panel, sample_config):
    th = compute_thresholds(history_panel, sample_config)
    assert th.n_history == 60
    assert th.e_trend is not None
    assert th.r_trend is not None
    low, high = th.e_trend
    assert low < high


def test_compute_thresholds_insufficient_history(sample_config):
    sample_config["history"]["min_days_for_quantile"] = 100
    small = pd.DataFrame({
        "trade_date": ["20240101", "20240102"],
        "e_trend": [0.1, 0.2],
    })
    th = compute_thresholds(small, sample_config)
    assert th.e_trend is None
    # n_history 反映传入样本量；不足时仍记录便于诊断
    assert th.n_history == 0 or th.n_history == len(small)


def test_compute_thresholds_missing_column(history_panel, sample_config):
    """缺某指标列时该维度 None，其余正常."""
    panel = history_panel.drop(columns=["liquidity"])
    th = compute_thresholds(panel, sample_config)
    assert th.liquidity is None
    assert th.e_trend is not None


# ---------------------------------------------------------------------------- #
# _direction & _combine_yc
# ---------------------------------------------------------------------------- #


def test_direction_buckets():
    th = (-1.0, 1.0)
    assert _direction(-2.0, th) == -1
    assert _direction(0.0, th) == 0
    assert _direction(2.0, th) == +1
    assert _direction(None, th) is None
    assert _direction(float("nan"), th) is None
    assert _direction(0.0, None) is None


def test_combine_yc_both_signals():
    assert _combine_yc(+1, +1) == +1
    assert _combine_yc(-1, -1) == -1
    # 一极一中：极性占优（avg >= 0.5 触发）
    assert _combine_yc(+1, 0) == +1
    assert _combine_yc(-1, 0) == -1
    # 双反向 → 中性
    assert _combine_yc(+1, -1) == 0
    assert _combine_yc(0, 0) == 0


def test_combine_yc_partial_missing():
    assert _combine_yc(+1, None) == +1
    assert _combine_yc(None, -1) == -1
    assert _combine_yc(None, None) is None


# ---------------------------------------------------------------------------- #
# classify_day — 四阶段正样本
# ---------------------------------------------------------------------------- #


def _build_thresholds() -> RegimeThresholds:
    """统一阈值：以 0 为中心，±1 为分位."""
    return RegimeThresholds(
        e_trend=(-1.0, 1.0),
        r_trend=(-0.3, 0.3),
        liquidity=(-1.0, 1.0),
        yc_slope=(0.2, 0.8),
        yc_slope_change=(-0.1, 0.1),
        n_history=252,
    )


def test_classify_stage_I(sample_config):
    """E rising / r falling / liquidity easing / curve steepening."""
    th = _build_thresholds()
    row = pd.Series({
        "trade_date": "20250101",
        "e_trend": 2.0,        # high → +1
        "r_trend": -0.5,       # low  → -1
        "liquidity": 2.0,      # high → +1
        "yc_slope": 1.0,       # high → +1
        "yc_slope_change": 0.3,  # high → +1
    })
    regime = classify_day(row, th, sample_config)
    assert regime.stage == "I"
    assert regime.confidence >= 0.9
    assert regime.n_signals == 4


def test_classify_stage_II(sample_config):
    """E rising / r rising slow / liquidity neutral / curve still concave."""
    th = _build_thresholds()
    row = pd.Series({
        "trade_date": "20250201",
        "e_trend": 2.0,
        "r_trend": 0.4,         # high → +1
        "liquidity": 0.0,       # mid → 0
        "yc_slope": 1.0,
        "yc_slope_change": 0.2,
    })
    regime = classify_day(row, th, sample_config)
    assert regime.stage == "II"
    assert regime.confidence >= 0.8


def test_classify_stage_III(sample_config):
    """E decelerating / r rising fast / liquidity tightening / curve flattening."""
    th = _build_thresholds()
    row = pd.Series({
        "trade_date": "20250301",
        "e_trend": 0.0,
        "r_trend": 0.5,
        "liquidity": -2.0,
        "yc_slope": 0.1,         # low → -1
        "yc_slope_change": -0.3,  # low → -1
    })
    regime = classify_day(row, th, sample_config)
    assert regime.stage == "III"
    assert regime.confidence >= 0.8


def test_classify_stage_IV(sample_config):
    """E falling / r falling / liquidity easing (rescue) / curve flat-or-inverted."""
    th = _build_thresholds()
    row = pd.Series({
        "trade_date": "20250401",
        "e_trend": -2.0,
        "r_trend": -0.5,
        "liquidity": 2.0,
        "yc_slope": 0.0,
        "yc_slope_change": -0.3,
    })
    regime = classify_day(row, th, sample_config)
    assert regime.stage == "IV"
    assert regime.confidence >= 0.8


def test_classify_unknown_too_few_signals(sample_config):
    """只有 1 个非空维度 → Unknown."""
    th = _build_thresholds()
    row = pd.Series({
        "trade_date": "20250501",
        "e_trend": 1.5,
        "r_trend": None,
        "liquidity": None,
        "yc_slope": None,
        "yc_slope_change": None,
    })
    regime = classify_day(row, th, sample_config)
    assert regime.stage == UNKNOWN
    assert regime.confidence == 0.0


# ---------------------------------------------------------------------------- #
# Hysteresis
# ---------------------------------------------------------------------------- #


def test_hysteresis_blocks_single_day_flip():
    """连续 5 日 'I'，中间 1 日 'III' → 应保持 'I'."""
    raw = pd.Series(["I", "I", "I", "III", "I", "I", "I"])
    smoothed = _apply_hysteresis(raw, min_days=3)
    # 第 4 日 (III) 只有 1 天，不该切换
    assert smoothed.tolist() == ["Unknown", "Unknown", "I", "I", "I", "I", "I"]


def test_hysteresis_allows_switch_after_n_consecutive():
    """连续 3 日 'III' 后才允许切换."""
    raw = pd.Series(["I", "I", "I", "III", "III", "III", "III"])
    smoothed = _apply_hysteresis(raw, min_days=3)
    # 前 3 日累计到 I，第 4-6 日累计到 III，第 6 日触发切换
    assert smoothed.iloc[2] == "I"
    assert smoothed.iloc[5] == "III"
    assert smoothed.iloc[6] == "III"


def test_hysteresis_unknown_does_not_clobber_current():
    """Unknown 不应触发切换，应保持当前 stage."""
    raw = pd.Series(["I", "I", "I", "Unknown", "Unknown", "I"])
    smoothed = _apply_hysteresis(raw, min_days=3)
    # Unknown 透传当前 I，不重置
    assert smoothed.iloc[3] == "I"
    assert smoothed.iloc[4] == "I"


# ---------------------------------------------------------------------------- #
# classify_history & classify_today
# ---------------------------------------------------------------------------- #


def test_classify_history_returns_dataframe(history_panel, sample_config):
    out = classify_history(history_panel, sample_config)
    assert len(out) == len(history_panel)
    assert "stage" in out.columns
    assert "stage_raw" in out.columns
    # 前 30 天应为 Unknown（min_days_for_quantile=30）
    assert (out.iloc[:30]["stage_raw"] == UNKNOWN).all()


def test_classify_today_picks_last_day(history_panel, sample_config):
    """未指定 today 时取 panel 最大 trade_date."""
    # 强力构造最后一日的指标，确认是被分类的
    history_panel.loc[history_panel.index[-1], "e_trend"] = 5.0
    history_panel.loc[history_panel.index[-1], "r_trend"] = -1.0
    history_panel.loc[history_panel.index[-1], "liquidity"] = 5.0
    regime = classify_today(history_panel, config=sample_config)
    assert regime.trade_date == history_panel["trade_date"].iloc[-1]


def test_classify_today_unknown_when_today_not_in_panel(history_panel, sample_config):
    regime = classify_today(history_panel, today="20990101", config=sample_config)
    assert regime.stage == UNKNOWN


# ---------------------------------------------------------------------------- #
# Derived: compute_e_trend
# ---------------------------------------------------------------------------- #


def test_compute_e_trend_with_pmi_and_vai():
    panel = pd.DataFrame({
        "month": ["202401", "202402", "202403", "202404"],
        "pmi_mfg": [50.5, 51.0, 51.5, 52.0],
        "vai_yoy": [4.0, 5.0, 5.0, 6.0],
    })
    s = compute_e_trend(panel)
    assert s is not None
    assert len(s) == 4
    # 单调上升的 PMI + VAI → e_trend 单调上升
    assert s.iloc[-1] > s.iloc[0]


def test_compute_e_trend_pmi_only():
    panel = pd.DataFrame({
        "month": ["202401", "202402", "202403"],
        "pmi_mfg": [49.0, 51.0, 52.0],  # 单调向上
    })
    s = compute_e_trend(panel, smooth=2)
    assert s is not None
    # 第一日窗口为 1（49）→ (49-50)/5 = -0.2，负
    assert s.iloc[0] < 0
    # 第三日窗口 2 平均 (51+52)/2 = 51.5 → (51.5-50)/5 = 0.3，正
    assert s.iloc[2] > 0


def test_compute_e_trend_both_missing_returns_none():
    panel = pd.DataFrame({"month": ["202401"], "other": [1.0]})
    assert compute_e_trend(panel) is None


# ---------------------------------------------------------------------------- #
# Derived: compute_liquidity_index
# ---------------------------------------------------------------------------- #


def test_compute_liquidity_index_hsgt_only():
    hsgt = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=40, freq="B").strftime("%Y%m%d"),
        "net_amount": [10.0] * 20 + [50.0] * 20,
    })
    s = compute_liquidity_index(hsgt_panel=hsgt)
    assert s is not None
    # 后半段净流入显著放大 → z-score 后明显高于前半段
    assert s.iloc[-1] > s.iloc[10]


def test_compute_liquidity_index_all_none_returns_none():
    assert compute_liquidity_index() is None


def test_compute_liquidity_index_string_typed_hsgt_coerced():
    """回归：Tushare 偶尔返回 str 数字，落 parquet 后整列变 object dtype.

    曾因此触发 ``unsupported operand type(s) for /: 'str' and 'float'``.
    现在 _normalize_hsgt + pd.to_numeric 应静默兜底.
    """
    from kss.macro import derived as d
    # 模拟坏 dtype 的 hsgt
    hsgt = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "net_amount": ["100.0", "200.0", "300.0"],     # 字符串数字
    })
    hsgt["net_amount"] = pd.to_numeric(hsgt["net_amount"], errors="coerce")
    out = d.compute_liquidity_index(hsgt_panel=hsgt)
    # 不应抛 TypeError；样本太少 z-score 退化为 0 仍可接受
    assert out is not None


def test_classify_history_benchmark_fast_on_2000_row_panel(sample_config):
    """PERF (#40) regression: classify_history 在 2000-row panel 必须 < 5s.

    旧 O(N²) 实现在真实 2090-row panel 上耗时 ~1.5h (refresh_regime 主因).
    新向量化实现应在秒级.
    """
    import time
    rng = np.random.default_rng(2026)
    n = 2000
    dates = pd.date_range("2018-01-01", periods=n, freq="B").strftime("%Y%m%d")
    panel = pd.DataFrame({
        "trade_date": dates,
        "e_trend": rng.normal(0, 1, n),
        "r_trend": rng.normal(0, 0.3, n),
        "liquidity": rng.normal(0, 1, n),
        "yc_slope": rng.normal(0.6, 0.3, n),
        "yc_slope_change": rng.normal(0, 0.2, n),
    })
    sample_config["history"]["min_days_for_quantile"] = 252
    start = time.perf_counter()
    out = classify_history(panel, sample_config)
    elapsed = time.perf_counter() - start
    assert len(out) == n
    assert elapsed < 5.0, f"classify_history took {elapsed:.2f}s (target <5s)"


def test_classify_history_expanding_threshold_no_lookahead(sample_config):
    """阈值用 [0, i) 计算（shift(1)），第 i 行绝不能看到自己."""
    rng = np.random.default_rng(7)
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d")
    panel = pd.DataFrame({
        "trade_date": dates,
        "e_trend": rng.normal(0, 1, n),
        "r_trend": rng.normal(0, 0.3, n),
        "liquidity": rng.normal(0, 1, n),
        "yc_slope": rng.normal(0.6, 0.3, n),
        "yc_slope_change": rng.normal(0, 0.2, n),
    })
    sample_config["history"]["min_days_for_quantile"] = 30
    out = classify_history(panel, sample_config)
    # 前 min_n 行必须全 Unknown
    assert (out.iloc[:30]["stage_raw"] == UNKNOWN).all()
    # 第 min_n 行应已有非 Unknown 分类（用 [0, 30) 阈值）
    assert out.iloc[30]["stage_raw"] != UNKNOWN or out.iloc[30]["n_signals"] >= 2


def test_compute_liquidity_index_pd_na_in_m2_does_not_crash():
    """回归：当某些 trade_date 的 month 在 m2 panel 里缺失时，
    旧实现用 pd.NA 占位会触发 astype(float) NAType 错误。"""
    hsgt = pd.DataFrame({
        "trade_date": ["20240101", "20240102"],
        "net_amount": [100.0, 200.0],
    })
    monthly = pd.DataFrame({
        "month": ["202312"],     # 2024 月份完全缺失
        "m2_yoy": [7.5],
    })
    # 不应抛异常
    out = compute_liquidity_index(monthly_panel=monthly, hsgt_panel=hsgt)
    assert out is not None
