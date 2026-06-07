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
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.notifications.manager import CHANNEL_CHOICES, send_to_channels  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REVIEW_DIR = PROJECT_ROOT / "storage" / "daily_review"

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
