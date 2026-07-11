"""通用规则引擎：entry/exit 布尔信号合成 + 事件驱动仓位状态机.

执行纪律与 ``kss.strategies.mi_signal`` 一致：t 收盘信号 → t+1 开盘执行；
仓位状态机（entry 开仓 / exit 平仓）与 ``mi_signal.positions_from_rules`` 同构，
只是信号来源换成了 ``kss.indicators.primitives`` 的三族基元。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from kss.indicators.primitives import (
    FAMILIES,
    FAMILY_BOLL_ATR,
    FAMILY_MA_CROSS,
    FAMILY_RSI_THRESHOLD,
    build_features,
)


@dataclass(frozen=True)
class IndicatorSpec:
    """一个可回测候选：基元族 + 参数（不可变，可哈希，安全用作注册表键的一部分）."""

    family: str
    params: dict[str, Any]

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"未知基元族: {self.family!r}；允许 {FAMILIES}")


def warm_period(spec: IndicatorSpec) -> int:
    """warm-up bar 数：覆盖族内最长回看窗口 + 缓冲，对齐 MI 的 ``max(n+5, 40)`` 惯例."""
    if spec.family == FAMILY_MA_CROSS:
        longest = int(spec.params["slow"])
    elif spec.family == FAMILY_RSI_THRESHOLD:
        longest = int(spec.params["period"])
    elif spec.family == FAMILY_BOLL_ATR:
        longest = max(int(spec.params["period"]), int(spec.params["atr_period"]))
    else:  # pragma: no cover - IndicatorSpec.__post_init__ 已拦截未知族
        raise ValueError(f"未知基元族: {spec.family!r}")
    return max(longest + 5, 40)


def _entry_exit_signals(
    feat: pd.DataFrame, spec: IndicatorSpec
) -> tuple[pd.Series, pd.Series]:
    """按族从特征列合成 entry/exit 布尔信号；只用 shift(1)/当前值，不前瞻。"""
    family, params = spec.family, spec.params

    if family == FAMILY_MA_CROSS:
        fast, slow = feat["ma_fast"], feat["ma_slow"]
        fast_prev, slow_prev = fast.shift(1), slow.shift(1)
        entry = (fast_prev <= slow_prev) & (fast > slow)  # 金叉
        exit_ = (fast_prev >= slow_prev) & (fast < slow)  # 死叉
        return entry, exit_

    if family == FAMILY_RSI_THRESHOLD:
        rsi, rsi_prev = feat["rsi"], feat["rsi"].shift(1)
        entry_level = float(params["entry_level"])
        exit_level = float(params["exit_level"])
        entry = (rsi_prev <= entry_level) & (rsi > entry_level)  # 上穿入场阈值
        exit_ = (rsi_prev >= exit_level) & (rsi < exit_level)  # 下穿离场阈值
        return entry, exit_

    if family == FAMILY_BOLL_ATR:
        close, close_prev = feat["close"], feat["close"].shift(1)
        upper_prev = feat["boll_upper"].shift(1)
        entry = (close_prev <= upper_prev) & (close > feat["boll_upper"])  # 突破上轨
        atr_mult = float(params["atr_mult"])
        stop_level = feat["rolling_high"] - atr_mult * feat["atr"]  # ATR 追踪止损
        exit_ = (close < stop_level) | (close < feat["boll_mid"])  # 止损或回归中轨
        return entry, exit_

    raise ValueError(f"未知基元族: {family!r}")  # pragma: no cover


def positions_from_spec(
    feat: pd.DataFrame, spec: IndicatorSpec, warm: int | None = None
) -> pd.Series:
    """事件驱动仓位：entry 开仓，exit 平仓（与 mi_signal.positions_from_rules 同式状态机）."""
    warm = warm_period(spec) if warm is None else warm
    entry, exit_ = _entry_exit_signals(feat, spec)
    entry = entry.fillna(False)
    exit_ = exit_.fillna(False)
    pos = np.zeros(len(feat), dtype=float)
    holding = 0.0
    for i in range(len(feat)):
        if i < warm:
            holding = 0.0
            pos[i] = 0.0
            continue
        if holding > 0:
            if bool(exit_.iloc[i]):
                holding = 0.0
        else:
            if bool(entry.iloc[i]):
                holding = 1.0
        pos[i] = holding
    return pd.Series(pos, index=feat.index)


def compute_positions(df: pd.DataFrame, spec: IndicatorSpec) -> pd.DataFrame:
    """一步到位：build_features + positions_from_spec，返回附加 ``position`` 列的 feat.

    样本过短时特征列自然全 NaN、仓位全 0（warm-up 覆盖全样本），不抛异常——
    与 ``mi_pack.run_symbol_pack`` 的"无行情或样本过短"早退语义一致，
    显式的 ``status="skipped"`` 判定留给 U2 的 walk-forward 引擎（其消费方）。
    """
    feat = build_features(df, spec.family, spec.params)
    feat["position"] = positions_from_spec(feat, spec).values
    return feat
