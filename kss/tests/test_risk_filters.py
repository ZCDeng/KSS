"""kss/strategies/risk_filters.py 单测.

覆盖：
- ``load_config`` 默认 + 自定义路径
- ``filter_high_leverage`` 行业分位过滤 / fallback 绝对值 / 缺数据保留
- ``filter_low_liquidity`` 窗口均值阈值 / 数据不足保留 / 缺数据保留
- ``filter_st_risk`` ST 名 / delist_date / 连亏 / 负净资产
- ``apply_all_filters`` 串行 + 多原因去重 / enabled=False 直通
- ``summarize_removed`` 理由聚合
- ``daily_amounts_from_cs_data`` 单位换算（千元 → 元）
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kss.strategies.risk_filters import (
    FilterResult,
    apply_all_filters,
    daily_amounts_from_cs_data,
    filter_high_leverage,
    filter_low_liquidity,
    filter_st_risk,
    load_config,
    summarize_removed,
)


# ---------------------------------------------------------------------------- #
# load_config
# ---------------------------------------------------------------------------- #


def test_load_config_default_has_three_filters() -> None:
    cfg = load_config()
    assert cfg["enabled"] is True
    assert {"leverage", "liquidity", "st_risk"}.issubset(cfg.keys())
    assert cfg["liquidity"]["window_days"] == 20
    assert cfg["leverage"]["industry_quantile"] == 0.80


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg["enabled"] is True
    assert cfg["leverage"]["industry_quantile"] == 0.80


# ---------------------------------------------------------------------------- #
# filter_high_leverage
# ---------------------------------------------------------------------------- #


def _build_fina(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_filter_high_leverage_industry_quantile() -> None:
    """5 只地产股 debt 60/65/70/75/95；阈值 80 分位约 79，95 应被剔除."""
    fina = _build_fina([
        {"ts_code": "A", "end_date": "20240930", "debt_to_assets": 60.0},
        {"ts_code": "B", "end_date": "20240930", "debt_to_assets": 65.0},
        {"ts_code": "C", "end_date": "20240930", "debt_to_assets": 70.0},
        {"ts_code": "D", "end_date": "20240930", "debt_to_assets": 75.0},
        {"ts_code": "E", "end_date": "20240930", "debt_to_assets": 95.0},
    ])
    industry = {c: "房地产" for c in ["A", "B", "C", "D", "E"]}
    kept, removed = filter_high_leverage(["A", "B", "C", "D", "E"], fina, industry)
    assert "E" in [r["ts_code"] for r in removed]
    assert set(kept) >= {"A", "B", "C"}


def test_filter_high_leverage_fallback_when_few_peers() -> None:
    """行业内 < min_industry_peers 时退化到绝对值 fallback_absolute_max=85.0.

    Tushare debt_to_assets 单位是百分点（50 = 50%，90 = 90%）.
    """
    fina = _build_fina([
        {"ts_code": "A", "end_date": "20240930", "debt_to_assets": 50.0},
        {"ts_code": "B", "end_date": "20240930", "debt_to_assets": 90.0},
    ])
    industry = {"A": "稀缺行业", "B": "稀缺行业"}
    cfg = load_config()
    cfg["leverage"]["min_industry_peers"] = 5
    cfg["leverage"]["fallback_absolute_max"] = 85.0
    kept, removed = filter_high_leverage(["A", "B"], fina, industry, cfg)
    assert "B" in [r["ts_code"] for r in removed]
    assert "A" in kept


def test_filter_high_leverage_missing_data_fail_safe_keep() -> None:
    """缺财务数据的股票保留（不剔除）."""
    fina = _build_fina([{"ts_code": "A", "end_date": "20240930", "debt_to_assets": 60.0}])
    industry = {"A": "X"}
    kept, removed = filter_high_leverage(["A", "B"], fina, industry)
    # B 完全无数据，保留
    assert "B" in kept


# ---------------------------------------------------------------------------- #
# filter_low_liquidity
# ---------------------------------------------------------------------------- #


def test_filter_low_liquidity_below_threshold_removed() -> None:
    # 20 日均成交额 1000 万 < 5000 万 → 剔除
    s = pd.Series([10_000_000.0] * 20, index=[f"2024{i:04d}" for i in range(1, 21)])
    daily = {"A": s}
    kept, removed = filter_low_liquidity(["A"], daily)
    assert removed and removed[0]["ts_code"] == "A"
    assert removed[0]["reason"] == "liquidity"


def test_filter_low_liquidity_above_threshold_kept() -> None:
    # 1 亿 > 5000 万 → 保留
    s = pd.Series([100_000_000.0] * 20, index=[f"2024{i:04d}" for i in range(1, 21)])
    kept, removed = filter_low_liquidity(["A"], {"A": s})
    assert kept == ["A"] and not removed


def test_filter_low_liquidity_insufficient_window_kept() -> None:
    """有效日 < min_valid_days 时按 fail-safe-include 保留."""
    s = pd.Series([1_000_000.0] * 3, index=["20240101", "20240102", "20240103"])
    kept, removed = filter_low_liquidity(["A"], {"A": s})
    assert "A" in kept and not removed


def test_filter_low_liquidity_missing_stock_kept() -> None:
    kept, removed = filter_low_liquidity(["A"], {})
    assert kept == ["A"] and not removed


# ---------------------------------------------------------------------------- #
# filter_st_risk
# ---------------------------------------------------------------------------- #


def test_filter_st_risk_name_st_removed() -> None:
    basic = pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "*ST 某某", "delist_date": None},
        {"ts_code": "000002.SZ", "name": "正常股", "delist_date": None},
    ])
    kept, removed = filter_st_risk(["000001.SZ", "000002.SZ"], basic)
    assert "000001.SZ" in [r["ts_code"] for r in removed]
    assert "000002.SZ" in kept


def test_filter_st_risk_delist_date_removed() -> None:
    basic = pd.DataFrame([
        {"ts_code": "X.SZ", "name": "已退", "delist_date": "20231231"},
        {"ts_code": "Y.SZ", "name": "在市", "delist_date": None},
    ])
    kept, removed = filter_st_risk(["X.SZ", "Y.SZ"], basic)
    assert "X.SZ" in [r["ts_code"] for r in removed]
    assert "Y.SZ" in kept


def test_filter_st_risk_consecutive_losses_removed() -> None:
    basic = pd.DataFrame([{"ts_code": "Z", "name": "连亏股", "delist_date": None}])
    fina = pd.DataFrame([
        {"ts_code": "Z", "end_date": "20221231", "n_income_attr_p": -1e8, "bps": 1.0},
        {"ts_code": "Z", "end_date": "20231231", "n_income_attr_p": -2e8, "bps": 0.5},
        {"ts_code": "Z", "end_date": "20240930", "n_income_attr_p": -5e7, "bps": 0.4},
    ])
    kept, removed = filter_st_risk(["Z"], basic, fina_panel=fina)
    assert removed and "consecutive_loss" in removed[0]["detail"]


def test_filter_st_risk_negative_equity_removed() -> None:
    basic = pd.DataFrame([{"ts_code": "N", "name": "负净", "delist_date": None}])
    fina = pd.DataFrame([
        {"ts_code": "N", "end_date": "20240930", "n_income_attr_p": 1e6, "bps": -0.5},
    ])
    cfg = load_config()
    cfg["st_risk"]["exclude_consecutive_loss_years"] = 0    # 关连亏只测净资产
    kept, removed = filter_st_risk(["N"], basic, fina_panel=fina, config=cfg)
    assert removed and "negative equity" in removed[0]["detail"]


def test_filter_st_risk_disabled_skips_all() -> None:
    cfg = load_config()
    cfg["st_risk"]["enabled"] = False
    basic = pd.DataFrame([{"ts_code": "X", "name": "*ST", "delist_date": None}])
    kept, removed = filter_st_risk(["X"], basic, config=cfg)
    assert kept == ["X"] and not removed


# ---------------------------------------------------------------------------- #
# apply_all_filters
# ---------------------------------------------------------------------------- #


def test_apply_all_filters_global_disabled_passthrough() -> None:
    cfg = load_config()
    cfg["enabled"] = False
    result = apply_all_filters(["A", "B"], config=cfg)
    assert result.kept == ["A", "B"]
    assert result.removed == []


def test_apply_all_filters_combines_three_reasons() -> None:
    basic = pd.DataFrame([
        {"ts_code": "ST_STK", "name": "*ST 雷", "delist_date": None},
        {"ts_code": "HEAVY", "name": "重杠杆", "delist_date": None},
        {"ts_code": "DEAD", "name": "死量", "delist_date": None},
        {"ts_code": "GOOD", "name": "正常", "delist_date": None},
    ])
    fina = pd.DataFrame([
        # debt_to_assets 单位是百分点（同 Tushare fina_indicator 约定）
        {"ts_code": "HEAVY", "end_date": "20240930", "debt_to_assets": 95.0,
         "n_income_attr_p": 1e8, "bps": 5.0},
        {"ts_code": "DEAD", "end_date": "20240930", "debt_to_assets": 50.0,
         "n_income_attr_p": 1e8, "bps": 5.0},
        {"ts_code": "GOOD", "end_date": "20240930", "debt_to_assets": 50.0,
         "n_income_attr_p": 1e8, "bps": 5.0},
    ])
    industry = {"HEAVY": "稀缺", "DEAD": "稀缺", "GOOD": "稀缺"}
    daily = {
        "HEAVY": pd.Series([200_000_000.0] * 20, index=[f"2024{i:04d}" for i in range(1, 21)]),
        "DEAD": pd.Series([1_000_000.0] * 20, index=[f"2024{i:04d}" for i in range(1, 21)]),
        "GOOD": pd.Series([200_000_000.0] * 20, index=[f"2024{i:04d}" for i in range(1, 21)]),
    }

    result = apply_all_filters(
        ["ST_STK", "HEAVY", "DEAD", "GOOD"],
        fina_panel=fina, industry_map=industry,
        daily_amounts=daily, stock_basic=basic,
    )
    assert "GOOD" in result.kept
    reasons = {r["ts_code"]: r["reason"] for r in result.removed}
    assert reasons.get("ST_STK") == "st_risk"
    # HEAVY 95% > fallback 85% → leverage 剔除
    assert reasons.get("HEAVY") == "leverage"
    # DEAD 1000 万 < 5000 万 → liquidity 剔除
    assert reasons.get("DEAD") == "liquidity"


def test_summarize_removed_counts_by_reason() -> None:
    removed = [
        {"ts_code": "A", "reason": "leverage", "detail": ""},
        {"ts_code": "B", "reason": "liquidity", "detail": ""},
        {"ts_code": "C", "reason": "leverage", "detail": ""},
    ]
    summary = summarize_removed(removed)
    assert summary == {"leverage": 2, "liquidity": 1}


# ---------------------------------------------------------------------------- #
# daily_amounts_from_cs_data
# ---------------------------------------------------------------------------- #


def test_daily_amounts_from_cs_data_unit_conversion(tmp_path: Path) -> None:
    """cs_data amount 单位是千元，应 × 1000 还原为元."""
    csv = tmp_path / "cs_data_688322.csv"
    csv.write_text(
        "trade_date,amount\n20240101,10000.0\n20240102,12000.0\n",
        encoding="utf-8",
    )
    out = daily_amounts_from_cs_data(["688322"], project_root=tmp_path)
    assert "688322" in out
    s = out["688322"]
    # 10000 千元 → 10,000,000 元
    assert float(s.iloc[0]) == 10_000_000.0
    assert float(s.iloc[1]) == 12_000_000.0


def test_daily_amounts_from_cs_data_missing_file_skipped(tmp_path: Path) -> None:
    out = daily_amounts_from_cs_data(["999999"], project_root=tmp_path)
    assert out == {}
