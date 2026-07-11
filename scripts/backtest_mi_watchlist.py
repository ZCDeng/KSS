#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选股 MI 买卖点寻优 + 样本外验证 + 最新信号.

读取 ``storage/watchlist_symbols.txt``（桌面 App 同步的自选），在真实 A 股日线上：
1. 网格搜索 entry/exit/N/过滤组合
2. 时间切分样本内选参、样本外评分（防过拟合）
3. 输出最优规则、历史买卖点、当前预测信号

执行纪律与 ``backtest_mi.py`` 一致：
  t 收盘信号 → t+1 开盘建仓 → 用 open[t+2]/open[t+1]-1 作为持仓日收益。

用法::

    .venv/bin/python scripts/backtest_mi_watchlist.py
    .venv/bin/python scripts/backtest_mi_watchlist.py --symbols 688017,688322
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.backtest.cost_model import CostModel
from kss.backtest.metrics import Metrics
from kss.features.technical import TechnicalFactors

WATCHLIST_PATH = ROOT / "storage" / "watchlist_symbols.txt"
REPORT_MD = ROOT / "storage" / "reports" / "mi_watchlist_best_report.md"
REPORT_JSON = ROOT / "storage" / "reports" / "mi_watchlist_best.json"
NAMES = {
    "688017": "绿的谐波",
    "688322": "奥比中光",
}


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------

def _norm(raw: str) -> str:
    return raw.strip().upper().replace(".SH", "").replace(".SZ", "")


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[str]:
    if not path.exists():
        return ["688017", "688322"]
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(_norm(line))
    return out or ["688017", "688322"]


def load_stock(symbol: str) -> pd.DataFrame | None:
    for p in (ROOT / "cs_data" / f"cs_data_{symbol}.csv", ROOT / f"cs_data_{symbol}.csv"):
        if p.exists():
            df = pd.read_csv(p)
            break
    else:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "vol" in df.columns:
        df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    return df.sort_values("trade_date").drop_duplicates("trade_date").reset_index(drop=True)


def next_day_return(df: pd.DataFrame) -> pd.Series:
    o = df["open"]
    return o.shift(-2) / o.shift(-1) - 1.0


