"""U2 测试：build_data_catalog 生成器。

覆盖 plan 的 test scenarios：happy/月季日期/overlay 合并/overlay drift/identifier 白名单/
cs_data 折叠/构建产物排除/sqlite/parquet 黑屏/单源失败/原子写/双根。
跑：.venv-desktop/bin/python -m pytest tests/test_build_data_catalog.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_data_catalog as bd  # noqa: E402


def _setup_roots(monkeypatch, project: Path, state: Path | None = None):
    state = state or project
    monkeypatch.setattr(bd, "PROJECT_ROOT", project)
    monkeypatch.setattr(bd, "STATE_ROOT", state)
    monkeypatch.setattr(bd, "OUTPUT_PATH", state / "storage" / "data_catalog.json")
    (project / "storage" / "macro").mkdir(parents=True, exist_ok=True)
    (state / "storage").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(bd, "_DB_CANDIDATES", [])
    monkeypatch.setattr(bd, "_DIR_DATASETS", [])


def _write_parquet(project: Path, name: str, df: pd.DataFrame):
    df.to_parquet(project / "storage" / "macro" / f"{name}.parquet")


def test_happy_parquet_schema_and_latest(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(project=tmp_path, name="margin_daily",
                   df=pd.DataFrame({"trade_date": ["20260101", "20260618"], "rzye": [1, 2]}))
    overlay = {"margin_daily": {"dateColumn": "trade_date", "dateKind": "column-max",
                                "meanings": {"rzye": "融资余额"}}}
    catalog, _ = bd.build_catalog(overlay)
    ds = next(d for d in catalog["datasets"] if d["name"] == "margin_daily")
    assert ds["latestDate"] == "20260618"          # Covers R6（几号的数据）
    assert ds["rows"] == 2
    names = {c["name"] for c in ds["columns"]}
    assert {"trade_date", "rzye"} <= names          # Covers R6（哪些字段）
    assert next(c for c in ds["columns"] if c["name"] == "rzye")["meaning"] == "融资余额"
    assert ds["overlayDrift"] == []


def test_month_and_quarter_date_derivation(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "macro_monthly", pd.DataFrame({"month": ["202504", "202605"], "m2": [1, 2]}))
    _write_parquet(tmp_path, "fina_quarterly",
                   pd.DataFrame({"ann_date": ["20260101", "20260521"], "end_date": ["20251231", "20260331"]}))
    _write_parquet(tmp_path, "pmi_monthly", pd.DataFrame({"PMI010600": [50.1]}))
    overlay = {
        "macro_monthly": {"dateColumn": "month", "dateKind": "column-max"},
        "fina_quarterly": {"dateColumn": "ann_date", "dateKind": "column-max"},
        "pmi_monthly": {"dateKind": "none"},
    }
    catalog, _ = bd.build_catalog(overlay)
    by = {d["name"]: d for d in catalog["datasets"]}
    assert by["macro_monthly"]["latestDate"] == "202605"
    assert by["fina_quarterly"]["latestDate"] == "20260521"   # ann_date, 不硬编 trade_date
    assert by["pmi_monthly"]["latestDate"] is None             # dateKind none → 不臆造


def test_unannotated_column_meaning_blank(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "x", pd.DataFrame({"a": [1], "b": [2]}))
    catalog, _ = bd.build_catalog({"x": {"meanings": {"a": "甲"}}})
    ds = catalog["datasets"][0]
    assert next(c for c in ds["columns"] if c["name"] == "b")["meaning"] == ""  # fail-soft 留空


def test_overlay_drift_detected(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "x", pd.DataFrame({"a": [1]}))
    catalog, _ = bd.build_catalog({"x": {"meanings": {"a": "甲", "gone": "已删列"}}})
    ds = catalog["datasets"][0]
    assert ds["overlayDrift"] == ["gone"]   # KTD-1 fail-loud 信号，不静默


def test_identifier_whitelist(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "x", pd.DataFrame({"ok": [1], "bad; drop": [2]}))
    catalog, _ = bd.build_catalog({})
    ds = catalog["datasets"][0]
    bad = [c for c in ds["columns"] if c.get("flagged")]
    assert bad and bad[0]["name"] == "__nonconforming__"   # KTD-8b 不原样落


def test_cs_data_folding(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    for code in ("688001", "688002", "688003"):
        (tmp_path / f"cs_data_{code}.csv").write_text(
            "ts_code,trade_date,close\n{c}.SH,2026-06-18,10\n".format(c=code), encoding="utf-8")
    catalog, _ = bd.build_catalog({"cs_data": {"dateColumn": "trade_date"}})
    cs = [d for d in catalog["datasets"] if d["name"] == "cs_data"]
    assert len(cs) == 1 and cs[0]["fileCount"] == 3        # 折叠为单一数据集
    assert cs[0]["latestDate"] == "2026-06-18"             # 横杠日期也能解析


def test_build_artifacts_excluded(tmp_path, monkeypatch):
    """_DB_CANDIDATES 是显式白名单；.build/ 下的 db 不会被纳入。"""
    _setup_roots(monkeypatch, tmp_path)
    (tmp_path / ".build").mkdir()
    sqlite3.connect(tmp_path / ".build" / "build.db").close()
    catalog, _ = bd.build_catalog({})
    assert all("build" not in d["name"] for d in catalog["datasets"])


def test_sqlite_table_introspection(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    dbp = tmp_path / "storage" / "t.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE ledger (id INTEGER, ret REAL)")
    con.commit(); con.close()
    monkeypatch.setattr(
        bd, "_DB_CANDIDATES",
        [("t", tmp_path, "storage/t.db", "测试库", None, frozenset())],
    )
    catalog, _ = bd.build_catalog({})
    ds = next(d for d in catalog["datasets"] if d["name"] == "t")
    assert ds["kind"] == "sqlite"
    tbl = next(t for t in ds["tables"] if t["table"] == "ledger")
    assert {c["name"] for c in tbl["columns"]} == {"id", "ret"}


def _make_intraday_db(path: Path, *, error_summary: str = "", details_json: str = "",
                      missing_json: str = "") -> None:
    """建一个含 BLOB + 自由文本列 + payload_observations 的伪分时库（U9/S5）。"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE ingest_runs (run_id TEXT, provider TEXT, error_summary TEXT)"
    )
    con.execute(
        "CREATE TABLE payload_blobs (payload_sha256 TEXT, payload BLOB, byte_size INTEGER)"
    )
    con.execute(
        "CREATE TABLE payload_observations "
        "(observation_id INTEGER, redacted_request_json TEXT)"
    )
    con.execute(
        "CREATE TABLE coverage_assessments "
        "(assessment_id INTEGER, trade_date TEXT, details_json TEXT, missing_json TEXT)"
    )
    con.execute("CREATE TABLE canonical_bars (canonical_id INTEGER, close REAL)")
    con.execute(
        "INSERT INTO ingest_runs VALUES ('r1', 'akshare', ?)", (error_summary,)
    )
    con.execute("INSERT INTO payload_blobs VALUES ('h1', X'00FF', 2)")
    con.execute("INSERT INTO payload_observations VALUES (1, '{}')")
    con.execute(
        "INSERT INTO coverage_assessments VALUES (1, '2026-06-22', ?, ?)",
        (details_json, missing_json),
    )
    con.execute("INSERT INTO canonical_bars VALUES (1, 10.0)")
    con.commit()
    con.close()


