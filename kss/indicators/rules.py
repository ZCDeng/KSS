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
    FAMILY_SR_LEVEL,
    FAMILY_VWAP,
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
    elif spec.family == FAMILY_SR_LEVEL:
        longest = int(spec.params["pivot_window"]) * 4
    elif spec.family == FAMILY_VWAP:
        longest = int(spec.params.get("max_hold_bars", 4))
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

    if family == FAMILY_SR_LEVEL:
        variant = params.get("rule_variant", "bounce")
        support, resistance = feat["sr_support"], feat["sr_resistance"]
        close, close_prev = feat["close"], feat["close"].shift(1)

        if variant == "bounce":
            support_prev, low_prev = support.shift(1), feat["low"].shift(1)
            entry = (low_prev <= support_prev * 1.01) & (close > close_prev) & support_prev.notna()
            exit_ = ((close < support * 0.99) & support.notna()) | (
                (close >= resistance) & resistance.notna()
            )
            return entry.fillna(False), exit_.fillna(False)

        if variant == "breakout":
            resistance_prev = resistance.shift(1)
            entry = (
                (close_prev <= resistance_prev) & (close > resistance_prev) & resistance_prev.notna()
            ).fillna(False)
            # 突破后 sr_resistance 会切到下一档更高阻力，追踪止损须记住本次突破的原阻力位——
            # ffill 只回看已发生的突破，无前瞻。
            broken_level = pd.Series(np.where(entry, resistance_prev, np.nan), index=feat.index).ffill()
            exit_ = (close < broken_level * 0.99) & broken_level.notna()
            return entry, exit_.fillna(False)

        raise ValueError(f"未知 sr_level 规则变体: {variant!r}")

    if family == FAMILY_VWAP:
        variant = params.get("rule_variant", "dev_reclaim")
        entry_th = -float(params["entry_dev_bps"]) / 10000.0
        stop_th = -float(params["stop_dev_bps"]) / 10000.0
        close, close_prev = feat["close"], feat["close"].shift(1)
        vwap, dev, dev_prev = feat["vwap"], feat["vwap_dev"], feat["vwap_dev"].shift(1)
        exit_ = ((close >= vwap) | (dev <= stop_th)).fillna(False)
        if variant == "dev_reclaim":
            # 左侧：上一根已低于 VWAP 达阈值，本根收阳但仍未站上 VWAP。
            entry = (
                (dev_prev <= entry_th)
                & (close > close_prev)
                & (dev <= 0)
                & (feat["bars_in_session"] >= 2)
            )
            return entry.fillna(False), exit_
        if variant == "close_dip":
            # 左侧：会话收盘仍低于 VWAP 达阈值（T+1 次日开盘买）。
            entry = feat["is_session_close"] & (dev <= entry_th) & vwap.notna()
            return entry.fillna(False), exit_
        raise ValueError(f"未知 vwap 规则变体: {variant!r}")

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
    hold_len = 0
    entry_date = None
    max_hold = spec.params.get("max_hold_bars")
    t1_exit = bool(spec.params.get("t1_exit", False))
    dates = pd.to_datetime(feat["trade_date"]).dt.strftime("%Y-%m-%d").values
    for i in range(len(feat)):
        if i < warm:
            holding = 0.0
            hold_len = 0
            entry_date = None
            pos[i] = 0.0
            continue
        if holding > 0:
            hold_len += 1
            timed_out = max_hold is not None and hold_len >= int(max_hold)
            same_session = t1_exit and entry_date is not None and dates[i] == entry_date
            if (bool(exit_.iloc[i]) or timed_out) and not same_session:
                holding = 0.0
                hold_len = 0
                entry_date = None
        else:
            if bool(entry.iloc[i]):
                holding = 1.0
                hold_len = 0
                entry_date = dates[i]
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


# ---------------------------------------------------------------------------
# 全样本重放：交易明细 + 当前动作（与 mi_signal.replay/extract_trades/current_action 同构）。
# ---------------------------------------------------------------------------


def extract_trades(df: pd.DataFrame, pos: pd.Series) -> list[dict[str, Any]]:
    """从仓位提取完整买卖回合（exec = 信号次 bar 开盘）；只用 open/close/trade_date 通用列。"""
    trades: list[dict[str, Any]] = []
    entry_i: int | None = None
    opens = df["open"].values
    dates = df["trade_date"].values
    closes = df["close"].values
    p = pos.values
    for i in range(1, len(p)):
        if p[i - 1] <= 0 and p[i] > 0:
            entry_i = i
        elif p[i - 1] > 0 and p[i] <= 0 and entry_i is not None:
            buy_i = min(entry_i + 1, len(df) - 1)
            sell_i = min(i + 1, len(df) - 1)
            buy_px = (
                float(opens[buy_i])
                if np.isfinite(opens[buy_i])
                else float(closes[entry_i])
            )
            sell_px = (
                float(opens[sell_i]) if np.isfinite(opens[sell_i]) else float(closes[i])
            )
            ret = sell_px / buy_px - 1.0 if buy_px > 0 else np.nan
            trades.append(
                {
                    "signal_buy_date": str(pd.Timestamp(dates[entry_i]).date()),
                    "exec_buy_date": str(pd.Timestamp(dates[buy_i]).date()),
                    "signal_sell_date": str(pd.Timestamp(dates[i]).date()),
                    "exec_sell_date": str(pd.Timestamp(dates[sell_i]).date()),
                    "buy_open": round(buy_px, 3),
                    "sell_open": round(sell_px, 3),
                    "trade_return": None if not np.isfinite(ret) else round(float(ret), 4),
                    "hold_days": int(i - entry_i),
                }
            )
            entry_i = None
    return trades


