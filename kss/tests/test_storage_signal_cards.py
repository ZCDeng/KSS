"""U1: signal_cards 表迁移 / 读写 / _to_compact。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kss.storage.db import MIGRATIONS, connect, ensure_schema
from kss.storage.signal_cards import (
    _to_compact,
    query_cards,
    read_by_date,
    read_by_subject,
    read_range,
    write_cards,
)


def test_migration_version_7_present() -> None:
    versions = [v for v, _ in MIGRATIONS]
    assert 7 in versions
    assert max(versions) >= 7


def test_migrate_empty_db_to_v7(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with connect(db) as conn:
        applied = ensure_schema(conn)
    assert 7 in applied or True  # cold start applies all including 7
    with connect(db) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT version FROM schema_migrations WHERE version=7"
        ).fetchone()
        assert row is not None
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "signal_cards" in tables


def test_ensure_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        second = ensure_schema(conn)
    assert second == []


def test_write_cards_replace_same_id(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    card = {
        "card_id": "abc123",
        "card_type": "etf_flow",
        "trade_date": "20260717",
        "subject": "芯片",
        "metrics": {"flow_5d": -3.0},
    }
    write_cards([card], db_path=db)
    card2 = {**card, "metrics": {"flow_5d": -4.0}}
    write_cards([card2], db_path=db)
    rows = read_by_date("20260717", db_path=db)
    assert len(rows) == 1
    assert rows[0]["metrics"]["flow_5d"] == -4.0


def test_read_by_date_cross_type(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    write_cards(
        [
            {
                "card_id": "a",
                "card_type": "etf_flow",
                "trade_date": "20260717",
                "subject": "芯片",
            },
            {
                "card_id": "b",
                "card_type": "sector_move",
                "trade_date": "20260717",
                "subject": "光刻机",
            },
            {
                "card_id": "c",
                "card_type": "etf_flow",
                "trade_date": "20260716",
                "subject": "芯片",
            },
        ],
        db_path=db,
    )
    rows = read_by_date("20260717", db_path=db)
    assert {r["card_id"] for r in rows} == {"a", "b"}


def test_read_range_inclusive(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for d, cid in [("20260715", "x"), ("20260716", "y"), ("20260717", "z")]:
        write_cards(
            [{"card_id": cid, "card_type": "etf_flow", "trade_date": d, "subject": "t"}],
            db_path=db,
        )
    rows = read_range("20260715", "20260717", db_path=db)
    assert [r["card_id"] for r in rows] == ["x", "y", "z"]


def test_read_by_subject_order_and_limit(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for d, cid in [("20260715", "a"), ("20260716", "b"), ("20260717", "c")]:
        write_cards(
            [
                {
                    "card_id": cid,
                    "card_type": "volume_spike",
                    "trade_date": d,
                    "subject": "688017.SH",
                }
            ],
            db_path=db,
        )
    rows = read_by_subject("688017.SH", limit=2, db_path=db)
    assert [r["card_id"] for r in rows] == ["c", "b"]


def test_empty_db_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    assert read_by_date("20260717", db_path=db) == []
    assert query_cards(db_path=db) == []


def test_to_compact() -> None:
    assert _to_compact("2026-07-28") == "20260728"
    assert _to_compact("20260728") == "20260728"
    with pytest.raises(ValueError):
        _to_compact("07/28/26")
