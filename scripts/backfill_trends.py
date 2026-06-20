#!/usr/bin/env python3
"""趋势页历史回填（U2）。

枚举可回填交易日（以 hsgt parquet 的 trade_date 为锚，北向最稠密）→ 逐日调
archive_trends_daily.build_trend_day → 原子写 storage/trends/<date>.json。

  - 默认回填最近 --days（默认 60）个交易日，且 < today（>=today 交给 U3 每日任务，避免抢写）。
  - 无 --force 跳过已存在；--force 覆盖。
  - 前置：北向需 pandas + parquet 引擎（pyarrow/fastparquet）；缺则报错指引，不静默产错数。
  - 缺板块/ETF 源的日仍落 json（部分 null + flags），摘要标注，不整体失败（Fail loud）。

用法（.venv-desktop）：
  python scripts/backfill_trends.py --days 60
  python scripts/backfill_trends.py --start 2026-05-01 --end 2026-06-18 --force
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import archive_trends_daily as atd  # noqa: E402  复用单日构建器

HSGT_PARQUET = ROOT / "storage" / "macro" / "hsgt_daily.parquet"


def _require_parquet_engine() -> None:
    if not any(importlib.util.find_spec(m) for m in ("pyarrow", "fastparquet")):
        sys.exit(
            "缺 parquet 引擎：北向回填读不了 hsgt_daily.parquet。\n"
            "  修复： .venv-desktop/bin/pip install pyarrow\n"
            "  （或改用 Tushare moneyflow_hsgt 历史，见计划 KTD7）"
        )


def _trading_dates() -> list[str]:
    """hsgt parquet 全部交易日（带横杠 YYYY-MM-DD，升序）。"""
    import pandas as pd
    df = pd.read_parquet(HSGT_PARQUET)
    compact = sorted({str(x) for x in df["trade_date"].tolist()})
    return [f"{c[:4]}-{c[4:6]}-{c[6:8]}" for c in compact if len(c) == 8]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="回填 storage/trends/*.json")
    ap.add_argument("--days", type=int, default=60, help="回填最近 N 个交易日（默认 60）")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD（与 --end 一起用，覆盖 --days）")
    ap.add_argument("--end", help="结束日 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="覆盖已存在")
    args = ap.parse_args(argv[1:])

    _require_parquet_engine()
    today = _date.today().isoformat()
    all_dates = _trading_dates()
    # >= today 不回填（交给每日任务）
    dates = [d for d in all_dates if d < today]
    if args.start and args.end:
        dates = [d for d in dates if args.start <= d <= args.end]
    else:
        dates = dates[-args.days:]

    if not dates:
        print("无可回填交易日", file=sys.stderr)
        return 0

    ok = skipped = 0
    miss = {"north": 0, "etf": 0, "sector": 0, "recs": 0}
    for d in dates:
        out = atd.TRENDS_DIR / f"{d}.json"
        if out.exists() and not args.force:
            skipped += 1
            continue
        path = atd.write_trend_day(d, force=True)
        import json
        flags = json.loads(path.read_text(encoding="utf-8"))["flags"]
        for k in miss:
            if not flags[k]:
                miss[k] += 1
        ok += 1

    print(
        f"[backfill] 回填 {ok} 天、跳过 {skipped} 天（区间 {dates[0]}→{dates[-1]}）。"
        f"\n  缺源统计： 北向 {miss['north']} / ETF {miss['etf']} / 板块 {miss['sector']} / 推荐 {miss['recs']}"
        f"\n  （ETF/板块缺源为预期——历史归档稀疏，随 going-forward 累积变密）",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
