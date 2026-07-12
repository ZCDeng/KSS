#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日终 MI Signal Pack 批跑（自选 / 指定 symbols）.

用法::

    .venv/bin/python scripts/run_mi_signal_pack.py
    .venv/bin/python scripts/run_mi_signal_pack.py --symbols 688017.SH,688322.SH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.backtest.mi_walk_forward import WFConfig
from kss.strategies.mi_pack import load_rules, run_symbol_pack


def load_watchlist(path: Path | None) -> list[str]:
    """自选列表：``path`` 为 None 时读 kss.db（plan 2026-07-12-005 / U15 割接自 txt 文件）；
    传显式路径时仍读该文件（测试/临时对照用）。"""
    if path is None:
        from kss.storage.watchlist import load_watchlist as _load_db  # noqa: PLC0415

        return [s.upper() for s in _load_db()]
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.upper())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="MI Signal Pack 日终批跑")
    ap.add_argument("--symbols", default=None, help="逗号分隔 ts_code")
    ap.add_argument(
        "--watchlist",
        default=None,
        help="显式指定 txt 文件路径（默认读 kss.db watchlist 表）",
    )
    ap.add_argument("--asof", default=None)
    ap.add_argument("--train-window", type=int, default=252)
    ap.add_argument("--retrain-freq", type=int, default=20)
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_watchlist(Path(args.watchlist) if args.watchlist else None)
    if not symbols:
        print("❌ 无自选/symbols")
        return 1

    rules = load_rules(ROOT / "storage" / "mi_rules.yaml")
    cfg = WFConfig(
        train_window=args.train_window,
        retrain_freq=args.retrain_freq,
    )
    print(f"📦 MI Signal Pack | n={len(symbols)} | train={cfg.train_window}")
    ok = skip = err = 0
    for sym in symbols:
        pack = run_symbol_pack(
            sym, asof=args.asof, rules=rules, root=ROOT, cfg=cfg
        )
        st = pack.get("status")
        if st == "ok":
            ok += 1
            print(
                f"  ✅ {pack['symbol']}  {pack.get('action')}  "
                f"N={pack.get('n')}  unpinned={pack.get('unpinned')}"
            )
        elif st == "skipped":
            skip += 1
            print(f"  ⏭  {pack['symbol']}  skipped: {pack.get('reason')}")
        else:
            err += 1
            print(f"  ❌ {pack['symbol']}  {st}: {pack.get('reason')}")
    print(f"完成 ok={ok} skip={skip} err={err}")
    print(f"latest → {ROOT / 'storage' / 'mi_signals' / 'latest'}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
