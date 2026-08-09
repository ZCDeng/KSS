"""可投资地图的个股标注、8 问答案与节点覆盖确认读写(plan U2).

三张表都是人工在界面上录入的判断, 是 KSS 里第一份不可再生数据 —— 没有任何
cron 能重跑出来. 因此:

- 写入一律带 ``updated_at``, 便于事后追溯改动时间.
- 提供 :func:`export_all` 全量导出, 让重签打包或追加迁移之前能先留一份拷贝.
- 主节点唯一性由 schema 上的部分唯一索引兜底, 不只靠写函数.

判定逻辑(区位、配额、陈旧度)不在这里, 在桥接层(plan KTD2). 本模块只做取数与落库.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

#: 8 问的列名, 顺序与源文 5.7 的题号一致.
QUESTION_COLUMNS: tuple[str, ...] = ("q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8")

#: 8 问题数.
QUESTION_COUNT = len(QUESTION_COLUMNS)


def _now() -> str:
    """当前 UTC 时间的 ISO 8601 串."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class StockLabel:
    """一只票挂在一个节点上的标注.

    Attributes:
        ts_code: 股票代码.
        node_id: 节点标识.
        is_primary: 是否主节点. 主节点决定该股显示的行业色(plan R6).
        updated_at: 最后更新时间.
    """

    ts_code: str
    node_id: str
    is_primary: bool
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        """转字典(桥接返回值用)."""
        return {
            "tsCode": self.ts_code,
            "nodeId": self.node_id,
            "isPrimary": self.is_primary,
            "updatedAt": self.updated_at,
        }


# --------------------------------------------------------------------------- #
# 个股标注
# --------------------------------------------------------------------------- #


def load_labels(
    ts_code: str,
    db_path: str | Path | None = None,
) -> list[StockLabel]:
    """读一只票的全部标注; 未标注返回空列表. 主节点排在最前."""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT ts_code, node_id, is_primary, updated_at
            FROM stock_map_labels WHERE ts_code = ?
            ORDER BY is_primary DESC, node_id
            """,
            (ts_code,),
        ).fetchall()
    return [
        StockLabel(
            ts_code=r["ts_code"],
            node_id=r["node_id"],
            is_primary=bool(r["is_primary"]),
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


def load_labels_bulk(
    ts_codes: list[str],
    db_path: str | Path | None = None,
) -> dict[str, list[StockLabel]]:
    """按代码列表批量读标注.

    推荐页与信号卡的候选来自全市场排名, 不在自选里; 逐只发一次桥接调用会有
    几十次往返, 所以四处落点共用这一个批量读(plan U6).

    Args:
        ts_codes: 股票代码列表; 空列表返回空字典.
        db_path: 库路径; ``None`` 走默认.

    Returns:
        ``{ts_code: [StockLabel, ...]}``; 未标注的代码不出现在键里.
    """
    codes = [c for c in dict.fromkeys(ts_codes) if c]
    if not codes:
        return {}
    placeholders = ",".join("?" * len(codes))
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT ts_code, node_id, is_primary, updated_at
            FROM stock_map_labels WHERE ts_code IN ({placeholders})
            ORDER BY ts_code, is_primary DESC, node_id
            """,
            codes,
        ).fetchall()
    out: dict[str, list[StockLabel]] = {}
    for r in rows:
        out.setdefault(r["ts_code"], []).append(
            StockLabel(
                ts_code=r["ts_code"],
                node_id=r["node_id"],
                is_primary=bool(r["is_primary"]),
                updated_at=r["updated_at"],
            )
        )
    return out


def set_labels(
    ts_code: str,
    primary_node_id: str,
    secondary_node_ids: list[str] | None = None,
    db_path: str | Path | None = None,
) -> None:
    """整体替换一只票的标注(先删后写).

    整表替换语义与自选列表一致: 标注代表「当前态」, 用户改主节点后旧主不该
    残留. 主节点唯一性另有 schema 上的部分唯一索引兜底.

    Args:
        ts_code: 股票代码.
        primary_node_id: 主节点; 空串视为清空该票全部标注.
        secondary_node_ids: 副节点列表, 可空. 与主节点重复的会被去掉.
        db_path: 库路径.
    """
    if not primary_node_id:
        delete_labels(ts_code, db_path=db_path)
        return
    seconds = [
        n for n in dict.fromkeys(secondary_node_ids or []) if n and n != primary_node_id
    ]
    now = _now()
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM stock_map_labels WHERE ts_code = ?", (ts_code,))
        conn.execute(
            "INSERT INTO stock_map_labels (ts_code, node_id, is_primary, updated_at)"
            " VALUES (?, ?, 1, ?)",
            (ts_code, primary_node_id, now),
        )
        for node_id in seconds:
            conn.execute(
                "INSERT INTO stock_map_labels (ts_code, node_id, is_primary, updated_at)"
                " VALUES (?, ?, 0, ?)",
                (ts_code, node_id, now),
            )


def delete_labels(ts_code: str, db_path: str | Path | None = None) -> None:
    """删除一只票的全部标注, 使其回到未上图态."""
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM stock_map_labels WHERE ts_code = ?", (ts_code,))


# --------------------------------------------------------------------------- #
# 8 问答案
# --------------------------------------------------------------------------- #