_INTRADAY_ALLOWLIST = frozenset({
    "ingest_runs", "coverage_assessments", "instrument_registry",
    "session_profiles", "provider_bar_contracts", "canonical_bars",
})
_INTRADAY_EXCLUDED = frozenset({
    "ingest_runs.error_summary",
    "coverage_assessments.details_json",
    "coverage_assessments.missing_json",
})


def _patch_intraday_candidate(monkeypatch, tmp_path, dbrel="storage/intraday_quotes.db"):
    monkeypatch.setattr(
        bd, "_DB_CANDIDATES",
        [("intraday_quotes", tmp_path, dbrel, "分时隔离库", _INTRADAY_ALLOWLIST, _INTRADAY_EXCLUDED)],
    )


def test_intraday_allowlist_and_blob_exclusion(tmp_path, monkeypatch):
    """Covers U9：catalog 暴露 allowlist 表字段，但无 BLOB 列、无 payload_blobs/observations 表。"""
    _setup_roots(monkeypatch, tmp_path)
    _make_intraday_db(tmp_path / "storage" / "intraday_quotes.db")
    _patch_intraday_candidate(monkeypatch, tmp_path)
    catalog, _ = bd.build_catalog({})
    ds = next(d for d in catalog["datasets"] if d["name"] == "intraday_quotes")
    table_names = {t["table"] for t in ds["tables"]}
    # allowlist 外的表整表排除
    assert "payload_blobs" not in table_names
    assert "payload_observations" not in table_names
    # allowlist 内的表可见
    assert {"ingest_runs", "coverage_assessments", "canonical_bars"} <= table_names
    # 全 catalog 序列化后无 BLOB 列名、无 redacted_request_json
    blob = json.dumps(catalog, ensure_ascii=False)
    assert '"payload"' not in blob
    assert "payload_blobs" not in blob
    assert "redacted_request_json" not in blob


