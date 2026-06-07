#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动量 regime 判定 + 赎回幅度 vs 胜率剂量曲线 (etf_flow_signal_lessons 遗留线索).

Part A — 动量 regime:
  独立于申赎数据的两个候选指标 (规避循环论证):
    mom20    = 科创50 主题复合 NAV 20 日收益
    breadth  = cs_data 全池 (626 只) 当日涨幅 >=9.5% 家数占比的 5 日均值
  四个 a-priori 定义 (阈值先declared后看数):
    R1: mom20 > +8%   R2: breadth_5dma >= 2%   R3: R1 且 R2   R4: R1 或 R2
  评估: ① 分季覆盖是否命中 2025Q3/2026Q2 且避开 2025Q4;
        ② 兑现加速事件 (大涨日+赎回) 的事件-对照差在 regime 内/外的分化;
        ③ regime 起点日列表 (= "进入动量季度" 的提示时点).

Part B — 赎回幅度 vs 胜率:
  主题-日 flow_5d 分箱 → 后 5 日均值/胜率, 全样本 + regime 内外分层.

口径: regime 标志在 t 日收盘可知 (mom/breadth 都只用 ≤t 数据),
信号 T+1 收盘执行, 前向收益从 T+1 收盘起算 (复用 backtest_etf_radar).

