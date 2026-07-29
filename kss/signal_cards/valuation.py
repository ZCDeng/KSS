"""估值/持仓卡：perilla_enrich_cache，按 cached_at 触发（非按交易日倍增）。"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from kss.config.paths import KSS_DB
from kss.signal_cards.common import base_card
from kss.storage.db import connect, ensure_schema
from kss.storage.signal_cards import _to_compact


def _latest_pe(payload: str) -> float | None:
    try:
        reader = csv.DictReader(io.StringIO(payload))
        rows = list(reader)
    except Exception:
        return None
    if not rows:
        return None
    last = rows[-1]
    for key in ("pe_ttm", "pe"):
        raw = (last.get(key) or "").strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def _holders_summary(payload: str) -> dict[str, Any]:
    try:
        reader = csv.DictReader(io.StringIO(payload))
        rows = list(reader)
    except Exception:
        return {"row_count": 0}
    return {
        "row_count": len(rows),
        "latest_end_date": (rows[-1].get("end_date") if rows else None),
        "latest_holder_count_proxy": len(rows),
    }


def generate_for_cached_snapshot(
    *,
    db_path: str | Path | None = None,
    extra_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """读 perilla_enrich_cache：A 股 holders/pe 产卡；us_peer 跳过。

    trade_date = cached_at 转紧凑；同一 cached_at 幂等。
    extra_symbols 中不在缓存的 → not_in_list 卡。
    """
    path = Path(db_path) if db_path is not None else KSS_DB
    with connect(path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT ts_code, kind, payload, cached_at FROM perilla_enrich_cache "
            "WHERE kind IN ('holders', 'pe')"
        ).fetchall()

    by_code: dict[str, dict[str, Any]] = {}
    for r in rows:
        ts_code = r["ts_code"]
        # A 股形态：含 .SH/.SZ/.BJ
        if "." not in ts_code:
            continue
        entry = by_code.setdefault(ts_code, {"cached_at": r["cached_at"]})
        if r["cached_at"] and (
            not entry.get("cached_at") or r["cached_at"] > entry["cached_at"]
        ):
            entry["cached_at"] = r["cached_at"]
        entry[r["kind"]] = r["payload"]

    cards: list[dict[str, Any]] = []
    seen = set(by_code.keys())

    for ts_code, data in sorted(by_code.items()):
        cached_at = data.get("cached_at") or ""
        if not cached_at:
            cards.append(
                base_card(
                    card_type="valuation",
                    trade_date="19700101",
                    subject=ts_code,
                    rule_id="valuation_no_cached_at",
                    metrics={},
                    threshold_source="none",
                    coverage="insufficient_data",
                    direction=None,
                )
            )
            continue
        td = _to_compact(cached_at) if "-" in cached_at else cached_at
        pe = _latest_pe(data["pe"]) if data.get("pe") else None
        holders = _holders_summary(data["holders"]) if data.get("holders") else {}
        coverage = "covered" if (pe is not None or holders.get("row_count")) else "insufficient_data"
        cards.append(
            base_card(
                card_type="valuation",
                trade_date=td,
                subject=ts_code,
                rule_id="valuation_perilla_snapshot",
                metrics={
                    "pe": pe,
                    "holders": holders,
                    "cached_at": cached_at,
                },
                threshold_source="none",
                coverage=coverage,
                data_as_of=td,
                direction=None,
            )
        )

    for sym in extra_symbols or []:
        if sym in seen:
            continue
        # 名单外：not_in_list
        cards.append(
            base_card(
                card_type="valuation",
                trade_date="19700101",
                subject=sym,
                rule_id="valuation_not_in_list",
                metrics={},
                threshold_source="none",
                coverage="not_in_list",
                direction=None,
            )
        )
    return cards


def generate_for_date(
    trade_date: str, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """按日入口：仅当 trade_date 等于某 cached_at 时返回对应估值卡，否则空。

    避免按交易日倍增；pipeline 每日调用安全。
    """
    td = _to_compact(trade_date) if "-" in trade_date else trade_date
    all_cards = generate_for_cached_snapshot(db_path=db_path)
    return [c for c in all_cards if c["trade_date"] == td and c["coverage"] != "not_in_list"]
