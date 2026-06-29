from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

from kss.memory.review_recall import (
    build_history_recap,
    extract_thesis_snippet,
    glob_symbol_reviews,
    today_features_from_stock,
)
from kss.memory.temporal_decay import timestamp_ms_for_date

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load_daily_review():
    spec = importlib.util.spec_from_file_location("daily_review_for_memory_tests", PROJECT_ROOT / "scripts/daily_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_validate_predictions():
    spec = importlib.util.spec_from_file_location(
        "validate_predictions_for_memory_tests",
        PROJECT_ROOT / "scripts/validate_predictions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _review_text(shape: str = "横盘震荡", action: str = "止损位 *10.00* 上移") -> str:
    return f"""# KSS 2026-06-18 复盘 / 2026-06-19 预测

📊 *测试股(688017) R06-18/F06-19*
  形态: ✅ MACD极值 / ❌ 量能极值 / ❌ 连续大阳(0根)

  *建议*:
     • {shape}，{action}

_自动生成 · 历史样本 IC≈0, 仅供参考, 非投资建议_
"""


def _render_stock(history_recap: list[str] | None = None) -> dict:
    stock = {
        "sym": "688017", "name": "测试股", "category": "alpha",
        "close": 10.0, "pct": 1.2, "open": 9.8, "high": 10.2, "low": 9.7,
        "turnover": 2.0, "turn_q": 0.5, "volume_ratio": 1.1,
        "macd_yest": 0.1, "macd_hist": 0.2, "macd_hist_q": 0.5, "is_macd_2y_high": False,
        "mf_today": None, "p1": False, "p2": False, "p3": False, "big_up_5d_cnt": 0,
        "p1_n": 0, "fund_n": 0, "fund_10d": float("nan"), "fund_1d": float("nan"),
        "fund_5d": float("nan"), "fund_w1": float("nan"), "fund_w5": float("nan"),
        "fund_w10": float("nan"), "history_len": 200,
        "levels": {"low_today": 9.7, "ma20": 9.5, "ma5": 9.8, "high_2y": 12.0,
                   "high_60d": 11.0, "high_today": 10.2, "open": 9.8, "limit_up": 12.0},
        "_df": pd.DataFrame({
            "trade_date": pd.to_datetime(["2026-06-20"]),
            "close": [10.0],
            "high": [10.2],
        }),
    }
    if history_recap is not None:
        stock["history_recap"] = history_recap
    return stock


def test_glob_symbol_reviews_only_returns_per_symbol_matches(tmp_path):
    (tmp_path / "2026-06-18_688017.SH.md").write_text("a", encoding="utf-8")
    (tmp_path / "2026-06-19_688017.SH.md").write_text("b", encoding="utf-8")
    (tmp_path / "2026-06-18_300750.SZ.md").write_text("c", encoding="utf-8")
    (tmp_path / "2026-06-18.md").write_text("old", encoding="utf-8")
    assert [p.name for p in glob_symbol_reviews("688017.SH", tmp_path)] == [
        "2026-06-18_688017.SH.md",
        "2026-06-19_688017.SH.md",
    ]


def test_extract_thesis_snippet_gets_shape_and_advice():
    snippet = extract_thesis_snippet(_review_text(action="止损位 *10.00*；突破观察位 *12.00*"))
    assert "形态:" in snippet
    assert "止损位" in snippet
    assert "自动生成" not in snippet


def test_extract_thesis_snippet_falls_back_without_advice():
    snippet = extract_thesis_snippet("📊 *测试股(688017)*\n  收 10.00\n  形态: 横盘\n")
    assert "形态" in snippet


def test_build_history_recap_renders_diverse_prior_and_excludes_today(tmp_path):
    shapes = ["仍横盘震荡"] * 6 + ["放量突破", "MACD缩柱", "资金改善", "回踩支撑"]
    for i, shape in enumerate(shapes, start=1):
        date = f"2026-06-{i:02d}"
        (tmp_path / f"{date}_688017.SH.md").write_text(_review_text(shape=shape), encoding="utf-8")
    (tmp_path / "2026-06-20_688017.SH.md").write_text(_review_text(shape="今日档"), encoding="utf-8")

    lines = build_history_recap(
        "688017.SH",
        "横盘震荡 MACD极值 量能极值",
        tmp_path,
        now_ms=timestamp_ms_for_date("2026-06-20"),
        k=5,
        exclude_date="2026-06-20",
    )
    body = "\n".join(lines)
    assert "近期复盘演变" in body
    assert "待验证先验" in body
    assert "今日档" not in body
    assert body.count("仍横盘震荡") == 1
    assert "过去判断" in body


def test_build_history_recap_zero_history_returns_empty(tmp_path):
    assert build_history_recap(
        "688017.SH",
        "横盘震荡",
        tmp_path,
        now_ms=timestamp_ms_for_date("2026-06-20"),
    ) == []


def test_today_features_uses_highest_probability_scenario():
    features = today_features_from_stock({
        "p1": True,
        "p2": False,
        "p3": False,
        "category": "alpha",
        "scenario_basis": "MACD极值 (P1)",
        "scenario_adj": {
            "A_break": 0.05,
            "B_up": 0.05,
            "C_flat": 0.70,
            "D_down": 0.10,
            "E_break": 0.10,
        },
    })
    assert "横盘震荡" in features
    assert "温和回落" not in features


def test_render_inserts_history_before_advice(monkeypatch):
    dr = _load_daily_review()
    stock = _render_stock(["  *近期复盘演变* (待验证先验)", "     · 2026-06-18: 形态: 横盘"])
    monkeypatch.setattr(dr, "SCENARIO_ENABLED", False)
    body = "\n\n".join(dr.render([stock], {}, "2026-06-20", "2026-06-23"))
    assert body.index("近期复盘演变") < body.index("*建议*")


def test_render_without_history_does_not_insert_empty_title(monkeypatch):
    dr = _load_daily_review()
    stock = _render_stock()
    monkeypatch.setattr(dr, "SCENARIO_ENABLED", False)
    body = "\n\n".join(dr.render([stock], {}, "2026-06-20", "2026-06-23"))
    assert "近期复盘演变" not in body
    assert "*建议*" in body


def test_validator_parse_ignores_history_recap(monkeypatch, tmp_path):
    dr = _load_daily_review()
    vp = _load_validate_predictions()
    stock = _render_stock(["  *近期复盘演变* (待验证先验)", "     · 2026-06-18: 形态: 横盘 止损位 *1.23*"])
    stock["scenario"] = {"n": 30, "p50": 0.01}
    stock["scenario_basis"] = "MACD极值 (P1)"
    stock["scenario_adj"] = {
        "A_break": 0.1, "B_up": 0.2, "C_flat": 0.4, "D_down": 0.2, "E_break": 0.1,
    }
    stock["scenario_reasons"] = {key: [] for key in stock["scenario_adj"]}
    stock["scenario_band"] = {"p10": -0.05, "p25": -0.01, "p75": 0.02, "p90": 0.05, "basis": "widened"}
    monkeypatch.setattr(dr, "SCENARIO_ENABLED", True)

    body = "\n\n".join(dr.render([stock], {}, "2026-06-20", "2026-06-23"))
    path = tmp_path / "2026-06-20_688017.SH.md"
    path.write_text("# KSS 2026-06-20 复盘 / 2026-06-23 预测\n\n" + body, encoding="utf-8")
    _, _, stocks = vp.parse_review(path)
    assert len(stocks) == 1
    assert stocks[0]["stop"] != 1.23
    assert stocks[0]["b50"] is not None
    assert stocks[0]["b80"] is not None


def test_validator_strips_raw_history_recap_block(tmp_path):
    vp = _load_validate_predictions()
    path = tmp_path / "2026-06-20_688017.SH.md"
    path.write_text(
        """# KSS 2026-06-20 复盘 / 2026-06-23 预测

📊 *测试股(688017) R06-20/F06-23*
  收 10.00 (+1.00%)
  *次日情形分布* (n=30, 基于 MACD极值, regime=中性)
  C. 横盘震荡 (-1~+1%)       20.0%   40.0%
  *预期区间* (区间底线: 全样本 P10/P90, 条件+无条件取宽):
     收盘 50% 概率落 *9.90 ~ 10.20* (中位 10.10)
     极端 80% 区间 9.50 ~ 10.50

  *近期复盘演变* (待验证先验)
     · 2026-06-18: 历史里可能仍有 止损位 *1.23*
     · 2026-06-17: 收盘 50% 概率落 *1.00 ~ 2.00* (中位 1.50)
     · 2026-06-16: 极端 80% 区间 0.50 ~ 2.50
     ↑ 以上为过去判断，请用今日数据重新验证，变化点优先

  *建议*:
     • 止损触发 → 减半仓留底仓, 止损位 *9.70*
""",
        encoding="utf-8",
    )

    _, _, stocks = vp.parse_review(path)
    assert len(stocks) == 1
    assert stocks[0]["stop"] == 9.70
    assert stocks[0]["b50"] == (9.90, 10.20)
    assert stocks[0]["b80"] == (9.50, 10.50)
