#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_review 预测 vs 实际行情滚动校验 → Telegram 周报.

解析 storage/daily_review/*.md 中每只股票的:
  - 次日情形分布 (修正后概率, 5 桶)
  - 预期区间 (50% 带 / 中位 / 80% 带)
  - 止损位
对齐 cs_data_<code>.csv 的次日实际 close/pct_chg/low, 输出:
  - 校准统计 (50%/80% 带覆盖率, 桶命中率, 实际桶被赋概率, Brier, 方向命中)
  - 偏差最大条目
  - console 通道额外输出逐条命中明细

雷达 grade 校验段: 读 storage/etf_radar/*.json 存档 (sector_review 每日落盘),
对成熟信号 (后向 ≥6 个交易日 NAV 可得) 计算后 5 日主题收益, 按 grade 分档
对比一年回测基线 (强势确认 +3.3%/75% · 中性偏多 +1.05%/71% · 偏弱 ~0%/50%),
divergence 事件逐条列出. 该段任何失败只降级跳过, 不阻塞预测校验主流程.

用法:
  python3 scripts/validate_predictions.py                    # 近 7 天, console
  python3 scripts/validate_predictions.py --lookback-days 14
  python3 scripts/validate_predictions.py --channel all      # console + telegram
  python3 scripts/validate_predictions.py --dry-run          # 仅打印, 不推送

cron 部署: 每周五 19:30 (daily_review 19:00 已刷新 cs_data 与当日复盘后).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.notifications.manager import CHANNEL_CHOICES, send_to_channels  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REVIEW_DIR = PROJECT_ROOT / "storage" / "daily_review"
RADAR_ARCHIVE_DIR = PROJECT_ROOT / "storage" / "etf_radar"

# 一年回测基线 (storage/reports/momentum_regime_research_20260607.md, 非重叠抽样)
GRADE_BASELINE: dict[str, tuple[float, float]] = {
    "强势确认": (3.3, 75.0),   # (后5日均值 %, 胜率 %)
    "中性偏多": (1.05, 71.0),
    "偏弱": (0.0, 50.0),
}

BUCKETS = ["强势突破上行", "温和上涨", "横盘震荡", "温和回落", "大跌破位"]


def bucket_of(pct: float) -> int:
    if pct > 5:
        return 0
    if pct > 1:
        return 1
    if pct >= -1:
        return 2
    if pct >= -5:
        return 3
    return 4


def parse_review(path: Path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"# KSS (\d{4}-\d{2}-\d{2}) 复盘 / (\d{4}-\d{2}-\d{2}) 预测", text)
    if not m:
        logger.warning("无法解析标题: %s", path.name)
        return None, None, []
    review_date, forecast_date = m.group(1), m.group(2)
    stocks = []
    for sec in re.split(r"\n---\n", text):
        hm = re.search(r"📊 \*(.+?)\((\d{6})\) R", sec)
        if not hm:
            continue
        name, code = hm.group(1), hm.group(2)
        close_m = re.search(r"收 ([\d.]+) \(([+-][\d.]+)%\)", sec)
        probs = {}
        for b in BUCKETS:
            pm = re.search(re.escape(b) + r" \([^)]*\)\s+([\d.]+)%\s+([\d.]+)%", sec)
            if pm:
                probs[b] = float(pm.group(2))  # 修正后
        band50 = re.search(r"收盘 50% 概率落 \*([\d.]+) ~ ([\d.]+)\* \(中位 ([\d.]+)\)", sec)
        band80 = re.search(r"极端 80% 区间 ([\d.]+) ~ ([\d.]+)", sec)
        stop = re.search(r"止损位 \*([\d.]+)\*", sec)
        stocks.append({
            "name": name, "code": code,
            "close": float(close_m.group(1)) if close_m else None,
            "probs": [probs.get(b) for b in BUCKETS],
            "b50": (float(band50.group(1)), float(band50.group(2))) if band50 else None,
            "median": float(band50.group(3)) if band50 else None,
            "b80": (float(band80.group(1)), float(band80.group(2))) if band80 else None,
            "stop": float(stop.group(1)) if stop else None,
        })
    return review_date, forecast_date, stocks


def load_actual(code: str, date: str):
    f = PROJECT_ROOT / f"cs_data_{code}.csv"
    if not f.exists():
        return None
    with f.open() as fh:
        for row in csv.DictReader(fh):
            if row["trade_date"] == date:
                return {
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "pct_chg": float(row["pct_chg"]),
                }
    return None


def collect(lookback_days: int):
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = []
    for path in sorted(REVIEW_DIR.glob("2026-*.md")):
        if path.stem < cutoff:
            continue
        review_date, forecast_date, stocks = parse_review(path)
        for s in stocks:
            act = load_actual(s["code"], forecast_date)
            rows.append({**s, "review": review_date, "fdate": forecast_date, "act": act})
    return rows


def score(verifiable: list[dict]) -> dict:
    n50 = n80 = nmodal = 0
    probs_realized, briers, dir_records, median_errs, details, devs = [], [], [], [], [], []
    for r in verifiable:
        a = r["act"]
        ab = bucket_of(a["pct_chg"])
        p = r["probs"]
        has_p = all(v is not None for v in p)
        modal = max(range(5), key=lambda i: p[i]) if has_p else None
        in50 = bool(r["b50"] and r["b50"][0] <= a["close"] <= r["b50"][1])
        in80 = bool(r["b80"] and r["b80"][0] <= a["close"] <= r["b80"][1])
        n50 += in50
        n80 += in80
        if has_p:
            probs_realized.append(p[ab] / 100)
            nmodal += (modal == ab)
            briers.append(sum(((p[i] / 100) - (1 if i == ab else 0)) ** 2 for i in range(5)))
            dir_records.append(((p[0] + p[1]) / 100, (p[3] + p[4]) / 100, a["pct_chg"]))
        med_err = (a["close"] - r["median"]) / r["median"] * 100 if r["median"] else None
        if med_err is not None:
            median_errs.append(abs(med_err))
        implied = (r["median"] / r["close"] - 1) * 100 if r["median"] and r["close"] else 0.0
        devs.append((abs(a["pct_chg"] - implied), r, implied))
        details.append({
            "r": r, "ab": ab, "modal": modal, "in50": in50, "in80": in80,
            "med_err": med_err,
            "stop_hit": bool(r["stop"] and a["low"] < r["stop"]),
            "p_realized": p[ab] if has_p else None,
        })
    n = len(verifiable)
    np_ = len(probs_realized)
    correct = sum(1 for pu, pd, pc in dir_records
                  if (pu > pd and pc > 0) or (pd > pu and pc < 0))
    return {
        "n": n,
        "cov50": n50 / n if n else 0.0,
        "cov80": n80 / n if n else 0.0,
        "modal_rate": nmodal / np_ if np_ else 0.0,
        "p_realized_avg": sum(probs_realized) / np_ if np_ else 0.0,
        "brier": sum(briers) / np_ if np_ else 0.0,
        "dir_rate": correct / np_ if np_ else 0.0,
        "median_mae": sum(median_errs) / len(median_errs) if median_errs else 0.0,
        "details": details,
        "devs": sorted(devs, reverse=True, key=lambda t: t[0]),
    }


def flag(cond_ok: bool) -> str:
    return "✅" if cond_ok else "⚠️"


def render_summary(s: dict, lookback_days: int) -> str:
    lines = [
        f"窗口: 近 {lookback_days} 天 · 可验证预测 {s['n']} 条",
        "",
        "*校准统计* (理想值括号内)",
        f"  {flag(abs(s['cov50'] - 0.5) <= 0.10)} 50% 区间覆盖: {s['cov50'] * 100:.0f}% (50%)",
        f"  {flag(s['cov80'] >= 0.70)} 80% 区间覆盖: {s['cov80'] * 100:.0f}% (80%)",
        f"  {flag(s['modal_rate'] > 0.25)} 模态桶命中: {s['modal_rate'] * 100:.0f}% (随机 20%)",
        f"  {flag(s['p_realized_avg'] > 0.25)} 实际桶均赋概率: {s['p_realized_avg'] * 100:.1f}% (随机 20%)",
        f"  {flag(s['brier'] < 0.75)} 多类 Brier: {s['brier']:.3f} (随机 0.80)",
        f"  {flag(s['dir_rate'] > 0.5)} 方向命中: {s['dir_rate'] * 100:.0f}% (基线 50%)",
        f"  中位预测 MAE: {s['median_mae']:.2f}%",
        "",
        "*偏差最大条目* (预期中位隐含% vs 实际%)",
    ]
    for d, r, implied in s["devs"][:5]:
        lines.append(
            f"  · {r['fdate'][5:]} {r['name']}: {implied:+.1f}% → {r['act']['pct_chg']:+.1f}% (偏 {d:.1f}pp)")
    stop_hits = sum(1 for d in s["details"] if d["stop_hit"])
    lines += ["", f"破止损位天次: {stop_hits}/{s['n']}"]
    lines += ["", "_历史 IC≈0 的先验如果连续两周 Brier>0.8 或方向<45%, 建议停用情形分布段_"]
    return "\n".join(lines)


def print_details(s: dict) -> None:
    hdr = (f"{'复盘日':<10} {'预测日':<10} {'股票':<14} {'实际%':>7} {'实际桶':<6} "
           f"{'桶P%':>5} {'模态命中':>4} {'50带':>4} {'80带':>4} {'破止损':>5}")
    print(hdr)
    print("-" * len(hdr))
    for d in s["details"]:
        r = d["r"]
        a = r["act"]
        print(f"{r['review']:<10} {r['fdate']:<10} {r['name']}({r['code']})"
              f" {a['pct_chg']:>7.2f} {BUCKETS[d['ab']]:<6}"
              f" {d['p_realized'] if d['p_realized'] is not None else float('nan'):>5.1f}"
              f" {'✓' if d['modal'] == d['ab'] else '✗':>4}"
              f" {'✓' if d['in50'] else '✗':>4} {'✓' if d['in80'] else '✗':>4}"
              f" {'⚠️' if d['stop_hit'] else '-':>5}")


# ====================================================================== #
# 雷达 grade 校验段
# ====================================================================== #


def load_radar_archives() -> list[dict]:
    """读全部雷达存档 (累积校验, 不限 lookback), 按日期升序."""
    out = []
    for path in sorted(RADAR_ARCHIVE_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("雷达存档损坏, 跳过 %s: %s", path.name, exc)
    return out


def build_radar_panel(start: str, end: str):
    """拉 basket fund_share + fund_nav → (flows, ret1) 主题日面板.

    与一年回测同一构造 (backtest_etf_radar.build_theme_panel):
    accum_nav 优先, T-1 份额加权.
    """
    import pandas as pd

    from backtest_etf_radar import build_theme_panel
    from kss.data.tushare_client import TushareClient
    from kss.sector.etf_radar import ETF_BASKET

    client = TushareClient()
    frames = []
    for funds in ETF_BASKET.values():
        for ts_code, name in funds:
            sh = client.fetch_fund_share(ts_code, start, end)
            time.sleep(0.3)
            nav = client.fetch_fund_nav(ts_code, start, end)
            time.sleep(0.3)
            if sh is None or nav is None:
                raise RuntimeError(f"{ts_code} ({name}) share/nav 拉取失败")
            nav = nav.rename(columns={"nav_date": "trade_date"})
            nav["nav"] = nav["accum_nav"].where(
                nav["accum_nav"].notna(), nav["unit_nav"])
            m = sh[["trade_date", "fd_share"]].merge(
                nav[["trade_date", "nav"]], on="trade_date", how="inner")
            m["ts_code"] = ts_code
            m["theme"] = next(t for t, fs in ETF_BASKET.items()
                              if any(c == ts_code for c, _ in fs))
            frames.append(m)
    return build_theme_panel(pd.concat(frames, ignore_index=True))


def validate_radar_grades(archives: list[dict]) -> dict | None:
    """按 grade 分档统计成熟信号的后 5 日收益; 无成熟信号返回 None."""
    import pandas as pd

    from backtest_etf_radar import fwd_return

    if not archives:
        return None
    start = (datetime.strptime(archives[0]["trade_date"], "%Y%m%d")
             - timedelta(days=10)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    _flows, ret1 = build_radar_panel(start, end)
    idx = {d: i for i, d in enumerate(ret1.index)}

    rows, n_immature = [], 0
    for arc in archives:
        d = arc.get("data_date") or arc["trade_date"]
        i = idx.get(d)
        fwd = fwd_return(ret1, i, 5) if i is not None else None
        if fwd is None:
            n_immature += 1
            continue
        for theme, m in arc.get("themes", {}).items():
            if m.get("grade") is None or pd.isna(fwd.get(theme)):
                continue
            rows.append({"date": d, "theme": theme, "grade": m["grade"],
                         "divergence": bool(m.get("divergence")),
                         "fwd5": float(fwd[theme]),
                         "source": arc.get("source", "live")})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    grades = {}
    for g, sub in df.groupby("grade"):
        grades[g] = {"n": len(sub), "n_dates": sub["date"].nunique(),
                     "mean": sub["fwd5"].mean(),
                     "win": (sub["fwd5"] > 0).mean() * 100}
    div = df[df["divergence"]][["date", "theme", "fwd5"]].to_dict("records")
    return {"grades": grades, "divergence": div,
            "n_archives": len(archives), "n_immature": n_immature,
            "n_backfill": int((df["source"] == "backfill").sum())}


def render_radar_section(stats: dict | None) -> str:
    if stats is None:
        return ("\n\n*雷达 grade 校验*\n  存档不足或信号未成熟 "
                "(需后向 ≥6 个交易日), 本周跳过")
    lines = ["", "", "*雷达 grade 校验* (累积, 后5日收益 vs 一年回测基线)"]
    for g in ("强势确认", "中性偏多", "偏弱"):
        if g not in stats["grades"]:
            continue
        s = stats["grades"][g]
        bm, bw = GRADE_BASELINE[g]
        drift = "✅" if abs(s["win"] - bw) <= 15 else "⚠️"
        lines.append(
            f"  {drift} {g}: {s['mean']:+.2f}% / 胜率 {s['win']:.0f}% "
            f"(n={s['n']}, {s['n_dates']}日) · 基线 {bm:+.1f}%/{bw:.0f}%")
    if stats["divergence"]:
        lines.append("  见顶预警事件:")
        for d in stats["divergence"]:
            lines.append(f"    · {d['date'][4:6]}-{d['date'][6:]} {d['theme']}: "
                         f"后5日 {d['fwd5']:+.2f}% (预警基线 -0.6%)")
    note = f"  存档 {stats['n_archives']} 日 (未成熟 {stats['n_immature']})"
    if stats["n_backfill"]:
        note += f" · 含回填样本 {stats['n_backfill']} 条"
    lines.append(note)
    lines.append("  _连续两周强势确认档胜率 <60% → 重校 grade 阈值_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--channel", choices=CHANNEL_CHOICES, default="console")
    ap.add_argument("--dry-run", action="store_true", help="仅打印, 不推送")
    args = ap.parse_args()

    rows = collect(args.lookback_days)
    verifiable = [r for r in rows if r["act"]]
    logger.info("预测条目 %d, 可验证 %d", len(rows), len(verifiable))

    title = f"KSS 预测校验周报 {datetime.now().strftime('%m-%d')}"
    if not verifiable:
        msg = f"近 {args.lookback_days} 天无可验证预测 (复盘缺失或 cs_data 未更新) — 请检查 daily_review / update_data cron"
        logger.warning(msg)
        if not args.dry_run:
            send_to_channels(msg, args.channel, title=title, parse_mode="Markdown")
        return 1

    s = score(verifiable)
    summary = render_summary(s, args.lookback_days)

    # 雷达 grade 校验段 —— 失败只降级, 不阻塞预测校验主流程
    try:
        radar_stats = validate_radar_grades(load_radar_archives())
        summary += render_radar_section(radar_stats)
    except Exception as exc:  # noqa: BLE001
        logger.error("雷达校验段失败 (已降级跳过): %s", exc)
        summary += f"\n\n*雷达 grade 校验*\n  ⚠️ 本段生成失败: {str(exc)[:80]}"

    print(f"📊 {title}\n")
    print(summary)
    print()
    print_details(s)

    if args.dry_run or args.channel == "console":
        return 0
    results = send_to_channels(summary, args.channel, title=title, parse_mode="Markdown")
    ok = results.get("telegram", True) if args.channel in ("telegram", "all") else True
    if not ok:
        logger.error("Telegram 推送失败")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
