"""ETF 申赎信号卡（唯一带 direction 的卡类型）。阈值来自一年剂量曲线回测。"""

from __future__ import annotations

from typing import Any

from kss.sector.etf_radar import (
    ACCEL_THRESHOLD_PCT,
    DIVERGENCE_RET_TH,
    GRADE_CONFIRM_TH,
)
from kss.signal_cards.common import base_card
from kss.storage import etf_radar as etf_store

# 左开右闭剂量表（docs/solutions/etf_flow_signal_lessons.md）
# 区间: (lo, hi]；最低档 lo=-inf；最高档 hi=+inf 且为开区间 >+2
# effective_n: 一年回测非重叠抽样下各档去重交易日近似（报告未逐档公开 n，
# 使用校准窗 floor=46 周频非重叠尺度的保守共用值；字段必填以绑定 R5）。
_DOSE_EFFECTIVE_N = 46

# (lo exclusive, hi inclusive, hist_forward_ret, win_rate, bucket, direction)
# 最高档特殊：lo exclusive, no upper → >+2
_DOSE_TABLE: list[tuple[float, float, float, float, str, str]] = [
    (float("-inf"), -5.0, 0.0314, 0.66, "flow5d_le_-5", "hist_favorable"),
    (-5.0, -2.0, 0.0345, 0.77, "flow5d_gt_-5_le_-2", "hist_favorable"),
    (-2.0, 0.0, 0.0105, 0.71, "flow5d_gt_-2_le_0", "hist_favorable"),
    (0.0, 2.0, 0.0021, 0.49, "flow5d_gt_0_le_2", "hist_unfavorable"),
    (2.0, float("inf"), -0.0030, 0.50, "flow5d_gt_2", "hist_unfavorable"),
]


def dose_bucket(flow_5d: float) -> tuple[str, float, float, str]:
    """flow_5d → (bucket_id, hist_forward_ret, win_rate, direction). 左开右闭。"""
    for lo, hi, fwd, wr, bucket, direction in _DOSE_TABLE:
        if lo == float("-inf"):
            if flow_5d <= hi:
                return bucket, fwd, wr, direction
        elif hi == float("inf"):
            if flow_5d > lo:
                return bucket, fwd, wr, direction
        else:
            # (lo, hi]
            if lo < flow_5d <= hi:
                return bucket, fwd, wr, direction
    # 不应到达
    raise ValueError(f"flow_5d={flow_5d} 未落入任何剂量档")


def generate_for_date(
    trade_date: str, *, db_path: Any = None
) -> list[dict[str, Any]]:
    """从 etf_radar_snapshots 读当日快照，逐主题产卡。"""
    snap = etf_store.read_by_date(trade_date, db_path=db_path)
    if snap is None:
        return []

    data_as_of = str(snap.get("data_date") or trade_date)
    stale = bool(snap.get("stale"))
    themes = snap.get("themes") or {}
    regime = snap.get("momentum_regime_r3") or {}
    in_regime = bool(regime.get("in_regime")) if isinstance(regime, dict) else False
    # 校准 regime 为动量期；当前窗口多为非动量 → regime_mismatch
    regime_mismatch = not in_regime

    cards: list[dict[str, Any]] = []
    for theme_name, metrics_raw in themes.items():
        if not isinstance(metrics_raw, dict):
            continue
        flow_5d = metrics_raw.get("flow_5d")
        flow_1d = metrics_raw.get("flow_1d")
        past5_ret = metrics_raw.get("past5_ret")
        divergence = bool(metrics_raw.get("divergence"))
        # schema 漂移：20260609 前缺 accel/n_funds/rank_5d
        has_accel = "accel" in metrics_raw
        has_n_funds = "n_funds" in metrics_raw
        has_rank = "rank_5d" in metrics_raw
        missing_keys = [
            k
            for k, present in (
                ("accel", has_accel),
                ("n_funds", has_n_funds),
                ("rank_5d", has_rank),
            )
            if not present
        ]

        metrics: dict[str, Any] = {
            "flow_1d": flow_1d,
            "flow_5d": flow_5d,
            "past5_ret": past5_ret,
            "grade": metrics_raw.get("grade"),
            "divergence": divergence,
            "accel": metrics_raw.get("accel") if has_accel else None,
            "n_funds": metrics_raw.get("n_funds") if has_n_funds else None,
            "rank_5d": metrics_raw.get("rank_5d") if has_rank else None,
            "missing_keys": missing_keys,
            "regime_mismatch": regime_mismatch,
            "grade_confirm_th": GRADE_CONFIRM_TH,
            "accel_threshold_pct": ACCEL_THRESHOLD_PCT,
            "divergence_ret_th": DIVERGENCE_RET_TH,
        }

        if stale:
            cards.append(
                base_card(
                    card_type="etf_flow",
                    trade_date=trade_date,
                    subject=str(theme_name),
                    rule_id="etf_flow_stale",
                    metrics=metrics,
                    threshold_source="backtested",
                    coverage="insufficient_data",
                    data_as_of=data_as_of,
                    direction=None,
                    extra={"regime_mismatch": regime_mismatch},
                )
            )
            continue

        if flow_5d is None:
            cards.append(
                base_card(
                    card_type="etf_flow",
                    trade_date=trade_date,
                    subject=str(theme_name),
                    rule_id="etf_flow_missing_flow5d",
                    metrics=metrics,
                    threshold_source="backtested",
                    coverage="insufficient_data",
                    data_as_of=data_as_of,
                    direction=None,
                    extra={"regime_mismatch": regime_mismatch},
                )
            )
            continue

        bucket, fwd, wr, direction = dose_bucket(float(flow_5d))
        rule_id = "etf_flow_dose"
        # 见顶预警：上涨中 flow 转正 — 用 divergence 规则，不产看多方向
        if divergence:
            rule_id = "etf_flow_divergence_top"
            # 强制不利读数：见顶预警不是看多
            direction = "hist_unfavorable"

        # 大跌日+申购：禁止抄底语义 — 不改 direction 表，但 rule 标注 not_bottom_fish
        # （后验：大跌 + 申购 已被证伪为抄底）
        bottom_fish_flag = False
        if (
            past5_ret is not None
            and float(past5_ret) < -3.0
            and float(flow_5d) > 0
        ):
            bottom_fish_flag = True
            rule_id = "etf_flow_drawdown_inflow_not_bottom"
            # 不得标 favorable
            direction = "hist_unfavorable"

        metrics["dose_bucket"] = bucket
        metrics["bottom_fish_disproven"] = bottom_fish_flag

        cards.append(
            base_card(
                card_type="etf_flow",
                trade_date=trade_date,
                subject=str(theme_name),
                rule_id=rule_id,
                metrics=metrics,
                threshold_source="backtested",
                coverage="covered" if not missing_keys else "covered",
                data_as_of=data_as_of,
                direction=direction,
                dose_bucket=bucket,
                hist_forward_ret=fwd,
                win_rate=wr,
                effective_n=_DOSE_EFFECTIVE_N,
                extra={
                    "regime_mismatch": regime_mismatch,
                    "schema_missing_keys": missing_keys,
                },
            )
        )
    return cards