def test_intraday_text_columns_excluded_s5(tmp_path, monkeypatch):
    """评审 S5：error_summary/details_json/missing_json 填合成 token → catalog 均不含。"""
    _setup_roots(monkeypatch, tmp_path)
    token = "deadbeef" * 5  # 40-hex token 形态
    _make_intraday_db(
        tmp_path / "storage" / "intraday_quotes.db",
        error_summary=f"401 {token}",
        details_json=f'{{"k":"{token}"}}',
        missing_json=f"[{token}]",
    )
    _patch_intraday_candidate(monkeypatch, tmp_path)
    catalog, _ = bd.build_catalog({})
    ds = next(d for d in catalog["datasets"] if d["name"] == "intraday_quotes")
    cols = {c["name"] for t in ds["tables"] for c in t["columns"]}
    assert "error_summary" not in cols
    assert "details_json" not in cols
    assert "missing_json" not in cols
    # 即使列里有 token 文本(数据值)，因列被排除，catalog 输出不含该 token
    assert token not in json.dumps(catalog, ensure_ascii=False)


def test_intraday_overlay_meaning_and_drift(tmp_path, monkeypatch):
    """Covers U9：overlay 注字段含义；overlay 漂移现 warning（沿 main 路径）。"""
    _setup_roots(monkeypatch, tmp_path)
    _make_intraday_db(tmp_path / "storage" / "intraday_quotes.db")
    _patch_intraday_candidate(monkeypatch, tmp_path)
    overlay = {"intraday_quotes": {"tables": {
        "ingest_runs": {"meanings": {"provider": "数据提供方", "gone_col": "已删列"}},
    }}}
    catalog, _ = bd.build_catalog(overlay)
    ds = next(d for d in catalog["datasets"] if d["name"] == "intraday_quotes")
    runs = next(t for t in ds["tables"] if t["table"] == "ingest_runs")
    prov = next(c for c in runs["columns"] if c["name"] == "provider")
    assert prov["meaning"] == "数据提供方"
    assert ds["overlayDrift"] == ["ingest_runs.gone_col"]  # KTD-1 fail-loud


def test_intraday_excluded_column_in_overlay_no_drift(tmp_path, monkeypatch):
    """overlay 声明被显式排除的敏感列 → 不计 overlayDrift（属正常排除非漂移）。"""
    _setup_roots(monkeypatch, tmp_path)
    _make_intraday_db(tmp_path / "storage" / "intraday_quotes.db")
    _patch_intraday_candidate(monkeypatch, tmp_path)
    overlay = {"intraday_quotes": {"tables": {
        "ingest_runs": {"meanings": {"error_summary": "失败原因(脱敏)"}},
    }}}
    catalog, _ = bd.build_catalog(overlay)
    ds = next(d for d in catalog["datasets"] if d["name"] == "intraday_quotes")
    assert "overlayDrift" not in ds  # 排除列不算漂移


def test_db_candidate_allowlist_can_be_callable(tmp_path, monkeypatch):
    """U17：_DB_CANDIDATES 的 table_allowlist 支持 callable，惰性求值(kss.db 域割接门控用)。"""
    _setup_roots(monkeypatch, tmp_path)
    dbp = tmp_path / "storage" / "u.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE visible_tbl (a INTEGER)")
    con.execute("CREATE TABLE hidden_tbl (b INTEGER)")
    con.commit(); con.close()
    calls = []

    def _lazy_allowlist():
        calls.append(1)
        return frozenset({"visible_tbl"})

    monkeypatch.setattr(
        bd, "_DB_CANDIDATES",
        [("u", tmp_path, "storage/u.db", "测试库(callable allowlist)", _lazy_allowlist, frozenset())],
    )
    catalog, _ = bd.build_catalog({})
    assert calls == [1]  # 惰性求值：build_catalog 调用时才求值一次
    ds = next(d for d in catalog["datasets"] if d["name"] == "u")
    table_names = {t["table"] for t in ds["tables"]}
    assert table_names == {"visible_tbl"}


