#!/usr/bin/env python3
"""分时收盘采集器（U2 薄 logger 起步；U5 加固为完整收盘采集器）.

**U2 范围（薄前向 raw-capture，立即起采，决策 D3）**：对 registry 里注册过的标的，
fail-closed 解析（0 活跃 → 跳过零调用、>1 → 终止 ``mapping_ambiguous``），serial 取
当场 session，单事务 append 到 ``IntradayStore``。**不做 canonical**（U3）、**不做**
trade_cal 终止语义 / 窗内追补 / retention（U5）。

凭据从不进 plist；token 解析推迟到接 Tushare 路径时（U2 只跑东财前向，无需 token）。

手动测试：
    .venv-desktop/bin/python scripts/collect_intraday.py --mode close --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.config.paths import INTRADAY_DB  # noqa: E402
from kss.data.intraday_client import (  # noqa: E402
    EastmoneyAkshareProvider,
    IntradayProvider,
    classify_eligibility,
)
from kss.data.intraday_store import (  # noqa: E402
    IntradayStore,
    MappingStatus,
    ObservationInput,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _today_shanghai() -> str:
    """今天的横杠日期 ``YYYY-MM-DD``（KTD7）。U5 会换成 trade_cal 确认的交易日。"""
    return datetime.now(tz=_SHANGHAI).strftime("%Y-%m-%d")


def collect_close(
    store: IntradayStore,
    provider: IntradayProvider,
    *,
    trade_date: str,
    interval_minutes: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """收盘采集一场（U2 薄 logger）。返回机读汇总 dict。

    fail-closed：每符号先 :meth:`resolve_instrument`——unknown 跳过（**零 provider
    调用**）、ambiguous 终止整 run（``mapping_ambiguous``，**零调用**）、resolved 才取数。
    """
    symbols = store.list_registered_symbols(provider.name)
    plan = [{"symbol": s, "asset_kind": k} for s, k in symbols]

    if dry_run:
        return {
            "mode": "close",
            "dry_run": True,
            "trade_date": trade_date,
            "provider": provider.name,
            "planned_symbols": plan,
            "status": "planned",
        }

    observations: list[ObservationInput] = []
    skipped: list[str] = []
    for symbol, asset_kind in symbols:
        res = store.resolve_instrument(symbol, provider.name, trade_date)
        if res.status is MappingStatus.UNKNOWN:
            skipped.append(symbol)  # 零 provider 调用
            continue
        if res.status is MappingStatus.AMBIGUOUS:
            # 终止整 run，零 provider 调用（KTD2 失败闭合）。
            run_id = store.record_terminal_failure(
                provider=provider.name,
                mode="close",
                trade_date=trade_date,
                failure_reason="mapping_ambiguous",
                error_summary=f"symbol {symbol} has >1 active mapping",
            )
            return {
                "mode": "close",
                "trade_date": trade_date,
                "provider": provider.name,
                "status": "failed",
                "failure_reason": "mapping_ambiguous",
                "run_id": run_id,
                "exit_code": 1,
            }
        # resolved：取当场 session（serial）。
        fetched = provider.fetch_bars(
            res.provider_symbol,
            interval_minutes=interval_minutes,
            asset_kind=asset_kind,
        )
        eligibility = classify_eligibility(provider.name, reachable=fetched.ok)
        observations.append(
            ObservationInput(
                instrument_id=res.instrument_id,
                provider=provider.name,
                interval_minutes=interval_minutes,
                request_meta={
                    "method": "GET",
                    "provider": provider.name,
                    "params": {
                        "symbol": res.provider_symbol,
                        "asset_kind": asset_kind,
                        "period": interval_minutes,
                    },
                },
                payload_rows=fetched.rows or None,
                source_asof_ts=fetched.source_asof_ts,
                availability_class="forward_observed",
                eligibility=eligibility.value,
                status_code=fetched.status_code,
                error=fetched.error,
            )
        )

    result = store.ingest_run(
        provider=provider.name,
        mode="close",
        trade_date=trade_date,
        observations=observations,
    )
    return {
        "mode": "close",
        "trade_date": trade_date,
        "provider": provider.name,
        "status": result.status,
        "failure_reason": result.failure_reason,
        "run_id": result.run_id,
        "requested_symbols": len(symbols),
        "skipped_symbols": skipped,
        "observations_written": result.observations_written,
        "blobs_written": result.blobs_written,
        "exit_code": 0 if result.status == "completed" else 1,
    }


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="分时收盘采集器（U2 薄 logger）")
    p.add_argument("--mode", choices=["close"], default="close",
                   help="采集模式（U2 仅 close；watch 在 U5）")
    p.add_argument("--trade-date", default=None,
                   help="交易日 YYYY-MM-DD（默认今天；U5 走 trade_cal 确认）")
    p.add_argument("--interval", type=int, default=1, help="分钟周期（默认 1）")
    p.add_argument("--dry-run", action="store_true", help="只规划不取数不落盘")
    p.add_argument("--db", default=None, help="库路径（默认 INTRADAY_DB / STATE_ROOT）")
    return p


def main(argv: list[str] | None = None) -> int:
    import json

    args = build_argparser().parse_args(argv)
    db_path = Path(args.db) if args.db else INTRADAY_DB
    store = IntradayStore(db_path)
    provider = EastmoneyAkshareProvider()
    trade_date = args.trade_date or _today_shanghai()

    summary = collect_close(
        store, provider,
        trade_date=trade_date,
        interval_minutes=args.interval,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return int(summary.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
