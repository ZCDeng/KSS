"""Signal Pack 存储 — kss.db mi_signal_packs / indicator_signal_packs 表
（plan 2026-07-12-005 / U15 割接自 storage/mi_signals/**/*.json 与
storage/indicator_signals/<entry_id>/**/*.json）。

两表都没有物理 latest/ 副本——「最新」改用 ``ORDER BY asof DESC LIMIT 1`` 派生，
比旧版「每次写入顺带覆盖 latest/ 文件」更正确（天然容错乱序补跑，不依赖写入顺序）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def write_mi_pack(pack: dict[str, Any], db_path: Path) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO mi_signal_packs (asof, symbol, payload_json, created_at) VALUES (?,?,?,?)",
            (pack["asof"], pack["symbol"], json.dumps(pack, ensure_ascii=False, default=str), None),
        )


def read_mi_pack(symbol: str, asof: str | None, db_path: Path) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        if asof is not None:
            row = conn.execute(
                "SELECT payload_json FROM mi_signal_packs WHERE symbol=? AND asof=?",
                (symbol, asof),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload_json FROM mi_signal_packs WHERE symbol=? ORDER BY asof DESC LIMIT 1",
                (symbol,),
            ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def write_indicator_pack(entry_id: str, pack: dict[str, Any], db_path: Path) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO indicator_signal_packs "
            "(entry_id, asof, symbol, payload_json, created_at) VALUES (?,?,?,?,?)",
            (entry_id, pack["asof"], pack["symbol"], json.dumps(pack, ensure_ascii=False, default=str), None),
        )


def read_indicator_pack(entry_id: str, symbol: str, asof: str | None, db_path: Path) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        if asof is not None:
            row = conn.execute(
                "SELECT payload_json FROM indicator_signal_packs WHERE entry_id=? AND symbol=? AND asof=?",
                (entry_id, symbol, asof),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload_json FROM indicator_signal_packs WHERE entry_id=? AND symbol=? "
                "ORDER BY asof DESC LIMIT 1",
                (entry_id, symbol),
            ).fetchone()
    return json.loads(row["payload_json"]) if row else None
