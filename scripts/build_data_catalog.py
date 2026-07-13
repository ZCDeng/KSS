#!/usr/bin/env python3
"""U2：自动反射数据资产字典 → STATE_ROOT/storage/data_catalog.json。

设计(见 docs/plans/2026-06-22-002-...-plan.md):
  - 生成离线、服务 stdlib：本脚本读 parquet(需 pandas/pyarrow)，由 .venv-desktop 跑；
    bridge 的 data-catalog/orientation 只 json.load 本产物(KTD-2)。
  - 双根(KTD-9)：macro parquet 在 PROJECT_ROOT，cs_data 在 STATE_ROOT；产物钉死写 STATE_ROOT。
  - schema 自动反射(永不漂移)；含义/dateColumn 从 data_catalog_meta.yaml overlay 合并。
  - overlay 失配 fail-loud(KTD-1)：overlay 写了但实际不存在的列 → overlayDrift + warn。
  - identifier 白名单(KTD-8b)：反射列/表名过 ^[A-Za-z0-9_]+$，非法存占位符。
  - 黑屏 fail-loud(KTD-8a)：磁盘有 parquet 但零 parquet 解析成功 → 非零退出。
  - freshness 自描述(KTD-5)：generatedAt + datasetsResolved/Expected + 每集 latestDate/overlayDrift。

手动测试：.venv-desktop/bin/python scripts/build_data_catalog.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _env_path(var: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else None


# 与 kss_app_bridge 同源的双根解析(KTD-9)。
PROJECT_ROOT = _env_path("KSS_PROJECT_ROOT") or Path(__file__).resolve().parents[1]
STATE_ROOT = _env_path("KSS_STATE_ROOT") or PROJECT_ROOT

OVERLAY_PATH = PROJECT_ROOT / "kss" / "config" / "data_catalog_meta.yaml"
OUTPUT_PATH = STATE_ROOT / "storage" / "data_catalog.json"

# 分时库(intraday_quotes.db)的反射策略(KTD5/S5)：只反射 allowlist 内的表；
# 永不反射 BLOB 列(payload_blobs.payload)，payload_blobs/payload_observations 整表排除；
# 另排除可能携带脱敏后仍敏感文本的自由文本列(error_summary/details_json/missing_json)。
_INTRADAY_TABLE_ALLOWLIST = frozenset({
    "ingest_runs",
    "coverage_assessments",
    "instrument_registry",
    "session_profiles",
    "provider_bar_contracts",
    "canonical_bars",
})
# 即便在 allowlist 表内，这些自由文本列也从 catalog 反射排除(S5)。
_INTRADAY_EXCLUDED_COLUMNS = frozenset({
    "ingest_runs.error_summary",
    "coverage_assessments.details_json",
    "coverage_assessments.missing_json",
})

# kss.db(U14 起的统一库)反射按域割接台账门控(U17)：只反射 migration_ledger.json 里
# goldenResult=pass 的域映射到的表——沿用 kss/storage/duck.py 的同一份 DOMAIN_TABLES/
# load_ledger 逻辑(单一真源，见 duck.visible_tables)，避免未割接域的陈旧快照表
# (mi_rules/pipeline_weights/theme_registry)在 catalog 里冒充新鲜数据。
# app_task_runs.stdout/stderr 是原始进程输出(可能含本地路径/环境细节)，沿 S5 纪律排除。
_KSS_DB_EXCLUDED_COLUMNS = frozenset({
    "app_task_runs.stdout",
    "app_task_runs.stderr",
})


def _kss_db_table_allowlist() -> frozenset[str]:
    from kss.storage.duck import visible_tables  # noqa: PLC0415 — 惰性导入避免 import-time 副作用

    return frozenset(visible_tables().keys())


# *.db 资产白名单(相对 STATE_ROOT / PROJECT_ROOT)，显式枚举以排除 .build/.cache 构建产物(KTD-4)。
# 每项：(数据集名, 根, 子路径, 描述, table_allowlist | None | callable, excluded_columns)。
#   数据集名显式给出(不再靠 Path(sub).stem 推导)——datasette/kss.db 与 storage/kss.db 同名
#   为 "kss.db"，stem 推导会撞名(U17 加统一库条目时发现)，显式命名从根上避免。
#   table_allowlist=None → 全表反射(既有日频库行为不变)；
#   非 None → 仅反射列出的表，且按 excluded_columns 滤列(KTD5 分时库专用)；
#   callable → 惰性求值(kss.db 专用：每次生成时重算，反映当下割接进度)。
# prediction_ledger/ledger.db、factor_health/factor_health.db 已被 U15 割接归档，
# 不再枚举——其表已并入下方 kss_unified 条目(predictions/ic_snapshots/crashes/factor_lifecycle)。
_DB_CANDIDATES = [
    ("kss_quotes", STATE_ROOT, "storage/kss_quotes.db", "行情库", None, frozenset()),
    ("kronos_predictions", STATE_ROOT, "storage/kronos/predictions.sqlite", "Kronos 预测", None, frozenset()),
    ("datasette_kss", PROJECT_ROOT, "datasette/kss.db", "datasette 探索库", None, frozenset()),
    ("intraday_quotes", STATE_ROOT, "storage/intraday_quotes.db", "分时隔离库(PIT-safe)",
     _INTRADAY_TABLE_ALLOWLIST, _INTRADAY_EXCLUDED_COLUMNS),
    ("kss_unified", STATE_ROOT, "storage/kss.db", "统一存储库(U14起,写入方按域逐一割接,U15/U17)",
     _kss_db_table_allowlist, _KSS_DB_EXCLUDED_COLUMNS),
]

# 目录数据集(json/md)：name → (相对根, 子路径, 文件 glob, 日期解析提示)
_DIR_DATASETS = [
    ("etf_radar", STATE_ROOT, "storage/etf_radar", "*.json", "ETF 申赎雷达日快照"),
    ("sector_rotation", STATE_ROOT, "storage/sector_rotation", "*.json", "板块热点轮动归档"),
    ("paper_trade", STATE_ROOT, "storage/paper_trade", "*.json", "纸交易选股按日"),
    ("daily_review", STATE_ROOT, "storage/daily_review", "*.md", "个股复盘按日"),
]

# 项目内日期两种格式并存：紧凑 YYYYMMDD(parquet/etf_radar) vs 横杠 YYYY-MM-DD(cs_data/daily_review)。
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{8})")


def _safe_name(name: str) -> tuple[str, bool]:
    """identifier 白名单(KTD-8b)。返回(安全名, 是否被 flag)。"""
    s = str(name)
    if _IDENTIFIER_RE.match(s):
        return s, False
    return "__nonconforming__", True


def _load_overlay() -> dict[str, Any]:
    if not OVERLAY_PATH.exists():
        return {}
    data = yaml.safe_load(OVERLAY_PATH.read_text(encoding="utf-8")) or {}
    return data.get("datasets", {}) or {}


def _columns_with_overlay(actual_cols: list[str], meanings: dict[str, str]) -> tuple[list[dict], list[str]]:
    """合并 schema(权威) + overlay 含义；返回(columns, overlayDrift)。
    overlayDrift = overlay 写了但实际不存在的列(KTD-1 fail-loud 信号)。"""
    cols: list[dict] = []
    for raw in actual_cols:
        safe, flagged = _safe_name(raw)
        entry = {"name": safe, "meaning": meanings.get(raw, "")}
        if flagged:
            entry["flagged"] = True
        cols.append(entry)
    drift = [c for c in meanings.keys() if c not in actual_cols]
    return cols, drift


def _build_parquet_dataset(name: str, path: Path, overlay: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd  # lazy(缺引擎 fail-loud)，见 archive_trends_daily.py:49

    ov = overlay.get(name, {})
    meanings = ov.get("meanings", {}) or {}
    date_col = ov.get("dateColumn")
    date_kind = ov.get("dateKind", "column-max")
    df = pd.read_parquet(path)
    cols, drift = _columns_with_overlay(list(df.columns), meanings)
    latest = None
    if date_kind == "column-max" and date_col and date_col in df.columns:
        try:
            latest = str(df[date_col].dropna().max())
        except Exception:  # noqa: BLE001
            latest = None
    ds = {
        "name": name,
        "kind": "parquet",
        "root": "project" if path.is_relative_to(PROJECT_ROOT) else "state",
        "path": _rel(path),
        "granularity": _granularity(date_col, date_kind),
        "source": "update_macro_daily.py / refresh_*",
        "dateColumn": date_col,
        "latestDate": latest,
        "rows": int(len(df)),
        "columns": cols,
        "overlayDrift": drift,
    }
    return ds


def _build_csv_glob_dataset(name: str, pattern: str, overlay: dict[str, Any]) -> dict[str, Any] | None:
    """同 schema 批量 CSV 折叠为单一逻辑数据集(KTD-4)。"""
    import csv

    files = sorted(STATE_ROOT.glob(pattern))
    if not files:
        return None
    ov = overlay.get(name, {})
    meanings = ov.get("meanings", {}) or {}
    date_col = ov.get("dateColumn")
    with files[0].open("r", encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    cols, drift = _columns_with_overlay(header, meanings)
    # 日期范围：扫所有文件成本高，取样首文件首尾(stdlib，避免引 pandas)。
    latest = _csv_latest(files[0], header, date_col)
    return {
        "name": name,
        "kind": "csv-glob",
        "root": "state",
        "path": f"{pattern} (×{len(files)})",
        "granularity": _granularity(date_col, "column-max"),
        "source": "update_cs_data.py",
        "dateColumn": date_col,
        "latestDate": latest,
        "fileCount": len(files),
        "columns": cols,
        "overlayDrift": drift,
    }


def _csv_latest(path: Path, header: list[str], date_col: str | None) -> str | None:
    if not date_col or date_col not in header:
        return None
    idx = header.index(date_col)
    last = None
    with path.open("r", encoding="utf-8", newline="") as fh:
        import csv
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) > idx and row[idx]:
                last = row[idx]
    return last


def _is_blob_dtype(dtype: Any) -> bool:
    """SQLite 声明类型含 BLOB 即视为 BLOB 列(KTD5：catalog 永不暴露 BLOB)。"""
    return "blob" in str(dtype or "").lower()


def _build_sqlite_dataset(
    name: str,
    path: Path,
    desc: str,
    *,
    table_allowlist: frozenset[str] | None = None,
    excluded_columns: frozenset[str] = frozenset(),
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """反射 sqlite schema。

    既有日频库：``table_allowlist=None`` → 全表全列反射(行为不变)。
    分时库(KTD5/S5)：``table_allowlist`` 限定可见表；BLOB 列与 ``excluded_columns``
    里的 ``<table>.<column>`` 自由文本列从反射结果剔除(凭据/敏感文本永不进 catalog)。

    overlay 结构（per-table 含义）::

        intraday_quotes:
          tables:
            ingest_runs:
              meanings: {run_id: ..., provider: ...}

    overlayDrift = overlay 写了含义但反射后不存在(被排除/改名/删除)的 ``<table>.<col>``
    列；沿 ``main()`` 现有告警路径(KTD-1 fail-loud，build_data_catalog.py:80-91 同语义)。
    """
    ov = (overlay or {}).get(name, {})
    ov_tables: dict[str, Any] = ov.get("tables", {}) or {}
    tables: list[dict] = []
    drift: list[str] = []
    reflected_cols: dict[str, set[str]] = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        for (tbl,) in cur.fetchall():
            if table_allowlist is not None and tbl not in table_allowlist:
                continue  # allowlist 外的表(含 payload_blobs/payload_observations)整表排除
            safe_tbl, _ = _safe_name(tbl)
            info = con.execute(f"PRAGMA table_info('{tbl}')").fetchall()
            tbl_meanings = (ov_tables.get(tbl, {}) or {}).get("meanings", {}) or {}
            present: set[str] = set()
            tcols = []
            for row in info:
                col_name = row[1]
                if _is_blob_dtype(row[2]):
                    continue  # BLOB 列永不暴露
                if f"{tbl}.{col_name}" in excluded_columns:
                    continue  # S5：自由文本列排除
                present.add(col_name)
                safe_c, flagged = _safe_name(col_name)
                c = {"name": safe_c, "dtype": row[2],
                     "meaning": tbl_meanings.get(col_name, "")}
                if flagged:
                    c["flagged"] = True
                tcols.append(c)
            reflected_cols[tbl] = present
            tables.append({"table": safe_tbl, "columns": tcols})
    finally:
        con.close()
    # overlayDrift：overlay 含义指向已反射表里不存在的列(改名/删除信号，KTD-1)。
    # 仅对**已反射的表**判漂移——表整体不在反射集(未建/不在 allowlist)不算列漂移；
    # 被显式排除的敏感列(excluded_columns)也不算漂移(属正常排除)。
    for tbl in reflected_cols:
        meanings = (ov_tables.get(tbl, {}) or {}).get("meanings", {}) or {}
        present = reflected_cols[tbl]
        for col in meanings:
            qualified = f"{tbl}.{col}"
            if col not in present and qualified not in excluded_columns:
                drift.append(qualified)
    ds = {
        "name": name,
        "kind": "sqlite",
        "root": "project" if path.is_relative_to(PROJECT_ROOT) else "state",
        "path": _rel(path),
        "granularity": "库表",
        "source": desc,
        "tables": tables,
    }
    if drift:
        ds["overlayDrift"] = drift
    return ds


def _build_dir_dataset(name: str, root: Path, sub: str, glob: str, desc: str) -> dict[str, Any] | None:
    d = root / sub
    if not d.exists():
        return None
    files = sorted(d.glob(glob))
    latest = None
    for f in files:
        m = _DATE_RE.search(f.name)
        if m:
            # 归一化到纯数字比较，保留原串展示。
            cur = m.group(1)
            if latest is None or cur.replace("-", "") > latest.replace("-", ""):
                latest = cur
    return {
        "name": name,
        "kind": "dir",
        "root": "project" if root == PROJECT_ROOT else "state",
        "path": f"{sub}/{glob}",
        "granularity": "按日文件",
        "source": desc,
        "latestDate": latest,
        "fileCount": len(files),
    }


def _granularity(date_col: str | None, date_kind: str) -> str:
    if date_kind == "none" or not date_col:
        return "快照"
    dc = date_col.lower()
    if dc in ("month",) or date_col == "MONTH":
        return "月频"
    if dc in ("ann_date", "end_date"):
        return "季频"
    return "日频"


def _rel(path: Path) -> str:
    for root in (STATE_ROOT, PROJECT_ROOT):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def build_catalog(overlay: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """构建 catalog dict。返回(catalog, parquet_files_on_disk)。
    纯函数式：副作用(写盘/退出)留给 main，便于测试。"""
    overlay = _load_overlay() if overlay is None else overlay
    datasets: list[dict[str, Any]] = []

    macro_dir = PROJECT_ROOT / "storage" / "macro"
    parquet_files = sorted(macro_dir.glob("*.parquet")) if macro_dir.exists() else []
    for pq in parquet_files:
        nm = pq.stem
        try:
            datasets.append(_build_parquet_dataset(nm, pq, overlay))
        except Exception as exc:  # noqa: BLE001  单源失败降级不连坐
            datasets.append({"name": nm, "kind": "parquet", "path": _rel(pq),
                             "error": f"read failed: {exc}"})

    for nm, pattern in (("cs_data", "cs_data_*.csv"), ("mf", "mf_*.csv")):
        try:
            ds = _build_csv_glob_dataset(nm, pattern, overlay)
            if ds:
                datasets.append(ds)
        except Exception as exc:  # noqa: BLE001
            datasets.append({"name": nm, "kind": "csv-glob", "error": f"read failed: {exc}"})

    for nm, root, sub, desc, allowlist, excluded in _DB_CANDIDATES:
        p = root / sub
        if not p.exists():
            continue
        if callable(allowlist):
            allowlist = allowlist()
        try:
            datasets.append(_build_sqlite_dataset(
                nm, p, desc,
                table_allowlist=allowlist,
                excluded_columns=excluded,
                overlay=overlay,
            ))
        except Exception as exc:  # noqa: BLE001
            datasets.append({"name": nm, "kind": "sqlite", "path": _rel(p),
                             "error": f"introspect failed: {exc}"})

    for nm, root, sub, glob, desc in _DIR_DATASETS:
        ds = _build_dir_dataset(nm, root, sub, glob, desc)
        if ds:
            datasets.append(ds)

    resolved = sum(1 for d in datasets if "error" not in d)
    catalog = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datasetsExpected": len(datasets),
        "datasetsResolved": resolved,
        "datasets": datasets,
    }
    return catalog, len(parquet_files)


def _write_atomic(catalog: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, OUTPUT_PATH)  # 原子(同盘 rename，非 shutil.move)


def main() -> int:
    catalog, parquet_on_disk = build_catalog()
    parquet_resolved = sum(
        1 for d in catalog["datasets"] if d.get("kind") == "parquet" and "error" not in d
    )
    drifted = [d["name"] for d in catalog["datasets"] if d.get("overlayDrift")]
    if drifted:
        print(f"[warn] overlay 失配(列已改名/删除?): {drifted}", file=sys.stderr)
    _write_atomic(catalog)
    print(f"[ok] data_catalog.json: {catalog['datasetsResolved']}/{catalog['datasetsExpected']} "
          f"datasets → {OUTPUT_PATH}")
    # 黑屏 fail-loud(KTD-8a)：磁盘有 parquet 但零解析成功 = 环境坏，非零退出。
    if parquet_on_disk > 0 and parquet_resolved == 0:
        print(f"[FATAL] {parquet_on_disk} 个 parquet 在盘但零解析成功 —— 检查 pandas/pyarrow 环境",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
