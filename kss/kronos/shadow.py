"""影子打分（U5）—— 前向战绩走 log_mv 同闸门（按 mined 计）+ 固定窗口毕业.

影子只读：达标前不影响任何决策。战绩走
:meth:`Significance.is_deployable(strategy_family="mined")`（n_trials=100，DSR 极严，
足以拒掉 Sharpe 1.93）。基础模型携带巨大隐藏试验次数，这个惩罚是目的不是障碍。

毕业 = **过闸门** 且 **满固定前向窗口**（:data:`FORWARD_WINDOW_TRADING_DAYS`）。
周校验给影子打 Brier / 方向命中 / 区间覆盖，沿用"连续两周不达标拉黑"停用规则
（见 docs/solutions/daily_review_prediction_validation.md）。

R8/R9/R10/R11，Covers AE2。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from kss.backtest.significance import Significance
from kss.kronos.config import BLACKLIST_AFTER_FAILED_WEEKS, FORWARD_WINDOW_TRADING_DAYS

logger = logging.getLogger(__name__)


@dataclass
class ShadowVerdict:
    """影子毕业判定."""

    n_obs: int
    window_full: bool
    gate_passed: bool
    graduated: bool
    sharpe: float
    dsr: float
    p_value: float
    details: dict = field(default_factory=dict)


def _realized_close(realized_frames: dict[str, pd.DataFrame], symbol: str, date: str) -> float | None:
    df = realized_frames.get(symbol)
    if df is None:
        return None
    d = pd.Timestamp(date).normalize()
    hit = df[pd.to_datetime(df["trade_date"]).dt.normalize() == d]
    if hit.empty:
        return None
    return float(hit["close"].iloc[0])


def build_shadow_returns(
    predictions: pd.DataFrame,
    realized_frames: dict[str, pd.DataFrame],
    *,
    horizon: int = 1,
) -> pd.Series:
    """把 Kronos 方向预测 + 真实收盘合成影子日度战绩序列.

    每条预测（symbol, base_date, horizon=h）：信号 = ``sign(point_ret)``，
    真实收益 = ``realized_close(target) / base_close - 1``，策略收益 =
    信号 × 真实收益（方向多空）。按 ``base_date`` 截面等权聚合成日度序列。

    Args:
        predictions: :class:`KronosPredictionStore` 读出的预测行.
        realized_frames: ``{symbol: cs_data}`` 用于查真实收盘.
        horizon: 用哪个预测步长打分（默认 1，日度）.

    Returns:
        按 ``base_date`` 索引的日度策略收益 ``pd.Series``（升序）.
    """
    if predictions is None or predictions.empty:
        return pd.Series(dtype=float)
    sub = predictions[predictions["horizon"] == horizon]
    recs: list[tuple[pd.Timestamp, float]] = []
    for _, row in sub.iterrows():
        rc = _realized_close(realized_frames, str(row["ts_code"]), str(row["target_date"]))
        if rc is None or not row.get("base_close"):
            continue
        realized_ret = rc / float(row["base_close"]) - 1.0
        signal = np.sign(float(row["point_ret"])) if pd.notna(row["point_ret"]) else 0.0
        recs.append((pd.Timestamp(row["base_date"]), signal * realized_ret))
    if not recs:
        return pd.Series(dtype=float)
    df = pd.DataFrame(recs, columns=["base_date", "ret"])
    daily = df.groupby("base_date")["ret"].mean().sort_index()
    return daily


def score_shadow(
    returns: pd.Series,
    *,
    forward_window: int = FORWARD_WINDOW_TRADING_DAYS,
) -> ShadowVerdict:
    """影子战绩判定：mined 闸门 + 固定窗口（``Covers AE2``）.

    Args:
        returns: 影子日度战绩序列.
        forward_window: 毕业所需的固定前向窗口（交易日）.

    Returns:
        :class:`ShadowVerdict`；毕业 = 过 mined 闸门 **且** 窗口已满.
    """
    r = returns.dropna()
    n = len(r)
    window_full = n >= forward_window
    details = Significance.is_deployable(
        r, strategy_family="mined", return_details=True
    )
    gate_passed = bool(details["passed"])
    graduated = gate_passed and window_full
    verdict = ShadowVerdict(
        n_obs=n,
        window_full=window_full,
        gate_passed=gate_passed,
        graduated=graduated,
        sharpe=float(details.get("sharpe", 0.0)),
        dsr=float(details.get("dsr", float("nan"))),
        p_value=float(details.get("p_value", 1.0)),
        details=details,
    )
    logger.info(
        "影子判定：n=%d window_full=%s gate=%s → graduated=%s (sharpe=%.2f dsr=%.2f)",
        n, window_full, gate_passed, graduated, verdict.sharpe, verdict.dsr,
    )
    return verdict


# z(0.9)-z(0.1) 区间宽度（标准正态），用于从分位带反推隐含 std。
_Z_BAND = stats.norm.ppf(0.9) - stats.norm.ppf(0.1)


def weekly_validation_metrics(
    predictions: pd.DataFrame,
    realized_frames: dict[str, pd.DataFrame],
    *,
    horizon: int = 1,
) -> dict:
    """周校验：Brier（方向）/ 方向命中 / 区间覆盖（``Covers R11``）.

    方向概率从存储的分位带反推：假设近正态，
    ``std ≈ (upper - lower) / (z90 - z10)``，
    ``p_up = P(close > base_close) = 1 - Phi((base - point)/std)``。

    Args:
        predictions: 预测行.
        realized_frames: ``{symbol: cs_data}``.
        horizon: 打分步长.

    Returns:
        ``{brier, dir_rate, coverage, n}``；无可验证样本时各项为 NaN、``n=0``.
    """
    if predictions is None or predictions.empty:
        return {"brier": float("nan"), "dir_rate": float("nan"), "coverage": float("nan"), "n": 0}
    sub = predictions[predictions["horizon"] == horizon]
    briers, dirs, covs = [], [], []
    for _, row in sub.iterrows():
        rc = _realized_close(realized_frames, str(row["ts_code"]), str(row["target_date"]))
        base = row.get("base_close")
        if rc is None or not base:
            continue
        base = float(base)
        point = float(row["point_close"])
        lower = float(row["lower_close"])
        upper = float(row["upper_close"])

        realized_up = 1.0 if rc > base else 0.0
        std = max((upper - lower) / _Z_BAND, 1e-9)
        p_up = float(1.0 - stats.norm.cdf((base - point) / std))
        briers.append((p_up - realized_up) ** 2)
        dirs.append(1.0 if (point > base) == (rc > base) else 0.0)
        covs.append(1.0 if lower <= rc <= upper else 0.0)

    n = len(briers)
    if n == 0:
        return {"brier": float("nan"), "dir_rate": float("nan"), "coverage": float("nan"), "n": 0}
    return {
        "brier": float(np.mean(briers)),
        "dir_rate": float(np.mean(dirs)),
        "coverage": float(np.mean(covs)),
        "n": n,
    }


def week_metrics_ok(metrics: dict) -> bool:
    """单周是否达标：方向 > 50% 且 Brier < 0.25（方向二分类随机基线）."""
    if metrics.get("n", 0) == 0:
        return False
    return metrics["dir_rate"] > 0.5 and metrics["brier"] < 0.25


def update_blacklist_state(
    prev_consecutive_fails: int,
    week_ok: bool,
    *,
    threshold: int = BLACKLIST_AFTER_FAILED_WEEKS,
) -> tuple[int, bool]:
    """更新连续不达标计数与拉黑状态（纯函数，状态由调用方持久化）.

    Args:
        prev_consecutive_fails: 此前连续不达标周数.
        week_ok: 本周是否达标.
        threshold: 连续多少周不达标 → 拉黑.

    Returns:
        ``(new_consecutive_fails, blacklisted)``.
    """
    new = 0 if week_ok else prev_consecutive_fails + 1
    return new, new >= threshold
