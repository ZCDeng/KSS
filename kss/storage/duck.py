"""Seesaw 分析 SQL 工具 — DuckDB 只读会话工厂（plan 2026-07-12-005 / U16，KTD11）。

设计要点（均经真机验证，非纯凭文档假设——DuckDB 1.5.4 实测）：

- 会话临时/每进程/内存态：``duckdb.connect(':memory:')``，用完即弃，永不作持久落点（KTD1）。
- **不 ATTACH 整个 kss.db**——ATTACH 会把库内全部表（含未割接域的陈旧快照表，如
  mi_rules/pipeline_weights/theme_registry）一次性暴露给用户查询，且 ATTACH 语句本身
  与 sqlite 扩展的 ``sqlite_scan``/``sqlite_attach``/``sqlite_query`` 三个表函数都**不受
  ``allowed_directories``/``enable_external_access`` 引擎级围栏约束**（实测：即使锁死配置，
  这三个函数仍可读任意路径的 sqlite 文件，见 test_duck_query.py 对应用例）。因此改为：
  只对割接台账门控通过的表逐个 ``CREATE VIEW <table> AS SELECT * FROM
  sqlite_scan(<db_path>, <table>)``——sqlite_scan 调用本身在建会话阶段执行（可信参数，
  非用户输入），未建视图的表（含 mi_rules 等）用户查询不到，天然满足「未割接域的表不进
  可见目录且查询被拒」（Approach 原文）。用户查询里出现 sqlite_scan/sqlite_attach/
  sqlite_query/ATTACH 一律在语句校验阶段拒绝（见 ``validate_query``）。
- 引擎级围栏仍然保留，覆盖 read_csv/read_text/glob/read_parquet 等核心文件函数
  （这些函数正确遵守 allowed_directories，实测验证）：``SET allowed_directories=[...]``
  必须先于 ``SET enable_external_access=false``，顺序反过来会报错（DuckDB 1.5.4 实测）；
  最后 ``SET lock_configuration=true`` 钉死，任何后续 SET 改配置都会被拒绝。
- 双根：macro parquet 在 PROJECT_ROOT/storage/macro（KTD-9 双根纪律），kss.db 在
  STATE_ROOT/storage——``allowed_directories`` 须同时含两个根，否则 bundle 模式下
  macro 数据集的视图查询会在建会话之后才报错（视图定义惰性求值，建视图时不受限，
  查询视图时才受限）。
- 只注册 parquet 数据集视图，不注册 cs_data/mf 等 csv-glob 数据集：这两者的物理文件由
  update_cs_data.py 直接写在 STATE_ROOT 顶层（不在 storage/ 树下），纳入会迫使
  allowed_directories 放宽到整个 STATE_ROOT，让 read_text/read_csv/glob 能读到 storage/
  之外的仓库源码/配置文件，与 KTD11 白名单目录围栏的意图相悖（实测验证：见
  test_duck_query.py 对应用例）。AE9 的换手率需求已由 parquet 数据集
  dailybasic_latest/hs300_dailybasic 的 turnover_rate 列覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kss.config.paths import PROJECT_ROOT, STATE_ROOT
from kss.storage.migration_ledger import load_ledger

# 域名(migration_ledger.json 键) → 该域覆盖的 kss.db 表名（可能不止一张，见 factor_health 域）。
# 只有出现在此映射里的表才有资格被暴露；映射之外的表（含 schema_migrations/sqlite_sequence
# 等基础设施表）永远不可见，即使未来 ledger 意外多出一个同名域也不会自动放行未知表。
DOMAIN_TABLES: dict[str, tuple[str, ...]] = {
    "watchlist": ("watchlist",),
    "indicator_registry": ("indicator_registry",),
    "sector_review_config": ("sector_review_config",),
    "stock_names": ("stock_names",),
    "indicator_lab_verdicts": ("indicator_lab_verdicts",),
    "intel_rewrite_items": ("intel_rewrite_items",),
    "intel_digest_notes": ("intel_digest_notes",),
    "app_task_runs": ("app_task_runs",),
    "perilla_enrich_cache": ("perilla_enrich_cache",),
    "intraday_session_cache": ("intraday_session_cache",),
    "mi_signal_packs": ("mi_signal_packs",),
    "indicator_signal_packs": ("indicator_signal_packs",),
    "intel_radar_cache": ("intel_radar_cache",),
    "sector_rotation_snapshots": ("sector_rotation_snapshots",),
    "paper_trade_picks": ("paper_trade_picks",),
    "predictions": ("predictions",),
    "factor_health_ic_snapshots_crashes_lifecycle": ("ic_snapshots", "crashes", "factor_lifecycle"),
    "daily_review_index": ("daily_review_index",),
    "reports_index": ("reports_index",),
    "etf_radar_snapshots": ("etf_radar_snapshots", "etf_radar_commentary_index", "etf_radar_morning_alert_state"),
    "news_digest_entries": ("news_digest_entries",),
    "trends_days": ("trends_days",),
}

# 语句必须以此开头（大小写不敏感，去前导空白/注释后判断）。
_ALLOWED_LEADING = ("SELECT", "WITH", "SUMMARIZE", "DESCRIBE")

# 全文黑名单：语句级关键词（DDL/DML/会话控制/危险扩展函数）。全部按词边界匹配，
# 防止 "selected_col" 之类的列名被 "SELECT" 之外的子串（如 "SET" 在 "asset" 里）误伤。
_BANNED_KEYWORDS = (
    "ATTACH", "DETACH", "COPY", "INSTALL", "LOAD", "PRAGMA", "SET", "RESET",
    "EXPORT", "IMPORT", "CALL", "CREATE", "DROP", "ALTER", "INSERT", "UPDATE",
    "DELETE", "TRUNCATE", "VACUUM", "CHECKPOINT", "BEGIN", "COMMIT", "ROLLBACK",
    "GRANT", "REVOKE",
    # sqlite 扩展的表函数——实测绕过 allowed_directories/enable_external_access 引擎围栏，
    # 必须在语句校验层单独拦截（不能指望引擎级围栏，见模块 docstring）。
    "SQLITE_ATTACH", "SQLITE_QUERY", "SQLITE_SCAN",
    # 通用「执行任意 SQL 文本」原语——同样可能绕过外层前缀白名单（嵌套一层 SELECT 外壳）。
    "QUERY_TABLE", "JSON_EXECUTE_SERIALIZED_SQL", "READ_DUCKDB", "COPY_DATABASE",
    "IMPORT_DATABASE", "EXPORT_DATABASE",
)
_BANNED_RE = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(_BANNED_KEYWORDS) + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_LEADING_TOKEN_RE = re.compile(r"^\s*(?:--[^\n]*\n|\s)*([A-Za-z_][A-Za-z0-9_]*)")

ROW_LIMIT = 200
TIMEOUT_SECONDS = 5.0


class QueryRejected(Exception):
    """语句校验未通过（不下发到 DuckDB 执行）。"""


def validate_query(sql: str) -> None:
    """语句白名单 + 全文黑名单 + 单语句校验。校验失败抛 QueryRejected(message)。"""
    text = sql.strip()
    if not text:
        raise QueryRejected("空查询")

    # 单语句：允许结尾一个分号，不允许分号后还有非空白内容（多语句注入的经典形态）。
    stripped = text.rstrip()
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    if ";" in stripped:
        raise QueryRejected("只允许单条语句（检测到分号分隔的多条语句）")

    m = _LEADING_TOKEN_RE.match(text)
    leading = m.group(1).upper() if m else ""
    if leading not in _ALLOWED_LEADING:
        raise QueryRejected(f"语句须以 {'/'.join(_ALLOWED_LEADING)} 开头，实际以 {leading or '(空)'} 开头")

    banned = _BANNED_RE.search(text)
    if banned:
        raise QueryRejected(f"查询包含禁用关键词/函数: {banned.group(1)}")


def _visible_tables(ledger_path: Path | None = None) -> dict[str, str]:
    """域割接台账门控：只有 goldenResult=pass 的域，其映射表才可见。返回 {table_name: domain}。"""
    ledger = load_ledger(ledger_path)
    out: dict[str, str] = {}
    for domain, tables in DOMAIN_TABLES.items():
        entry = ledger.get(domain)
        if not entry or entry.get("goldenResult") != "pass":
            continue
        for tbl in tables:
            out[tbl] = domain
    return out


def visible_tables(ledger_path: Path | None = None) -> dict[str, str]:
    """``_visible_tables`` 的公开入口——供 build_data_catalog.py（U17）复用同一份域割接
    门控逻辑，避免在 catalog 反射侧另维护一份易漂移的表白名单。"""
    return _visible_tables(ledger_path)


def _catalog_datasets(catalog_path: Path) -> list[dict[str, Any]]:
    import json

    if not catalog_path.is_file():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("datasets", []) or []


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def open_session(
    *,
    db_path: Path | None = None,
    catalog_path: Path | None = None,
    project_root: Path | None = None,
    state_root: Path | None = None,
    ledger_path: Path | None = None,
    extension_directory: Path | None = None,
) -> Any:
    """建一个临时只读 DuckDB 会话：割接门控表视图 + parquet/csv-glob 视图 + 引擎级围栏。

    调用方用完即弃（不缓存/不复用连接——每次查询独立会话，简单可靠优先于性能，
    符合 KTD1「每进程用完即弃」纪律）。
    """
    import duckdb

    proot = project_root or PROJECT_ROOT
    sroot = state_root or STATE_ROOT
    kss_db = db_path or (sroot / "storage" / "kss.db")
    catalog = catalog_path or (sroot / "storage" / "data_catalog.json")

    con = duckdb.connect(":memory:")

    # 扩展目录：优先用打包期 vendor 好的本地目录（离线可加载）；不存在则退回 DuckDB
    # 默认行为（在线下载到 ~/.duckdb/extensions，仅首次需要网络）。见 Assumptions：
    # 打包脚本尚未接线自动 vendor 下载步骤（U16 范围内只做好这个开关，下载步骤留 packaging follow-up）。
    ext_dir = extension_directory or (sroot / ".duckdb_extensions")
    if ext_dir.is_dir():
        con.execute(f"SET extension_directory='{ext_dir.as_posix()}'")
    con.execute("INSTALL sqlite")
    con.execute("LOAD sqlite")

    # CREATE VIEW 不支持 prepared parameter（DuckDB 1.5.4 实测：执行时报
    # "BinderException: Unexpected prepared parameter. This type of statement
    # can't be prepared!"），所有视图定义只能内联字面量；下方涉及的路径/表名均来自
    # 内部常量或 _IDENTIFIER_RE 白名单过滤后的目录扫描结果，不含用户输入。
    def _sql_literal(s: str) -> str:
        return str(s).replace("'", "''")

    # 割接门控表视图。
    visible = _visible_tables(ledger_path)
    kss_db_literal = _sql_literal(kss_db)
    for tbl in visible:
        if not _IDENTIFIER_RE.match(tbl):
            continue  # 防御性：理论上 DOMAIN_TABLES 里的表名都合法，但不信任拼写错误
        con.execute(f"CREATE VIEW \"{tbl}\" AS SELECT * FROM sqlite_scan('{kss_db_literal}', '{tbl}')")

    # 行情数据集视图：仅 parquet（KTD11 approach 原文「视图注册来自 data_catalog（数据集名→
    # parquet 路径）」）。csv-glob 数据集（cs_data/mf）的物理文件由 update_cs_data.py 直接写在
    # STATE_ROOT 顶层，不在 STATE_ROOT/storage 树下——纳入会强迫 allowed_directories 放宽到整个
    # STATE_ROOT，让 read_text/read_csv/glob 能读到仓库源码/配置等 storage/ 之外的任意文件，
    # 与 KTD11 test scenario ⑥（白名单目录外路径被拒）直接冲突，故不做。AE9 的换手率需求已由
    # parquet 数据集 dailybasic_latest/hs300_dailybasic 的 turnover_rate 列覆盖，无需 cs_data。
    for ds in _catalog_datasets(catalog):
        name = ds.get("name")
        if not name or not _IDENTIFIER_RE.match(name) or ds.get("kind") != "parquet":
            continue
        root = proot if ds.get("root") == "project" else sroot
        path = root / ds.get("path", "")
        if path.is_file():
            con.execute(f"CREATE VIEW \"{name}\" AS SELECT * FROM read_parquet('{_sql_literal(path)}')")

    # 引擎级围栏（KTD11）：allowed_directories 必须先设，enable_external_access 才能改
    # （DuckDB 1.5.4 实测：顺序反过来会报 "Cannot change allowed_directories when
    # enable_external_access is disabled"）。双根：STATE_ROOT/storage + PROJECT_ROOT/storage/macro。
    allowed = {str(sroot / "storage"), str(proot / "storage" / "macro")}
    dirs_literal = ",".join(f"'{d}'" for d in sorted(allowed))
    con.execute(f"SET allowed_directories=[{dirs_literal}]")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")

    return con


def run_query(
    sql: str,
    *,
    db_path: Path | None = None,
    catalog_path: Path | None = None,
    project_root: Path | None = None,
    state_root: Path | None = None,
    ledger_path: Path | None = None,
    row_limit: int = ROW_LIMIT,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """校验 + 执行一条只读分析 SQL，返回结构化结果（bridge sql-query 命令的核心实现）。

    Returns:
        ``{"status": "ok", "columns": [...], "rows": [[...], ...], "rowCount": N,
        "truncated": bool}`` 或 ``{"status": "rejected"|"timeout"|"error", "reason": str}``。
    """
    import threading

    try:
        validate_query(sql)
    except QueryRejected as exc:
        return {"status": "rejected", "reason": str(exc)}

    con = open_session(
        db_path=db_path, catalog_path=catalog_path, project_root=project_root,
        state_root=state_root, ledger_path=ledger_path,
    )

    result: dict[str, Any] = {}
    error: BaseException | None = None

    def _work() -> None:
        nonlocal error
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(row_limit + 1)
            truncated = len(rows) > row_limit
            rows = rows[:row_limit]
            result["columns"] = cols
            result["rows"] = [list(r) for r in rows]
            result["rowCount"] = len(rows)
            result["truncated"] = truncated
        except BaseException as exc:  # noqa: BLE001 — 转成结构化结果，不让 dispatch 崩
            error = exc

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        try:
            con.interrupt()
        except Exception:  # noqa: BLE001
            pass
        thread.join(timeout=1.0)
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "timeout", "reason": f"查询超过 {timeout:g}s 超时熔断"}

    try:
        con.close()
    except Exception:  # noqa: BLE001
        pass

    if error is not None:
        return {"status": "error", "reason": str(error)[:300]}

    return {"status": "ok", **result}
