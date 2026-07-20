#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S/R 位命中统计报告：自选股池批量跑 hit_stats，画位质量可量化体检.

成功判据落地（plan 2026-07-20-001 U6）：信号侧有 GO 门禁把关，位侧此前无判据——
本报告用触及后 N 日反弹/跌破占比给位质量一个可复算的数字，不靠肉眼看图。

单票失败/样本不足不拖垮整池（同 run_indicator_signal_pack.py 既有先例）。用法::

    .venv/bin/python scripts/report_sr_hit_stats.py
    .venv/bin/python scripts/report_sr_hit_stats.py --asof 2026-07-20
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.indicators.pack import load_ohlcv
from kss.indicators.registry import state_root
from kss.indicators.sr_levels import hit_stats
from kss.storage.watchlist import load_watchlist

_EMPTY_STATS = {"levels": 0, "touches": 0, "rebound_rate": None, "breakdown_rate": None}


def _report_dir(root: Path) -> Path:
    d = root / "storage" / "reports" / "indicator_lab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def collect(symbols: list[str], root: Path) -> pd.DataFrame:
    """遍历自选股池跑 hit_stats，单票失败记为 error 行，不中断整池。"""
    rows: list[dict] = []
    for sym in symbols:
        try:
            df = load_ohlcv(sym, root)
            if df is None or len(df) < 20:
                rows.append({"symbol": sym, "status": "skipped", "reason": "无行情或样本过短", **_EMPTY_STATS})
                continue
            stats = hit_stats(df)
            rows.append({"symbol": sym, "status": "ok", "reason": "", **stats})
        except Exception as exc:  # noqa: BLE001 — 单票故障不拖垮整池
            rows.append({"symbol": sym, "status": "error", "reason": str(exc), **_EMPTY_STATS})
    return pd.DataFrame(rows)


def render_markdown(df: pd.DataFrame, asof: str) -> str:
    lines = [f"# S/R 位命中统计 — {asof}\n", f"- 样本: {len(df)} 只自选股\n"]
    lines.append("| 标的 | 状态 | 位数 | 触及次数 | 反弹占比 | 跌破占比 | 备注 |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for _, row in df.iterrows():
        rebound = f"{row['rebound_rate'] * 100:.1f}%" if pd.notna(row["rebound_rate"]) else "-"
        breakdown = f"{row['breakdown_rate'] * 100:.1f}%" if pd.notna(row["breakdown_rate"]) else "-"
        lines.append(
            f"| {row['symbol']} | {row['status']} | {row['levels']} | {row['touches']} | "
            f"{rebound} | {breakdown} | {row['reason']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="S/R 位命中统计报告（自选股池批量）")
    ap.add_argument("--asof", default=None, help="报告日期 YYYY-MM-DD，缺省取今日")
    args = ap.parse_args()

    root = state_root()
    symbols = load_watchlist(db_path=root / "storage" / "kss.db")
    if not symbols:
        print("ℹ️  自选股池为空，跳过")
        return 0

    asof = args.asof or date.today().isoformat()
    df = collect(symbols, root)

    out_dir = _report_dir(root)
    csv_path = out_dir / f"sr_hit_stats_{asof}.csv"
    md_path = out_dir / f"sr_hit_stats_{asof}.md"
    df.to_csv(csv_path, index=False)
    md_path.write_text(render_markdown(df, asof), encoding="utf-8")

    ok = int((df["status"] == "ok").sum())
    err = int((df["status"] == "error").sum())
    print(f"完成 {ok}/{len(symbols)} 只（error={err}）")
    print(f"  CSV: {csv_path}")
    print(f"  MD:  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
