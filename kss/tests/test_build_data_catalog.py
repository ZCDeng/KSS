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
    monkeypatch.setattr(bd, "_DB_CANDIDATES", [(tmp_path, "storage/t.db", "测试库")])
    catalog, _ = bd.build_catalog({})
    ds = next(d for d in catalog["datasets"] if d["name"] == "t")
    assert ds["kind"] == "sqlite"
    tbl = next(t for t in ds["tables"] if t["table"] == "ledger")
    assert {c["name"] for c in tbl["columns"]} == {"id", "ret"}


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
