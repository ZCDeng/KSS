#!/usr/bin/env python3
"""全市场 SW L1 行业映射回填 —— P2 部门轮换打分的底座 (plan 007).

把 Tushare ``stock_basic`` 返回的 ``industry`` 字段（实际为申万一级中文名）
落地到 :data:`kss.config.paths.INDUSTRY_MAP_PARQUET`，供
:func:`kss.macro.rotation.load_industry_map` 给 ``scan_combo_signals`` 每只
入场候选打 rotation_score 用.

数据源策略（按可用性降级）：

1. **首选** Tushare ``stock_basic(fields='ts_code,industry')`` —— 5000+ 全市场
   一次拉完，``industry`` 字段中文，与 :mod:`kss.macro.rotation` yaml 配置直接对齐.
2. **兜底** 读 ``storage/stock_names.csv`` —— 当 Tushare 配额 / 网络异常时
   仍能拼出 ~600 KCB 股的 SW L2/L3 颗粒度映射（``score_industry_fit``
   有前缀匹配，可向上 fallback 到一级）.

用法::

    python3 scripts/backfill_industry_map.py                # Tushare 优先
    python3 scripts/backfill_industry_map.py --csv-only     # 强制走 csv 兜底（离线 / 测试）
    python3 scripts/backfill_industry_map.py --exchange SSE # 仅拉上交所

覆盖率目标 ≥ 95% A 股。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.config.paths import INDUSTRY_MAP_PARQUET, STORAGE_ROOT  # noqa: E402
from kss.macro.pipeline import atomic_to_parquet  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


_STOCK_NAMES_CSV = STORAGE_ROOT / "stock_names.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--exchange", type=str, default="",
                   help="仅拉单交易所 SSE / SZSE，默认全市场")
    p.add_argument("--csv-only", action="store_true",
                   help="跳过 Tushare，仅用 storage/stock_names.csv 兜底")
    p.add_argument(
        "--out", type=Path, default=INDUSTRY_MAP_PARQUET,
        help=f"输出 parquet 路径（默认 {INDUSTRY_MAP_PARQUET}）",
    )
    return p.parse_args()


def fetch_from_tushare(exchange: str) -> pd.DataFrame | None:
    """尝试从 Tushare ``stock_basic`` 拿全市场 ``ts_code + industry``.

    Returns:
        含 ``ts_code`` / ``sw_l1_name`` 的 DataFrame；失败 / 空返回 ``None``.
    """
    try:
        from kss.data.tushare_client import TushareClient
    except ImportError as exc:
        logger.warning("无法 import TushareClient: %s", exc)
        return None
    try:
        client = TushareClient()
    except Exception as exc:    # noqa: BLE001
        logger.warning("初始化 TushareClient 失败 (token 缺失？): %s", exc)
        return None

    parts: list[pd.DataFrame] = []
    listed = client.fetch_stock_basic(exchange=exchange, list_status="L")
    if listed is not None and not listed.empty:
        parts.append(listed)
    else:
        logger.warning("Tushare 在市清单为空")

    # 退市股也带 industry 字段；并入避免 risk_filter 那边 ts_code 对不上
    delisted = client.fetch_stock_basic(exchange=exchange, list_status="D")
    if delisted is not None and not delisted.empty:
        parts.append(delisted)

    if not parts:
        return None

    df = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["ts_code"], keep="last",
    )
    if "industry" not in df.columns:
        logger.warning("Tushare stock_basic 返回缺 industry 列")
        return None
    out = df[["ts_code", "industry"]].copy()
    out = out.rename(columns={"industry": "sw_l1_name"})
    out["ts_code"] = out["ts_code"].astype(str)
    out["sw_l1_name"] = out["sw_l1_name"].astype(str).str.strip()
    out = out[out["sw_l1_name"].notna() & (out["sw_l1_name"] != "") & (out["sw_l1_name"] != "nan")]
    return out.reset_index(drop=True)


def fetch_from_csv(csv_path: Path) -> pd.DataFrame | None:
    """从 ``storage/stock_names.csv`` 兜底（KCB 约 600 只）."""
    if not csv_path.exists():
        logger.error("兜底 csv 不存在: %s", csv_path)
        return None
    try:
        df = pd.read_csv(csv_path, usecols=["ts_code", "industry"], encoding="utf-8")
    except Exception as exc:    # noqa: BLE001
        logger.error("读 %s 失败: %s", csv_path, exc)
        return None
    df["ts_code"] = df["ts_code"].astype(str)
    df["industry"] = df["industry"].astype(str).str.strip()
    df = df[df["industry"].notna() & (df["industry"] != "") & (df["industry"] != "nan")]
    df = df.rename(columns={"industry": "sw_l1_name"})
    df = df.drop_duplicates(subset=["ts_code"], keep="last")
    return df[["ts_code", "sw_l1_name"]].reset_index(drop=True)


def main() -> int:
    args = parse_args()

    df: pd.DataFrame | None = None
    if not args.csv_only:
        logger.info("尝试从 Tushare stock_basic 拉全市场 industry ...")
        df = fetch_from_tushare(args.exchange)
        if df is not None and not df.empty:
            logger.info("Tushare 拿到 %d 行", len(df))

    if df is None or df.empty:
        logger.info("降级 csv 兜底: %s", _STOCK_NAMES_CSV)
        df = fetch_from_csv(_STOCK_NAMES_CSV)

    if df is None or df.empty:
        logger.error("两源都失败，无 industry_map 可写")
        return 1

    atomic_to_parquet(df, args.out)

    a_share_total = (
        df["ts_code"].str.endswith(".SH").sum()
        + df["ts_code"].str.endswith(".SZ").sum()
    )
    n_unique_industries = df["sw_l1_name"].nunique()
    logger.info(
        "industry_map_swl1 落地 %s (%d 行，A 股 %d 只，%d 个独立行业)",
        args.out, len(df), a_share_total, n_unique_industries,
    )

    # 覆盖率提示（plan 007 验收：≥ 95% A 股）
    if a_share_total < 4000:
        logger.warning(
            "A 股覆盖 %d，低于 plan 验收 4000 阈值 —— 可能 Tushare 配额受限走了 csv 兜底",
            a_share_total,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
