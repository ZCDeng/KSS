#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 调仓雷达信号一年回测 (2025-06 ~ 2026-06).

验证 kss/sector/etf_radar.py 的信号对主题未来收益是否有预测力, 三个检验:

  1. 横截面轮动 IC: flow_5d 主题排名 vs 未来 5 日主题收益 (Spearman)
  2. 底部接货: 主题日收益 <= -2% 且 flow_1d >= +0.5% 的事件 → 后 5/10 日收益,
     对照组 = 同样大跌但 flow_1d < 0
  3. 兑现加速: 主题日收益 >= +2% 且 flow_1d <= -1.5% → 后 5/10 日收益,
     对照组 = 同样大涨但 flow_1d > 0

前视规避: fd_share T 日数据 T+1 早间披露 → 信号最早 T+1 收盘可执行;
所有前向收益从 T+1 收盘起算 (nav[t+1] → nav[t+1+h]).

主题收益用成分 ETF 份额加权 NAV (accum_nav 优先, 规避分红断崖).

用法:
  python3 backtest_etf_radar.py            # 拉数 + 回测 (有缓存则跳过拉数)
  python3 backtest_etf_radar.py --refresh  # 强制重拉
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
_KSS_STATE = Path(__import__("os").environ.get("KSS_STATE_ROOT") or PROJECT_ROOT)  # U1: bundle-mode 写入重定向
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402
from kss.sector.etf_radar import ETF_BASKET  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

START, END = "20250601", "20260605"
CACHE = _KSS_STATE / "storage" / "etf_radar_backtest_raw.parquet"
REPORT = _KSS_STATE / "storage" / "reports" / "etf_radar_backtest_20260607.md"

DIP_RET, DIP_FLOW = -2.0, 0.5      # 检验 2 阈值
RALLY_RET, RALLY_FLOW = 2.0, -1.5  # 检验 3 阈值
FWD_HORIZONS = (5, 10)


def pull_raw(client: TushareClient) -> pd.DataFrame:
    """拉 1 年 fund_share + fund_nav, 长表: theme/ts_code/trade_date/fd_share/nav."""
    frames = []
    for theme, funds in ETF_BASKET.items():
        for ts_code, name in funds:
            sh = client.fetch_fund_share(ts_code, START, END)
            time.sleep(0.3)
            nav = client.get_pro().fund_nav(
                ts_code=ts_code, start_date=START, end_date=END)
            time.sleep(0.3)
            if sh is None or nav is None or nav.empty:
                # fail loud: 任何一只缺数直接中止, 不带病出结论
                raise RuntimeError(f"{ts_code} ({name}) share/nav 拉取失败")
            nav = nav.rename(columns={"nav_date": "trade_date"})
            nav["nav"] = nav["accum_nav"].where(
                nav["accum_nav"].notna(), nav["unit_nav"])
            m = sh[["trade_date", "fd_share"]].merge(
                nav[["trade_date", "nav"]], on="trade_date", how="inner")
            m["ts_code"], m["theme"] = ts_code, theme
            frames.append(m)
            logger.info("%s %s: %d 行", theme, ts_code, len(m))
    return pd.concat(frames, ignore_index=True)


