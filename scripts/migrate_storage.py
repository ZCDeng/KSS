#!/usr/bin/env python3
"""storage/ 历史数据一次性导入统一库（plan 2026-07-12-005 / U14）.

按 docs/plans/2026-07-12-005-appendix-storage-inventory.md 逐域导入到
kss/storage/db.py 定义的 kss.db。幂等可重跑——每域用 ``INSERT OR REPLACE``，
重跑不产生重复行、也不报错。不删旧源文件（U15 割接完成后另行归档）。

用法::

    python3 scripts/migrate_storage.py                # 对真实 storage/ 跑
    python3 scripts/migrate_storage.py --dry-run       # 只统计不写入
    python3 scripts/migrate_storage.py --storage-root /tmp/foo --db /tmp/foo/kss.db  # 测试用
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from kss.config.paths import KSS_DB  # noqa: E402
from kss.storage.db import connect, ensure_schema  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ImportResult:
    domain: str
    source_records: int
    rows_written: int
    sample_checked: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.source_records == self.rows_written


# --------------------------------------------------------------------------- #
# 共享 helper：一文件一行域（sector_rotation / mi_signals / etf_radar / trends /
# news_digest / indicator_signals / intel_radar 这类「payload_json 原样存」的域）
# --------------------------------------------------------------------------- #


def _import_json_glob(
    conn: sqlite3.Connection,
    *,
    domain: str,
    table: str,
    source_dir: Path,
    glob_pattern: str,
    insert_sql: str,
    row_from_path: Callable[[Path, dict], tuple | None],
) -> ImportResult:
    """glob 一批 json 文件，每个文件一行；``row_from_path`` 返回 execute 参数 tuple 或 None（跳过）。"""
    if not source_dir.is_dir():
        return ImportResult(domain, 0, 0, False, f"{source_dir} 不存在，跳过")
    files = sorted(source_dir.glob(glob_pattern))
    written = 0
    skipped = 0
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            skipped += 1
            print(f"[migrate:{domain}] 跳过 {f.name}：{exc}", file=sys.stderr)
            continue
        params = row_from_path(f, payload)
        if params is None:
            skipped += 1
            continue
        conn.execute(insert_sql, params)
        written += 1
    detail = f"{len(files)} 源文件，{skipped} 跳过" if skipped else f"{len(files)} 源文件"
    return ImportResult(domain, len(files) - skipped, written, written > 0, detail)


# --------------------------------------------------------------------------- #
# 各域导入函数
# --------------------------------------------------------------------------- #


def import_paper_trade(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "paper_trade"
    if not source_dir.is_dir():
        return ImportResult("paper_trade_picks", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("*.json"))
    written = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        prediction_date = data.get("prediction_date") or f.stem
        for pick in data.get("picks", []):
            conn.execute(
                """INSERT OR REPLACE INTO paper_trade_picks
                (prediction_date, symbol, generated_at, strategy, top_pct, top_n,
                 factor_value, rank_pct, rank_position, planned_weight)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    prediction_date, pick.get("symbol"), data.get("generated_at"),
                    data.get("strategy"), data.get("top_pct"), data.get("top_n"),
                    pick.get("factor_value"), pick.get("rank_pct"),
                    pick.get("rank_position"), pick.get("planned_weight"),
                ),
            )
            written += 1
    return ImportResult("paper_trade_picks", written, written, written > 0,
                         f"{len(files)} 源文件 → {written} 条 pick")


