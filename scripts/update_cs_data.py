#!/usr/bin/env python3
"""增量更新所有 cs_data_*.csv 到最新交易日.

补 `kss update` 命令的空壳——`kss/cli/commands/update.py` 当前只 echo 不实际拉数据.
本脚本直接用 :class:`TushareClient` 拉日线 OHLCV + daily_basic（市值/PE/PB），
按 ``cs_data_<code>.csv`` 格式写盘，与现有 `paper_trade_log_mv.py` 兼容.

用法::

    python3 scripts/update_cs_data.py                  # 增量更新所有 cs_data_*.csv
    python3 scripts/update_cs_data.py --pattern 688    # 仅更新科创板
    python3 scripts/update_cs_data.py --since 2025-05-10  # 强制从该日开始拉

部署建议（每个交易日 8:30 cron，开盘前更新）::

    30 8 * * 1-5 cd /path/to/KSS && python3 scripts/update_cs_data.py >> /tmp/kss_update.log 2>&1
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# cs_data_*.csv 列结构（必须与现有 CSV 一致才能被 FactorPipeline 消费）
EXPECTED_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
    "turnover_rate", "volume_ratio", "pe", "pb", "total_mv",
]


def update_one(
    csv_path: Path,
    client: TushareClient,
    since: str | None = None,
    end_date: str | None = None,
) -> tuple[int, str]:
    """增量更新单只股票.

    Returns:
        (新增行数, 最终最大日期 YYYY-MM-DD).
    """
    ts_code = csv_path.stem.replace("cs_data_", "") + ".SH"
    if "300" in ts_code.split(".")[0][:3] or "301" in ts_code.split(".")[0][:3]:
        ts_code = csv_path.stem.replace("cs_data_", "") + ".SZ"

    existing = pd.read_csv(csv_path)
    existing["trade_date"] = pd.to_datetime(existing["trade_date"])
    max_date = existing["trade_date"].max()

    # 起始日：since 优先，否则 max_date + 1 天
    if since is not None:
        start = since.replace("-", "")
    else:
        start = (max_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = (end_date or datetime.now().strftime("%Y%m%d")).replace("-", "")

    if start > end:
        return 0, max_date.strftime("%Y-%m-%d")  # 已是最新

    # 拉新数据
    daily = client.fetch_daily(ts_code, start, end)
    daily_basic = client.fetch_daily_basic(ts_code, start, end)
    if daily is None or daily.empty:
        return 0, max_date.strftime("%Y-%m-%d")

    # 合并 daily + daily_basic（按 trade_date 对齐）
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    if daily_basic is not None and not daily_basic.empty:
        daily_basic["trade_date"] = pd.to_datetime(daily_basic["trade_date"], format="%Y%m%d")
        merged = daily.merge(
            daily_basic[["trade_date", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv"]],
            on="trade_date", how="left",
        )
    else:
        merged = daily.copy()
        for col in ("turnover_rate", "volume_ratio", "pe", "pb", "total_mv"):
            if col not in merged.columns:
                merged[col] = float("nan")

    # 对齐期望列
    for col in EXPECTED_COLS:
        if col not in merged.columns:
            merged[col] = float("nan")
    merged = merged[EXPECTED_COLS]

    # 合并到现有 CSV：drop 重复行（按 trade_date 去重，保留新数据）
    existing_out = existing[EXPECTED_COLS].copy()
    existing_out["trade_date"] = pd.to_datetime(existing_out["trade_date"])
    combined = pd.concat([existing_out, merged], ignore_index=True)
    combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    combined = combined.sort_values("trade_date").reset_index(drop=True)

    # 写回（trade_date 格式化为 YYYY-MM-DD 与现有 CSV 一致）
    combined["trade_date"] = combined["trade_date"].dt.strftime("%Y-%m-%d")
    combined.to_csv(csv_path, index=False)

    n_new = len(combined) - len(existing)
    new_max = combined["trade_date"].max()
    return n_new, new_max


def main() -> None:
    parser = argparse.ArgumentParser(description="增量更新 cs_data_*.csv")
    parser.add_argument(
        "--pattern", type=str, default="",
        help="文件名子串过滤，例：'688'（仅科创板）",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="强制起始日期 YYYY-MM-DD（覆盖增量逻辑）",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="结束日期 YYYY-MM-DD（默认今天）",
    )
    parser.add_argument(
        "--throttle", type=float, default=0.6,
        help="每只之间 sleep 秒数（防 Tushare 频控；默认 0.6）",
    )
    args = parser.parse_args()

    pattern = f"cs_data_{args.pattern}*.csv" if args.pattern else "cs_data_*.csv"
    files = sorted((PROJECT_ROOT).glob(pattern))
    if not files:
        logger.error("未找到匹配 %s", pattern)
        sys.exit(1)

    logger.info("待更新 %d 个文件，throttle=%.1fs", len(files), args.throttle)
    client = TushareClient()

    n_updated = 0
    n_unchanged = 0
    n_failed = 0
    started = time.time()
    for i, f in enumerate(files, 1):
        try:
            n_new, max_date = update_one(f, client, since=args.since, end_date=args.end)
            if n_new > 0:
                logger.info("[%d/%d] %s +%d 行 → %s", i, len(files), f.name, n_new, max_date)
                n_updated += 1
            else:
                n_unchanged += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[%d/%d] %s 失败: %s", i, len(files), f.name, exc)
            n_failed += 1
        # Tushare 频控：免费版 ~120 次/分钟
        if args.throttle > 0 and i < len(files):
            time.sleep(args.throttle)

    elapsed = time.time() - started
    logger.info(
        "完成: 更新 %d / 未变 %d / 失败 %d，耗时 %.1fs",
        n_updated, n_unchanged, n_failed, elapsed,
    )
    if n_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
