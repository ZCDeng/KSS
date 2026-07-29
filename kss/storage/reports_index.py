"""reports_index 写入 helper — signal weekly 等报告登记（plan 2026-07-28-002 / U1）.

表已在迁移 v1 建好；此前仅 migrate_storage 导入历史文件。本模块提供运行时 upsert。
"""

from __future__ import annotations

from pathlib import Path

from kss.storage.db import connect, ensure_schema


def record_signal_weekly(
    path: str | Path,
    *,
    report_name: str | None = None,
    category: str = "signal_weekly",
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """登记周报到 reports_index；同 report_name 删除后重插（upsert）。"""
    p = Path(path)
    name = report_name or p.name
    file_path = str(path)
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM reports_index WHERE report_name=?", (name,))
        conn.execute(
            "INSERT INTO reports_index (report_name, file_path, category, generated_at) "
            "VALUES (?,?,?,?)",
            (name, file_path, category, generated_at),
        )
