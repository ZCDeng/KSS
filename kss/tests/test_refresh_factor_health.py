"""scripts/refresh_factor_health.py —— 因子健康度 driver 测试 (U8 ②).

覆盖 plan #8 在生产可达:
- pipeline rank_ic 双源 (近窗后验 / 较早窗先验) → promote 可达 (同口径不 method_mismatch);
- 后验衰减 → demote (置 PENDING_REVIEW);
- 后验 vs 先验符号分歧 → 记 IC_SOURCE_DIVERGENCE;
- 同口径 rank_ic: 仲裁不返回 method_mismatch;
- split_ic_series 分窗语义 (历史不足 → 先验空).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kss.backtest.factor_health import (
    CRASH_IC_SOURCE_DIVERGENCE,
    STATE_ACTIVE,
    STATE_PENDING_REVIEW,
    VERDICT_DEMOTE,
    VERDICT_DIVERGENCE,
    VERDICT_METHOD_MISMATCH,
    VERDICT_PROMOTE,
    FactorCrashRegistry,
)
import scripts.refresh_factor_health as rfh


_THRESHOLDS = {
    "icir_baseline": 0.25,
    "rolling_icir_window": 60,
    "crash_consecutive_n": 5,
    "ic_floor": 0.02,
    "half_life_min_periods": 20,
    "realized_ic_min_n": 20,
    "realized_icir_promote": 0.30,
    "realized_icir_demote": 0.25,
    "ic_divergence_min_abs": 0.01,
}


def _ic_series(values: list[float], start: str = "2026-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D").strftime("%Y-%m-%d")
    return pd.Series(values, index=list(idx), dtype=float)


def _patch_one_pipeline(monkeypatch, ic_series: pd.Series):
    """让 refresh 只看到一条 pipeline 'pl', 其 _daily_rank_ic 返回给定 IC 序列."""
    monkeypatch.setattr(rfh, "PIPELINE_HIT_READERS", {"pl": lambda: {"x": [("a", 1.0)]}})

    def _fake_daily_rank_ic(hits, horizon):
        return ic_series, [], []

    monkeypatch.setattr(rfh, "_daily_rank_ic", _fake_daily_rank_ic)


# --------------------------------------------------------------------------- #
# split_ic_series
# --------------------------------------------------------------------------- #

def test_split_recent_posterior_earlier_prior():
    s = _ic_series([0.1] * 30)
    prior, posterior = rfh.split_ic_series(s, recent_window=10)
    assert posterior.index.nunique() == 10
    assert prior.index.nunique() == 20
    # 后验取最近, 先验取较早 (时间不重叠)
    assert prior.index.max() < posterior.index.min()


def test_split_insufficient_history_prior_empty():
    s = _ic_series([0.1] * 5)
    prior, posterior = rfh.split_ic_series(s, recent_window=10)
    assert prior.empty
    assert posterior.index.nunique() == 5


# --------------------------------------------------------------------------- #
# promote 可达 (双源同口径一致)
# --------------------------------------------------------------------------- #

def test_promote_reachable_dual_source_same_method(tmp_path, monkeypatch):
    # 先验 (较早 30 期) + 后验 (近 25 期) 都强正 IC, 同号 → ICIR 高 → promote
    s = _ic_series([0.15] * 30 + [0.16, 0.14] * 12 + [0.15])  # 55 期, 抖动给非零 std
    _patch_one_pipeline(monkeypatch, s)
    summary = rfh.refresh(
        horizon=5, recent_window=25, db_path=tmp_path / "fh.db",
        thresholds=dict(_THRESHOLDS), notify=False,
    )
    res = summary["pl"]
    assert res["prior_present"] is True
    # 后验 n=25 >= min_n(20), ICIR 高且先验同号 → promote; 关键: 绝不 method_mismatch
    assert res["verdict"] not in (VERDICT_METHOD_MISMATCH,)
    assert res["verdict"] == VERDICT_PROMOTE
    assert res["state"] == STATE_ACTIVE


# --------------------------------------------------------------------------- #
# 衰减 → demote
# --------------------------------------------------------------------------- #

def test_decay_posterior_demotes(tmp_path, monkeypatch):
    # 先验强正 (30 期); 后验衰减到接近 0 (25 期低 ICIR) → demote
    s = _ic_series([0.15] * 30 + [0.001, -0.001] * 12 + [0.0])
    _patch_one_pipeline(monkeypatch, s)
    summary = rfh.refresh(
        horizon=5, recent_window=25, db_path=tmp_path / "fh.db",
        thresholds=dict(_THRESHOLDS), notify=False,
    )
    res = summary["pl"]
    assert res["verdict"] == VERDICT_DEMOTE
    assert res["state"] == STATE_PENDING_REVIEW


# --------------------------------------------------------------------------- #
# 符号分歧 → 记 IC_SOURCE_DIVERGENCE
# --------------------------------------------------------------------------- #

def test_sign_divergence_recorded(tmp_path, monkeypatch):
    # 先验强负, 后验强正 (高 ICIR 想升权) → 符号分歧 → divergence + 崩盘记录
    s = _ic_series([-0.15] * 30 + [0.16, 0.14] * 12 + [0.15])
    _patch_one_pipeline(monkeypatch, s)
    db = tmp_path / "fh.db"
    summary = rfh.refresh(
        horizon=5, recent_window=25, db_path=db,
        thresholds=dict(_THRESHOLDS), notify=False,
    )
    res = summary["pl"]
    assert res["verdict"] == VERDICT_DIVERGENCE
    assert res["divergence"] is True
    # 崩盘库有 IC_SOURCE_DIVERGENCE 记录
    registry = FactorCrashRegistry(db_path=db)
    crashes = registry.query(crash_type=CRASH_IC_SOURCE_DIVERGENCE)
    assert len(crashes) >= 1
    assert crashes[0].factor_id == "pipeline:pl"


# --------------------------------------------------------------------------- #
# 同口径不 method_mismatch (回归守卫)
# --------------------------------------------------------------------------- #

def test_same_rank_ic_method_never_mismatch(tmp_path, monkeypatch):
    s = _ic_series([0.15] * 30 + [0.16, 0.14] * 12 + [0.15])
    _patch_one_pipeline(monkeypatch, s)
    summary = rfh.refresh(
        horizon=5, recent_window=25, db_path=tmp_path / "fh.db",
        thresholds=dict(_THRESHOLDS), notify=False,
    )
    assert summary["pl"]["verdict"] != VERDICT_METHOD_MISMATCH
