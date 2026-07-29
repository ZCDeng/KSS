#!/usr/bin/env python3
"""构建信号卡周报。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from kss.signal_cards.weekly import build_weekly_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--db", default=None)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv)
    path = build_weekly_report(
        args.start,
        args.end,
        db_path=args.db,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
