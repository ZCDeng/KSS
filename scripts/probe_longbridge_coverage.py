#!/usr/bin/env python3
"""Longbridge 覆盖扫描探针（U2 / R3 / KTD5）：实测 KSS 全池谁走长桥、谁回东财.

对每个标的跑一次 ``LongbridgeProvider.fetch_quote``：有数据 → covered，无返回/error
→ route_to_eastmoney。北交所（``.BJ``）静态归东财，不打网。产物是一份带 ``scanned_at``
的机读 manifest（原子写），供 :func:`kss.data.longbridge_coverage.route_provider` 消费。

**密钥卫生（security-lens P2）**：凭据**只从 env 读**（``LONGBRIDGE_*``），绝不 inline；
manifest 产物**不含任何凭据**。

**运维动作，非 CI**：打真网、需凭据。核心 :func:`scan_coverage` 接受可注入 provider +
symbols，便于单测（不 live）。

手动跑：
    LONGBRIDGE_APP_KEY=... LONGBRIDGE_APP_SECRET=... LONGBRIDGE_ACCESS_TOKEN=... \\
      .venv/bin/python scripts/probe_longbridge_coverage.py \\
        --out kss/data/longbridge_coverage.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.data.intraday_client import LongbridgeProvider  # noqa: E402
from kss.data.longbridge_coverage import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    RESCAN_INTERVAL_DAYS,
    is_beijing_exchange,
    normalize_symbol,
)

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def scan_coverage(
    provider: Any,
    symbols: Iterable[str],
    *,
    scanned_at: str | None = None,
) -> dict[str, Any]:
    """逐标的探覆盖，产出 manifest dict（可注入 provider，便于单测，不 live）.

    北交所静态归东财，不调 provider（省额度 + 实测确认无返回）。
    """
    scanned_at = scanned_at or datetime.now(tz=_SHANGHAI_TZ).isoformat(timespec="seconds")
    covered: list[str] = []
    route_em: list[str] = []
    for raw in symbols:
        sym = normalize_symbol(raw)
        if is_beijing_exchange(sym):
            route_em.append(sym)
            continue
        res = provider.fetch_quote(sym)
        if res.ok:
            covered.append(sym)
        else:
            route_em.append(sym)
    return {
        "schemaVersion": 1,
        "scanned_at": scanned_at,
        "rescan_interval_days": RESCAN_INTERVAL_DAYS,
        "source": "scripts/probe_longbridge_coverage.py 全池实测",
        "notes": [
            "ChinaConnect LV1 实时（陆股通池）；covered = 实测 fetch_quote 有返回",
            "北交所（.BJ）静态归东财，未打网",
            f"scanned_at 超 {RESCAN_INTERVAL_DAYS} 天视为可能陈旧（陆股通季度调整）",
        ],
        # 排序 + 去重，确定性产物（便于 diff 审阅）。
        "covered": sorted(set(covered)),
        "route_to_eastmoney": sorted(set(route_em)),
    }


def _load_symbols(args: argparse.Namespace) -> list[str]:
    """从库读注册标的，或从 --symbols-file 读（每行一个）。"""
    if args.symbols_file:
        text = Path(args.symbols_file).read_text(encoding="utf-8")
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    # 默认：从 intraday_store 读 longbridge/eastmoney 注册标的。
    from kss.config.paths import INTRADAY_DB  # noqa: PLC0415
    from kss.data.intraday_store import IntradayStore  # noqa: PLC0415

    db_path = Path(args.db) if args.db else INTRADAY_DB
    store = IntradayStore(db_path)
    syms: list[str] = []
    for provider_name in ("longbridge", "eastmoney_akshare"):
        try:
            syms.extend(s for s, _ak in store.list_registered_symbols(provider_name))
        except Exception:  # noqa: BLE001 — provider 未注册即空
            continue
    return syms


def _write_atomic(manifest: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_path)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Longbridge 覆盖扫描（生成路由 manifest）")
    p.add_argument(
        "--out",
        default=str(DEFAULT_MANIFEST_PATH),
        help="manifest 输出路径（JSON，原子写；默认 kss/data/longbridge_coverage.json）",
    )
    p.add_argument("--db", default=None, help="库路径（默认 INTRADAY_DB）")
    p.add_argument(
        "--symbols-file",
        default=None,
        help="标的清单文件（每行一个）；缺省时从库读注册标的",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    # fail-loud：缺凭据直接报错，不静默产空 manifest。
    missing = [
        k for k in ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"[FATAL] 缺凭据（env）：{', '.join(missing)}", file=sys.stderr)
        return 2

    symbols = _load_symbols(args)
    if not symbols:
        print("[FATAL] 无标的可扫（库空 or --symbols-file 空）", file=sys.stderr)
        return 1

    provider = LongbridgeProvider()
    manifest = scan_coverage(provider, symbols)
    out_path = Path(args.out)
    _write_atomic(manifest, out_path)
    print(
        f"[ok] 覆盖 manifest → {out_path}  "
        f"covered={len(manifest['covered'])} "
        f"route_to_eastmoney={len(manifest['route_to_eastmoney'])} "
        f"scanned_at={manifest['scanned_at']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
