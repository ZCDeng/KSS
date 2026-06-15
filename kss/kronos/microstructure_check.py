"""A 股微结构校验闸门（U6）—— U7 诊断的前置 gating.

合成 K 线在被 U7 采信前必须带 A 股微结构，否则压测无效（``Covers AE4``）：

- **涨跌停截断**：价格日变动不得超过涨跌停（主板 ±10%，科创 688/创业 300 ±20%），
  且对抗 regime 应至少出现一次触板——只有截断被尊重 + 板确实被触到，才说明
  生成器学到了 A 股的硬约束。
- **T+1 跳空**：存在隔夜跳空（``open != 前一日 close``）；恒等于前收的序列不真实。

校验不达标的批次标记无效、不进 U7 诊断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kss.kronos.config import (
    LIMIT_PCT_MAIN,
    LIMIT_PCT_STAR_CHINEXT,
    LIMIT_TOLERANCE,
)

logger = logging.getLogger(__name__)

# 跳空判定阈值：|open/pre_close - 1| 超过此值算一次 T+1 跳空。
_GAP_EPS = 1e-3
# 触板率偏离真实分布的告警阈值（绝对差）。
_LIMIT_RATE_DEVIATION_WARN = 0.10


def limit_pct_for(symbol: str) -> float:
    """按板块返回涨跌停比例：688/300 → 20%，其余 → 10%."""
    code = str(symbol).split(".")[0]
    if code.startswith(("688", "300")):
        return LIMIT_PCT_STAR_CHINEXT
    return LIMIT_PCT_MAIN


@dataclass
class MicrostructureReport:
    """单只合成序列的微结构校验报告."""

    symbol: str
    valid: bool
    has_limit_hit: bool
    over_limit: bool
    has_gap: bool
    limit_hit_rate: float
    gap_rate: float
    n_bars: int
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _pct_chg(df: pd.DataFrame) -> np.ndarray:
    """逐 bar 收益：优先用 pre_close，否则用前一根 close 链式推."""
    close = df["close"].to_numpy(dtype=float)
    if "pre_close" in df.columns and df["pre_close"].notna().all():
        pre = df["pre_close"].to_numpy(dtype=float)
    else:
        pre = np.concatenate([[close[0]], close[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pre > 0, close / pre - 1.0, 0.0)


def _gap(df: pd.DataFrame) -> np.ndarray:
    """逐 bar 隔夜跳空：open/pre_close - 1（首 bar 记 0）."""
    open_ = df["open"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    if "pre_close" in df.columns and df["pre_close"].notna().all():
        pre = df["pre_close"].to_numpy(dtype=float)
    else:
        pre = np.concatenate([[open_[0]], close[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pre > 0, open_ / pre - 1.0, 0.0)


def check_microstructure(
    df: pd.DataFrame,
    symbol: str,
    *,
    real_limit_rate: float | None = None,
    tol: float = LIMIT_TOLERANCE,
) -> MicrostructureReport:
    """校验单只合成 K 线是否带 A 股微结构（``Covers AE4``）.

    Args:
        df: 合成 OHLCV（至少 open/close；有 pre_close 更准）.
        symbol: 用于判板块涨跌停比例.
        real_limit_rate: 真实触板率；给定则做偏离告警（不影响 valid）.
        tol: 触板判定容差.

    Returns:
        :class:`MicrostructureReport`；``valid`` = 截断被尊重 + 至少一次触板 + 有跳空.
    """
    n = len(df)
    if n < 2:
        return MicrostructureReport(
            symbol=symbol, valid=False, has_limit_hit=False, over_limit=False,
            has_gap=False, limit_hit_rate=0.0, gap_rate=0.0, n_bars=n,
            reasons=["bar 数不足"],
        )

    limit = limit_pct_for(symbol)
    pct = np.abs(_pct_chg(df))
    gap = np.abs(_gap(df))

    over_limit = bool((pct > limit + tol).any())
    limit_hits = (pct >= limit - tol) & (pct <= limit + tol)
    has_limit_hit = bool(limit_hits.any())
    gaps = gap > _GAP_EPS
    has_gap = bool(gaps.any())

    limit_hit_rate = float(limit_hits.mean())
    gap_rate = float(gaps.mean())

    reasons: list[str] = []
    if over_limit:
        reasons.append(f"价格越过涨跌停 ±{limit:.0%}（截断未生效）")
    if not has_limit_hit:
        reasons.append("无涨跌停触板（缺涨跌停截断特征）")
    if not has_gap:
        reasons.append("无 T+1 跳空（open 恒等于前收）")

    warnings: list[str] = []
    if real_limit_rate is not None:
        dev = abs(limit_hit_rate - real_limit_rate)
        if dev > _LIMIT_RATE_DEVIATION_WARN:
            warnings.append(
                f"触板率 {limit_hit_rate:.1%} 偏离真实 {real_limit_rate:.1%}（差 {dev:.1%}）"
            )

    valid = (not over_limit) and has_limit_hit and has_gap
    return MicrostructureReport(
        symbol=symbol, valid=valid, has_limit_hit=has_limit_hit, over_limit=over_limit,
        has_gap=has_gap, limit_hit_rate=limit_hit_rate, gap_rate=gap_rate,
        n_bars=n, reasons=reasons, warnings=warnings,
    )


@dataclass
class BatchValidation:
    """一批合成序列的校验汇总."""

    valid: bool
    n_total: int
    n_valid: int
    reports: list[MicrostructureReport] = field(default_factory=list)


def validate_batch(
    frames: list[tuple[str, pd.DataFrame]],
    *,
    real_limit_rate: float | None = None,
    min_valid_frac: float = 0.5,
) -> BatchValidation:
    """批量校验；有效占比低于 ``min_valid_frac`` → 整批判无效（不进 U7）.

    Args:
        frames: ``[(symbol, 合成 OHLCV), ...]``.
        real_limit_rate: 真实触板率（偏离告警）.
        min_valid_frac: 整批通过所需的最低有效占比.

    Returns:
        :class:`BatchValidation`.
    """
    reports = [check_microstructure(df, sym, real_limit_rate=real_limit_rate) for sym, df in frames]
    n_total = len(reports)
    n_valid = sum(r.valid for r in reports)
    batch_valid = n_total > 0 and (n_valid / n_total) >= min_valid_frac
    logger.info("批校验：%d/%d 有效 → batch_valid=%s", n_valid, n_total, batch_valid)
    return BatchValidation(valid=batch_valid, n_total=n_total, n_valid=n_valid, reports=reports)
