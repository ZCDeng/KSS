"""U4 — daily_review 次日预测修复 (校准优先) 单元测试.

覆盖 docs/plans/2026-06-21-001-backtest-loop-closure-plan.md 的 U4 验收:
- 无条件兜底加宽 80% 区间 (R1, widen_interval / unconditional_interval)
- regime 开关在动量段抑制反向 (均值回归) 先验 (R2, adjusted_scenarios + detect_regime)
- 删常量「5-10 日仍看涨」后该建议不出现 (R3, _advice_block)
- 止损为仓位语义、与中期观点解耦 (R4, _advice_block)
- SCENARIO_ENABLED off 时情形分布段不渲染 (R5, render)
- Brier 校准/分辨分解数值正确 (R6, brier_decomposition)
- 校准统计读 U2 账本 realized_ret/outcome 按 regime 聚合 (calibration_from_ledger)

characterization-first: 改前已固化随机基线 (Brier 0.828 / 方向 43% / 80%覆盖 53%),
本测试锁住修复后的确定性行为, 而非复现随机基线。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load("daily_review_322_017", "scripts/daily_review_322_017.py")
vp = _load("validate_predictions", "scripts/validate_predictions.py")


# --------------------------------------------------------------------------- #
# R1: 无条件兜底加宽区间
# --------------------------------------------------------------------------- #


def _df_with_fwd(fwd_1d: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"fwd_1d": fwd_1d})


def test_unconditional_interval_quantiles():
    df = _df_with_fwd(list(np.linspace(-0.10, 0.10, 101)))
    u = dr.unconditional_interval(df)
    assert u["n"] == 101
    assert u["p10"] == pytest.approx(-0.08, abs=1e-9)
    assert u["p90"] == pytest.approx(0.08, abs=1e-9)


def test_unconditional_interval_empty():
    assert dr.unconditional_interval(_df_with_fwd([np.nan, np.nan])) == {}


def test_widen_takes_wider_tail_each_side():
    cond = {"n": 30, "p10": -0.032, "p25": -0.015, "p75": 0.020, "p90": 0.041}
    uncond = {"n": 800, "p10": -0.068, "p25": -0.030, "p75": 0.030, "p90": 0.073}
    b = dr.widen_interval(cond, uncond)
    # 上行尾部条件偏窄 (0.041 < 0.073) → 取无条件; 下行同理
    assert b["p10"] == pytest.approx(-0.068)
    assert b["p90"] == pytest.approx(0.073)
    assert b["basis"] == "widened"


def test_widen_keeps_wider_conditional_side():
    # 条件下行更宽 (-0.09) 应保留, 上行用无条件
    cond = {"n": 30, "p10": -0.090, "p25": -0.040, "p75": 0.015, "p90": 0.020}
    uncond = {"n": 800, "p10": -0.068, "p25": -0.030, "p75": 0.030, "p90": 0.073}
    b = dr.widen_interval(cond, uncond)
    assert b["p10"] == pytest.approx(-0.090)  # 条件更负, 保留
    assert b["p90"] == pytest.approx(0.073)   # 无条件更正


def test_widen_small_n_forces_unconditional():
    cond = {"n": 12, "p10": -0.200, "p25": -0.10, "p75": 0.10, "p90": 0.200}
    uncond = {"n": 800, "p10": -0.068, "p25": -0.030, "p75": 0.030, "p90": 0.073}
    b = dr.widen_interval(cond, uncond)
    # n<20: 不允许小样本独立定宽 (即便它更宽), 强制无条件
    assert b["p90"] == pytest.approx(0.073)
    assert b["p10"] == pytest.approx(-0.068)
    assert b["basis"] == "uncond"


def test_widen_coverage_improves():
    """无条件兜底把窄条件区间加宽 → 实测落点更可能被 80% 区间覆盖."""
    realized = np.linspace(-0.09, 0.09, 200)  # 真实次日收益分布 (尾部到 ±9%)
    cond = {"n": 15, "p10": -0.030, "p25": -0.015, "p75": 0.015, "p90": 0.030}
    uncond = dr.unconditional_interval(_df_with_fwd(list(realized)))
    cond_cov = np.mean((realized >= cond["p10"]) & (realized <= cond["p90"]))
    band = dr.widen_interval(cond, uncond)
    wide_cov = np.mean((realized >= band["p10"]) & (realized <= band["p90"]))
    assert wide_cov > cond_cov
    assert wide_cov >= 0.80  # 加宽后达成 80% 覆盖目标


def test_widen_degrades_when_uncond_missing():
    cond = {"n": 30, "p10": -0.03, "p25": -0.01, "p75": 0.02, "p90": 0.04}
    assert dr.widen_interval(cond, {})["p90"] == pytest.approx(0.04)
    assert dr.widen_interval({}, {}) == {}


# --------------------------------------------------------------------------- #
# R2: regime 开关 — 动量段抑制反向 (均值回归) 先验
# --------------------------------------------------------------------------- #


def _macd_squeeze_stock() -> dict:
    """构造一个触发 MACD 缩柱均值回归乘子的 stock dict (顶背离前奏)."""
    return {
        "macd_yest": 1.0, "macd_hist": 0.5, "macd_hist_q": 0.95,
        "p1": False, "p2": False, "p3": False,
        "fund_10d": np.nan, "fund_1d": np.nan,
    }


def _base_dist() -> dict:
    return {"n": 40, "A_break": 0.15, "B_up": 0.20, "C_flat": 0.30,
            "D_down": 0.25, "E_break": 0.10}


def test_neutral_applies_mean_reversion():
    probs, reasons = dr.adjusted_scenarios(_base_dist(), _macd_squeeze_stock(),
                                           regime=dr.REGIME_NEUTRAL)
    # 中性: MACD 缩柱把 D/E 上调、A 下调 (反向均值回归先验)
    assert "MACD缩柱+" in reasons["D_down"]
    raw = _base_dist()
    assert probs["A_break"] < raw["A_break"]  # A 被压低
    assert probs["D_down"] > raw["D_down"]    # D 被抬高


def test_momentum_suppresses_reverse_prior():
    probs, reasons = dr.adjusted_scenarios(_base_dist(), _macd_squeeze_stock(),
                                           regime=dr.REGIME_MOMENTUM)
    # 动量: 跳过均值回归乘子 (D/E 不被均值回归抬高), A 不被压低
    assert "MACD缩柱+" not in reasons["D_down"]
    assert "MACD缩柱+" not in reasons["E_break"]
    raw = _base_dist()
    # 归一化后 A 相对中性显著抬升 (反向先验被抑制)
    neutral_probs, _ = dr.adjusted_scenarios(_base_dist(), _macd_squeeze_stock(),
                                             regime=dr.REGIME_NEUTRAL)
    assert probs["A_break"] > neutral_probs["A_break"]


def test_detect_regime_injected_and_failsafe():
    assert dr.detect_regime("20260618", regime_status={"in_regime": True}) == dr.REGIME_MOMENTUM
    assert dr.detect_regime("20260618", regime_status={"in_regime": False}) == dr.REGIME_NEUTRAL
    # status_fn 注入 → 不联网; 异常 → fail-safe neutral
    assert dr.detect_regime("20260618", status_fn=lambda d: {"in_regime": True}) == dr.REGIME_MOMENTUM

    def _boom(_):
        raise RuntimeError("network down")

    assert dr.detect_regime("20260618", status_fn=_boom) == dr.REGIME_NEUTRAL
    assert dr.detect_regime("20260618", status_fn=lambda d: None) == dr.REGIME_NEUTRAL


def test_detect_regime_reuses_momentum_regime_contract():
    """build_regime_status 的 in_regime 字段被直接消费 (复用而非新建)."""
    from kss.sector import momentum_regime
    # 注入符合 build_regime_status 返回 schema 的 dict
    status = {"in_regime": True, "mom20": 12.0, "mom20_th": momentum_regime.MOM_TH,
              "breadth_5dma": 0.03, "breadth_th": momentum_regime.BREADTH_TH}
    assert dr.detect_regime("20260618", regime_status=status) == dr.REGIME_MOMENTUM


# --------------------------------------------------------------------------- #
# R3 + R4: 删常量涨观点 + 止损仓位语义
# --------------------------------------------------------------------------- #


def _advice_stock() -> dict:
    return {
        "p1": True, "p2": True, "p3": True,
        "macd_yest": 0.5, "macd_hist": 1.0, "macd_hist_q": 0.8,
        "fund_5d": 0.02, "fund_w10": 0.7, "fund_10d": 0.05, "fund_w1": 0.6,
        "fund_1d": 0.001, "fund_w5": 0.6,
        "mf_today": None, "category": "alpha", "close": 320.0,
        "levels": {"low_today": 323.0, "ma20": 310.0, "high_2y": 340.0,
                   "high_60d": 335.0},
    }


def test_advice_no_constant_bull_view():
    lines = "\n".join(dr._advice_block(_advice_stock()))
    # R3: bull_signals + fund_10d>3% 原会追加「5-10 日仍看涨」, 已删除
    assert "仍看涨" not in lines


def test_advice_stop_is_position_semantics():
    s = _advice_stock()
    lines = "\n".join(dr._advice_block(s))
    # R4: 止损改为减半仓留底仓 + 与中期观点解耦
    assert "减半仓" in lines
    assert "解耦" in lines
    # 止损价仍由代码渲染 (low_today * 0.99), 且保留可解析 token
    stop = s["levels"]["low_today"] * 0.99
    assert f"止损位 *{stop:.2f}*" in lines


# --------------------------------------------------------------------------- #
# R5: SCENARIO_ENABLED 撤段闸
# --------------------------------------------------------------------------- #


def _render_one_stock(monkeypatch, enabled: bool) -> str:
    df = pd.read_csv(PROJECT_ROOT / "cs_data_688322.csv")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df = dr.add_indicators(df)
    s = dr.stock_section("688322", "奥比中光", df, None, regime=dr.REGIME_NEUTRAL)
    s["_df"] = df
    s["category"] = "alpha"
    monkeypatch.setattr(dr, "SCENARIO_ENABLED", enabled)
    chunks = dr.render([s], {}, "2026-06-18", "2026-06-19")
    return "\n\n".join(chunks)


def test_scenario_segment_rendered_when_enabled(monkeypatch):
    body = _render_one_stock(monkeypatch, enabled=True)
    assert "次日情形分布" in body


def test_scenario_segment_removed_when_disabled(monkeypatch):
    body = _render_one_stock(monkeypatch, enabled=False)
    # 撤段: 情形分布段整块不渲染; 关键位 / 操作建议仍在
    assert "次日情形分布" not in body
    assert "关键位" in body
    assert "建议" in body


def test_scenario_header_shows_regime(monkeypatch):
    df = pd.read_csv(PROJECT_ROOT / "cs_data_688322.csv")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df = dr.add_indicators(df)
    s = dr.stock_section("688322", "奥比中光", df, None, regime=dr.REGIME_MOMENTUM)
    table = "\n".join(dr._scenario_table(s))
    assert "regime=动量" in table


# --------------------------------------------------------------------------- #
# R6: Brier 校准/分辨分解
# --------------------------------------------------------------------------- #


def test_brier_decomp_perfect_calibration():
    recs = [(1.0, 1)] * 10 + [(0.0, 0)] * 10
    d = vp.brier_decomposition(recs)
    assert d["reliability"] == pytest.approx(0.0, abs=1e-9)   # 完美校准
    assert d["resolution"] == pytest.approx(0.25, abs=1e-9)   # 满分辨
    assert d["brier"] == pytest.approx(0.0, abs=1e-9)


def test_brier_decomp_no_resolution():
    import random
    random.seed(7)
    recs = [(random.choice([0.2, 0.4, 0.6, 0.8]), random.choice([0, 1]))
            for _ in range(600)]
    d = vp.brier_decomposition(recs)
    # 预测概率与结果无关 → 分辨 ≈ 0 (无区分度), 这正是实测失效模式
    assert d["resolution"] < 0.01


def test_brier_decomp_identity_holds():
    recs = [(0.7, 1), (0.7, 0), (0.3, 0), (0.9, 1), (0.1, 0), (0.6, 1)]
    d = vp.brier_decomposition(recs)
    # brier ≈ reliability - resolution + uncertainty
    assert d["brier"] == pytest.approx(
        d["reliability"] - d["resolution"] + d["uncertainty"], abs=1e-9)


def test_brier_decomp_empty():
    d = vp.brier_decomposition([])
    assert d["n"] == 0 and d["brier"] == 0.0


# --------------------------------------------------------------------------- #
# U2 取数: 校准统计读账本 realized_ret/outcome 按 regime 聚合
# --------------------------------------------------------------------------- #


def test_calibration_from_ledger_by_regime():
    recs = [
        {"regime_label": "momentum", "realized_ret": 0.05, "outcome": "win"},
        {"regime_label": "momentum", "realized_ret": -0.02, "outcome": "loss"},
        {"regime_label": "neutral", "realized_ret": 0.01, "outcome": "win"},
        {"regime_label": "neutral", "realized_ret": None, "outcome": None},  # 未结算
    ]
    agg = vp.calibration_from_ledger(recs)
    assert agg["momentum"]["n"] == 2
    assert agg["momentum"]["win_rate"] == pytest.approx(0.5)
    assert agg["momentum"]["mean_ret"] == pytest.approx(0.015)
    assert agg["neutral"]["n"] == 1  # 未结算被排除
    assert agg["neutral"]["win_rate"] == pytest.approx(1.0)


def test_calibration_from_ledger_uses_real_ledger(tmp_path):
    """端到端: 写 U2 账本 → 结算 → 按 regime 聚合 (不读价格细节)."""
    from kss.prediction.ledger import PredictionLedger, PredictionRecord

    led = PredictionLedger(db_path=tmp_path / "ledger.db")
    led.record_prediction(PredictionRecord(
        prediction_id="20260601_688322", prediction_date="20260601",
        symbol="688322", regime_label="momentum"))
    led.settle("20260601_688322", t1_open=100.0, t2_open=105.0)  # +5% win
    settled = led.query(status="settled")
    agg = vp.calibration_from_ledger(settled)
    assert agg["momentum"]["n"] == 1
    assert agg["momentum"]["win_rate"] == pytest.approx(1.0)
    assert agg["momentum"]["mean_ret"] == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# 解析回环: 修复后的渲染文本仍可被 validate_predictions 解析 (止损 / 区间)
# --------------------------------------------------------------------------- #


def test_render_text_parseable_by_validator(monkeypatch, tmp_path):
    body = _render_one_stock(monkeypatch, enabled=True)
    full = "# KSS 2026-06-18 复盘 / 2026-06-19 预测\n\n" + body
    path = tmp_path / "2026-06-18.md"
    path.write_text(full, encoding="utf-8")
    review_date, forecast_date, stocks = vp.parse_review(path)
    assert review_date == "2026-06-18"
    assert stocks, "应解析出至少一只股票"
    st = stocks[0]
    # 止损价仍被解析 (R4 改文案但保留 token)
    assert st["stop"] is not None
    # 区间仍被解析 (R1 加宽不破坏格式)
    assert st["b50"] is not None and st["b80"] is not None