def load_answers(
    ts_code: str,
    db_path: str | Path | None = None,
) -> dict[int, bool | None]:
    """读一只票的 8 问答案.

    Returns:
        ``{1..8: True | False | None}``; 从未录入过的票八题全为 ``None``.
        ``True`` 表示答「是」(高暴露), ``False`` 表示答「否」, ``None`` 表示
        未答或选了「未知」—— 两者在库里都是 NULL, 都不计入已定题数.
    """
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            f"SELECT {', '.join(QUESTION_COLUMNS)} FROM stock_exposure_answers"
            " WHERE ts_code = ?",
            (ts_code,),
        ).fetchone()
    if row is None:
        return {i: None for i in range(1, QUESTION_COUNT + 1)}
    return {
        i: (None if row[col] is None else bool(row[col]))
        for i, col in enumerate(QUESTION_COLUMNS, start=1)
    }


def load_answers_bulk(
    ts_codes: list[str],
    db_path: str | Path | None = None,
) -> dict[str, dict[int, bool | None]]:
    """按代码列表批量读 8 问答案; 未录入的代码不出现在键里."""
    codes = [c for c in dict.fromkeys(ts_codes) if c]
    if not codes:
        return {}
    placeholders = ",".join("?" * len(codes))
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            f"SELECT ts_code, {', '.join(QUESTION_COLUMNS)}"
            f" FROM stock_exposure_answers WHERE ts_code IN ({placeholders})",
            codes,
        ).fetchall()
    return {
        r["ts_code"]: {
            i: (None if r[col] is None else bool(r[col]))
            for i, col in enumerate(QUESTION_COLUMNS, start=1)
        }
        for r in rows
    }


def set_answer(
    ts_code: str,
    question: int,
    value: bool | None,
    db_path: str | Path | None = None,
) -> None:
    """写一只票的单题答案.

    Args:
        ts_code: 股票代码.
        question: 题号, 1 到 8.
        value: ``True`` 是, ``False`` 否, ``None`` 未知或清空.
        db_path: 库路径.

    Raises:
        ValueError: 题号越界.
    """
    if not 1 <= question <= QUESTION_COUNT:
        raise ValueError(f"题号必须在 1 到 {QUESTION_COUNT} 之间, 收到 {question}")
    col = QUESTION_COLUMNS[question - 1]
    stored = None if value is None else int(bool(value))
    now = _now()
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO stock_exposure_answers (ts_code, updated_at)"
            " VALUES (?, ?) ON CONFLICT(ts_code) DO NOTHING",
            (ts_code, now),
        )
        conn.execute(
            f"UPDATE stock_exposure_answers SET {col} = ?, updated_at = ?"
            " WHERE ts_code = ?",
            (stored, now, ts_code),
        )


def answers_updated_at(
    ts_code: str,
    db_path: str | Path | None = None,
) -> str:
    """读一只票 8 问答案的最后更新时间; 未录入返回空串."""
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT updated_at FROM stock_exposure_answers WHERE ts_code = ?",
            (ts_code,),
        ).fetchone()
    return row["updated_at"] if row else ""


# --------------------------------------------------------------------------- #
# 节点覆盖确认
# --------------------------------------------------------------------------- #


def load_node_coverage(db_path: str | Path | None = None) -> dict[str, str]:
    """读全部已人工确认无标的的节点.

    Returns:
        ``{node_id: confirmed_at}``. 不在键里的节点是「未核」, 与「已确认无
        标的」是两个不同的态(plan R9) —— 只有后者计入无暴露结论.
    """
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT node_id, confirmed_at FROM map_node_coverage"
        ).fetchall()
    return {r["node_id"]: r["confirmed_at"] for r in rows}


def set_node_coverage(
    node_id: str,
    confirmed: bool,
    note: str = "",
    db_path: str | Path | None = None,
) -> None:
    """把一个节点标成已确认无标的, 或撤销该确认.

    Args:
        node_id: 节点标识.
        confirmed: ``True`` 记为已确认无标的, ``False`` 撤销回未核.
        note: 可选备注.
        db_path: 库路径.
    """
    with connect(db_path) as conn:
        ensure_schema(conn)
        if not confirmed:
            conn.execute("DELETE FROM map_node_coverage WHERE node_id = ?", (node_id,))
            return
        conn.execute(
            "INSERT INTO map_node_coverage (node_id, confirmed_at, note)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(node_id) DO UPDATE SET confirmed_at = excluded.confirmed_at,"
            " note = excluded.note",
            (node_id, _now(), note or None),
        )


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


def export_all(db_path: str | Path | None = None) -> dict[str, Any]:
    """全量导出三张表的原始行, 供重签打包或追加迁移之前留档.

    这份数据没有任何上游可以重跑出来, 导出是唯一的第二份拷贝.

    Returns:
        ``{"exportedAt", "labels", "answers", "nodeCoverage"}``, 值都是原始行
        的字典列表, 不做任何判定或聚合.
    """
    with connect(db_path) as conn:
        ensure_schema(conn)
        labels = [
            dict(r)
            for r in conn.execute(
                "SELECT ts_code, node_id, is_primary, updated_at FROM stock_map_labels"
                " ORDER BY ts_code, is_primary DESC, node_id"
            ).fetchall()
        ]
        answers = [
            dict(r)
            for r in conn.execute(
                f"SELECT ts_code, {', '.join(QUESTION_COLUMNS)}, updated_at"
                " FROM stock_exposure_answers ORDER BY ts_code"
            ).fetchall()
        ]
        coverage = [
            dict(r)
            for r in conn.execute(
                "SELECT node_id, confirmed_at, note FROM map_node_coverage"
                " ORDER BY node_id"
            ).fetchall()
        ]
    return {
        "exportedAt": _now(),
        "labels": labels,
        "answers": answers,
        "nodeCoverage": coverage,
    }
