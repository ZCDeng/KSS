"""U14 测试：kss/storage/db.py 连接工厂/迁移器 + scripts/migrate_storage.py 导入器.

跑：uv run pytest kss/tests/test_storage_db.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from kss.storage.db import MIGRATIONS, connect, ensure_schema  # noqa: E402
import migrate_storage as ms  # noqa: E402


# --------------------------------------------------------------------------- #
# ① 两进程（用两线程模拟）并发写不 lock 死
# --------------------------------------------------------------------------- #


def test_concurrent_writes_do_not_deadlock(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)

    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            with connect(db_path) as conn:
                for i in range(20):
                    conn.execute(
                        "INSERT OR REPLACE INTO watchlist (ts_code, added_at) VALUES (?, ?)",
                        (f"{n}{i:03d}.SH", None),
                    )
        except sqlite3.OperationalError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"并发写触发锁错误（WAL/busy_timeout 未生效）: {errors}"
    with connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) c FROM watchlist").fetchone()["c"]
    assert n == 80  # 4 线程 × 20 行，全部落地无丢失


# --------------------------------------------------------------------------- #
# ② 每域导入后行数=源文件记录数、抽样字段逐值相等
# --------------------------------------------------------------------------- #


@pytest.fixture
def fixture_storage_root(tmp_path: Path) -> Path:
    """最小可用的 storage/ 镜像——每个 importer 覆盖到至少一条记录。"""
    root = tmp_path / "storage"
    (root / "paper_trade").mkdir(parents=True)
    (root / "paper_trade" / "2026-07-01.json").write_text(json.dumps({
        "prediction_date": "2026-07-01", "generated_at": "2026-07-01T09:05:00",
        "strategy": "log_mv", "top_pct": 0.05, "top_n": 2,
        "picks": [
            {"symbol": "688017.SH", "factor_value": -0.5, "rank_pct": 0.01,
             "rank_position": 1, "planned_weight": 0.5},
            {"symbol": "688322.SH", "factor_value": -0.3, "rank_pct": 0.02,
             "rank_position": 2, "planned_weight": 0.5},
        ],
    }), encoding="utf-8")

    (root / "watchlist_symbols.txt").write_text("688017.SH\n688322.SH\n", encoding="utf-8")

    (root / "sector_review_config.json").write_text(json.dumps({
        "top_n_industry": 5, "persistence_days": 3,
    }), encoding="utf-8")

    (root / "pipeline_weights.json").write_text(json.dumps({
        "log_mv": 0.25, "bj50_scan": 0.75, "_updated": "20260701",
    }), encoding="utf-8")

    (root / "sector_rotation").mkdir()
    (root / "sector_rotation" / "20260701.json").write_text(json.dumps({
        "tradeDate": "20260701", "industries": {"半导体": {}},
    }), encoding="utf-8")

    return root


def test_import_paper_trade_row_count_and_field_equality(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        result = ms.import_paper_trade(conn, fixture_storage_root)
        assert result.ok
        assert result.source_records == 2  # 2 picks in the one fixture file
        rows = conn.execute(
            "SELECT * FROM paper_trade_picks WHERE prediction_date='2026-07-01' ORDER BY symbol"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "688017.SH"
    assert rows[0]["rank_position"] == 1
    assert rows[0]["planned_weight"] == 0.5
    assert rows[1]["symbol"] == "688322.SH"
    assert rows[1]["factor_value"] == -0.3


def test_import_watchlist_row_count_and_values(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        result = ms.import_watchlist(conn, fixture_storage_root)
        assert result.ok
        assert result.source_records == 2
        codes = {r["ts_code"] for r in conn.execute("SELECT ts_code FROM watchlist")}
    assert codes == {"688017.SH", "688322.SH"}


def test_import_sector_review_config_matches_source(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        ms.import_sector_review_config(conn, fixture_storage_root)
        row = conn.execute("SELECT config_json FROM sector_review_config WHERE config_key='default'").fetchone()
    payload = json.loads(row["config_json"])
    assert payload["top_n_industry"] == 5
    assert payload["persistence_days"] == 3


def test_import_pipeline_weights_excludes_underscore_meta_keys(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        result = ms.import_pipeline_weights(conn, fixture_storage_root)
        assert result.source_records == 2  # log_mv + bj50_scan，_updated 不算
        rows = {r["weight_key"]: r["weight_value"] for r in conn.execute("SELECT weight_key, weight_value FROM pipeline_weights")}
    assert rows == {"log_mv": 0.25, "bj50_scan": 0.75}


# --------------------------------------------------------------------------- #
# ③ 重跑导入幂等
# --------------------------------------------------------------------------- #


def test_full_migration_idempotent_on_rerun(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    r1 = ms.run_migration(storage_root=fixture_storage_root, db_path=db_path)
    r2 = ms.run_migration(storage_root=fixture_storage_root, db_path=db_path)

    for a, b in zip(r1, r2):
        assert a.domain == b.domain
        assert a.rows_written == b.rows_written, f"{a.domain} 重跑行数不一致：{a.rows_written} vs {b.rows_written}"

    with connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) c FROM paper_trade_picks").fetchone()["c"]
    assert n == 2  # 不因重跑翻倍


def test_dry_run_does_not_persist(fixture_storage_root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    ms.run_migration(storage_root=fixture_storage_root, db_path=db_path, dry_run=True)
    assert not db_path.exists(), "dry-run 不该在目标路径留下文件"


# --------------------------------------------------------------------------- #
# ④ schema 版本表升级路径
# --------------------------------------------------------------------------- #


def test_ensure_schema_records_applied_versions(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        first = ensure_schema(conn)
        assert first == [v for v, _ in MIGRATIONS]
        second = ensure_schema(conn)
        assert second == []  # 幂等：第二次无新迁移可应用
        versions = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert versions == {v for v, _ in MIGRATIONS}


def test_new_migration_applies_without_rerunning_old_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)

    extra_migrations = MIGRATIONS + (
        (max(v for v, _ in MIGRATIONS) + 1, "CREATE TABLE IF NOT EXISTS _upgrade_probe (k TEXT PRIMARY KEY) STRICT;"),
    )
    monkeypatch.setattr("kss.storage.db.MIGRATIONS", extra_migrations)
    with connect(db_path) as conn:
        applied = ensure_schema(conn)
        assert applied == [extra_migrations[-1][0]]
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "_upgrade_probe" in tables


# --------------------------------------------------------------------------- #
# ⑤ STRICT 表拒绝类型漂移写入
# --------------------------------------------------------------------------- #


def test_strict_table_rejects_blob_into_text_column(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO watchlist (ts_code, added_at) VALUES (?, ?)",
                (b"\x00\x01binary", None),
            )


def test_strict_table_rejects_non_numeric_text_into_integer_column(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reports_index (report_id, report_name, file_path) VALUES (?, ?, ?)",
                ("not-a-number", "x", "y"),
            )
