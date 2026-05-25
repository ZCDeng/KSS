"""Macro 状态查询层 —— 从 storage parquet 读 regime / valuation / rotation.

这些函数原先散落在 ``scan_combo_signals.py`` 的 ``_lookup_*`` helpers，
plan 010 #19 提到 kss.macro 让其他下游消费者（dashboard、agent、daily report）
都能复用，不必复制粘贴.

返回值用 typed dataclass 而非 dict，给静态分析 + IDE 自动补全更好支持.
原 dict-based caller 仍可通过 ``.as_dict()`` 平滑过渡.

每次调用都从 parquet 读（成本约几 ms 4MB 文件），无缓存以保证刷新立即可见.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from kss.config.paths import REGIME_PARQUET, VALUATION_PARQUET

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeInfo:
    """单日 regime 查询结果."""

    stage: str
    confidence: float
    as_of: str
    stale: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValuationInfo:
    """单日 valuation n 查询结果."""

    pe: float | None
    n_years: float | None
    rule: str
    percentile_5y: float | None
    as_of: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RotationHint:
    """部门轮换提示（按 regime stage 查）."""

    preferred: list[str]
    avoid: list[str]
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageTransition:
    """跨日阶段切换事件 (plan 011).

    捕捉 ``regime_daily.parquet`` 末两行 ``stage`` 列发生变化的事件，
    供告警系统按 (date, from, to) 三元组去重推送.
    """

    from_stage: str
    to_stage: str
    confidence_delta: float
    as_of_date: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------- #
# Regime
# ---------------------------------------------------------------------------- #


def lookup_regime(
    trade_date: str,
    max_stale_days: int = 3,
    path: Path | None = None,
) -> RegimeInfo | None:
    """从 ``regime_daily.parquet`` 取 ``trade_date`` 的 stage.

    当日无记录时取最近一日兜底；``stale`` 标志表示距 ``trade_date``
    超 ``max_stale_days`` 个日历日，调用方应据此打印警告.

    Args:
        trade_date: 目标日 ``YYYYMMDD``.
        max_stale_days: 视为 stale 的日历日阈值（默认 3）.
        path: 自定义 parquet 路径（测试用）.

    Returns:
        :class:`RegimeInfo` 或 ``None``（缺文件 / 缺记录 / stage='Unknown'）.
    """
    p = path or REGIME_PARQUET
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception as exc:    # noqa: BLE001
        logger.warning("lookup_regime: read parquet 失败 %s", exc)
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    row = df[df["trade_date"] == str(trade_date)]
    if row.empty:
        prev = df[df["trade_date"] <= str(trade_date)]
        if prev.empty:
            return None
        row = prev.tail(1)
    r = row.iloc[-1]
    stage = str(r.get("stage", "Unknown"))
    if stage == "Unknown":
        return None
    as_of = str(r.get("trade_date", ""))
    return RegimeInfo(
        stage=stage,
        confidence=float(r.get("confidence", 0.0)),
        as_of=as_of,
        stale=_is_stale(as_of, str(trade_date), max_stale_days),
    )


# ---------------------------------------------------------------------------- #
# Valuation
# ---------------------------------------------------------------------------- #


def lookup_valuation(
    trade_date: str,
    percentile_window: int = 1260,
    path: Path | None = None,
) -> ValuationInfo | None:
    """从 ``valuation_n_daily.parquet`` 取 ``trade_date`` 的 n + 历史分位 + 阈值规则.

    Args:
        trade_date: 目标日 ``YYYYMMDD``.
        percentile_window: 分位回看交易日数（默认 1260 ≈ 5 年）.
        path: 自定义 parquet 路径（测试用）.

    Returns:
        :class:`ValuationInfo` 或 ``None``（缺文件 / 缺记录 / n 为 NaN）.
    """
    p = path or VALUATION_PARQUET
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception as exc:    # noqa: BLE001
        logger.warning("lookup_valuation: read parquet 失败 %s", exc)
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    row = df[df["trade_date"] == str(trade_date)]
    if row.empty:
        prev = df[df["trade_date"] <= str(trade_date)]
        if prev.empty:
            return None
        row = prev.tail(1)
    r = row.iloc[-1]
    n_val = r.get("n_years")
    if n_val is None or n_val != n_val:    # NaN
        return None
    n_val = float(n_val)

    # 用 valuation 模块算 stage_rule + percentile（避免 import 循环）
    from kss.macro.valuation import compute_n_percentile, stage_rule_for_n

    history = df[df["trade_date"] < str(trade_date)]["n_years"]
    pct = compute_n_percentile(history, n_val, window=percentile_window)
    return ValuationInfo(
        pe=float(r.get("pe")) if pd.notna(r.get("pe")) else None,
        n_years=n_val,
        rule=stage_rule_for_n(n_val),
        percentile_5y=pct,
        as_of=str(r["trade_date"]),
    )


# ---------------------------------------------------------------------------- #
# Rotation
# ---------------------------------------------------------------------------- #


def lookup_rotation(stage: str | None) -> RotationHint | None:
    """按 regime stage 取部门轮换提示.

    Args:
        stage: 阶段标签 ``I/II/III/IV``；``None`` 时返回 ``None``.

    Returns:
        :class:`RotationHint` 或 ``None``（缺 yaml 或 stage 不存在）.
    """
    if not stage:
        return None
    try:
        from kss.macro.rotation import (
            get_avoid_industries,
            get_preferred_industries,
            get_rationale,
        )
    except ImportError:
        return None
    prefs = get_preferred_industries(stage)
    avoid = get_avoid_industries(stage)
    if not prefs and not avoid:
        return None
    return RotationHint(
        preferred=prefs,
        avoid=avoid,
        rationale=get_rationale(stage),
    )


# ---------------------------------------------------------------------------- #
# Stage transition (plan 011)
# ---------------------------------------------------------------------------- #


def detect_stage_transition(
    parquet_path: Path | None = None,
) -> StageTransition | None:
    """对比 ``regime_daily.parquet`` 末两行的 ``stage`` 列，捕捉跨日切换.

    用于 ``scripts/update_macro_daily.py`` 末段触发 Telegram 阶段切换告警.
    使用 ``stage`` 列（已经过 hysteresis 平滑，对应原计划文档的
    "stage_smoothed"）；当列名不存在或 panel 不足 2 行时返回 ``None``.

    Args:
        parquet_path: 自定义 parquet 路径（测试用）；默认 ``REGIME_PARQUET``.

    Returns:
        :class:`StageTransition` 若末两行 stage 不同且**两端均非 ``Unknown``**.
        其他情况返回 ``None``（无切换 / 数据不足 / 含 Unknown / 读取失败）.
    """
    p = parquet_path or REGIME_PARQUET
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception as exc:    # noqa: BLE001
        logger.warning("detect_stage_transition: read parquet 失败 %s", exc)
        return None
    if df is None or df.empty or "stage" not in df.columns:
        return None
    if len(df) < 2:
        return None

    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date").reset_index(drop=True)
    prev_row = df.iloc[-2]
    curr_row = df.iloc[-1]
    from_stage = str(prev_row.get("stage", "Unknown"))
    to_stage = str(curr_row.get("stage", "Unknown"))
    if from_stage == to_stage:
        return None
    if from_stage == "Unknown" or to_stage == "Unknown":
        return None
    try:
        conf_prev = float(prev_row.get("confidence", 0.0) or 0.0)
        conf_curr = float(curr_row.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf_prev = conf_curr = 0.0
    return StageTransition(
        from_stage=from_stage,
        to_stage=to_stage,
        confidence_delta=conf_curr - conf_prev,
        as_of_date=str(curr_row["trade_date"]),
    )


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


def _is_stale(as_of: str, target: str, max_days: int) -> bool:
    """as_of 是否距 target 超 ``max_days`` 个日历日."""
    try:
        d1 = datetime.strptime(as_of, "%Y%m%d")
        d2 = datetime.strptime(target, "%Y%m%d")
        return abs((d2 - d1).days) > max_days
    except Exception:    # noqa: BLE001
        return False


__all__ = [
    "RegimeInfo",
    "RotationHint",
    "StageTransition",
    "ValuationInfo",
    "detect_stage_transition",
    "lookup_regime",
    "lookup_rotation",
    "lookup_valuation",
]
