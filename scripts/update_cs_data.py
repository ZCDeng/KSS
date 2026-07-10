#!/usr/bin/env python3
"""增量更新所有 cs_data_*.csv 到最新交易日.

补 `kss update` 命令的空壳——`kss/cli/commands/update.py` 当前只 echo 不实际拉数据.
本脚本直接用 :class:`TushareClient` 拉日线 OHLCV + daily_basic（市值/PE/PB），
按 ``cs_data_<code>.csv`` 格式写盘，与现有 `paper_trade_log_mv.py` 兼容.

**运行态数据**：根目录 ``cs_data_*.csv`` 已 gitignore，只由本脚本 / cron
``run_update_data_daily.sh`` 写盘；不要 ``git add`` 进库（``checkout`` 会冲掉日更）。

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
_KSS_STATE = Path(__import__("os").environ.get("KSS_STATE_ROOT") or PROJECT_ROOT)  # U1: cs_data 重定向
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient, _fetch_with_retry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# cs_data_*.csv 列结构（必须与现有 CSV 一致才能被 FactorPipeline 消费）
EXPECTED_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
    "turnover_rate", "volume_ratio", "pe", "pb", "total_mv",
]


def _merge_to_expected(daily: pd.DataFrame, daily_basic: pd.DataFrame | None) -> pd.DataFrame:
    """daily + daily_basic → EXPECTED_COLS 对齐的 df (trade_date 为 datetime)。

    update_one 与 ensure_history 共用, 保证两条路径列结构一致。
    """
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    if daily_basic is not None and not daily_basic.empty:
        daily_basic = daily_basic.copy()
        daily_basic["trade_date"] = pd.to_datetime(daily_basic["trade_date"], format="%Y%m%d")
        merged = daily.merge(
            daily_basic[["trade_date", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv"]],
            on="trade_date", how="left",
        )
    else:
        merged = daily
    for col in EXPECTED_COLS:
        if col not in merged.columns:
            merged[col] = float("nan")
    return merged[EXPECTED_COLS]


def _list_date(client: TushareClient, ts_code: str, exch: str) -> str:
    """证券上市日 YYYYMMDD; 取不到 → 回退固定早期日 (覆盖 STAR/ChiNext/BJ 全历史)。"""
    exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(exch)
    try:
        basic = client.fetch_stock_basic(exchange=exchange)
        if basic is not None and not basic.empty:
            hit = basic[basic["ts_code"] == ts_code]
            if len(hit) and pd.notna(hit.iloc[0].get("list_date")):
                return str(hit.iloc[0]["list_date"])
    except Exception as exc:  # noqa: BLE001  上市日取不到不阻断, 用早期日兜底
        logger.warning("  list_date 取失败 %s: %s", ts_code, exc)
    return "20180101"  # STAR(2019)/BJ(2021) 均晚于此; ChiNext 老股早于此则少拿早期段 (复盘只用近年)


def ensure_history(
    code: str,
    exch: str,
    client: TushareClient | None = None,
    end_date: str | None = None,
) -> tuple[int, str]:
    """U2: 新股 cs_data 缺失时按上市日全量回填 (净新增, 非复用增量 update_one)。

    cs_data 存在 → no-op (增量交给 update_one / daily_review 自身)。
    缺失 → fetch_stock_basic 查上市日 → range-fetch daily+daily_basic → EXPECTED_COLS
    → **原子写** (.tmp 后 os.replace, 防半截 csv 被后续误判"存在")。

    Returns:
        (写入行数, 状态)；状态 ∈ {"exists", "written", "empty"}。
    """
    if exch not in ("SH", "SZ", "BJ"):
        raise ValueError(f"非法交易所后缀: {exch!r}")
    csv_path = _KSS_STATE / f"cs_data_{code}.csv"
    if csv_path.exists():
        return 0, "exists"

    client = client or TushareClient()
    ts_code = f"{code}.{exch}"
    start = _list_date(client, ts_code, exch)
    end = (end_date or datetime.now().strftime("%Y%m%d")).replace("-", "")

    daily = client.fetch_daily(ts_code, start, end)
    if daily is None or daily.empty:
        logger.warning("  ensure_history %s 无数据 (start=%s)", ts_code, start)
        return 0, "empty"
    daily_basic = client.fetch_daily_basic(ts_code, start, end)
    merged = _merge_to_expected(daily, daily_basic).sort_values("trade_date").reset_index(drop=True)
    merged["trade_date"] = merged["trade_date"].dt.strftime("%Y-%m-%d")

    tmp = csv_path.with_suffix(".csv.tmp")
    merged.to_csv(tmp, index=False)
    import os as _os
    _os.replace(tmp, csv_path)  # 原子: 半截写入不会留下被误判存在的 csv
    logger.info("  ensure_history %s 回填 %d 行 → %s", ts_code, len(merged), csv_path.name)
    return len(merged), "written"


_NAME_INDEX_PATH = _KSS_STATE / "storage" / "macro" / "stock_name_index.json"
_NAME_META_CACHE: dict | None = None
_NAME_INDEX_WARNED = False


def _infer_kind_from_code(ts_code: str) -> str:
    """名称索引不可用时的保守证券类型兜底。

    股票前缀优先按 stock；常见沪深 ETF/基金前缀按 fund；沪深京指数前缀按 index。
    不能识别的代码仍回退 stock，避免对普通股票误走基金/指数 API。
    """
    code, _, exch = ts_code.partition(".")
    exch = exch.upper()
    if len(code) != 6 or exch not in {"SH", "SZ", "BJ"}:
        return "stock"

    if exch == "SH":
        if code.startswith(("600", "601", "603", "605", "688", "689")):
            return "stock"
        if code.startswith((
            "510", "511", "512", "513", "515", "516", "517", "518", "519",
            "520", "522", "560", "561", "562", "563", "588", "589",
        )):
            return "fund"
        if code.startswith(("000", "880", "881", "882", "883", "884", "885", "886")):
            return "index"
    elif exch == "SZ":
        if code.startswith(("000", "001", "002", "003", "300", "301")):
            return "stock"
        if code.startswith((
            "150", "159", "160", "161", "162", "163", "164", "165", "166",
            "167", "168", "169", "184",
        )):
            return "fund"
        if code.startswith(("399", "980")):
            return "index"
    elif exch == "BJ":
        if code.startswith("899"):
            return "index"
        if code.startswith((
            "430", "830", "831", "832", "833", "834", "835", "836", "837",
            "838", "839", "870", "871", "872", "873", "920",
        )):
            return "stock"
    return "stock"


def _kind_for(ts_code: str) -> str:
    """从名称索引取证券类型 (stock/fund/index)，缺失时按代码段保守推断。

    决定增量取数走 daily(+daily_basic) / fund_daily / index_daily：ETF/基金与指数
    在 Tushare 不走 `daily`（返回空），且无 daily_basic 的 pe/pb/换手。
    """
    global _NAME_META_CACHE, _NAME_INDEX_WARNED
    if _NAME_META_CACHE is None:
        import json

        try:
            _NAME_META_CACHE = json.loads(
                _NAME_INDEX_PATH.read_text(encoding="utf-8")
            ).get("meta", {})
        except Exception:  # noqa: BLE001
            _NAME_META_CACHE = {}
            if not _NAME_INDEX_WARNED:
                logger.warning(
                    "名称索引不可用，按代码段推断证券类型: %s",
                    _NAME_INDEX_PATH,
                )
                _NAME_INDEX_WARNED = True
    indexed = (_NAME_META_CACHE.get(ts_code) or {}).get("kind")
    if indexed in {"stock", "fund", "index"}:
        return indexed
    return _infer_kind_from_code(ts_code)


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
    existing = pd.read_csv(csv_path)

    # 交易所后缀优先从 ts_code 列恢复（ensure_history 回填的 SZ/BJ 才不会被
    # 增量误请求成 .SH）；列缺失时退化到文件名前缀推断（仅识别 300/301→SZ）。
    code = csv_path.stem.replace("cs_data_", "")
    ts_code = ""
    if "ts_code" in existing.columns and len(existing):
        ts_code = str(existing["ts_code"].iloc[-1]).strip()
    if not ts_code or "." not in ts_code:
        head = code[:3]
        exch = "SZ" if head in ("300", "301") else "SH"
        ts_code = f"{code}.{exch}"

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

    # 拉新数据：按证券类型选 API —— 股票走 daily + daily_basic，ETF/基金走 fund_daily，
    # 指数走 index_daily（后两者无 daily_basic，对齐 EXPECTED_COLS 时填 NaN）。
    kind = _kind_for(ts_code)
    if kind == "fund":
        pro = client.get_pro()
        daily = _fetch_with_retry(
            lambda: pro.fund_daily(ts_code=ts_code, start_date=start, end_date=end),
            f"fund_daily {ts_code}",
        )
        daily_basic = None
    elif kind == "index":
        pro = client.get_pro()
        daily = _fetch_with_retry(
            lambda: pro.index_daily(ts_code=ts_code, start_date=start, end_date=end),
            f"index_daily {ts_code}",
        )
        daily_basic = None
    else:
        daily = client.fetch_daily(ts_code, start, end)
        daily_basic = client.fetch_daily_basic(ts_code, start, end)
    if daily is None or daily.empty:
        # 空返回曾被静默记为「未变」，导致 6-26 之后缺口积压而不报警。
        gap_days = (pd.Timestamp(end[:4] + "-" + end[4:6] + "-" + end[6:8]) - max_date).days
        if gap_days >= 3:
            logger.warning(
                "%s 增量拉取为空，本地仍停在 %s（距 end=%s 约 %d 日）",
                csv_path.name,
                max_date.strftime("%Y-%m-%d"),
                end,
                gap_days,
            )
        return 0, max_date.strftime("%Y-%m-%d")

    # 合并 daily + daily_basic + 对齐 EXPECTED_COLS（与 ensure_history 共用）
    merged = _merge_to_expected(daily, daily_basic)

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
    files = sorted((_KSS_STATE).glob(pattern))
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

    end_s = (args.end or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    end_ts = pd.Timestamp(f"{end_s[:4]}-{end_s[4:6]}-{end_s[6:8]}")
    stale = _scan_stale_cs_files(files, end_ts, min_gap_days=4)
    status_path = _KSS_STATE / "storage" / "logs" / "cron" / "update_cs_data_last.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status = {
        "finishedAt": datetime.now().isoformat(timespec="seconds"),
        "end": end_ts.strftime("%Y-%m-%d"),
        "updated": n_updated,
        "unchanged": n_unchanged,
        "failed": n_failed,
        "staleCount": len(stale),
        "stale": stale[:40],
        "elapsedSec": round(elapsed, 1),
    }
    status_path.write_text(
        __import__("json").dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if n_failed > 0:
        sys.exit(1)
    if stale:
        logger.error(
            "ALERT stale_gap: %d 只 cs_data 落后 end=%s ≥4 日（示例 %s）→ %s",
            len(stale),
            end_ts.strftime("%Y-%m-%d"),
            ", ".join(s["file"] for s in stale[:8]),
            status_path,
        )
        sys.exit(2)  # 2 = 完成但有缺口；wrapper 不重试整批，只告警


def _scan_stale_cs_files(
    files: list[Path],
    end_ts: pd.Timestamp,
    min_gap_days: int = 4,
) -> list[dict[str, object]]:
    """扫描本地 max(trade_date) 落后 end 超过 min_gap_days 的文件。"""
    out: list[dict[str, object]] = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["trade_date"])
            if df.empty:
                continue
            mx = pd.to_datetime(df["trade_date"]).max()
            gap = int((end_ts - mx).days)
            if gap >= min_gap_days:
                out.append({
                    "file": f.name,
                    "maxDate": mx.strftime("%Y-%m-%d"),
                    "gapDays": gap,
                })
        except Exception as exc:  # noqa: BLE001
            out.append({"file": f.name, "error": str(exc)})
    return out


if __name__ == "__main__":
    main()