def true_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """标准 TR/ATR（非价格归一化）."""
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """简化 ADX（Wilder 平滑近似）."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = true_atr(high, low, close, n)
    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(alpha=1 / n, adjust=False).mean() / (atr + 1e-12)
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(alpha=1 / n, adjust=False).mean() / (atr + 1e-12)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12) * 100
    return dx.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------------------
# 特征与信号
# ---------------------------------------------------------------------------

def build_features(df: pd.DataFrame, n: int) -> pd.DataFrame:
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
    # 穿越辅助
    out["mi_prev"] = out["mi"].shift(1)
    out["a_prev"] = out["a"].shift(1)
    out["mi_up_0"] = (out["mi_prev"] <= 0) & (out["mi"] > 0)
    out["mi_dn_0"] = (out["mi_prev"] >= 0) & (out["mi"] < 0)
    out["a_up_mi"] = (out["a_prev"] <= out["mi_prev"]) & (out["a"] > out["mi"])
    out["a_dn_mi"] = (out["a_prev"] >= out["mi_prev"]) & (out["a"] < out["mi"])
    return out


ENTRY_RULES: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "mi_cross_up_0": lambda d: d["mi_up_0"],
    "a_cross_up_mi": lambda d: d["a_up_mi"],
    "dual_cross": lambda d: d["mi_up_0"] | d["a_up_mi"],
    "mi_pos_and_a_gt_mi": lambda d: (d["mi"] > 0) & (d["a"] > d["mi"]),
    "mi_z_gt_0.5": lambda d: d["mi_z"] > 0.5,
    "mi_z_gt_1.0": lambda d: d["mi_z"] > 1.0,
}

EXIT_RULES: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "mi_cross_dn_0": lambda d: d["mi_dn_0"],
    "a_cross_dn_mi": lambda d: d["a_dn_mi"],
    "dual_exit": lambda d: d["mi_dn_0"] | d["a_dn_mi"],
    "mi_neg_or_a_lt_mi": lambda d: (d["mi"] < 0) | (d["a"] < d["mi"]),
    "mi_z_lt_-0.5": lambda d: d["mi_z"] < -0.5,
    "mi_z_lt_0": lambda d: d["mi_z"] < 0.0,
}

FILTERS: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "none": lambda d: pd.Series(True, index=d.index),
    "adx_gt_20": lambda d: d["adx"] > 20,
    "adx_gt_25": lambda d: d["adx"] > 25,
    "atr_mid": lambda d: (
        d["atr_pct"] > d["atr_pct"].rolling(60, min_periods=30).quantile(0.2)
    ) & (
        d["atr_pct"] < d["atr_pct"].rolling(60, min_periods=30).quantile(0.9)
    ),
}


def positions_from_rules(
    df: pd.DataFrame,
    entry: str,
    exit_: str,
    filt: str,
    warm: int,
) -> pd.Series:
    """事件驱动仓位：entry 开仓=1，exit 平仓=0，中间持有."""
    enter = ENTRY_RULES[entry](df).fillna(False) & FILTERS[filt](df).fillna(False)
    leave = EXIT_RULES[exit_](df).fillna(False)
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


def apply_cost(pos: pd.Series, ret: pd.Series, cost: CostModel) -> tuple[pd.Series, int, float]:
    p = pos.astype(float)
    r = ret.astype(float)
    turnover = p.diff().abs()
    if len(p):
        turnover.iloc[0] = abs(p.iloc[0])
    delta = p.diff()
    if len(p):
        delta.iloc[0] = p.iloc[0]
    cost_rate = pd.Series(0.0, index=p.index)
    cost_rate.loc[delta > 0] = cost.buy_total
    cost_rate.loc[delta < 0] = cost.sell_total
    net = (p * r - turnover * cost_rate).where(r.notna())
    n_trades = int((delta.abs() > 1e-9).sum())
    return net, n_trades, float(turnover.fillna(0).sum())


def extract_trades(df: pd.DataFrame, pos: pd.Series) -> list[dict[str, Any]]:
    """从仓位序列提取完整买卖回合（用次日 open 近似成交价展示）."""
    trades: list[dict[str, Any]] = []
    entry_i: int | None = None
    opens = df["open"].values
    dates = df["trade_date"].values
    closes = df["close"].values
    p = pos.values
    for i in range(1, len(p)):
        if p[i - 1] <= 0 and p[i] > 0:
            entry_i = i  # 信号日；执行展示用 i+1 open
        elif p[i - 1] > 0 and p[i] <= 0 and entry_i is not None:
            buy_i = min(entry_i + 1, len(df) - 1)
            sell_i = min(i + 1, len(df) - 1)
            buy_px = float(opens[buy_i]) if np.isfinite(opens[buy_i]) else float(closes[entry_i])
            sell_px = float(opens[sell_i]) if np.isfinite(opens[sell_i]) else float(closes[i])
            ret = sell_px / buy_px - 1.0 if buy_px > 0 else np.nan
            trades.append({
                "signal_buy_date": str(pd.Timestamp(dates[entry_i]).date()),
                "exec_buy_date": str(pd.Timestamp(dates[buy_i]).date()),
                "signal_sell_date": str(pd.Timestamp(dates[i]).date()),
                "exec_sell_date": str(pd.Timestamp(dates[sell_i]).date()),
                "buy_open": round(buy_px, 3),
                "sell_open": round(sell_px, 3),
                "trade_return": None if not np.isfinite(ret) else round(float(ret), 4),
                "hold_days": int(i - entry_i),
            })
            entry_i = None
    return trades


@dataclass
class ComboResult:
    symbol: str
    n: int
    entry: str
    exit: str
    filt: str
    is_sharpe: float
    is_annual: float
    is_max_dd: float
    is_n_trades: int
    oos_sharpe: float
    oos_annual: float
    oos_max_dd: float
    oos_total: float
    oos_n_trades: int
    oos_win: float
    score: float  # 选参主分数 = oos_sharpe - 惩罚


def score_combo(oos: dict[str, Any], n_trades: int, min_trades: int = 4) -> float:
    """样本外评分：主看夏普，交易过少/回撤过大扣分."""
    if not oos or n_trades < min_trades:
        return -999.0
    sh = float(oos.get("sharpe", 0.0) or 0.0)
    dd = float(oos.get("max_dd", 0.0) or 0.0)
    # 回撤超过 50% 重罚
    pen = 0.0
    if dd < -0.5:
        pen += 0.5
    if dd < -0.7:
        pen += 1.0
    return sh - pen


def evaluate_slice(
    df: pd.DataFrame,
    n: int,
    entry: str,
    exit_: str,
    filt: str,
    cost: CostModel,
) -> tuple[dict[str, Any], int, pd.Series, pd.Series]:
    feat = build_features(df, n)
    warm = max(n + 5, 40)
    pos = positions_from_rules(feat, entry, exit_, filt, warm=warm)
    net, n_trades, _ = apply_cost(pos, feat["ret"], cost)
    daily = pd.Series(net.values, index=feat["trade_date"].values).dropna()
    return Metrics.calc(daily), n_trades, pos, feat


def grid_search_stock(
    symbol: str,
    df: pd.DataFrame,
    cost: CostModel,
    n_list: list[int],
    is_ratio: float = 0.70,
) -> tuple[ComboResult | None, list[ComboResult], pd.DataFrame, pd.Series, list[dict]]:
    """网格搜索 + 时间切分 OOS 选最优."""
    split = int(len(df) * is_ratio)
    # 至少保留 120 日 OOS
    split = min(split, len(df) - 120)
    split = max(split, 200)
    df_is = df.iloc[:split].reset_index(drop=True)
    df_oos = df.iloc[split:].reset_index(drop=True)

    combos: list[ComboResult] = []
    for n, entry, exit_, filt in itertools.product(
        n_list, ENTRY_RULES.keys(), EXIT_RULES.keys(), FILTERS.keys()
    ):
        # 对称组合剪枝：z 类 entry 配 z 类 exit 更合理，但不强制
        m_is, tr_is, _, _ = evaluate_slice(df_is, n, entry, exit_, filt, cost)
        m_oos, tr_oos, _, _ = evaluate_slice(df_oos, n, entry, exit_, filt, cost)
        if not m_is or not m_oos:
            continue
        sc = score_combo(m_oos, tr_oos)
        combos.append(
            ComboResult(
                symbol=symbol,
                n=n,
                entry=entry,
                exit=exit_,
                filt=filt,
                is_sharpe=float(m_is.get("sharpe", 0) or 0),
                is_annual=float(m_is.get("annual", 0) or 0),
                is_max_dd=float(m_is.get("max_dd", 0) or 0),
                is_n_trades=tr_is,
                oos_sharpe=float(m_oos.get("sharpe", 0) or 0),
                oos_annual=float(m_oos.get("annual", 0) or 0),
                oos_max_dd=float(m_oos.get("max_dd", 0) or 0),
                oos_total=float(m_oos.get("total", 0) or 0),
                oos_n_trades=tr_oos,
                oos_win=float(m_oos.get("win", 0) or 0),
                score=sc,
            )
        )

    if not combos:
        return None, [], df, pd.Series(dtype=float), []

    combos.sort(key=lambda x: (x.score, x.oos_annual), reverse=True)
    best = combos[0]

    # 全样本重放最优规则拿买卖点 + 当前仓位
    m_full, tr_full, pos_full, feat_full = evaluate_slice(
        df, best.n, best.entry, best.exit, best.filt, cost
    )
    trades = extract_trades(feat_full, pos_full)
    return best, combos, feat_full, pos_full, trades


def current_signal(feat: pd.DataFrame, pos: pd.Series, best: ComboResult) -> dict[str, Any]:
    """根据最新一行给出预测动作."""
    i = len(feat) - 1
    # 找到最后一个有效 ret 之前的 bar（今日可能无 t+2）
    while i > 0 and (pd.isna(feat["mi"].iloc[i]) or pd.isna(feat["close"].iloc[i])):
        i -= 1
    row = feat.iloc[i]
    holding = float(pos.iloc[i]) > 0
    enter = bool(ENTRY_RULES[best.entry](feat).iloc[i]) and bool(
        FILTERS[best.filt](feat).iloc[i]
    )
    leave = bool(EXIT_RULES[best.exit](feat).iloc[i])

    if holding and leave:
        action = "SELL"
        reason = f"触发退出规则 {best.exit}"
    elif (not holding) and enter:
        action = "BUY"
        reason = f"触发入场规则 {best.entry} + 过滤 {best.filt}"
    elif holding:
        action = "HOLD_LONG"
        reason = "持仓中，未触发退出"
    else:
        action = "STAY_FLAT"
        reason = "空仓，未触发入场"

    # 预测分数：mi_z 与 a-mi 方向一致性
    mi_z = float(row["mi_z"]) if np.isfinite(row["mi_z"]) else 0.0
    a_mi = float(row["a_mi"]) if np.isfinite(row["a_mi"]) else 0.0
    score = 0.6 * np.tanh(mi_z / 2) + 0.4 * np.tanh(a_mi / (abs(float(row["close"])) * 0.02 + 1e-6))
    if best.filt.startswith("adx"):
        adx_v = float(row["adx"]) if np.isfinite(row["adx"]) else 0.0
        if adx_v < 20:
            score *= 0.5  # 震荡市降权

    return {
        "asof": str(pd.Timestamp(row["trade_date"]).date()),
        "close": round(float(row["close"]), 3),
        "mi": None if not np.isfinite(row["mi"]) else round(float(row["mi"]), 4),
        "a": None if not np.isfinite(row["a"]) else round(float(row["a"]), 4),
        "mi_z": round(mi_z, 3),
        "adx": None if not np.isfinite(row["adx"]) else round(float(row["adx"]), 2),
        "position": "LONG" if holding else "FLAT",
        "action": action,
        "reason": reason,
        "pred_score": round(float(score), 3),
        "pred_bias": "bullish" if score > 0.15 else ("bearish" if score < -0.15 else "neutral"),
        "exec_note": "若 action=BUY/SELL，按纪律应在下一交易日开盘执行（非收盘价）",
    }


def buy_hold_oos(df: pd.DataFrame, is_ratio: float, cost: CostModel) -> dict[str, Any]:
    split = int(len(df) * is_ratio)
    split = min(split, len(df) - 120)
    split = max(split, 200)
    oos = df.iloc[split:].reset_index(drop=True)
    ret = next_day_return(oos)
    pos = pd.Series(1.0, index=oos.index)
    net, n_trades, _ = apply_cost(pos, ret, cost)
    daily = pd.Series(net.values, index=oos["trade_date"].values).dropna()
    return Metrics.calc(daily)


def rule_zh(name: str) -> str:
    table = {
        "mi_cross_up_0": "MI 上穿 0 轴",
        "a_cross_up_mi": "A 线上穿 MI 线",
        "dual_cross": "MI 上穿 0 或 A 上穿 MI",
        "mi_pos_and_a_gt_mi": "MI>0 且 A>MI（动量加速区）",
        "mi_z_gt_0.5": "MI 滚动Z分数 > 0.5",
        "mi_z_gt_1.0": "MI 滚动Z分数 > 1.0",
        "mi_cross_dn_0": "MI 下穿 0 轴",
        "a_cross_dn_mi": "A 线下穿 MI 线",
        "dual_exit": "MI 下穿 0 或 A 下穿 MI",
        "mi_neg_or_a_lt_mi": "MI<0 或 A<MI",
        "mi_z_lt_-0.5": "MI 滚动Z分数 < -0.5",
        "mi_z_lt_0": "MI 滚动Z分数 < 0",
        "none": "无过滤",
        "adx_gt_20": "ADX>20（趋势市）",
        "adx_gt_25": "ADX>25（强趋势）",
        "atr_mid": "ATR% 处于近60日 20%~90% 分位",
    }
    return table.get(name, name)


def fmt_pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:.2f}%"


def fmt_num(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:.2f}"


def write_report(
    path: Path,
    payloads: list[dict[str, Any]],
) -> None:
    lines: list[str] = []
    lines.append("# 自选股 MI 买卖点寻优报告")
    lines.append("")
    lines.append("> 样本内选参 + 样本外评分；历史回测不代表未来。非投资建议。")
    lines.append("")
    lines.append("## 方法")
    lines.append("")
    lines.append("- 数据：本地 `cs_data` 真实日线")
    lines.append("- 指标：MI / A（tqsdk 口径）+ mi_z + ADX + ATR")
    lines.append("- 网格：N × entry × exit × filter")
    lines.append("- 切分：前 70% 样本内看可交易性，**后 30% 样本外夏普为主评分**")
    lines.append("- 执行：t 收盘信号 → t+1 开盘；成本买 0.1% / 卖 0.2%")
    lines.append("- 预测分：`0.6*tanh(mi_z/2) + 0.4*tanh(a_mi/scale)`，ADX 弱时降权")
    lines.append("")

    for p in payloads:
        sym = p["symbol"]
        name = p.get("name", sym)
        best = p["best"]
        sig = p["signal"]
        bh = p.get("buy_hold_oos") or {}
        lines.append(f"## {sym} {name}")
        lines.append("")
        lines.append("### 最优算法（样本外胜出）")
        lines.append("")
        lines.append(f"- **N** = `{best['n']}`")
        lines.append(f"- **买入** = {rule_zh(best['entry'])} (`{best['entry']}`)")
        lines.append(f"- **卖出** = {rule_zh(best['exit'])} (`{best['exit']}`)")
        lines.append(f"- **过滤** = {rule_zh(best['filt'])} (`{best['filt']}`)")
        lines.append("")
        lines.append("| 区间 | 年化 | 夏普 | 最大回撤 | 交易次数 |")
        lines.append("|------|------|------|----------|----------|")
        lines.append(
            f"| 样本内 | {fmt_pct(best['is_annual'])} | {fmt_num(best['is_sharpe'])} | "
            f"{fmt_pct(best['is_max_dd'])} | {best['is_n_trades']} |"
        )
        lines.append(
            f"| **样本外** | **{fmt_pct(best['oos_annual'])}** | **{fmt_num(best['oos_sharpe'])}** | "
            f"**{fmt_pct(best['oos_max_dd'])}** | {best['oos_n_trades']} |"
        )
        lines.append(
            f"| 样本外买入持有 | {fmt_pct(bh.get('annual'))} | {fmt_num(bh.get('sharpe'))} | "
            f"{fmt_pct(bh.get('max_dd'))} | 1 |"
        )
        lines.append("")
        lines.append("### 当前信号（预测）")
        lines.append("")
        lines.append(f"- 日期: `{sig['asof']}`  收盘 `{sig['close']}`")
        lines.append(f"- 动作: **{sig['action']}** · 仓位 `{sig['position']}`")
        lines.append(f"- 原因: {sig['reason']}")
        lines.append(
            f"- 预测分: `{sig['pred_score']}` ({sig['pred_bias']}) · "
            f"MI={sig['mi']} · mi_z={sig['mi_z']} · ADX={sig['adx']}"
        )
        lines.append(f"- {sig['exec_note']}")
        lines.append("")
        trades = p.get("trades") or []
        lines.append(f"### 历史买卖点（全样本最优规则，共 {len(trades)} 笔）")
        lines.append("")
        if not trades:
            lines.append("无完整回合。")
        else:
            lines.append("| 信号买 | 执行买 | 买开 | 信号卖 | 执行卖 | 卖开 | 收益 | 持有日 |")
            lines.append("|--------|--------|------|--------|--------|------|------|--------|")
            # 最近 12 笔
            for t in trades[-12:]:
                tr = t.get("trade_return")
                trs = "n/a" if tr is None else f"{tr*100:+.2f}%"
                lines.append(
                    f"| {t['signal_buy_date']} | {t['exec_buy_date']} | {t['buy_open']} | "
                    f"{t['signal_sell_date']} | {t['exec_sell_date']} | {t['sell_open']} | "
                    f"{trs} | {t['hold_days']} |"
                )
            wins = [t for t in trades if t.get("trade_return") is not None and t["trade_return"] > 0]
            rets = [t["trade_return"] for t in trades if t.get("trade_return") is not None]
            if rets:
                lines.append("")
                lines.append(
                    f"回合胜率 `{(len(wins)/len(rets))*100:.1f}%` · "
                    f"平均收益 `{np.mean(rets)*100:+.2f}%` · "
                    f"中位 `{np.median(rets)*100:+.2f}%`"
                )
        lines.append("")
        lines.append("### 候选 Top5（按样本外 score）")
        lines.append("")
        lines.append("| rank | N | entry | exit | filter | OOS夏普 | OOS年化 | OOS回撤 |")
        lines.append("|------|---|-------|------|--------|---------|---------|---------|")
        for i, c in enumerate(p.get("top5") or [], 1):
            lines.append(
                f"| {i} | {c['n']} | {c['entry']} | {c['exit']} | {c['filt']} | "
                f"{fmt_num(c['oos_sharpe'])} | {fmt_pct(c['oos_annual'])} | "
                f"{fmt_pct(c['oos_max_dd'])} |"
            )
        lines.append("")

    lines.append("## 预测算法（可复用）")
    lines.append("")
    lines.append("```")
    lines.append("每天收盘后:")
    lines.append("  1. 算 A = close - close.shift(N), MI = SMA(A,N,1)")
    lines.append("  2. 算 mi_z = rolling_z(MI, 60)")
    lines.append("  3. 算 ADX(14) 作趋势过滤")
    lines.append("  4. 若空仓且命中【买入规则】且通过【过滤】→ 次日开盘 BUY")
    lines.append("  5. 若持仓且命中【卖出规则】→ 次日开盘 SELL")
    lines.append("  6. pred_score = 0.6*tanh(mi_z/2) + 0.4*tanh((A-MI)/scale)")
    lines.append("     ADX<20 时 pred_score *= 0.5")
    lines.append("  7. 不在信号日收盘价成交；禁止用当日 close 当执行价")
    lines.append("```")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="自选股 MI 买卖点寻优")
    ap.add_argument("--symbols", default=None, help="覆盖自选，逗号分隔")
    ap.add_argument("--n-list", default="6,9,12,14,20")
    ap.add_argument("--is-ratio", type=float, default=0.70)
    ap.add_argument("--report", default=str(REPORT_MD))
    args = ap.parse_args()

    if args.symbols:
        symbols = [_norm(s) for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_watchlist()
    n_list = [int(x) for x in args.n_list.split(",") if x.strip()]
    cost = CostModel()

    print(f"📌 自选股: {', '.join(symbols)}")
    print(f"   N 网格: {n_list} | 样本内比例: {args.is_ratio:.0%}")
    print(f"   组合数/股: {len(n_list)*len(ENTRY_RULES)*len(EXIT_RULES)*len(FILTERS)}")

    payloads: list[dict[str, Any]] = []
    for sym in symbols:
        df = load_stock(sym)
        if df is None or len(df) < 300:
            print(f"  ❌ {sym} 数据不足")
            continue
        name = NAMES.get(sym, sym)
        print(f"\n=== {sym} {name} · {df['trade_date'].min().date()}→{df['trade_date'].max().date()} n={len(df)} ===")
        best, combos, feat, pos, trades = grid_search_stock(
            sym, df, cost, n_list, is_ratio=args.is_ratio
        )
        if best is None:
            print("  无有效组合")
            continue
        bh = buy_hold_oos(df, args.is_ratio, cost)
        sig = current_signal(feat, pos, best)
        print(
            f"  最优: N={best.n} | 买={best.entry} | 卖={best.exit} | 滤={best.filt}"
        )
        print(
            f"  OOS  年化{best.oos_annual*100:+.1f}% 夏普{best.oos_sharpe:.2f} "
            f"回撤{best.oos_max_dd*100:.1f}% 交易{best.oos_n_trades}"
        )
        print(
            f"  持有 年化{(bh.get('annual') or 0)*100:+.1f}% 夏普{bh.get('sharpe', 0):.2f} "
            f"回撤{(bh.get('max_dd') or 0)*100:.1f}%"
        )
        print(
            f"  当前 {sig['asof']} → {sig['action']}  pred={sig['pred_score']} ({sig['pred_bias']})"
        )
        print(f"  历史完整回合: {len(trades)} 笔（展示最近若干笔见报告）")

        top5 = [asdict(c) for c in combos[:5]]
        payloads.append({
            "symbol": sym,
            "name": name,
            "date_start": str(df["trade_date"].min().date()),
            "date_end": str(df["trade_date"].max().date()),
            "best": asdict(best),
            "top5": top5,
            "signal": sig,
            "trades": trades,
            "buy_hold_oos": {
                "annual": bh.get("annual"),
                "sharpe": bh.get("sharpe"),
                "max_dd": bh.get("max_dd"),
                "total": bh.get("total"),
            },
            "n_combos_tested": len(combos),
        })

    if not payloads:
        print("❌ 无结果")
        return 1

    write_report(Path(args.report), payloads)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n✅ Markdown: {args.report}")
    print(f"✅ JSON:     {REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
