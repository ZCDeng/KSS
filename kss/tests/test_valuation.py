"""kss/macro/valuation.py 单测.

覆盖：
- ``compute_time_premium`` 书附录 7B 数值案例 + 边界值
- ``stage_rule_for_n`` 5 个阈值区间
- ``modulate_entry_count`` 行为规则映射
- ``compute_n_percentile`` 历史分位 + 样本不足降级
- ``compute_hs300_n_from_panel`` 向量化合成
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from kss.macro.valuation import (
    DEFAULT_EQUITY_RISK_PREMIUM,
    compute_hs300_n_from_panel,
    compute_n_percentile,
    compute_time_premium,
    modulate_entry_count,
    stage_rule_for_n,
)


# ---------------------------------------------------------------------------- #
# compute_time_premium
# ---------------------------------------------------------------------------- #


def test_compute_n_book_example_pe20_r10pct_g10pct() -> None:
    """书附录 7B：PE=20, r=10%, g=10%, ERP=0 → n = log(2)/log(1.1) ≈ 7.27."""
    # 用 ERP=0, risk_free=10% 凑出 r=10%
    n = compute_time_premium(pe=20.0, risk_free_rate=0.10, growth_rate=0.10, equity_premium=0.0)
    expected = math.log(2.0) / math.log(1.10)
    assert n == pytest.approx(expected, rel=1e-9)
    assert n == pytest.approx(7.272, abs=0.01)


def test_compute_n_book_example_ratio_1_21_growth_10pct() -> None:
    """plan 提到的"弥补 21% 差异 + 10% 增长 = n≈2"案例."""
    # P/S_NWG = 1.21 ↔ PE * r = 1.21 → 取 PE=12.1, r=0.10
    n = compute_time_premium(pe=12.1, risk_free_rate=0.10, growth_rate=0.10, equity_premium=0.0)
    assert n == pytest.approx(2.0, abs=0.01)


def test_compute_n_negative_when_undervalued() -> None:
    """PE * r < 1 时 n 应为负（隐含价 < NWG，反转区）."""
    # PE = 5, r = 10% → PE*r = 0.5 < 1
    n = compute_time_premium(pe=5.0, risk_free_rate=0.10, growth_rate=0.05, equity_premium=0.0)
    assert n is not None
    assert n < 0


def test_compute_n_pe_zero_returns_none() -> None:
    assert compute_time_premium(pe=0.0, risk_free_rate=0.10, growth_rate=0.10) is None
    assert compute_time_premium(pe=-1.0, risk_free_rate=0.10, growth_rate=0.10) is None
    assert compute_time_premium(pe=None, risk_free_rate=0.10, growth_rate=0.10) is None


def test_compute_n_growth_non_positive_returns_none() -> None:
    """g <= 0 时几何增长无意义."""
    assert compute_time_premium(pe=20.0, risk_free_rate=0.10, growth_rate=0.0) is None
    assert compute_time_premium(pe=20.0, risk_free_rate=0.10, growth_rate=-0.05) is None


def test_compute_n_uses_default_equity_premium() -> None:
    """不传 equity_premium 时使用模块默认值."""
    n_default = compute_time_premium(pe=20.0, risk_free_rate=0.025, growth_rate=0.08)
    n_explicit = compute_time_premium(
        pe=20.0, risk_free_rate=0.025, growth_rate=0.08,
        equity_premium=DEFAULT_EQUITY_RISK_PREMIUM,
    )
    assert n_default == n_explicit


# ---------------------------------------------------------------------------- #
# stage_rule_for_n
# ---------------------------------------------------------------------------- #


def test_stage_rule_bubble() -> None:
    assert stage_rule_for_n(15.0) == "bubble"
    assert stage_rule_for_n(10.01) == "bubble"


def test_stage_rule_hot() -> None:
    assert stage_rule_for_n(8.0) == "hot"
    assert stage_rule_for_n(5.01) == "hot"
    assert stage_rule_for_n(10.0) == "hot"


def test_stage_rule_normal() -> None:
    assert stage_rule_for_n(3.0) == "normal"
    assert stage_rule_for_n(0.0) == "normal"
    assert stage_rule_for_n(5.0) == "normal"
    assert stage_rule_for_n(None) == "normal"    # 缺值默认 normal


def test_stage_rule_cool_and_reversal() -> None:
    assert stage_rule_for_n(-1.0) == "cool"
    assert stage_rule_for_n(-2.0) == "cool"
    assert stage_rule_for_n(-2.5) == "reversal"
    assert stage_rule_for_n(-10.0) == "reversal"


# ---------------------------------------------------------------------------- #
# modulate_entry_count
# ---------------------------------------------------------------------------- #


def test_modulate_entry_bubble_zero() -> None:
    assert modulate_entry_count("bubble", 5) == 0


def test_modulate_entry_hot_halves() -> None:
    assert modulate_entry_count("hot", 5) == 2
    assert modulate_entry_count("hot", 1) == 1    # 至少 1


def test_modulate_entry_cool_expands() -> None:
    assert modulate_entry_count("cool", 5) == 7
    assert modulate_entry_count("cool", 7) == 7    # 不超过 7


def test_modulate_entry_normal_keeps() -> None:
    assert modulate_entry_count("normal", 5) == 5


def test_modulate_entry_reversal_zero() -> None:
    assert modulate_entry_count("reversal", 5) == 0


# ---------------------------------------------------------------------------- #
# compute_n_percentile
# ---------------------------------------------------------------------------- #


def test_compute_n_percentile_basic() -> None:
    hist = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0])
    # current = 2.5 → 3 个值 ≤ 2.5 → 60%
    p = compute_n_percentile(hist, 2.5, window=5)
    assert p == pytest.approx(0.6)


def test_compute_n_percentile_top_and_bottom() -> None:
    hist = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert compute_n_percentile(hist, 10.0, window=5) == pytest.approx(1.0)
    assert compute_n_percentile(hist, -1.0, window=5) == pytest.approx(0.0)


def test_compute_n_percentile_empty_returns_none() -> None:
    assert compute_n_percentile(pd.Series([], dtype=float), 1.0) is None
    assert compute_n_percentile(pd.Series([float("nan"), float("nan")]), 1.0) is None


def test_compute_n_percentile_current_none() -> None:
    assert compute_n_percentile(pd.Series([1.0, 2.0]), None) is None


# ---------------------------------------------------------------------------- #
# compute_hs300_n_from_panel
# ---------------------------------------------------------------------------- #


def test_compute_hs300_n_from_panel_basic() -> None:
    pe = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "pe_ttm": [12.0, 13.0, 14.0],
    })
    rates = pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "yld_10y": [2.5, 2.5, 2.5],     # 百分点
    })
    out = compute_hs300_n_from_panel(pe, rates, growth_rate=0.08, equity_premium=0.05)
    assert len(out) == 3
    assert {"trade_date", "pe", "risk_free", "n_years"}.issubset(out.columns)
    # PE 升高 → n 也升高（单调）
    assert out["n_years"].iloc[2] > out["n_years"].iloc[0]


def test_compute_hs300_n_from_panel_inner_join() -> None:
    """缺日期会被 inner-join 丢掉."""
    pe = pd.DataFrame({"trade_date": ["20240101", "20240102"], "pe_ttm": [12.0, 13.0]})
    rates = pd.DataFrame({"trade_date": ["20240102"], "yld_10y": [2.5]})
    out = compute_hs300_n_from_panel(pe, rates, growth_rate=0.08)
    assert len(out) == 1
    assert out["trade_date"].iloc[0] == "20240102"


def test_compute_hs300_n_from_panel_empty_inputs() -> None:
    empty = pd.DataFrame(columns=["trade_date", "pe_ttm"])
    rates = pd.DataFrame({"trade_date": ["20240101"], "yld_10y": [2.5]})
    out = compute_hs300_n_from_panel(empty, rates, growth_rate=0.08)
    assert out.empty