def signal_strength(feat: pd.DataFrame, family: str) -> pd.Series:
    """族内信号强度代理，约 [-1,1]，供动作展示用；不参与 U2 GO/NO-GO 裁决。"""
    if family == FAMILY_MA_CROSS:
        spread = feat["ma_fast"] - feat["ma_slow"]
        z = (spread - spread.rolling(60, min_periods=20).mean()) / (
            spread.rolling(60, min_periods=20).std() + 1e-8
        )
        return np.tanh(z / 2)
    if family == FAMILY_RSI_THRESHOLD:
        return np.tanh((feat["rsi"] - 50.0) / 25.0)
    if family == FAMILY_BOLL_ATR:
        width = (feat["boll_upper"] - feat["boll_lower"]).replace(0, np.nan)
        pos = (feat["close"] - feat["boll_mid"]) / width
        return np.tanh(pos * 2)
    if family == FAMILY_SR_LEVEL:
        mid = (feat["sr_support"] + feat["sr_resistance"]) / 2.0
        width = (feat["sr_resistance"] - feat["sr_support"]).replace(0, np.nan)
        pos = (feat["close"] - mid) / width
        return np.tanh(pos.fillna(0) * 2)
    if family == FAMILY_VWAP:
        return np.tanh(feat["vwap_dev"].fillna(0) * 20)
    raise ValueError(f"未知基元族: {family!r}")  # pragma: no cover


_ACTION_TEMPLATES = {
    FAMILY_MA_CROSS: ("金叉入场、死叉离场", "均线交叉"),
    FAMILY_RSI_THRESHOLD: ("RSI 上穿入场阈值入场、下穿离场阈值离场", "RSI 阈值"),
    FAMILY_BOLL_ATR: ("突破布林上轨入场、ATR 追踪止损或回归中轨离场", "布林·ATR"),
}


def rule_sentence(spec: IndicatorSpec) -> str:
    """一句话规则描述（可解释性维度消费）。"""
    if spec.family == FAMILY_SR_LEVEL:
        variant = spec.params.get("rule_variant", "bounce")
        desc = (
            "回踩支撑确认反弹入场、跌破支撑或触及阻力离场"
            if variant == "bounce"
            else "收盘突破阻力入场、回落破位（追踪止损）离场"
        )
        return f"支撑阻力·{variant}（{spec.params}）：{desc}"
    if spec.family == FAMILY_VWAP:
        variant = spec.params.get("rule_variant", "dev_reclaim")
        desc = (
            "价低于会话 VWAP 达阈值后收阳仍未站上（左侧）入场，站上 VWAP 或止损离场"
            if variant == "dev_reclaim"
            else "会话收盘低于 VWAP 达阈值入场（T+1 次日开），站上 VWAP 或止损离场"
        )
        return f"会话VWAP·{variant}（{spec.params}）：{desc}"
    desc, label = _ACTION_TEMPLATES[spec.family]
    return f"{label}（{spec.params}）：{desc}"


def current_action(feat: pd.DataFrame, pos: pd.Series, spec: IndicatorSpec) -> dict[str, Any]:
    """最新有效 bar 的动作与信号强度代理。"""
    i = len(feat) - 1
    while i > 0 and pd.isna(feat["close"].iloc[i]):
        i -= 1
    entry, exit_ = _entry_exit_signals(feat, spec)
    holding = float(pos.iloc[i]) > 0
    enter = bool(entry.iloc[i]) if pd.notna(entry.iloc[i]) else False
    leave = bool(exit_.iloc[i]) if pd.notna(exit_.iloc[i]) else False

    if holding and leave:
        action, reason = "SELL", f"触发退出（{spec.family}）"
    elif (not holding) and enter:
        action, reason = "BUY", f"触发入场（{spec.family}）"
    elif holding:
        action, reason = "HOLD_LONG", "持仓中，未触发退出"
    else:
        action, reason = "STAY_FLAT", "空仓，未触发入场"

    strength = signal_strength(feat, spec.family)
    score = float(strength.iloc[i]) if pd.notna(strength.iloc[i]) else 0.0
    close = float(feat["close"].iloc[i]) if pd.notna(feat["close"].iloc[i]) else 1.0

    return {
        "asof": str(pd.Timestamp(feat["trade_date"].iloc[i]).date()),
        "close": round(close, 3),
        "position": "LONG" if holding else "FLAT",
        "action": action,
        "reason": reason,
        "pred_score": round(score, 3),
        "pred_bias": "bullish" if score > 0.15 else ("bearish" if score < -0.15 else "neutral"),
        "exec_note": "BUY/SELL 按纪律于下一交易日开盘执行",
    }


def replay(df: pd.DataFrame, spec: IndicatorSpec) -> dict[str, Any]:
    """全样本重放：仓位、trades、当前动作（与 mi_signal.replay 同构）。"""
    feat = build_features(df, spec.family, spec.params)
    pos = positions_from_spec(feat, spec)
    feat["position"] = pos.values
    trades = extract_trades(feat, pos)
    action = current_action(feat, pos, spec)
    preview_n = 10
    return {
        "family": spec.family,
        "params": dict(spec.params),
        "feat": feat,
        "positions": pos,
        "trades": trades,
        "trades_preview": trades[-preview_n:],
        "action": action,
        "rule_sentence": rule_sentence(spec),
    }
