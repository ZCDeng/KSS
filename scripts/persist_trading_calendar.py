#!/usr/bin/env python3
"""Persist the authoritative A-share open-day calendar for scheduled research.

This script is deliberately separate from the LLM scheduler.  It consumes the
existing Tushare credential only while querying ``trade_cal``, writes a
non-secret calendar snapshot atomically, and never prints credential or
provider exception text.  The scheduled research runner then reads this local
snapshot and never guesses holidays from weekdays.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# `python scripts/persist_trading_calendar.py` 下 sys.path[0] 是 scripts/,仓库根不在
# 路径里,``from kss.data...`` 必然 ModuleNotFoundError,又被 persist_calendar 的
# ``except Exception`` 吞成 return False——日历因此从未真正写出过,周报每周 blocked。
# 与同目录 run_news_digest.py 同一写法补上仓库根。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _iso_open_dates(frame: object) -> list[str]:
    """Normalize a Tushare trade_cal frame without coupling the writer to pandas."""
    try:
        rows = frame.to_dict("records")  # type: ignore[union-attr]
    except Exception:
        return []
    values: list[str] = []
    for row in rows:
        try:
            if int(row.get("is_open", 0)) != 1:
                continue
            raw = str(row.get("cal_date") or "")
            if len(raw) == 8 and raw.isdigit():
                values.append(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        except (TypeError, ValueError):
            continue
    return sorted(set(values))


def persist_calendar(*, state_root: Path, through: date) -> bool:
    """Fetch a bounded calendar window and atomically persist verified open days."""
    # Include earlier weeks for shortened-week detection and a small forward
    # horizon for the next scheduled run.  Calendar truth still comes only
    # from Tushare; there is intentionally no weekday fallback.
    start = through - timedelta(days=45)
    end = through + timedelta(days=14)
    try:
        from kss.data.tushare_client import TushareClient

        frame = TushareClient().get_pro().trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception:
        return False
    values = _iso_open_dates(frame)
    if not values:
        return False

    destination = state_root / "storage" / "agent" / "research" / "trading_calendar.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "source": "tushare_trade_cal",
        "exchange": "SSE",
        "through": through.isoformat(),
        "open_dates": values,
    }
    fd, temporary = tempfile.mkstemp(prefix=".trading_calendar.", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(body, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--through", default=date.today().isoformat())
    args = parser.parse_args()
    try:
        target = date.fromisoformat(args.through)
    except ValueError:
        print('{"status":"blocked","reason":"invalid_calendar_date"}')
        return 2
    ok = persist_calendar(state_root=args.state_root, through=target)
    print(json.dumps({"status": "ready" if ok else "blocked", "reason": None if ok else "trading_calendar_unavailable"}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