def test_db_candidates_names_unique():
    """回归：datasette/kss.db 与 storage/kss.db 若都靠 Path(sub).stem 推导会撞名 "kss"
    (U17 加统一库条目时的真实 bug)。显式命名后须两两不同。"""
    names = [entry[0] for entry in bd._DB_CANDIDATES]
    assert len(names) == len(set(names)), f"_DB_CANDIDATES 数据集名撞车: {names}"


def test_kss_db_table_allowlist_reflects_real_ledger():
    """U17 test scenario ②：kss_db_table_allowlist 对真实仓库 migration_ledger.json 求值
    不报错，且已知未割接域(mi_rules 所属)不在其中。"""
    allowlist = bd._kss_db_table_allowlist()
    assert isinstance(allowlist, frozenset)
    assert "mi_rules" not in allowlist
    assert "watchlist" in allowlist  # 已割接域，真实仓库应可见


def test_real_overlay_yaml_aligns_with_intraday_schema(tmp_path, monkeypatch):
    """真实 data_catalog_meta.yaml 的 intraday_quotes overlay 列名对齐真 store schema → 零漂移.

    用**真 IntradayStore** 建库(真 schema)，验真实 overlay 无失配——防 overlay 漂移留缝。
    """
    from kss.data.intraday_store import IntradayStore

    _setup_roots(monkeypatch, tmp_path)
    dbp = tmp_path / "storage" / "intraday_quotes.db"
    IntradayStore(dbp)  # 建全 schema
    _patch_intraday_candidate(monkeypatch, tmp_path)
    overlay = bd._load_overlay()
    assert "intraday_quotes" in overlay  # overlay 确有 intraday 条目
    catalog, _ = bd.build_catalog(overlay)
    ds = next(d for d in catalog["datasets"] if d["name"] == "intraday_quotes")
    # 真实 overlay 列名须与真 schema(扣除排除列)逐一对齐 → 零漂移。
    assert ds.get("overlayDrift", []) == []
    # 同时复核真库的 BLOB/排除列确实不出现。
    blob = json.dumps(catalog, ensure_ascii=False)
    assert '"payload"' not in blob and "payload_blobs" not in blob
    assert "redacted_request_json" not in blob
    cols = {c["name"] for t in ds["tables"] for c in t["columns"]}
    assert {"error_summary", "details_json", "missing_json"}.isdisjoint(cols)


def test_parquet_blackout_fail_loud(tmp_path, monkeypatch):
    """磁盘有 parquet 但全部解析失败 → datasetsResolved 反映降级 + main 非零退出(KTD-8a)。"""
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "a", pd.DataFrame({"x": [1]}))
    _write_parquet(tmp_path, "b", pd.DataFrame({"x": [1]}))

    def boom(*a, **k):
        raise RuntimeError("pyarrow gone")
    monkeypatch.setattr(bd, "_build_parquet_dataset", boom)
    catalog, parquet_on_disk = bd.build_catalog({})
    assert parquet_on_disk == 2
    assert all("error" in d for d in catalog["datasets"] if d["kind"] == "parquet")
    monkeypatch.setattr(bd, "build_catalog", lambda *a, **k: (catalog, parquet_on_disk))
    assert bd.main() == 1   # 黑屏 = 非零退出


def test_single_source_failure_not_contagious(tmp_path, monkeypatch):
    _setup_roots(monkeypatch, tmp_path)
    _write_parquet(tmp_path, "good", pd.DataFrame({"x": [1]}))
    (tmp_path / "storage" / "macro" / "bad.parquet").write_text("not a parquet", encoding="utf-8")
    catalog, _ = bd.build_catalog({})
    by = {d["name"]: d for d in catalog["datasets"]}
    assert "error" in by["bad"] and "error" not in by["good"]   # 降级不连坐


def test_atomic_write_and_dual_root(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    state = tmp_path / "state"
    _setup_roots(monkeypatch, project, state)
    _write_parquet(project, "macro_daily", pd.DataFrame({"trade_date": ["20260618"], "v": [1]}))
    (state / "cs_data_688001.csv").write_text("ts_code,trade_date\n688001.SH,2026-06-18\n", encoding="utf-8")
    catalog, _ = bd.build_catalog({})
    bd._write_atomic(catalog)
    out = state / "storage" / "data_catalog.json"
    assert out.exists()                                    # 产物钉死写 STATE_ROOT(KTD-9)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    pq = next(d for d in loaded["datasets"] if d["name"] == "macro_daily")
    assert pq["root"] == "project"                         # parquet 从 PROJECT_ROOT 读
    assert not (state / "storage" / "data_catalog.json.tmp").exists()  # tmp 已 rename
