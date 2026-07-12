"""ts_code → name/industry/concept 参考表（plan 2026-07-12-005 / U15 域割接自 stock_names.csv）.

纯参考数据，无程序化写入方（人工维护）——``set_stock_names`` 只给迁移脚本/测试用，
生产读路径只用 ``load_stock_names``。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kss.storage.db import connect, ensure_schema

_COLUMNS = ("ts_code", "name", "industry", "concept")


def load_stock_names(db_path: str | Path | None = None) -> pd.DataFrame:
    """返回列 ts_code/name/industry/concept 的 DataFrame（dtype str，空值填 ''）。

    库/表不存在或为空 → 空 DataFrame（保留列名，调用方可安全 `.columns` 判断，不用
    额外 try/except）。
    """
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM stock_names").fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=list(_COLUMNS))
    return df.fillna("").astype(str)


def set_stock_names(rows: list[dict[str, str]], db_path: str | Path | None = None) -> None:
    """整表替换（参考表是当前态）；供迁移脚本/测试写入，生产无调用方。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM stock_names")
        for r in rows:
            conn.execute(
                "INSERT INTO stock_names (ts_code, name, industry, concept) VALUES (?,?,?,?)",
                (r.get("ts_code"), r.get("name"), r.get("industry"), r.get("concept")),
            )
