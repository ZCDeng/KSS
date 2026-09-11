#!/usr/bin/env python3
"""Live probe for HiThink endpoints. Reads key from env/secrets only.

Writes:
  - storage/reports/hithink_probe/*.json  (git-ignored raw-ish summaries)
  - kss/tests/fixtures/hithink/*.json      (redacted, committed)

Never prints the API key.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.config.paths import STATE_ROOT  # noqa: E402
from kss.data.hithink_client import HithinkClient, ymd_to_date_ms  # noqa: E402
from kss.security.redaction import redact_text  # noqa: E402

P0_SYMBOLS = ("688008.SH", "300750.SZ", "920735.BJ")
LEGACY_BJ = "830799.BJ"
FIXTURE_DIR = PROJECT_ROOT / "kss" / "tests" / "fixtures" / "hithink"
PROBE_DIR = STATE_ROOT / "storage" / "reports" / "hithink_probe"


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _redact_obj(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in {"request_id"}:
                out[k] = "redacted"
            else:
                out[k] = _redact_obj(v, key)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v, key) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, known_secrets=(key,)) or obj
    return obj


def _trim_items(data: Any, n: int = 3) -> Any:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for field in ("item", "stock_items", "hot_money_items"):
        rows = out.get(field)
        if isinstance(rows, list) and len(rows) > n:
            out[field] = rows[:n]
            out[f"{field}_truncated"] = True
            out[f"{field}_original_count"] = len(rows)
    return out


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _p0_longbridge(symbol: str) -> dict[str, Any]:
    try:
        from kss.data.intraday_client import LongbridgeProvider  # noqa: PLC0415

        res = LongbridgeProvider().fetch_quote(symbol)
        if not res.ok:
            return {"error": res.error or "empty", "last_done": None}
        row = res.rows[0] if res.rows else {}
        return {
            "last_done": row.get("last_done"),
            "source_asof_ts": res.source_asof_ts,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__, "last_done": None}


def main() -> int:
    client = HithinkClient()
    if not client.api_key:
        print(json.dumps({"error": "not_configured"}))
        return 2
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    key = client.api_key
    summary: dict[str, Any] = {"probed_at": _now(), "endpoints": {}}

    def record(name: str, data: Any, *, fixture: str | None, keep: int = 3) -> None:
        ok = data is not None
        summary["endpoints"][name] = {"ok": ok}
        if not ok:
            return
        raw_path = PROBE_DIR / f"{name}.json"
        _write(raw_path, _redact_obj(data, key))
        if fixture:
            trimmed = _trim_items(data if isinstance(data, dict) else {"data": data}, keep)
            _write(FIXTURE_DIR / fixture, _redact_obj(trimmed, key))

    # Weekend today-pool is empty; persist last session (YYYYMMDD) so fixtures have rows.
    prev = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    # Saturday/Sunday → Friday 2026-09-11 in this run; still pass date_ms explicitly.
    if datetime.now(ZoneInfo("Asia/Shanghai")).weekday() >= 5:
        prev = "20260911"
    record(
        "limit_up_pool",
        client.get_limit_up_pool(date_ms=ymd_to_date_ms(prev), size=50, page=1),
        fixture="limit_up_pool.json",
    )
    record(
        "anomaly_analysis_list",
        client.get_anomaly_analysis_list(tag_codes="LIMIT_UP,SHARP_RISE"),
        fixture="anomaly_analysis_list.json",
    )
    record("dragon_tiger_list", client.get_dragon_tiger_list(board_type="all"), fixture="dragon_tiger_list.json")
    auction_codes = ["600519.SH", "300750.SZ", "688008.SH"]
    record(
        "auction_live",
        client.get_auction_snapshot(auction_codes, stage="live"),
        fixture="auction_live.json",
        keep=3,
    )
    record(
        "auction_final",
        client.get_auction_snapshot(auction_codes, stage="final"),
        fixture="auction_final.json",
        keep=3,
    )

    snap = client.get_prices_snapshot(list(P0_SYMBOLS))
    record("prices_snapshot_p0", snap, fixture="prices_snapshot_p0.json", keep=5)
    items = (snap or {}).get("item") if isinstance(snap, dict) else []
    by_code = {str(r.get("thscode")): r for r in items if isinstance(r, dict)}
    p0: dict[str, Any] = {"probed_at": _now(), "symbols": {}, "legacy_bj": LEGACY_BJ}
    for sym in P0_SYMBOLS:
        ht = by_code.get(sym) or {}
        lb = _p0_longbridge(sym)
        p0["symbols"][sym] = {
            "hithink_last_price": ht.get("last_price"),
            "hithink_price_change_ratio_pct": ht.get("price_change_ratio_pct"),
            "longbridge": lb,
        }
    legacy_snap = client.get_prices_snapshot([LEGACY_BJ])
    p0["legacy_bj_snapshot_ok"] = legacy_snap is not None
    p0["legacy_bj_error"] = (client.last_error or {}).get("message") if legacy_snap is None else None
    bj_price = (p0["symbols"].get("920735.BJ") or {}).get("hithink_last_price")
    weekday = datetime.now(ZoneInfo("Asia/Shanghai")).weekday()  # 0=Mon
    hour = datetime.now(ZoneInfo("Asia/Shanghai")).hour
    in_session = weekday < 5 and 9 <= hour < 15
    bj_ok = isinstance(bj_price, (int, float))
    if in_session and bj_ok:
        verdict, reason = "GO", ".BJ last_price present during session"
    else:
        verdict, reason = "NO-GO", (
            ".BJ last_price missing" if not bj_ok
            else "not in auction/continuous trading session; weekend or off-hours last_price is not live proof"
        )
    p0["bj_has_last_price"] = bj_ok
    p0["in_session"] = in_session
    p0["u7_verdict"] = verdict
    p0["u7_reason"] = reason
    _write(PROBE_DIR / "p0_delay.json", p0)
    summary["p0"] = {
        "bj_has_last_price": bj_ok,
        "in_session": in_session,
        "u7_verdict": verdict,
        "u7_reason": reason,
    }
    _write(PROBE_DIR / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
