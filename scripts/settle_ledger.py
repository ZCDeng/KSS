#!/usr/bin/env python3
"""预测账本结算 driver —— 让账本从「只写影子」变「真实结算 + 归因」（U8 ①）.

`kss/prediction/ledger.py` 的 ``settle()`` / ``replay_paper_trade()`` 此前只有测试
驱动，无生产 cron → 真实记录的 ``realized_ret`` / 归因永远是 NULL。本脚本是那条缺失的
生产 driver：

  (a) ``--backfill``：把历史 ``storage/paper_trade/*.json`` 回放回填进账本（幂等，
      复用 ``replay_paper_trade``，不重造）；
  (b) ``--settle``：找所有 due-open 记录（``prediction_date`` 使 T+2 数据已存在于
      cs_data），用真实 **T+1 open / T+2 open** 调 ``ledger.settle()`` 结算 + 跑 F3 归因；
      价从 cs_data 取（**金融数字代码确定性渲染**，LLM 不碰数值）；超 ``stale_settle_days``
      个交易日仍缺数据 → ``mark_data_missing``（fail loud，不幻觉填值）。

PIT 纪律：账本结算价做快照（``t1_open`` / ``t2_open``），settle() 自带重结算守卫防
cs_data 漂移覆盖；本脚本不回流回测（只读 cs_data 结算 → 写账本，单向）。

用法::

    .venv-desktop/bin/python scripts/settle_ledger.py --backfill           # 回填历史 JSON
    .venv-desktop/bin/python scripts/settle_ledger.py --settle             # 结算所有 due-open
    .venv-desktop/bin/python scripts/settle_ledger.py --backfill --settle  # 回填 + 结算（日常）
    .venv-desktop/bin/python scripts/settle_ledger.py --settle --date 2026-06-18  # 指定 cutoff

cron（工作日收盘后，见 deploy/launchd/com.zcdeng.kss.ledger_settle.plist）::

    --backfill --settle --channel all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.prediction.ledger import (  # noqa: E402
    DEFAULT_STALE_SETTLE_DAYS,
    STATUS_OPEN,
    PredictionLedger,
    replay_paper_trade,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# PIT 结算价：cs_data 取 T+1 open / T+2 open（代码确定性渲染真值，LLM 不碰）
# --------------------------------------------------------------------------- #

# 进程内 cs_data 缓存：单次结算扫多条记录共用，避免重复 read_csv。
_CS_CACHE: dict[str, pd.DataFrame | None] = {}


def _read_cs(symbol: str) -> pd.DataFrame | None:
    """读 ``cs_data_{code6}.csv``（裸 6 位码），按 trade_date 升序；缺失/坏文件 → None."""
    code6 = symbol.split(".")[0]
    if code6 in _CS_CACHE:
        return _CS_CACHE[code6]
    path = PROJECT_ROOT / f"cs_data_{code6}.csv"
    df: pd.DataFrame | None = None
    if path.exists():
        try:
            raw = pd.read_csv(path, dtype={"trade_date": str})
            if "trade_date" in raw.columns and "open" in raw.columns:
                df = raw.sort_values("trade_date").reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cs_data 读取失败 %s: %s", path, exc)
            df = None
    _CS_CACHE[code6] = df
    return df


def t1_t2_open(symbol: str, prediction_date: str) -> tuple[float, float] | None:
    """PIT 取 ``(T+1 open, T+2 open)``：预测日之后的第 1 / 第 2 个交易日开盘价.

    与 ``_horizon_return(hold=1)`` 同口径（买 T+1 open、卖 T+2 open）。任一缺失 /
    非法（0 / NaN）→ None（settle() 据此保持 status=open，不误结算）。
    返回**原始价格**（非比值），由 settle() 渲染 realized_ret，全程代码判定。
    """
    df = _read_cs(symbol)
    if df is None:
        return None
    future = df[df["trade_date"] > prediction_date]
    if len(future) < 2:
        return None
    try:
        t1 = float(future.iloc[0]["open"])
        t2 = float(future.iloc[1]["open"])
    except (KeyError, ValueError, TypeError):
        return None
    if t1 == 0 or pd.isna(t1) or pd.isna(t2):
        return None
    return (t1, t2)


# --------------------------------------------------------------------------- #
# (a) 回填：历史 paper_trade JSON → 账本（幂等，复用 replay_paper_trade）
# --------------------------------------------------------------------------- #

def run_backfill(ledger: PredictionLedger) -> dict[str, int]:
    """回放历史 ``paper_trade/*.json`` 回填进账本（幂等；不在此处结算）.

    退役收口前置：回填完成后 JSON 视为只读审计/输入日志，分析以账本为准。
    """
    stats = replay_paper_trade(ledger, settle=False)
    logger.info(
        "回填完成：新入账 %d / 跳过(已存在) %d",
        stats["records"], stats["skipped"],
    )
    return stats


# --------------------------------------------------------------------------- #
# (b) 结算：所有 due-open 记录 → settle()（真实 T+1/T+2 open + F3 归因）
# --------------------------------------------------------------------------- #

def run_settle(
    ledger: PredictionLedger,
    cutoff_date: str,
    *,
    stale_settle_days: int = DEFAULT_STALE_SETTLE_DAYS,
) -> dict[str, Any]:
    """结算所有 ``prediction_date <= cutoff_date`` 的 open 记录.

    每条 due-open 记录：
      - cs_data 有 T+1/T+2 open → ``settle()`` 结算 + F3 归因（含 outcome / attribution）；
      - 价缺但**预测日距 cutoff 超 stale_settle_days** → ``mark_data_missing``（fail loud）；
      - 价缺且未超期 → 留 open，下次再试（T+2 可能还没到）。

    Args:
        cutoff_date: 结算队列上界（含），通常为最新可用交易日.
        stale_settle_days: open 超 N 个交易日仍缺数据 → data_missing（用日历日近似计数）.

    Returns:
        ``{"due": n, "settled": n, "data_missing": n, "still_open": n,
           "attribution": {category: count}}``.
    """
    due = ledger.open_due_for_settle(cutoff_date)
    n_settled = n_missing = n_open = 0
    attr_counts: dict[str, int] = {}
    cutoff_dt = pd.to_datetime(cutoff_date)

    for rec in due:
        pred_id = rec["prediction_id"]
        symbol = rec["symbol"]
        pred_date = rec["prediction_date"]
        prices = t1_t2_open(symbol, pred_date)
        if prices is not None:
            t1, t2 = prices
            # 结算日 regime hook：F3 的 regime_shift 需结算日 regime；账本入账已存
            # entry regime_label，结算日 regime 留作后续 hook（缺则该类别不触发，fail-safe）。
            settled = ledger.settle(pred_id, t1_open=t1, t2_open=t2)
            if settled.get("status") == "settled":
                n_settled += 1
                cat = settled.get("attribution_category") or "unknown"
                attr_counts[cat] = attr_counts.get(cat, 0) + 1
            else:
                n_open += 1
            continue
        # 价缺：超期升级 data_missing，否则留 open 等数据
        age_days = (cutoff_dt - pd.to_datetime(pred_date)).days
        if age_days > stale_settle_days:
            ledger.mark_data_missing(pred_id)
            n_missing += 1
            logger.warning(
                "结算缺数据且超期 %d 日 → data_missing: %s", age_days, pred_id
            )
        else:
            n_open += 1

    out = {
        "due": len(due),
        "settled": n_settled,
        "data_missing": n_missing,
        "still_open": n_open,
        "attribution": attr_counts,
    }
    logger.info(
        "结算完成：due %d → settled %d / data_missing %d / 仍 open %d；归因 %s",
        out["due"], n_settled, n_missing, n_open, attr_counts,
    )
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def _resolve_cutoff(arg_date: str | None) -> str:
    """结算 cutoff：显式 --date 优先；否则用今日（cs_data 自带 T+2 守卫，无需精确交易日）."""
    if arg_date:
        return arg_date
    return datetime.now().strftime("%Y-%m-%d")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="预测账本结算 driver（回填 + 结算 + 归因）")
    ap.add_argument("--backfill", action="store_true",
                    help="回放历史 paper_trade/*.json 回填进账本（幂等）")
    ap.add_argument("--settle", action="store_true",
                    help="结算所有 due-open 记录（真实 T+1/T+2 open + F3 归因）")
    ap.add_argument("--date", type=str, default=None,
                    help="结算 cutoff 日期 YYYY-MM-DD（默认今日；prediction_date <= cutoff 入队）")
    ap.add_argument("--stale-settle-days", type=int, default=DEFAULT_STALE_SETTLE_DAYS,
                    help=f"open 超 N 个交易日仍缺数据 → data_missing（默认 {DEFAULT_STALE_SETTLE_DAYS}）")
    ap.add_argument("--channel", type=str, default=None,
                    help="结算摘要推送通道：console / telegram / all（不给则不推）")
    args = ap.parse_args(argv)

    if not (args.backfill or args.settle):
        ap.error("至少给一个动作：--backfill 和/或 --settle")

    ledger = PredictionLedger()
    summary: dict[str, Any] = {}

    if args.backfill:
        summary["backfill"] = run_backfill(ledger)

    if args.settle:
        cutoff = _resolve_cutoff(args.date)
        summary["settle"] = run_settle(
            ledger, cutoff, stale_settle_days=args.stale_settle_days
        )
        summary["cutoff"] = cutoff

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.channel:
        _notify_summary(summary, args.channel)
    return 0


def _notify_summary(summary: dict[str, Any], channel: str) -> None:
    """结算摘要走 send_to_channels（数字代码渲染；告警失败不连坐）."""
    try:
        from kss.notifications.manager import send_to_channels

        lines = ["[账本结算]"]
        if "backfill" in summary:
            bf = summary["backfill"]
            lines.append(f"回填 新入账 {bf['records']} / 跳过 {bf['skipped']}")
        if "settle" in summary:
            st = summary["settle"]
            lines.append(
                f"结算 due {st['due']} → settled {st['settled']} / "
                f"data_missing {st['data_missing']} / 仍 open {st['still_open']}"
            )
            if st["attribution"]:
                attr = " ".join(f"{k}:{v}" for k, v in st["attribution"].items())
                lines.append(f"归因 {attr}")
        send_to_channels("\n".join(lines), channel=channel, title="预测账本结算")
    except Exception as exc:  # noqa: BLE001 —— 告警失败不打断结算
        logger.error("结算摘要推送失败: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