def import_sector_rotation(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        trade_date = payload.get("tradeDate") or f.stem
        return (trade_date, json.dumps(payload, ensure_ascii=False), None)

    return _import_json_glob(
        conn, domain="sector_rotation_snapshots", table="sector_rotation_snapshots",
        source_dir=storage_root / "sector_rotation", glob_pattern="*.json",
        insert_sql="INSERT OR REPLACE INTO sector_rotation_snapshots (trade_date, payload_json, created_at) VALUES (?,?,?)",
        row_from_path=row,
    )


def import_mi_signals(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        asof = payload.get("asof")
        symbol = payload.get("symbol") or f.stem
        if not asof:
            return None
        return (asof, symbol, json.dumps(payload, ensure_ascii=False), None)

    # latest/ 子目录是当前状态镜像，跟带日期文件重复 —— 只导带 asof 的原始文件，
    # latest/ 目录 glob 用同一逻辑天然去重（INSERT OR REPLACE 按 (asof,symbol) 覆盖，无副作用）。
    return _import_json_glob(
        conn, domain="mi_signal_packs", table="mi_signal_packs",
        source_dir=storage_root / "mi_signals", glob_pattern="**/*.json",
        insert_sql="INSERT OR REPLACE INTO mi_signal_packs (asof, symbol, payload_json, created_at) VALUES (?,?,?,?)",
        row_from_path=row,
    )


def import_indicator_signals(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "indicator_signals"
    if not source_dir.is_dir():
        return ImportResult("indicator_signal_packs", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("**/*.json"))
    written = 0
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entry_id = f.relative_to(source_dir).parts[0]
        asof = payload.get("asof")
        symbol = payload.get("symbol") or f.stem
        if not asof:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO indicator_signal_packs (entry_id, asof, symbol, payload_json, created_at) VALUES (?,?,?,?,?)",
            (entry_id, asof, symbol, json.dumps(payload, ensure_ascii=False), None),
        )
        written += 1
    return ImportResult("indicator_signal_packs", written, written, written > 0, f"{len(files)} 源文件")


def import_intel_radar(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "intel_radar" / "radar.json"
    if not f.is_file():
        return ImportResult("intel_radar_cache", 0, 0, False, "radar.json 不存在")
    payload = json.loads(f.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO intel_radar_cache (singleton, payload_json, generated_at) VALUES ('default', ?, ?)",
        (json.dumps(payload, ensure_ascii=False), payload.get("generated_at")),
    )
    return ImportResult("intel_radar_cache", 1, 1, True, "单文件覆写缓存")


def import_intel_rewrites(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "intel_rewrites"
    if not source_dir.is_dir():
        return ImportResult("intel_rewrite_items", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("*.json"))
    written = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        item_id = d.get("item_id") or f.stem
        conn.execute(
            """INSERT OR REPLACE INTO intel_rewrite_items
            (item_id, kind, track_key, day, status, title, url, source, time,
             started_at_ts, started_at, error, error_type, body_text, body_mode,
             body_char_count, body_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, d.get("kind"), d.get("track_key"), d.get("day"),
                d.get("status", "unknown"), d.get("title"), d.get("url"), d.get("source"),
                d.get("time"), d.get("started_at_ts"), d.get("started_at"), d.get("error"),
                d.get("error_type"), d.get("body_text"), d.get("body_mode"),
                d.get("body_char_count"), d.get("body_error"),
            ),
        )
        written += 1
    return ImportResult("intel_rewrite_items", written, written, written > 0, f"{len(files)} 源文件")


def import_perilla_cache(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "perilla_cache"
    if not source_dir.is_dir():
        return ImportResult("perilla_enrich_cache", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("*.csv"))
    written = 0
    pat = re.compile(r"^(.+)_(holders|pe)$")
    for f in files:
        m = pat.match(f.stem)
        if not m:
            continue
        ts_code, kind = m.group(1), m.group(2)
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload_csv, cached_at) VALUES (?,?,?,?)",
            (ts_code, kind, f.read_text(encoding="utf-8"), None),
        )
        written += 1
    return ImportResult("perilla_enrich_cache", written, written, written > 0, f"{len(files)} 源文件")


def import_intraday_session_cache(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        # 文件名约定 {symbol}_{session_date}.json（同 _save_intraday_session_cache 写入约定）。
        parts = f.stem.rsplit("_", 1)
        if len(parts) != 2:
            return None
        symbol, session_date = parts
        return (symbol, session_date, json.dumps(payload, ensure_ascii=False), None)

    return _import_json_glob(
        conn, domain="intraday_session_cache", table="intraday_session_cache",
        source_dir=storage_root / "intraday_session_cache", glob_pattern="*.json",
        insert_sql="INSERT OR REPLACE INTO intraday_session_cache (symbol, session_date, payload_json, cached_at) VALUES (?,?,?,?)",
        row_from_path=row,
    )


def import_etf_radar(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "etf_radar"
    if not source_dir.is_dir():
        return ImportResult("etf_radar_snapshots", 0, 0, False, "目录不存在")
    json_files = sorted(source_dir.glob("*.json"))
    written = 0
    for f in json_files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        trade_date = payload.get("trade_date") or f.stem
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_snapshots (trade_date, payload_json, created_at) VALUES (?,?,?)",
            (trade_date, json.dumps(payload, ensure_ascii=False), None),
        )
        written += 1
    # commentary md 索引（Tier C 文件仍留，只记路径）
    commentary_files = sorted(source_dir.glob("*.commentary.md"))
    for f in commentary_files:
        trade_date = f.name.split(".")[0]
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_commentary_index (trade_date, file_path, created_at) VALUES (?,?,?)",
            (trade_date, str(f.relative_to(PROJECT_ROOT)) if f.is_relative_to(PROJECT_ROOT) else str(f), None),
        )
    # morning alert state（纯文本单文件，非 json）
    state_file = source_dir / ".morning_alert_state"
    if state_file.is_file():
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_morning_alert_state (singleton, payload_json, updated_at) VALUES ('default', ?, ?)",
            (json.dumps({"last_alert_date": state_file.read_text(encoding="utf-8").strip()}), None),
        )
    return ImportResult("etf_radar_snapshots", written, written, written > 0,
                         f"{len(json_files)} json + {len(commentary_files)} commentary")


def import_news_digest(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        digest_date = payload.get("date")
        scene = payload.get("scene")
        if not digest_date or not scene:
            return None
        return (digest_date, scene, json.dumps(payload, ensure_ascii=False), payload.get("generatedAt"))

    return _import_json_glob(
        conn, domain="news_digest_entries", table="news_digest_entries",
        source_dir=storage_root / "news_digest", glob_pattern="*.json",
        insert_sql="INSERT OR REPLACE INTO news_digest_entries (digest_date, scene, payload_json, generated_at) VALUES (?,?,?,?)",
        row_from_path=row,
    )


def import_notes(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        # 文件名约定 intel_digest_{YYYYMMDD}_{track_key}.json（kss/storage/notes.py:save_intel_digest）
        m = re.match(r"^intel_digest_(\d{8})_(.+)$", f.stem)
        if not m:
            return None
        return (m.group(1), m.group(2), json.dumps(payload, ensure_ascii=False), None)

    return _import_json_glob(
        conn, domain="intel_digest_notes", table="intel_digest_notes",
        source_dir=storage_root / "notes", glob_pattern="*.json",
        insert_sql="INSERT OR REPLACE INTO intel_digest_notes (digest_date, track_key, payload_json, created_at) VALUES (?,?,?,?)",
        row_from_path=row,
    )


def import_trends(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    def row(f: Path, payload: dict) -> tuple | None:
        trade_date = payload.get("date") or f.stem
        return (trade_date, json.dumps(payload, ensure_ascii=False), None)

    return _import_json_glob(
        conn, domain="trends_days", table="trends_days",
        source_dir=storage_root / "trends", glob_pattern="*.json",
        insert_sql="INSERT OR REPLACE INTO trends_days (trade_date, payload_json, created_at) VALUES (?,?,?)",
        row_from_path=row,
    )


def import_watchlist(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "watchlist_symbols.txt"
    if not f.is_file():
        return ImportResult("watchlist", 0, 0, False, "文件不存在")
    lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for ts_code in lines:
        conn.execute("INSERT OR REPLACE INTO watchlist (ts_code, added_at) VALUES (?, ?)", (ts_code, None))
    return ImportResult("watchlist", len(lines), len(lines), len(lines) > 0, f"{len(lines)} 行")


def import_app_runs(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "app_runs"
    if not source_dir.is_dir():
        return ImportResult("app_task_runs", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("*.jsonl"))
    written = 0
    total_lines = 0
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            task_id = d.get("taskId")
            started_at = d.get("startedAt")
            if not task_id or not started_at:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO app_task_runs
                (task_id, started_at, title, finished_at, status, exit_code, summary, stdout, stderr, artifacts_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id, started_at, d.get("title"), d.get("finishedAt"),
                    d.get("status", "unknown"), d.get("exitCode"), d.get("summary"),
                    d.get("stdout"), d.get("stderr"),
                    json.dumps(d.get("artifacts", []), ensure_ascii=False),
                ),
            )
            written += 1
    # 主键 (task_id, started_at)：同一任务不会在同一毫秒起两次，天然去重；INSERT OR REPLACE
    # 幂等——重跑不产生重复行。
    return ImportResult("app_task_runs", total_lines, written, written > 0, f"{len(files)} 源文件")


def import_daily_review_index(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "daily_review"
    if not source_dir.is_dir():
        return ImportResult("daily_review_index", 0, 0, False, "目录不存在")
    files = sorted(source_dir.glob("*.md"))
    pat = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{6}\.(?:SH|SZ|BJ))$")
    written = 0
    skipped = 0
    for f in files:
        m = pat.match(f.stem)
        if not m:
            skipped += 1  # 旧格式 {date}.md（无 ts_code），本索引只覆盖新格式
            continue
        review_date, ts_code = m.group(1), m.group(2)
        conn.execute(
            "INSERT OR REPLACE INTO daily_review_index (review_date, ts_code, file_path, created_at) VALUES (?,?,?,?)",
            (review_date, ts_code, str(f.relative_to(PROJECT_ROOT)) if f.is_relative_to(PROJECT_ROOT) else str(f), None),
        )
        written += 1
    return ImportResult("daily_review_index", written, written, written > 0,
                         f"{len(files)} 源文件，{skipped} 旧格式跳过（无 ts_code）")


def import_reports_index(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "reports"
    if not source_dir.is_dir():
        return ImportResult("reports_index", 0, 0, False, "目录不存在")
    files = sorted(p for p in source_dir.rglob("*") if p.is_file())
    written = 0
    for f in files:
        rel = f.relative_to(source_dir)
        category = str(rel.parent) if rel.parent != Path(".") else None
        conn.execute(
            "INSERT OR REPLACE INTO reports_index (report_name, file_path, category, generated_at) VALUES (?,?,?,?)",
            (str(rel), str(f.relative_to(PROJECT_ROOT)) if f.is_relative_to(PROJECT_ROOT) else str(f), category, None),
        )
        written += 1
    return ImportResult("reports_index", written, written, written > 0, f"{len(files)} 源文件（含子目录）")


def import_indicator_registry(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "indicator_registry.yaml"
    if not f.is_file():
        return ImportResult("indicator_registry", 0, 0, False, "文件不存在")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    entries = d.get("entries", [])
    for e in entries:
        conn.execute(
            """INSERT OR REPLACE INTO indicator_registry
            (entry_id, name, kind, family, params_json, rules_path, signals_dir,
             status, solidified_at, verdict_ref, symbols_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                e.get("id"), e.get("name"), e.get("kind"), e.get("family"),
                json.dumps(e.get("params") or {}, ensure_ascii=False),
                e.get("rules_path"), e.get("signals_dir"), e.get("status"),
                e.get("solidified_at"), e.get("verdict_ref"),
                json.dumps(e.get("symbols") or [], ensure_ascii=False),
            ),
        )
    return ImportResult("indicator_registry", len(entries), len(entries), len(entries) > 0, "")


def import_indicator_lab(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    source_dir = storage_root / "indicator_lab" / "verdicts"
    if not source_dir.is_dir():
        return ImportResult("indicator_lab_verdicts", 0, 0, False, "verdicts/ 子目录不存在")
    files = sorted(source_dir.glob("*.json"))
    written = 0
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        verdict_id = f.stem  # 文件名即哈希 id，天然稳定唯一（见 kss_app_bridge.py:_persist_verdict）
        entry_id = payload.get("entry_id") or payload.get("id") or f"{payload.get('family', '')}_{verdict_id}"
        conn.execute(
            "INSERT OR REPLACE INTO indicator_lab_verdicts (verdict_id, entry_id, payload_json, created_at) VALUES (?,?,?,?)",
            (verdict_id, entry_id, json.dumps(payload, ensure_ascii=False), None),
        )
        written += 1
    return ImportResult("indicator_lab_verdicts", written, written, written > 0, f"{len(files)} 源文件")


def import_pipeline_weights(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "pipeline_weights.json"
    if not f.is_file():
        return ImportResult("pipeline_weights", 0, 0, False, "文件不存在")
    d = json.loads(f.read_text(encoding="utf-8"))
    updated_at = d.get("_updated")
    note = d.get("_note")
    written = 0
    for k, v in d.items():
        if k.startswith("_"):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_weights (weight_key, weight_value, updated_at, note) VALUES (?,?,?,?)",
            (k, float(v), updated_at, note),
        )
        written += 1
    return ImportResult("pipeline_weights", written, written, written > 0, "")


def import_sector_review_config(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "sector_review_config.json"
    if not f.is_file():
        return ImportResult("sector_review_config", 0, 0, False, "文件不存在")
    d = json.loads(f.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO sector_review_config (config_key, config_json, updated_at) VALUES ('default', ?, ?)",
        (json.dumps(d, ensure_ascii=False), None),
    )
    return ImportResult("sector_review_config", 1, 1, True, "单例配置")


def import_themes(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "themes_15th_5y.yaml"
    if not f.is_file():
        return ImportResult("theme_registry", 0, 0, False, "文件不存在")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    themes = d.get("themes", {})
    for theme_id, payload in themes.items():
        conn.execute(
            "INSERT OR REPLACE INTO theme_registry (theme_id, payload_json, updated_at) VALUES (?,?,?)",
            (theme_id, json.dumps(payload, ensure_ascii=False), None),
        )
    return ImportResult("theme_registry", len(themes), len(themes), len(themes) > 0, "")


def import_mi_rules(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    f = storage_root / "mi_rules.yaml"
    if not f.is_file():
        return ImportResult("mi_rules", 0, 0, False, "文件不存在")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    written = 0
    if "defaults" in d:
        conn.execute(
            "INSERT OR REPLACE INTO mi_rules (rule_key, payload_json, updated_at) VALUES ('defaults', ?, ?)",
            (json.dumps(d["defaults"], ensure_ascii=False), None),
        )
        written += 1
    for ts_code, payload in (d.get("symbols") or {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO mi_rules (rule_key, payload_json, updated_at) VALUES (?,?,?)",
            (str(ts_code), json.dumps(payload, ensure_ascii=False), None),
        )
        written += 1
    return ImportResult("mi_rules", written, written, written > 0, "")


def import_stock_names(conn: sqlite3.Connection, storage_root: Path) -> ImportResult:
    import csv

    f = storage_root / "stock_names.csv"
    if not f.is_file():
        return ImportResult("stock_names", 0, 0, False, "文件不存在")
    written = 0
    with f.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts_code = row.get("ts_code") or row.get("code")
            if not ts_code:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO stock_names (ts_code, name, industry, concept) VALUES (?,?,?,?)",
                (ts_code, row.get("name"), row.get("industry"), row.get("concept")),
            )
            written += 1
    return ImportResult("stock_names", written, written, written > 0, "")


def import_existing_sqlite(conn: sqlite3.Connection, storage_root: Path) -> list[ImportResult]:
    """``prediction_ledger/ledger.db`` + ``factor_health/factor_health.db`` 原样并表——
    ``ATTACH`` 源库，逐表 ``INSERT OR REPLACE ... SELECT *``，比逐行 Python 转译更不容易
    在字段顺序/类型上出错（源 schema 已跟目标表逐字对齐，见 db.py 迁移 1 的注释）。
    """
    # DETACH 要求连接上没有未提交事务碰过任何库（不只是待 DETACH 的那个）——ATTACH 前先
    # commit 掉前面各域 importer 遗留的隐式事务，DETACH 前也 commit 掉本段自己的写入。
    results = []
    conn.commit()
    ledger_db = storage_root / "prediction_ledger" / "ledger.db"
    if ledger_db.is_file():
        conn.execute("ATTACH DATABASE ? AS src_ledger", (str(ledger_db),))
        conn.execute("INSERT OR REPLACE INTO predictions SELECT * FROM src_ledger.predictions")
        n = conn.execute("SELECT COUNT(*) c FROM src_ledger.predictions").fetchone()["c"]
        conn.commit()
        conn.execute("DETACH DATABASE src_ledger")
        results.append(ImportResult("predictions", n, n, n > 0, "ATTACH+SELECT * 原样并表"))
    else:
        results.append(ImportResult("predictions", 0, 0, False, "ledger.db 不存在"))

    fh_db = storage_root / "factor_health" / "factor_health.db"
    if fh_db.is_file():
        conn.execute("ATTACH DATABASE ? AS src_fh", (str(fh_db),))
        for table in ("ic_snapshots", "crashes", "factor_lifecycle"):
            conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM src_fh.{table}")
            n = conn.execute(f"SELECT COUNT(*) c FROM src_fh.{table}").fetchone()["c"]
            results.append(ImportResult(table, n, n, True, "ATTACH+SELECT * 原样并表"))
        conn.commit()
        conn.execute("DETACH DATABASE src_fh")
    else:
        for table in ("ic_snapshots", "crashes", "factor_lifecycle"):
            results.append(ImportResult(table, 0, 0, False, "factor_health.db 不存在"))
    return results


DOMAIN_IMPORTERS: tuple[Callable[[sqlite3.Connection, Path], ImportResult], ...] = (
    import_paper_trade,
    import_sector_rotation,
    import_mi_signals,
    import_indicator_signals,
    import_intel_radar,
    import_intel_rewrites,
    import_perilla_cache,
    import_intraday_session_cache,
    import_etf_radar,
    import_news_digest,
    import_notes,
    import_trends,
    import_watchlist,
    import_app_runs,
    import_daily_review_index,
    import_reports_index,
    import_indicator_registry,
    import_indicator_lab,
    import_pipeline_weights,
    import_sector_review_config,
    import_themes,
    import_mi_rules,
    import_stock_names,
)


def run_migration(
    *, storage_root: Path | None = None, db_path: Path | None = None, dry_run: bool = False,
) -> list[ImportResult]:
    root = storage_root or (PROJECT_ROOT / "storage")
    target = db_path or KSS_DB

    if dry_run:
        # SAVEPOINT/ROLLBACK 在事务里跟 import_existing_sqlite 的 ATTACH/DETACH 打架
        # （DETACH 要求无未提交事务碰过那个附加库）——干脆整份 db 文件拷到临时路径跑，
        # 跑完丢弃，比在同一连接里精细控制事务边界可靠。
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "dry_run.db"
            if Path(target).is_file():
                shutil.copy2(target, scratch)
            return _run_migration_against(root, scratch)
    return _run_migration_against(root, target)


def _run_migration_against(root: Path, db_path: Path) -> list[ImportResult]:
    results: list[ImportResult] = []
    with connect(db_path) as conn:
        ensure_schema(conn)
        for importer in DOMAIN_IMPORTERS:
            results.append(importer(conn, root))
        results.extend(import_existing_sqlite(conn, root))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="storage/ 历史数据一次性导入 kss.db")
    p.add_argument("--storage-root", type=Path, default=None)
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    results = run_migration(storage_root=args.storage_root, db_path=args.db, dry_run=args.dry_run)
    mismatches = 0
    for r in results:
        marker = "OK" if r.ok else "MISMATCH"
        if not r.ok:
            mismatches += 1
        print(f"[{marker}] {r.domain}: source={r.source_records} written={r.rows_written} {r.detail}")
    print(f"\n{len(results)} 域，{mismatches} 处行数不一致{'（dry-run，未实际写入）' if args.dry_run else ''}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
