#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日终指标 Signal Pack 批跑：遍历注册表 active + kind=primitive 条目，刷各自固化时的标的.

与 ``run_mi_signal_pack.py`` 平行但不重叠——MI 是 kind=mi_legacy，走自己的既有 cron，
本脚本明确跳过它。

零固化回退（plan 2026-07-20-001 KTD2）：条目 ``symbols`` 为空且从未固化
（``solidified_at`` 为空）时，回退当前自选股池——sr 等装好即 active 的条目首次批跑
即可产出信号，不必等手动固化。已固化但 symbols 被清空的条目仍按现状跳过，不外溢到
全自选股池（避免任何未来手工注册/异常清空的条目被静默拉去跑全池）。

用法::

    .venv/bin/python scripts/run_indicator_signal_pack.py
    .venv/bin/python scripts/run_indicator_signal_pack.py --asof 2026-07-10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.backtest.indicator_walk_forward import WFConfig
from kss.indicators import ledger_bridge
from kss.indicators import pack as ipack
from kss.indicators.registry import KIND_PRIMITIVE, active_entries, state_root
from kss.storage.watchlist import load_watchlist


def main() -> int:
    ap = argparse.ArgumentParser(description="指标 Signal Pack 日终批跑（基元库条目）")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--train-window", type=int, default=252)
    ap.add_argument("--retrain-freq", type=int, default=20)
    args = ap.parse_args()

    entries = [e for e in active_entries() if e.kind == KIND_PRIMITIVE]
    if not entries:
        print("ℹ️  注册表内无 active 的基元库指标，跳过")
        return 0

    cfg = WFConfig(train_window=args.train_window, retrain_freq=args.retrain_freq)
    total_ok = total_skip = total_err = 0
    watchlist_cache: list[str] | None = None
    for entry in entries:
        symbols = entry.symbols or []
        if not symbols:
            if entry.solidified_at:
                print(f"⏭  {entry.id}: 已固化但标的列表为空，跳过")
                continue
            if watchlist_cache is None:
                watchlist_cache = load_watchlist(db_path=state_root() / "storage" / "kss.db")
            if not watchlist_cache:
                print(f"⏭  {entry.id}: 未固化且自选股池为空，跳过")
                continue
            symbols = watchlist_cache
            print(f"ℹ️  {entry.id}: 未固化，回退自选股池")
        print(f"📦 {entry.id} ({entry.family}) | n={len(symbols)}")
        last_asof: str | None = None
        for sym in symbols:
            try:
                pack = ipack.run_entry_pack(entry, sym, asof=args.asof, cfg=cfg)
            except Exception as exc:  # noqa: BLE001 — 单票故障不拖垮整池
                total_err += 1
                print(f"  ❌ {sym}  exception: {exc}")
                continue
            status = pack.get("status")
            if status == "ok":
                total_ok += 1
                last_asof = pack.get("asof") or last_asof
                print(f"  ✅ {pack['symbol']}  {pack.get('action')}  pred={pack.get('pred_score')}")
                try:
                    ledger_bridge.record_pack(entry, pack)
                except Exception as exc:  # noqa: BLE001 — 账本写入失败不阻断 pack 产出
                    print(f"  ⚠️  {sym} 账本写入失败: {exc}")
            elif status == "skipped":
                total_skip += 1
                print(f"  ⏭  {pack['symbol']}  skipped: {pack.get('reason')}")
            else:
                total_err += 1
                print(f"  ❌ {pack['symbol']}  {status}: {pack.get('reason')}")

        window_end = args.asof or last_asof
        if window_end is None:
            print(f"  ⏭  {entry.id}: 本轮无成功 pack，跳过健康度刷新")
            continue
        try:
            snap = ledger_bridge.refresh_factor_health(entry, window_end)
            if snap is not None:
                print(f"  📊 {entry.id} sign_proxy n={snap.n_periods} icir={snap.icir:.3f}")
        except Exception as exc:  # noqa: BLE001 — 健康度刷新失败不阻断 pack 产出
            print(f"  ⚠️  {entry.id} 健康度刷新失败: {exc}")

    print(f"完成 ok={total_ok} skip={total_skip} err={total_err}")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
