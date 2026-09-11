#!/usr/bin/env python3
"""Fetch HiThink auction snapshot and atomically persist it.

Usage:
  python scripts/fetch_hithink_auction.py --stage live
  python scripts/fetch_hithink_auction.py --stage final --thscodes 600519.SH,300750.SZ
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.hithink_auction import (  # noqa: E402
    AUCTION_STAGES,
    fetch_auction_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HiThink 集合竞价落盘")
    parser.add_argument("--stage", choices=sorted(AUCTION_STAGES), default="live")
    parser.add_argument("--thscodes", default="", help="逗号分隔 thscode；缺省=自选∪北证池")
    args = parser.parse_args(argv)
    codes = [c.strip() for c in args.thscodes.split(",") if c.strip()] or None
    payload = fetch_auction_snapshot(stage=args.stage, thscodes=codes, persist=True)
    # Never print the API key. Summarize only.
    err = payload.get("error")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    summary = {
        "ok": err is None,
        "error": err,
        "stage": payload.get("stage"),
        "trade_date": payload.get("trade_date"),
        "count": len(payload.get("thscodes") or []),
        "data_status": data.get("data_status"),
        "item": len(data.get("item") or []) if isinstance(data, dict) else 0,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if err is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
