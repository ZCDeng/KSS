#!/usr/bin/env python3
"""构建每日信号卡：单日或 --backfill start end。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from kss.signal_cards.pipeline import backfill, build_for_date  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build deterministic signal cards")
    p.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="回补区间")
    p.add_argument("--db", default=None, help="可选 kss.db 路径")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    args = p.parse_args(argv)

    if args.backfill:
        results = backfill(args.backfill[0], args.backfill[1], db_path=args.db)
        summary = [
            {
                "trade_date": r.trade_date,
                "n_cards": len(r.cards),
                "by_type": r.by_type,
                "failed": r.failed_generators,
                "written": r.written,
            }
            for r in results
        ]
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            for s in summary:
                print(
                    f"{s['trade_date']}: cards={s['n_cards']} written={s['written']} "
                    f"by_type={s['by_type']} failed={s['failed']}"
                )
        return 0 if all(not r.failed_generators or r.cards for r in results) else 1

    if not args.date:
        # 默认：ETF 最新日
        from kss.storage import etf_radar

        hist = etf_radar.read_history(1, db_path=args.db)
        if not hist:
            print("no etf snapshot and no --date", file=sys.stderr)
            return 2
        args.date = hist[0]["trade_date"]

    result = build_for_date(args.date, db_path=args.db, write=True)
    payload = {
        "trade_date": result.trade_date,
        "n_cards": len(result.cards),
        "by_type": result.by_type,
        "failed_generators": result.failed_generators,
        "written": result.written,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"{result.trade_date}: cards={len(result.cards)} written={result.written} "
            f"by_type={result.by_type} failed={result.failed_generators}"
        )
    if not result.cards and result.failed_generators:
        return 1
    if not result.cards and not result.by_type:
        # 无任何数据源
        print("no cards produced (no source data?)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
