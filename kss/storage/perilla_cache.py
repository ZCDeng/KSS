"""紫苏叶富化缓存 — kss.db perilla_enrich_cache 表（plan 2026-07-12-005 / U15 割接自
storage/perilla_cache/*.csv，顺带把从未真正落地过的 storage/us_peer_cache/*.json
路径并轨进同一张表）。

主键 (ts_code, kind)：kind=holders/pe 时 ts_code 是 A 股 ts_code、payload 是 CSV 文本
（kss.perilla_enrich.aggregate._cached_df 用）；kind=us_peer 时 ts_code 借用存美股
ticker（含汇率对 "CNY=X"）、payload 是 JSON 文本（kss.perilla_enrich.us_peer 用）。
两类 kind 的 key 空间天然不交（A 股 ts_code 形如 688012.SH，美股 ticker 无此后缀），
payload 统一 TEXT 存，格式由 kind 决定，调用方自行序列化/反序列化。
"""

from __future__ import annotations

from pathlib import Path

from kss.storage.db import connect, ensure_schema


def read_cache_entry(ts_code: str, kind: str, db_path: Path) -> tuple[str, str | None] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload, cached_at FROM perilla_enrich_cache WHERE ts_code=? AND kind=?",
            (ts_code, kind),
        ).fetchone()
    if row is None:
        return None
    return row["payload"], row["cached_at"]


def write_cache_entry(ts_code: str, kind: str, payload: str, cached_at: str | None, db_path: Path) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload, cached_at) VALUES (?,?,?,?)",
            (ts_code, kind, payload, cached_at),
        )
