"""U16 测试：kss/storage/duck.py 会话工厂 + 护栏（plan KTD11 test scenarios ①-⑦ + 额外发现的
sqlite 扩展绕过缺口）。
跑：.venv-desktop/bin/python -m pytest kss/tests/test_duck_query.py -q
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from kss.storage import duck


def _make_kss_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE watchlist (ts_code TEXT, added_at TEXT)")
    con.execute("INSERT INTO watchlist VALUES ('688017.SH', '2026-01-01')")
    con.execute("CREATE TABLE mi_rules (id INTEGER)")  # 未割接域，须不可见
    con.execute("INSERT INTO mi_rules VALUES (1)")
    con.commit()
    con.close()


def _make_ledger(path: Path, *, watchlist_pass: bool = True) -> None:
    ledger = {
        "watchlist": {
            "completedAt": "2026-07-01T00:00:00",
            "goldenResult": "pass" if watchlist_pass else "fail",
            "notes": "test fixture",
        },
    }
    path.write_text(json.dumps(ledger), encoding="utf-8")


def _make_catalog(path: Path, parquet_path: Path, project_root: Path) -> None:
    catalog = {
        "datasets": [
            {
                "name": "macro_daily",
                "kind": "parquet",
                "root": "project",
                "path": str(parquet_path.relative_to(project_root)),
            },
        ]
    }
    path.write_text(json.dumps(catalog), encoding="utf-8")


def _make_parquet(root: Path, rel: str) -> Path:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(
        f"COPY (SELECT '20260101' AS trade_date, 1.23::DOUBLE AS shibor_on) "
        f"TO '{full.as_posix()}' (FORMAT PARQUET)"
    )
    con.close()
    return full


@pytest.fixture
def env(tmp_path):
    proot = tmp_path / "project"
    sroot = tmp_path / "state"
    (sroot / "storage").mkdir(parents=True)
    (proot / "storage" / "macro").mkdir(parents=True)

    kss_db = sroot / "storage" / "kss.db"
    _make_kss_db(kss_db)

    ledger_path = sroot / "storage" / "migration_ledger.json"
    _make_ledger(ledger_path)

    parquet_path = _make_parquet(proot, "storage/macro/macro_daily.parquet")
    catalog_path = sroot / "storage" / "data_catalog.json"
    _make_catalog(catalog_path, parquet_path, proot)

    return {
        "db_path": kss_db,
        "catalog_path": catalog_path,
        "project_root": proot,
        "state_root": sroot,
        "ledger_path": ledger_path,
    }


def _run(env, sql, **kw):
    return duck.run_query(
        sql,
        db_path=env["db_path"],
        catalog_path=env["catalog_path"],
        project_root=env["project_root"],
        state_root=env["state_root"],
        ledger_path=env["ledger_path"],
        **kw,
    )


# ① 语句白名单：SELECT/SUMMARIZE 放行，ATTACH/COPY/PRAGMA/INSTALL/SET/多语句拒绝
@pytest.mark.parametrize("sql", [
    "ATTACH 'x.db'",
    "COPY watchlist TO 'x.csv'",
    "PRAGMA table_info(watchlist)",
    "INSTALL httpfs",
    "SET threads=1",
    "SELECT 1; DROP TABLE watchlist",
])
def test_banned_statements_rejected(env, sql):
    r = _run(env, sql)
    assert r["status"] == "rejected"


@pytest.mark.parametrize("sql", ["SELECT 1", "select 1", "WITH x AS (SELECT 1) SELECT * FROM x",
                                  "SUMMARIZE SELECT 1 AS a", "DESCRIBE SELECT 1 AS a"])
def test_allowed_statement_prefixes_pass(env, sql):
    r = _run(env, sql)
    assert r["status"] == "ok", r


# ② 跨库 join（kss.db 表 x parquet 视图）结果正确
def test_cross_source_join(env):
    r = _run(env, "SELECT w.ts_code, m.shibor_on FROM watchlist w, macro_daily m")
    assert r["status"] == "ok", r
    assert r["rows"] == [["688017.SH", 1.23]]


# ③ 行上限截断与超时熔断
def test_row_cap_truncation(env):
    r = _run(env, "SELECT * FROM range(50)", row_limit=5)
    assert r["status"] == "ok"
    assert r["rowCount"] == 5
    assert r["truncated"] is True


def test_timeout(env):
    r = _run(env, "SELECT count(*) FROM range(200000000) a, range(200) b", timeout=1.0)
    assert r["status"] == "timeout"


# ⑤ READ_ONLY 下写语句报错而非生效（先在校验层拒绝，属同一防线的前段）
@pytest.mark.parametrize("sql", ["INSERT INTO watchlist VALUES ('x', 'y')",
                                  "UPDATE watchlist SET ts_code='x'",
                                  "DELETE FROM watchlist"])
def test_write_statements_rejected(env, sql):
    r = _run(env, sql)
    assert r["status"] == "rejected"


# ⑥ read_text/glob/read_csv 指向白名单目录外路径被拒
def test_file_functions_outside_allowlist_rejected(env):
    r = _run(env, "SELECT * FROM read_text('/etc/hosts')")
    assert r["status"] == "error"
    assert "Permission" in r["reason"] or "disabled" in r["reason"]


def test_file_functions_inside_allowlist_permitted(env):
    r = _run(env, "SELECT * FROM macro_daily")
    assert r["status"] == "ok"
    assert r["rowCount"] == 1


# ⑦ 未割接域的表不可见且查询被拒
def test_ungated_table_invisible(env):
    r = _run(env, "SELECT * FROM mi_rules")
    assert r["status"] == "error"
    assert "not exist" in r["reason"] or "does not exist" in r["reason"]


def test_ungated_domain_when_golden_not_pass(env):
    _make_ledger(env["ledger_path"], watchlist_pass=False)
    r = _run(env, "SELECT * FROM watchlist")
    assert r["status"] == "error"


# 额外发现的缺口：sqlite 扩展表函数绕过 allowed_directories/enable_external_access 引擎围栏，
# 且可嵌套在合法 SELECT 内部通过首 token 白名单——必须靠全文黑名单单独拦截。
@pytest.mark.parametrize("sql", [
    "SELECT * FROM sqlite_scan('/etc/passwd', 'x')",
    "SELECT sqlite_attach('/etc/passwd')",
    "SELECT * FROM sqlite_query('/etc/passwd', 'select 1')",
])
def test_sqlite_extension_bypass_functions_rejected(env, sql):
    r = _run(env, sql)
    assert r["status"] == "rejected", r


def test_validate_query_empty_and_whitespace():
    with pytest.raises(duck.QueryRejected):
        duck.validate_query("")
    with pytest.raises(duck.QueryRejected):
        duck.validate_query("   ")


def test_open_session_lockdown_prevents_reconfig(env):
    con = duck.open_session(
        db_path=env["db_path"], catalog_path=env["catalog_path"],
        project_root=env["project_root"], state_root=env["state_root"],
        ledger_path=env["ledger_path"],
    )
    with pytest.raises(Exception):
        con.execute("SET enable_external_access=true")
    con.close()
