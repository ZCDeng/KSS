"""S/R 位识别单测：因果性、聚类评分、命中统计."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.indicators.sr_levels import (
    DEFAULT_LEVEL_PARAMS,
    causal_features,
    detect_levels,
    hit_stats,
    to_levels_overlay,
)

# 周期 8 的三角波：低点 100（支撑）出现在 idx 3/11/19，高点 108（阻力）出现在 idx 7/15。
_WAVE_CYCLE = [106, 104, 102, 100, 102, 104, 106, 108]


def _wave_df(n_cycles: int = 3, start: str = "2023-01-02") -> pd.DataFrame:
    vals = (_WAVE_CYCLE * n_cycles)
    n = len(vals)
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": vals,
            "high": vals,
            "low": vals,
            "close": vals,
        }
    )


def _params(**overrides) -> dict:
    return {**DEFAULT_LEVEL_PARAMS, "pivot_window": 3, "atr_period": 5, **overrides}


def test_detect_levels_finds_touched_platform() -> None:
    df = _wave_df(3)
    levels = detect_levels(df, _params())
    support = [lv for lv in levels if lv.kind == "support"]
    assert support, "应检出支撑位"
    tol = 3.0
    assert any(abs(lv.price - 100.0) <= tol for lv in support)


def test_more_touches_scores_higher() -> None:
    df = _wave_df(3)
    levels = detect_levels(df, _params())
    support = max((lv for lv in levels if lv.kind == "support"), key=lambda lv: lv.touches, default=None)
    resistance = max((lv for lv in levels if lv.kind == "resistance"), key=lambda lv: lv.touches, default=None)
    assert support is not None and resistance is not None
    # 支撑触及 3 次（idx 3/11/19）多于阻力触及 2 次（idx 7/15），且最近一次触及更靠后。
    assert support.touches > resistance.touches
    assert support.score > resistance.score


def test_causal_asof_unaffected_by_future_bars() -> None:
    df_short = _wave_df(3)  # 24 根，最后一根日期 = asof
    asof = str(df_short["trade_date"].iloc[19].date())

    levels_short = detect_levels(df_short, _params(), asof=asof)

    df_long = _wave_df(5)  # 追加更多未来 bar
    levels_long = detect_levels(df_long, _params(), asof=asof)

    assert [(lv.price, lv.kind, lv.score, lv.touches) for lv in levels_short] == [
        (lv.price, lv.kind, lv.score, lv.touches) for lv in levels_long
    ]


def test_multi_timeframe_switch_only_changes_overlapping_score() -> None:
    df = _wave_df(6)  # 48 根业务日 ≈ 9-10 周，覆盖多个周线采样点
    levels_off = detect_levels(df, _params(multi_timeframe=False))
    levels_on = detect_levels(df, _params(multi_timeframe=True))

    def _find(levels, kind, price, tol=3.0):
        for lv in levels:
            if lv.kind == kind and abs(lv.price - price) <= tol:
                return lv
        return None

    off_support = _find(levels_off, "support", 100.0)
    on_support = _find(levels_on, "support", 100.0)
    assert off_support is not None and on_support is not None
    # 支撑位在多个周线窗口都作为周线低点重合，开启多周期应等于或高于关闭时的评分。
    assert on_support.score >= off_support.score


def test_short_or_empty_sample_returns_empty_list() -> None:
    df = _wave_df(3).iloc[:5]  # 少于 pivot_window*2+5 = 11 根
    assert detect_levels(df, _params()) == []

    empty = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close"])
    assert detect_levels(empty, _params()) == []


def test_hit_stats_reports_rebound_ratio() -> None:
    df = _wave_df(4)
    stats = hit_stats(df, _params(), forward_days=4)
    assert stats["levels"] > 0
    assert stats["touches"] > 0
    assert stats["rebound_rate"] is not None
    # 周期波形上涨段占多数，支撑触及后 4 日多数应反弹。
    assert stats["rebound_rate"] >= 0.5


def test_causal_features_no_lookahead_and_shape() -> None:
    df = _wave_df(3)
    feat = causal_features(df, _params())
    assert len(feat) == len(df)
    assert set(feat.columns) == {
        "nearest_support",
        "nearest_resistance",
        "support_strength",
        "resistance_strength",
    }
    # 早期 bar（尚无 pivot 确认）应为 NaN，不产生前瞻信息。
    assert feat["nearest_support"].iloc[0:3].isna().all()


def test_to_levels_overlay_shape() -> None:
    df = _wave_df(3)
    levels = detect_levels(df, _params())
    overlay = to_levels_overlay(levels)
    assert overlay["status"] == "ok"
    assert len(overlay["levels"]) == len(levels)
    if overlay["levels"]:
        item = overlay["levels"][0]
        assert set(item.keys()) == {"price", "kind", "strength", "touches"}


def test_to_levels_overlay_status_passthrough() -> None:
    overlay = to_levels_overlay([], status="skipped", reason="无行情或样本过短")
    assert overlay["status"] == "skipped"
    assert overlay["reason"] == "无行情或样本过短"
    assert overlay["levels"] == []
