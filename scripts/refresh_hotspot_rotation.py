#!/usr/bin/env python3
"""刷新板块热点轮动快照.

用法：
    python3 scripts/refresh_hotspot_rotation.py --date 20260618
    python3 scripts/refresh_hotspot_rotation.py --date latest
    python3 scripts/refresh_hotspot_rotation.py --date latest --dry-run
    python3 scripts/refresh_hotspot_rotation.py --date 20260618 --lookback-days 10 --enable-kaipan --enable-leaders

默认保存到 ``storage/sector_rotation/YYYYMMDD.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kss.data.tushare_client import TushareClient
from kss.sector.hotspot_rotation import (
    build_hotspot_rotation_snapshot,
    save_snapshot,
    snapshot_to_dict,
)

logger = logging.getLogger(__name__)


def _latest_trade_date() -> str:
    """返回最近一个交易日（简化版：跳过周末，不处理节假日）."""
    d = datetime.now()
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ap = argparse.ArgumentParser(description="刷新板块热点轮动快照")
    ap.add_argument(
        "--date",
        default="latest",
        help="交易日 YYYYMMDD，或 'latest'（默认）",
    )
    ap.add_argument(
        "--output-dir",
        default="storage/sector_rotation",
        help="归档目录",
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=5,
        help="历史回看交易日数（含当日）",
    )
    ap.add_argument(
        "--top-n-industry",
        type=int,
        default=None,
        help="行业榜保留前 N",
    )
    ap.add_argument(
        "--top-n-concept",
        type=int,
        default=None,
        help="概念榜保留前 N",
    )
    ap.add_argument(
        "--top-n-kaipan",
        type=int,
        default=None,
        help="KAIPAN 榜保留前 N",
    )
    ap.add_argument(
        "--classification-top-n",
        type=int,
        default=10,
        help="四象限分类阈值",
    )
    ap.add_argument(
        "--enable-kaipan",
        action="store_true",
        help="启用 KAIPAN 外部数据源",
    )
    ap.add_argument(
        "--enable-leaders",
        action="store_true",
        help="启用板块龙头股 persistence 计算",
    )
    ap.add_argument(
        "--leaders-top-n-boards",
        type=int,
        default=15,
        help="最多为前 N 个板块获取龙头",
    )
    ap.add_argument(
        "--leaders-top-n-stocks",
        type=int,
        default=5,
        help="每个板块返回前 N 个龙头",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印 JSON，不写文件",
    )
    args = ap.parse_args()

    trade_date = _latest_trade_date() if args.date == "latest" else args.date
    try:
        datetime.strptime(trade_date, "%Y%m%d")
    except ValueError:
        logger.error("--date 格式非法: %r", trade_date)
        return 1

    client = TushareClient()
    snap = build_hotspot_rotation_snapshot(
        trade_date,
        client=client,
        output_dir=args.output_dir,
        lookback_days=args.lookback_days,
        top_n_industry=args.top_n_industry,
        top_n_concept=args.top_n_concept,
        top_n_kaipan=args.top_n_kaipan,
        classification_top_n=args.classification_top_n,
        enable_kaipan=args.enable_kaipan,
        enable_leaders=args.enable_leaders,
        leaders_top_n_boards=args.leaders_top_n_boards,
        leaders_top_n_stocks=args.leaders_top_n_stocks,
    )
    if snap is None:
        logger.error("无法生成 %s 的快照", trade_date)
        return 1

    if args.dry_run:
        print(json.dumps(snapshot_to_dict(snap), ensure_ascii=False, indent=2))
        return 0

    save_snapshot(snap, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