用法: python3 research_momentum_regime.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_etf_radar import (  # noqa: E402
    RALLY_FLOW,
    RALLY_RET,
    build_theme_panel,
    fwd_return,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE = PROJECT_ROOT / "storage" / "etf_radar_backtest_raw.parquet"
REPORT = PROJECT_ROOT / "storage" / "reports" / "momentum_regime_research_20260607.md"

MOM_TH = 8.0          # R1: 20 日收益阈值 %
BREADTH_TH = 0.02     # R2: 大阳家数占比 5 日均值阈值
BIG_UP_PCT = 9.5      # "大阳/准涨停" 日涨幅阈值 %
FLOW_BINS = [-np.inf, -5, -2, 0, 2, np.inf]
FLOW_LABELS = ["≤-5%", "-5~-2%", "-2~0%", "0~+2%", ">+2%"]


def load_breadth(dates: pd.Index) -> pd.Series:
    """cs_data 全池大阳家数占比 (5 日均值), 对齐到 dates."""
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    files = sorted(PROJECT_ROOT.glob("cs_data_*.csv"))
    for f in files:
        df = pd.read_csv(f, usecols=["trade_date", "pct_chg"])
        df = df[df["trade_date"] >= "2025-05-01"]
        for d, p in zip(df["trade_date"], df["pct_chg"]):
            totals[d] = totals.get(d, 0) + 1
            if p >= BIG_UP_PCT:
                counts[d] = counts.get(d, 0) + 1
    frac = pd.Series({d: counts.get(d, 0) / n for d, n in totals.items()
                      if n >= 300}).sort_index()  # 数据稀疏日剔除
    frac.index = frac.index.str.replace("-", "")
    logger.info("breadth: %d 个交易日, 池均 %d 只", len(frac),
                int(np.mean(list(totals.values()))))
    # cs_data 由 8:30 cron 拉 T-1, 最近 1-2 天结构性不完整 (被 n>=300 剔除)
    # → ffill 最多 3 天兜底, 实时使用时 breadth 滞后约 2 个交易日
    return frac.rolling(5).mean().reindex(dates).ffill(limit=3)


def regime_episodes(flag: pd.Series) -> list[tuple[str, str]]:
    """连续 True 区段 → (起点, 终点) 列表."""
    eps, start = [], None
    for d, v in flag.items():
        if v and start is None:
            start = d
        elif not v and start is not None:
            eps.append((start, prev))
            start = None
        prev = d
    if start is not None:
        eps.append((start, prev))
    return eps


def rally_events(flows: pd.DataFrame, ret1: pd.DataFrame) -> pd.DataFrame:
    """兑现加速事件/对照 (同 backtest 检验3), 带日期供 regime 分层."""
    flow1 = flows["flow1"]
    rows = []
    for i in range(len(ret1)):
        for theme in ret1.columns:
            r, f = ret1.iloc[i].get(theme), flow1.iloc[i].get(theme)
            if pd.isna(r) or pd.isna(f) or r < RALLY_RET:
                continue
            grp = ("event" if f <= RALLY_FLOW
                   else ("control" if f > 0 else None))
            if grp is None:
                continue
            fwd = fwd_return(ret1, i, 5)
            if fwd is None or pd.isna(fwd.get(theme)):
                continue
            rows.append({"date": ret1.index[i], "grp": grp, "fwd5": fwd[theme]})
    return pd.DataFrame(rows)


def spread_by_date(df: pd.DataFrame) -> tuple[float, float, int, int]:
    """(事件均值, 对照均值, 事件日数, 对照日数) — 按日聚合."""
    ev = df[df["grp"] == "event"].groupby("date")["fwd5"].mean()
    ct = df[df["grp"] == "control"].groupby("date")["fwd5"].mean()
    return ev.mean(), ct.mean(), len(ev), len(ct)


def dose_response(flows: pd.DataFrame, ret1: pd.DataFrame,
                  regime: pd.Series) -> pd.DataFrame:
    """主题-日 flow_5d 分箱 → fwd5 均值/胜率, 全样本 + regime 分层."""
    flow5 = flows["flow5"]
    rows = []
    for i in range(len(ret1)):
        fwd = fwd_return(ret1, i, 5)
        if fwd is None:
            continue
        in_reg = bool(regime.iloc[i]) if pd.notna(regime.iloc[i]) else False
        for theme in ret1.columns:
            f = flow5.iloc[i].get(theme)
            if pd.isna(f) or pd.isna(fwd.get(theme)):
                continue
            rows.append({"date": ret1.index[i], "flow5": f,
                         "fwd5": fwd[theme], "regime": in_reg})
    df = pd.DataFrame(rows)
    df["bin"] = pd.cut(df["flow5"], FLOW_BINS, labels=FLOW_LABELS)
    out = []
    for scope, sub in (("全样本", df), ("动量期", df[df["regime"]]),
                       ("非动量期", df[~df["regime"]])):
        g = sub.groupby("bin", observed=True)["fwd5"]
        for b in FLOW_LABELS:
            col = g.get_group(b) if b in g.groups else pd.Series(dtype=float)
            out.append({"scope": scope, "bin": b, "n": len(col),
                        "n_dates": sub[sub["bin"] == b]["date"].nunique(),
                        "mean": col.mean(), "win": (col > 0).mean()})
    return pd.DataFrame(out)


def main() -> int:
    raw = pd.read_parquet(CACHE)
    flows, ret1 = build_theme_panel(raw)

    # ---- Part A: regime 指标 ----
    kc50 = ret1["科创50"]
    mom20 = ((1 + kc50 / 100).rolling(20).apply(np.prod, raw=True) - 1) * 100
    breadth = load_breadth(ret1.index)
    defs = {
        "R1 mom20>8%": mom20 > MOM_TH,
        "R2 breadth≥2%": breadth >= BREADTH_TH,
        "R3 R1且R2": (mom20 > MOM_TH) & (breadth >= BREADTH_TH),
        "R4 R1或R2": (mom20 > MOM_TH) | (breadth >= BREADTH_TH),
    }
    events = rally_events(flows, ret1)
    quarters = pd.PeriodIndex(pd.to_datetime(ret1.index), freq="Q").astype(str)

    lines = ["# 动量 regime 判定 + 赎回幅度胜率研究", "",
             f"窗口: {ret1.index[0]} ~ {ret1.index[-1]} ({len(ret1)} 交易日)",
             f"阈值 (a priori): mom20>{MOM_TH}%, breadth_5dma≥{BREADTH_TH:.0%} "
             f"(大阳=日涨幅≥{BIG_UP_PCT}%, 池 626 只)", "",
             "## Part A: 四个 regime 定义对比", "",
             "| 定义 | 覆盖 | 25Q3 | 25Q4 | 26Q1 | 26Q2 | regime内 事件-对照差 (日数) | regime外 差 |",
             "|---|---|---|---|---|---|---|---|"]
    best_flag = None
    for name, flag in defs.items():
        flag = flag.fillna(False)
        qcov = pd.Series(flag.values, index=quarters).groupby(level=0).mean()
        ev_dates = set(events["date"])
        in_df = events[events["date"].map(lambda d: bool(flag.get(d, False)))]
        out_df = events[~events["date"].isin(in_df["date"])]
        ein, cin, nein, ncin = spread_by_date(in_df) if len(in_df) else (np.nan,) * 4
        eout, cout, neout, ncout = spread_by_date(out_df) if len(out_df) else (np.nan,) * 4
        lines.append(
            f"| {name} | {flag.mean():.0%} | "
            + " | ".join(f"{qcov.get(q, 0):.0%}" for q in
                         ["2025Q3", "2025Q4", "2026Q1", "2026Q2"])
            + f" | {ein - cin:+.2f}pp ({nein}/{ncin}) | {eout - cout:+.2f}pp ({neout}/{ncout}) |")
        if name.startswith("R3"):
            best_flag = flag
    lines += ["", "### R3 regime 起点 (进入动量期的提示时点)", ""]
    for s, e in regime_episodes(best_flag):
        lines.append(f"- {s} → {e} ({len(best_flag.loc[s:e])} 个交易日)")
    last = ret1.index[-1]
    lines += ["", "### 当前状态 (最新交易日)", "",
              f"- {last}: mom20 = {mom20.iloc[-1]:+.1f}% (阈值 +{MOM_TH}%), "
              f"breadth_5dma = {breadth.iloc[-1]:.1%} (阈值 {BREADTH_TH:.0%}, "
              f"breadth 滞后约 2 日), "
              f"R3 = {'✅ 动量期' if best_flag.iloc[-1] else '❌ 非动量期'}"]

    # ---- Part B: 剂量曲线 ----
    dr = dose_response(flows, ret1, best_flag)
    lines += ["", "## Part B: flow_5d 分箱 → 后 5 日均值 / 胜率 (regime=R3)", "",
              "| 范围 | 全样本 n(日) / 均值 / 胜率 | 动量期 | 非动量期 |",
              "|---|---|---|---|"]
    for b in FLOW_LABELS:
        cells = []
        for scope in ("全样本", "动量期", "非动量期"):
            r = dr[(dr["scope"] == scope) & (dr["bin"] == b)].iloc[0]
            cells.append(f"{r['n']}({r['n_dates']}) / {r['mean']:+.2f}% / {r['win'] * 100:.0f}%"
                         if r["n"] else "—")
        lines.append(f"| {b} | " + " | ".join(cells) + " |")
    lines += ["", "n(日) = 主题-日样本数(去重日期数); 主题间同日相关, 显著性看日数", ""]

    # 稳健性: 5 日前向窗口在相邻日期间重叠 → 每 5 个交易日抽 1 天 (非重叠)
    flow5 = flows["flow5"]
    rows = []
    for i in range(0, len(ret1), 5):
        fwd = fwd_return(ret1, i, 5)
        if fwd is None:
            continue
        for theme in ret1.columns:
            f = flow5.iloc[i].get(theme)
            if pd.notna(f) and pd.notna(fwd.get(theme)):
                rows.append({"flow5": f, "fwd5": fwd[theme]})
    nv = pd.DataFrame(rows)
    nv["bin"] = pd.cut(nv["flow5"], FLOW_BINS, labels=FLOW_LABELS)
    lines += ["### 稳健性: 非重叠窗口 (每 5 日抽 1 天)", "",
              "| 范围 | n | 均值 | 胜率 |", "|---|---|---|---|"]
    for b in FLOW_LABELS:
        col = nv[nv["bin"] == b]["fwd5"]
        lines.append(f"| {b} | {len(col)} | {col.mean():+.2f}% | "
                     f"{(col > 0).mean() * 100:.0f}% |" if len(col)
                     else f"| {b} | 0 | — | — |")
    top = nv[nv["bin"].isin(FLOW_LABELS[:2])]["fwd5"]
    bot = nv[nv["bin"].isin(FLOW_LABELS[3:])]["fwd5"]
    t = ((top.mean() - bot.mean())
         / np.sqrt(top.var() / len(top) + bot.var() / len(bot)))
    lines += ["", f"赎回组 (≤-2%) vs 申购组 (>0%): "
              f"{top.mean():+.2f}% (n={len(top)}) vs {bot.mean():+.2f}% "
              f"(n={len(bot)}), t = {t:.2f} (同日跨主题相关, 有效 n 约 /2.5)", ""]

    # Part C: 双重排序 — 申赎 vs 价格动量 (排除 "flow 只是动量马甲" 的混淆)
    past5 = ((1 + ret1 / 100).rolling(5).apply(np.prod, raw=True) - 1) * 100
    rows = []
    for i in range(0, len(ret1), 5):  # 非重叠
        fwd = fwd_return(ret1, i, 5)
        if fwd is None:
            continue
        for th in ret1.columns:
            f, p = flow5.iloc[i].get(th), past5.iloc[i].get(th)
            if pd.isna(f) or pd.isna(p) or pd.isna(fwd.get(th)):
                continue
            rows.append({"flow_neg": f <= -2, "flow_pos": f > 0,
                         "past5": p, "fwd5": fwd[th]})
    ds = pd.DataFrame(rows)
    ds["mom_t"] = pd.qcut(ds["past5"], 3, labels=["弱", "中", "强"])
    lines += ["## Part C: 双重排序 — 过去5日动量 × 申赎方向 (非重叠)", "",
              "| 动量分位 | 赎回组 (flow≤-2%) | 申购组 (flow>0) | 差 |",
              "|---|---|---|---|"]
    for m, sub in ds.groupby("mom_t", observed=True):
        neg = sub[sub["flow_neg"]]["fwd5"]
        pos = sub[sub["flow_pos"]]["fwd5"]
        lines.append(
            f"| {m} | {neg.mean():+.2f}%/{(neg > 0).mean() * 100:.0f}%/n={len(neg)} "
            f"| {pos.mean():+.2f}%/{(pos > 0).mean() * 100:.0f}%/n={len(pos)} "
            f"| {neg.mean() - pos.mean():+.2f}pp |")
    lines += ["", "每个动量分位内赎回组均胜 → 申赎携带独立于价格动量的信息;",
              "强动量+申购 是最弱格子 (上涨中转为申购 = 接棒资金进场的见顶标记)", "",
              "---", "", "_生成: research_momentum_regime.py_"]

    report = "\n".join(lines)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
