"""可投资地图存储层测试(plan U2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kss.storage.db import MIGRATIONS, connect, ensure_schema
from kss.storage.investability import (
    QUESTION_COUNT,
    answers_updated_at,
    delete_labels,
    export_all,
    load_answers,
    load_answers_bulk,
    load_labels,
    load_labels_bulk,
    load_node_coverage,
    set_answer,
    set_labels,
    set_node_coverage,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """一个已建好 schema 的临时库."""
    p = tmp_path / "kss.db"
    with connect(p) as conn:
        ensure_schema(conn)
    return p


# --------------------------------------------------------------------------- #
# 迁移与表结构
# --------------------------------------------------------------------------- #


def test_migration_9_appended_and_applied(db: Path) -> None:
    """第 9 条迁移已追加, 且建出三张表."""
    versions = [v for v, _ in MIGRATIONS]
    assert versions == sorted(versions), "迁移版本必须递增"
    assert versions[-1] == 9
    with connect(db) as conn:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"stock_map_labels", "stock_exposure_answers", "map_node_coverage"} <= names


def test_tables_are_strict(db: Path) -> None:
    """三张表都是 STRICT: 往整数列写非数字文本被拒."""
    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO stock_map_labels (ts_code, node_id, is_primary, updated_at)"
                " VALUES ('688008.SH', 'compute.01', 'yes', '2026-08-09')"
            )


def test_no_foreign_key_to_watchlist(db: Path) -> None:
    """标注不随自选整表重写而消失(自选每次点星都 DELETE 再重写)."""
    set_labels("688008.SH", "compute.05", db_path=db)
    with connect(db) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM watchlist")
    assert len(load_labels("688008.SH", db_path=db)) == 1


def test_ensure_schema_is_idempotent(db: Path) -> None:
    """重复建表不重跑已应用迁移."""
    with connect(db) as conn:
        assert ensure_schema(conn) == []


# --------------------------------------------------------------------------- #
# 个股标注
# --------------------------------------------------------------------------- #


def test_set_and_load_labels_primary_first(db: Path) -> None:
    """写一主一副后读回, 主节点排最前且标记唯一."""
    set_labels("688008.SH", "compute.05", ["telecom.03"], db_path=db)
    labels = load_labels("688008.SH", db_path=db)
    assert [(x.node_id, x.is_primary) for x in labels] == [
        ("compute.05", True),
        ("telecom.03", False),
    ]
    assert all(x.updated_at for x in labels)


def test_replacing_primary_leaves_exactly_one(db: Path) -> None:
    """改主节点后旧主不残留, 仍恰有一个主节点."""
    set_labels("688008.SH", "compute.05", ["telecom.03"], db_path=db)
    set_labels("688008.SH", "compute.04", db_path=db)
    labels = load_labels("688008.SH", db_path=db)
    assert len(labels) == 1
    assert labels[0].node_id == "compute.04"
    assert sum(1 for x in labels if x.is_primary) == 1


def test_primary_uniqueness_enforced_by_schema(db: Path) -> None:
    """绕开写函数直插第二个主节点被数据库拒绝, 不靠应用层自觉."""
    set_labels("688008.SH", "compute.05", db_path=db)
    with connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stock_map_labels (ts_code, node_id, is_primary, updated_at)"
            " VALUES ('688008.SH', 'compute.04', 1, '2026-08-09')"
        )


def test_secondary_dedup_and_primary_collision(db: Path) -> None:
    """副节点去重, 且与主节点重复的被剔除."""
    set_labels(
        "688008.SH",
        "compute.05",
        ["telecom.03", "telecom.03", "compute.05"],
        db_path=db,
    )
    labels = load_labels("688008.SH", db_path=db)
    assert len(labels) == 2


def test_delete_labels_returns_to_unlabelled(db: Path) -> None:
    """删除全部标注后该票回到未上图态."""
    set_labels("688008.SH", "compute.05", db_path=db)
    delete_labels("688008.SH", db_path=db)
    assert load_labels("688008.SH", db_path=db) == []


def test_empty_primary_clears_labels(db: Path) -> None:
    """主节点传空串等同于清空该票标注."""
    set_labels("688008.SH", "compute.05", ["telecom.03"], db_path=db)
    set_labels("688008.SH", "", db_path=db)
    assert load_labels("688008.SH", db_path=db) == []


def test_load_labels_bulk(db: Path) -> None:
    """批量读返回按代码分组的字典, 未标注的代码不出现在键里."""
    set_labels("688008.SH", "compute.05", db_path=db)
    set_labels("688981.SH", "infotech.03", ["infotech.04"], db_path=db)
    out = load_labels_bulk(
        ["688008.SH", "688981.SH", "000001.SZ", "688008.SH"], db_path=db
    )
    assert set(out) == {"688008.SH", "688981.SH"}
    assert len(out["688981.SH"]) == 2
    assert out["688981.SH"][0].is_primary is True


def test_load_labels_bulk_empty_input(db: Path) -> None:
    """空代码列表返回空字典, 不发查询."""
    assert load_labels_bulk([], db_path=db) == {}


# --------------------------------------------------------------------------- #
# 8 问答案
# --------------------------------------------------------------------------- #


def test_load_answers_never_recorded(db: Path) -> None:
    """从未录入的票八题全为 None."""
    answers = load_answers("688008.SH", db_path=db)
    assert len(answers) == QUESTION_COUNT
    assert set(answers.values()) == {None}


def test_set_single_answer_leaves_others_blank(db: Path) -> None:
    """只写第 3 题时, 第 3 题有值其余七题仍为 None."""
    set_answer("688008.SH", 3, True, db_path=db)
    answers = load_answers("688008.SH", db_path=db)
    assert answers[3] is True
    assert [answers[i] for i in (1, 2, 4, 5, 6, 7, 8)] == [None] * 7


def test_overwrite_answer_updates_timestamp(db: Path) -> None:
    """改答案就地覆盖旧值, 更新时间随之变化(plan KTD11)."""
    set_answer("688008.SH", 3, True, db_path=db)
    first = answers_updated_at("688008.SH", db_path=db)
    set_answer("688008.SH", 3, False, db_path=db)
    assert load_answers("688008.SH", db_path=db)[3] is False
    assert answers_updated_at("688008.SH", db_path=db) >= first


def test_unknown_stored_as_null(db: Path) -> None:
    """选「未知」与未答在库里都是 NULL, 都不计入已定题数."""
    set_answer("688008.SH", 1, True, db_path=db)
    set_answer("688008.SH", 1, None, db_path=db)
    assert load_answers("688008.SH", db_path=db)[1] is None


def test_question_index_out_of_range(db: Path) -> None:
    """题号越界抛 ValueError."""
    for bad in (0, 9, -1):
        with pytest.raises(ValueError):
            set_answer("688008.SH", bad, True, db_path=db)


def test_load_answers_bulk(db: Path) -> None:
    """批量读 8 问返回按代码分组的字典."""
    set_answer("688008.SH", 1, True, db_path=db)
    set_answer("688981.SH", 8, False, db_path=db)
    out = load_answers_bulk(["688008.SH", "688981.SH", "000001.SZ"], db_path=db)
    assert set(out) == {"688008.SH", "688981.SH"}
    assert out["688008.SH"][1] is True
    assert out["688981.SH"][8] is False


# --------------------------------------------------------------------------- #
# 节点覆盖确认
# --------------------------------------------------------------------------- #


def test_node_coverage_confirm_and_revoke(db: Path) -> None:
    """确认无标的后可读出, 撤销后回到未核(键里消失)."""
    assert load_node_coverage(db_path=db) == {}
    set_node_coverage("water.03", True, note="全市场无标的", db_path=db)
    cov = load_node_coverage(db_path=db)
    assert set(cov) == {"water.03"}
    assert cov["water.03"]
    set_node_coverage("water.03", False, db_path=db)
    assert load_node_coverage(db_path=db) == {}


def test_node_coverage_reconfirm_updates_time(db: Path) -> None:
    """重复确认同一节点是更新而不是插入失败."""
    set_node_coverage("water.03", True, db_path=db)
    first = load_node_coverage(db_path=db)["water.03"]
    set_node_coverage("water.03", True, note="复核过", db_path=db)
    assert load_node_coverage(db_path=db)["water.03"] >= first


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


def test_export_all_shape(db: Path) -> None:
    """全量导出含三张表原始行与导出时间, 不做任何聚合."""
    set_labels("688008.SH", "compute.05", ["telecom.03"], db_path=db)
    set_answer("688008.SH", 2, True, db_path=db)
    set_node_coverage("water.03", True, db_path=db)
    dump = export_all(db_path=db)
    assert set(dump) == {"exportedAt", "labels", "answers", "nodeCoverage"}
    assert len(dump["labels"]) == 2
    assert len(dump["answers"]) == 1
    assert len(dump["nodeCoverage"]) == 1
    assert dump["answers"][0]["q2"] == 1
    assert dump["exportedAt"]


def test_export_empty_db(db: Path) -> None:
    """空库导出返回三个空列表而不是报错."""
    dump = export_all(db_path=db)
    assert dump["labels"] == []
    assert dump["answers"] == []
    assert dump["nodeCoverage"] == []
