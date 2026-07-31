#!/usr/bin/env python3
"""风格对照日更 —— 主推荐 formal-daily-picks 之后跑四风格对照池.

用法::

    python3 scripts/style_contrast_daily.py
    python3 scripts/style_contrast_daily.py --date 2026-07-30
    python3 scripts/style_contrast_daily.py --top-n 5

失败隔离：单风格失败只占位，进程仍 exit 0（除非面板整体不可用）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_KSS_STATE = Path(os.environ.get("KSS_STATE_ROOT") or PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("style_contrast_daily")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="风格对照日更")
    parser.add_argument("--date", type=str, default=None, help="prediction_date YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument(
        "--data-glob",
        type=str,
        default=None,
        help="cs_data CSV glob；默认 STATE_ROOT/cs_data_*.csv 与 PROJECT_ROOT",
    )
    parser.add_argument("--no-gate", action="store_true", help="跳过门禁评估，一律 research_blocked")
    args = parser.parse_args(argv)

    from kss.strategies.style_runner import load_style_panel, run_style_contrast_day

    state = _KSS_STATE
    db_path = state / "storage" / "kss.db"
    if args.data_glob:
        data_glob = args.data_glob
    else:
        # 与 paper_trade_log_mv 一致：优先 state，否则 project
        cand = list(state.glob("cs_data_*.csv"))
        if cand:
            data_glob = str(state / "cs_data_*.csv")
        else:
            data_glob = str(PROJECT_ROOT / "cs_data_*.csv")

    logger.info("加载风格面板 glob=%s", data_glob)
    try:
        panel = load_style_panel(data_glob)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    if panel.empty:
        logger.error("风格面板为空")
        return 2

    try:
        result = run_style_contrast_day(
            panel,
            prediction_date=args.date,
            top_n=args.top_n,
            db_path=db_path,
            evaluate_gate=not args.no_gate,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("风格对照日更失败: %s", exc)
        return 1

    logger.info(
        "style_contrast ok=%s failed=%s date=%s",
        result["ok_count"],
        result["failed_count"],
        result["prediction_date"],
    )
    for slot in result["slots"]:
        logger.info("  %s: %s", slot["style_id"], slot.get("status"))
    # 有任意成功槽即 0；全失败仍 0（占位已写），面板级错误才非 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