def build_theme_panel(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """主题日面板: (flow_1d/flow_5d 宽表, 主题日收益宽表)."""
    raw = raw.sort_values("trade_date")
    shares = raw.pivot_table(index="trade_date", columns="theme",
                             values="fd_share", aggfunc="sum")
    flow1 = shares.pct_change() * 100
    flow5 = (shares / shares.shift(5) - 1) * 100
    # 主题收益: 份额加权 NAV 收益 (用前一日份额做权重, 避免权重前视)
    rets = {}
    for theme in shares.columns:
        sub = raw[raw["theme"] == theme].pivot_table(
            index="trade_date", columns="ts_code", values="nav")
        w = raw[raw["theme"] == theme].pivot_table(
            index="trade_date", columns="ts_code", values="fd_share").shift(1)
        r = sub.pct_change() * 100
        rets[theme] = (r * w).sum(axis=1) / w.sum(axis=1)
    ret1 = pd.DataFrame(rets)
    return pd.concat({"flow1": flow1, "flow5": flow5}, axis=1), ret1


def fwd_return(ret1: pd.DataFrame, t_idx: int, horizon: int) -> pd.Series | None:
    """t+1 收盘 → t+1+h 收盘的累计收益 % (按日收益复利)."""
    if t_idx + 1 + horizon >= len(ret1):
        return None
    window = ret1.iloc[t_idx + 2: t_idx + 2 + horizon]  # ret[t+2..t+1+h] 即 nav t+1→t+1+h
    return ((1 + window / 100).prod() - 1) * 100


def test_rank_ic(flows: pd.DataFrame, ret1: pd.DataFrame) -> dict:
    """检验 1: flow_5d 截面排名 vs 未来 5 日收益 Spearman IC."""
    flow5 = flows["flow5"]
    daily_ic = []
    for i in range(len(ret1)):
        sig = flow5.iloc[i].dropna()
        fwd = fwd_return(ret1, i, 5)
        if fwd is None or len(sig) < 5:
            continue
        fwd = fwd.reindex(sig.index).dropna()
        if len(fwd) < 5:
            continue
        ic = sig.reindex(fwd.index).corr(fwd, method="spearman")
        if pd.notna(ic):
            daily_ic.append((ret1.index[i], ic))
    ics = pd.Series(dict(daily_ic))
    weekly = ics.iloc[::5]  # 非重叠抽样, 规避重叠窗口自相关虚增显著性
    return {
        "n_daily": len(ics), "ic_mean": ics.mean(), "ic_pos": (ics > 0).mean(),
        "n_weekly": len(weekly), "ic_weekly_mean": weekly.mean(),
        "t_weekly": weekly.mean() / weekly.std() * np.sqrt(len(weekly))
        if len(weekly) > 1 else np.nan,
    }


def test_events(flows: pd.DataFrame, ret1: pd.DataFrame,
                ret_th: float, flow_th: float, dip: bool) -> dict:
    """检验 2/3: 极端日 ± 申赎条件的事件组 vs 对照组前向收益."""
    flow1 = flows["flow1"]
    rows = []
    for i in range(len(ret1)):
        for theme in ret1.columns:
            r, f = ret1.iloc[i].get(theme), flow1.iloc[i].get(theme)
            if pd.isna(r) or pd.isna(f):
                continue
            hit = r <= ret_th if dip else r >= ret_th
            if not hit:
                continue
            grp = ("event" if (f >= flow_th if dip else f <= flow_th)
                   else ("control" if (f < 0 if dip else f > 0) else None))
            if grp is None:
                continue
            entry = {"grp": grp}
            ok = True
            for h in FWD_HORIZONS:
                fwd = fwd_return(ret1, i, h)
                if fwd is None or pd.isna(fwd.get(theme)):
                    ok = False
                    break
                entry[f"fwd{h}"] = fwd[theme]
            if ok:
                rows.append(entry)
    df = pd.DataFrame(rows)
    out: dict = {}
    for grp in ("event", "control"):
        sub = df[df["grp"] == grp] if not df.empty else df
        out[grp] = {"n": len(sub)}
        for h in FWD_HORIZONS:
            col = sub[f"fwd{h}"] if len(sub) else pd.Series(dtype=float)
            out[grp][f"fwd{h}_mean"] = col.mean()
            out[grp][f"fwd{h}_win"] = (col > 0).mean() if len(col) else np.nan
    return out


def test_events_by_date(flows: pd.DataFrame, ret1: pd.DataFrame,
                        ret_th: float, flow_th: float, dip: bool) -> dict:
    """稳健性: 事件按日期聚合 (消同日跨主题聚集) + 分季均值.

    个体层面事件在同一天跨主题高度相关 (88 事件仅 46 个去重日期),
    直接用个体 n 做 t 检验会虚增显著性; 以按日聚合为准.
    """
    flow1 = flows["flow1"]
    rows = []
    for i in range(len(ret1)):
        for theme in ret1.columns:
            r, f = ret1.iloc[i].get(theme), flow1.iloc[i].get(theme)
            if pd.isna(r) or pd.isna(f):
                continue
            if not (r <= ret_th if dip else r >= ret_th):
                continue
            grp = ("event" if (f >= flow_th if dip else f <= flow_th)
                   else ("control" if (f < 0 if dip else f > 0) else None))
            if grp is None:
                continue
            fwd = fwd_return(ret1, i, 5)
            if fwd is None or pd.isna(fwd.get(theme)):
                continue
            rows.append({"date": ret1.index[i], "grp": grp, "fwd5": fwd[theme]})
    df = pd.DataFrame(rows)
    ev = df[df["grp"] == "event"].groupby("date")["fwd5"].mean()
    ct = df[df["grp"] == "control"].groupby("date")["fwd5"].mean()
    t = ((ev.mean() - ct.mean())
         / np.sqrt(ev.var() / len(ev) + ct.var() / len(ct))
         if len(ev) > 1 and len(ct) > 1 else np.nan)
    df["q"] = pd.to_datetime(df["date"]).dt.to_period("Q").astype(str)
    quarters = {
        q: (sub[sub["grp"] == "event"]["fwd5"].mean(),
            sub[sub["grp"] == "control"]["fwd5"].mean(),
            len(sub[sub["grp"] == "event"]))
        for q, sub in df.groupby("q")
    }
    return {"ev_mean": ev.mean(), "ev_n": len(ev),
            "ct_mean": ct.mean(), "ct_n": len(ct), "t": t, "quarters": quarters}


def fmt_events(label: str, res: dict) -> list[str]:
    lines = [f"### {label}", "",
             "| 组 | n | 后5日均值 | 后5日胜率 | 后10日均值 | 后10日胜率 |",
             "|---|---|---|---|---|---|"]
    for grp, zh in (("event", "事件组"), ("control", "对照组")):
        g = res[grp]
        lines.append(
            f"| {zh} | {g['n']} | {g['fwd5_mean']:+.2f}% | {g['fwd5_win']*100:.0f}% "
            f"| {g['fwd10_mean']:+.2f}% | {g['fwd10_win']*100:.0f}% |")
    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if CACHE.exists() and not args.refresh:
        raw = pd.read_parquet(CACHE)
        logger.info("缓存命中: %s (%d 行)", CACHE, len(raw))
    else:
        raw = pull_raw(TushareClient())
        raw.to_parquet(CACHE, index=False)
        logger.info("原始数据落盘: %s (%d 行)", CACHE, len(raw))

    flows, ret1 = build_theme_panel(raw)
    logger.info("面板: %d 个交易日 × %d 主题", len(ret1), ret1.shape[1])

    ic = test_rank_ic(flows, ret1)
    dip = test_events(flows, ret1, DIP_RET, DIP_FLOW, dip=True)
    rally = test_events(flows, ret1, RALLY_RET, RALLY_FLOW, dip=False)
    dip_rb = test_events_by_date(flows, ret1, DIP_RET, DIP_FLOW, dip=True)
    rally_rb = test_events_by_date(flows, ret1, RALLY_RET, RALLY_FLOW, dip=False)

    lines = [
        "# ETF 调仓雷达信号一年回测", "",
        f"窗口: {START} ~ {END} · {len(ret1)} 个交易日 × {ret1.shape[1]} 主题",
        "前视规避: 信号 T+1 收盘可执行, 前向收益从 T+1 收盘起算", "",
        "## 检验 1: flow_5d 截面轮动 IC (vs 未来5日主题收益)", "",
        f"- 日频 IC 均值: {ic['ic_mean']:+.3f} (n={ic['n_daily']}, IC>0 占比 {ic['ic_pos']*100:.0f}%)",
        f"- 周频非重叠 IC 均值: {ic['ic_weekly_mean']:+.3f} (n={ic['n_weekly']}, t={ic['t_weekly']:.2f})", "",
    ]
    lines += fmt_events(
        f"检验 2: 底部接货 (主题日收益≤{DIP_RET}% 时, 申购≥+{DIP_FLOW}% vs 流出)", dip)
    lines += fmt_events(
        f"检验 3: 兑现加速 (主题日收益≥+{RALLY_RET}% 时, 赎回≤{RALLY_FLOW}% vs 申购)", rally)
    lines += ["## 稳健性 (事件按日聚合, 消同日跨主题聚集)", ""]
    for label, rb in (("底部接货", dip_rb), ("兑现加速", rally_rb)):
        lines.append(
            f"- {label}: 事件 {rb['ev_mean']:+.2f}% (n={rb['ev_n']} 日) vs "
            f"对照 {rb['ct_mean']:+.2f}% (n={rb['ct_n']} 日), t = {rb['t']:.2f}")
    lines += ["", "兑现加速分季 (事件均值 / 对照均值 / 事件 n):", ""]
    for q, (e, c, n) in rally_rb["quarters"].items():
        lines.append(f"- {q}: {e:+.2f}% / {c:+.2f}% / n={n}")
    lines += ["", "---", "",
              "_生成: backtest_etf_radar.py · 6 主题小截面; 显著性以按日聚合 t 为准, "
              "|t|<2 视为无信号; 事件组 n<20 时结论仅供方向参考_"]
    report = "\n".join(lines)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    logger.info("报告落盘: %s", REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
