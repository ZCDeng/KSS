"""MI 规则引擎：特征、仓位、买卖点、当前动作与 pred_score.

形态键（entry/exit/filter）钉死；可选 thr 字典仅 z 族消费。
执行纪律：t 收盘信号，exec 日展示为下一 bar 开盘。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from kss.features.technical import TechnicalFactors

# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------


def next_day_return(df: pd.DataFrame) -> pd.Series:
    """t 信号 → open[t+2]/open[t+1]-1."""
    o = df["open"]
    return o.shift(-2) / o.shift(-1) - 1.0


def true_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14
) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = true_atr(high, low, close, n)
    plus_di = (
        100
        * pd.Series(plus_dm, index=close.index).ewm(alpha=1 / n, adjust=False).mean()
        / (atr + 1e-12)
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=close.index).ewm(alpha=1 / n, adjust=False).mean()
        / (atr + 1e-12)
    )
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12) * 100
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def build_features(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """计算 MI/A/mi_z 与穿越辅助列."""
    if n <= 0:
        raise ValueError(f"N 必须为正，收到 {n}")
    out = df.copy()
    mi = TechnicalFactors.mi(out["close"], periods=(n,))
    out["a"] = mi[f"mi_a_{n}"]
    out["mi"] = mi[f"mi_{n}"]
    out["mi_z"] = (
        (out["mi"] - out["mi"].rolling(60, min_periods=30).mean())
        / (out["mi"].rolling(60, min_periods=30).std() + 1e-8)
    )
    out["a_mi"] = out["a"] - out["mi"]
    out["adx"] = adx(out["high"], out["low"], out["close"], 14)
    out["atr"] = true_atr(out["high"], out["low"], out["close"], 14)
    out["atr_pct"] = out["atr"] / out["close"]
    out["ret"] = next_day_return(out)
    out["mi_prev"] = out["mi"].shift(1)
    out["a_prev"] = out["a"].shift(1)
    out["mi_up_0"] = (out["mi_prev"] <= 0) & (out["mi"] > 0)
    out["mi_dn_0"] = (out["mi_prev"] >= 0) & (out["mi"] < 0)
    out["a_up_mi"] = (out["a_prev"] <= out["mi_prev"]) & (out["a"] > out["mi"])
    out["a_dn_mi"] = (out["a_prev"] >= out["mi_prev"]) & (out["a"] < out["mi"])
    return out


# ---------------------------------------------------------------------------
# 规则（形态键 + thr）
# ---------------------------------------------------------------------------

Thr = dict[str, float] | None


def _entry_signal(df: pd.DataFrame, entry: str, thr: Thr) -> pd.Series:
    thr = thr or {}
    if entry == "mi_cross_up_0":
        return df["mi_up_0"]
    if entry == "a_cross_up_mi":
        return df["a_up_mi"]
    if entry == "dual_cross":
        return df["mi_up_0"] | df["a_up_mi"]
    if entry == "mi_pos_and_a_gt_mi":
        return (df["mi"] > 0) & (df["a"] > df["mi"])
    if entry in ("mi_z_gt", "mi_z_gt_0.5", "mi_z_gt_1.0"):
        # 参数化：mi_z_gt 用 thr['entry_z']；兼容旧键名
        if entry == "mi_z_gt_0.5":
            z = thr.get("entry_z", 0.5)
        elif entry == "mi_z_gt_1.0":
            z = thr.get("entry_z", 1.0)
        else:
            z = float(thr.get("entry_z", 0.5))
        return df["mi_z"] > z
    raise ValueError(f"未知 entry 形态: {entry}")


def _exit_signal(df: pd.DataFrame, exit_: str, thr: Thr) -> pd.Series:
    thr = thr or {}
    if exit_ == "mi_cross_dn_0":
        return df["mi_dn_0"]
    if exit_ == "a_cross_dn_mi":
        return df["a_dn_mi"]
    if exit_ == "dual_exit":
        return df["mi_dn_0"] | df["a_dn_mi"]
    if exit_ == "mi_neg_or_a_lt_mi":
        return (df["mi"] < 0) | (df["a"] < df["mi"])
    if exit_ in ("mi_z_lt", "mi_z_lt_0", "mi_z_lt_-0.5"):
        if exit_ == "mi_z_lt_0":
            z = thr.get("exit_z", 0.0)
        elif exit_ == "mi_z_lt_-0.5":
            z = thr.get("exit_z", -0.5)
        else:
            z = float(thr.get("exit_z", 0.0))
        return df["mi_z"] < z
    raise ValueError(f"未知 exit 形态: {exit_}")


def _filter_signal(df: pd.DataFrame, filt: str) -> pd.Series:
    if filt == "none":
        return pd.Series(True, index=df.index)
    if filt == "adx_gt_20":
        return df["adx"] > 20
    if filt == "adx_gt_25":
        return df["adx"] > 25
    if filt == "atr_mid":
        return (
            df["atr_pct"] > df["atr_pct"].rolling(60, min_periods=30).quantile(0.2)
        ) & (
            df["atr_pct"] < df["atr_pct"].rolling(60, min_periods=30).quantile(0.9)
        )
    raise ValueError(f"未知 filter 形态: {filt}")


def positions_from_rules(
    df: pd.DataFrame,
    entry: str,
    exit_: str,
    filt: str,
    warm: int,
    thr: Thr = None,
) -> pd.Series:
    """事件驱动仓位：entry 开仓，exit 平仓."""
    enter = _entry_signal(df, entry, thr).fillna(False) & _filter_signal(df, filt).fillna(
        False
    )
    leave = _exit_signal(df, exit_, thr).fillna(False)
    pos = np.zeros(len(df), dtype=float)
    holding = 0.0
    for i in range(len(df)):
        if i < warm:
            holding = 0.0
            pos[i] = 0.0
            continue
        if holding > 0:
            if bool(leave.iloc[i]):
                holding = 0.0
        else:
            if bool(enter.iloc[i]):
                holding = 1.0
        pos[i] = holding
    return pd.Series(pos, index=df.index)


def extract_trades(df: pd.DataFrame, pos: pd.Series) -> list[dict[str, Any]]:
    """从仓位提取完整买卖回合（exec = 信号次 bar 开盘）."""
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
                    "trade_return": None
                    if not np.isfinite(ret)
                    else round(float(ret), 4),
                    "hold_days": int(i - entry_i),
                }
            )
            entry_i = None
    return trades


def _action_reason(
    holding: bool,
    enter: bool,
    leave: bool,
    entry: str,
    exit_: str,
    filt: str,
    thr: Thr,
) -> tuple[str, str]:
    if holding and leave:
        return "SELL", f"触发退出 {exit_}"
    if (not holding) and enter:
        thr_s = f" thr={thr}" if thr else ""
        return "BUY", f"触发入场 {entry}{thr_s} + 过滤 {filt}"
    if holding:
        return "HOLD_LONG", "持仓中，未触发退出"
    return "STAY_FLAT", "空仓，未触发入场"


def current_action(
    feat: pd.DataFrame,
    pos: pd.Series,
    entry: str,
    exit_: str,
    filt: str,
    thr: Thr = None,
) -> dict[str, Any]:
    """最新有效 bar 的动作与 pred_score."""
    i = len(feat) - 1
    while i > 0 and (pd.isna(feat["mi"].iloc[i]) or pd.isna(feat["close"].iloc[i])):
        i -= 1
    row = feat.iloc[i]
    holding = float(pos.iloc[i]) > 0
    enter = bool(_entry_signal(feat, entry, thr).iloc[i]) and bool(
        _filter_signal(feat, filt).iloc[i]
    )
    leave = bool(_exit_signal(feat, exit_, thr).iloc[i])
    action, reason = _action_reason(holding, enter, leave, entry, exit_, filt, thr)

    mi_z = float(row["mi_z"]) if np.isfinite(row["mi_z"]) else 0.0
    a_mi = float(row["a_mi"]) if np.isfinite(row["a_mi"]) else 0.0
    close = float(row["close"]) if np.isfinite(row["close"]) else 1.0
    score = 0.6 * np.tanh(mi_z / 2) + 0.4 * np.tanh(
        a_mi / (abs(close) * 0.02 + 1e-6)
    )
    adx_v = float(row["adx"]) if np.isfinite(row["adx"]) else 0.0
    if adx_v < 20:
        score *= 0.5

    return {
        "asof": str(pd.Timestamp(row["trade_date"]).date()),
        "close": round(close, 3),
        "mi": None if not np.isfinite(row["mi"]) else round(float(row["mi"]), 4),
        "a": None if not np.isfinite(row["a"]) else round(float(row["a"]), 4),
        "mi_z": round(mi_z, 3),
        "adx": None if not np.isfinite(row["adx"]) else round(adx_v, 2),
        "position": "LONG" if holding else "FLAT",
        "action": action,
        "reason": reason,
        "pred_score": round(float(score), 3),
        "pred_bias": "bullish"
        if score > 0.15
        else ("bearish" if score < -0.15 else "neutral"),
        "exec_note": "BUY/SELL 按纪律于下一交易日开盘执行",
    }


@dataclass(frozen=True)
class RuleSpec:
    """钉死形态 + 可选 thr + N."""

    entry: str
    exit: str
    filt: str = "none"
    n: int = 12
    thr: Thr = None


def replay(
    df: pd.DataFrame,
    rule: RuleSpec,
) -> dict[str, Any]:
    """全样本重放：特征、仓位、trades、当前动作、MI 序列."""
    feat = build_features(df, rule.n)
    warm = max(rule.n + 5, 40)
    pos = positions_from_rules(
        feat, rule.entry, rule.exit, rule.filt, warm=warm, thr=rule.thr
    )
    trades = extract_trades(feat, pos)
    action = current_action(feat, pos, rule.entry, rule.exit, rule.filt, rule.thr)
    mi_series = [
        {
            "date": str(pd.Timestamp(d).date()),
            "mi": None if not np.isfinite(m) else round(float(m), 4),
            "a": None if not np.isfinite(a) else round(float(a), 4),
        }
        for d, m, a in zip(
            feat["trade_date"].values, feat["mi"].values, feat["a"].values, strict=False
        )
        if pd.notna(d)
    ]
    preview_n = 10
    return {
        "n": rule.n,
        "entry": rule.entry,
        "exit": rule.exit,
        "filter": rule.filt,
        "thr": rule.thr or {},
        "positions": pos,
        "feat": feat,
        "trades": trades,
        "trades_preview": trades[-preview_n:],
        "action": action,
        "mi_series": mi_series,
    }


def uses_z_thresholds(entry: str, exit_: str) -> bool:
    """是否消费 thr 字典."""
    z_entries = {"mi_z_gt", "mi_z_gt_0.5", "mi_z_gt_1.0"}
    z_exits = {"mi_z_lt", "mi_z_lt_0", "mi_z_lt_-0.5"}
    return entry in z_entries or exit_ in z_exits
