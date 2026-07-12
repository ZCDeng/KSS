"""统一 Tier A 库连接工厂 + schema 迁移器（plan 2026-07-12-005 / U14, KTD1）.

设计纪律：

- **落点**：``kss/config/paths.py:KSS_DB``（``STATE_ROOT/storage/kss.db``）——不硬编码，
  与 bundle 双根解析共用同一真相源。
- **连接**：WAL + ``busy_timeout``，与 ``kss/prediction/ledger.py`` / ``kss/backtest/factor_health.py``
  既有两个 sqlite 库的连接纪律逐字一致（多进程写安全：cron 短进程 + app/sidecar 长进程并发写不
  ``database is locked``，等而不炸）。
- **STRICT 表**：全部表 ``) STRICT;`` 收尾——防 DuckDB 后续 ``ATTACH ... (TYPE sqlite)`` 时
  列因宽松类型退化成 VARCHAR（KTD1 原话）。
- **复杂嵌套域用 payload_json 兜底，不强行拆列**：``sector_rotation`` / ``etf_radar`` /
  ``news_digest`` / ``trends`` / ``mi_signals`` / ``indicator_signals`` / ``intel_radar`` 六域的
  源 JSON 结构深、字段随策略迭代常变（如 etf_radar 的 ``themes`` 是动态 key 的字典）。把这些
  拆成人工设计的列表，覆盖不全就是静默丢字段——比保留原始 JSON 更危险。这些域改为「索引列
  （日期/symbol，供 WHERE 用）+ ``payload_json TEXT NOT NULL``（原始内容原样存）」，STRICT 表
  仍然成立（``payload_json`` 是真实的 TEXT 类型，不是「假装严格」），DuckDB/Seesaw 一侧可用
  ``json_extract`` 查具体字段（U16 范围）。已有稳定关系型 schema 的域（``predictions`` /
  ``crashes`` / ``factor_lifecycle`` / ``ic_snapshots``，来自既有的 ``ledger.db`` /
  ``factor_health.db``）原样迁入，不重新设计。

- **schema 版本化**：``schema_migrations`` 记录已应用的迁移版本号；``ensure_schema()`` 幂等——
  重复调用只应用尚未记录的迁移，不重跑已应用的 DDL（``CREATE TABLE IF NOT EXISTS`` 本身也幂等，
  双保险）。新增表/加列一律追加新迁移，不改历史迁移的 SQL 文本。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kss.config.paths import KSS_DB

# ---------------------------------------------------------------------------
# 连接工厂
# ---------------------------------------------------------------------------


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """打开 kss.db 连接（WAL + busy_timeout），退出时提交并关闭。

    与 ``PredictionLedger._connect`` / ``FactorHealthTracker._connect`` 完全同款纪律。

    ``PRAGMA journal_mode=WAL`` 只在库尚未是 WAL 模式时才发——journal mode 是持久化在
    文件里的属性，一旦某连接把它设成功，此后所有连接天然继承，无需重发。这不只是省一次
    调用：journal-mode 切换本身需要短暂独占访问，若多个进程/线程在**库刚创建、尚无人设过
    WAL** 的窗口内同时首次连接，都去发 `PRAGMA journal_mode=WAL`，会彼此撞见
    ``database is locked``——且这个特定 pragma 的失败不吃 ``busy_timeout``（该 pragma 只
    对常规读写锁生效，不对 journal-mode 切换生效），所以只能靠减少「谁去切」的次数来避免，
    重试不是这里的正确修法。加一次读探测把切换收窄到「真正的第一个连接」。
    """
    path = Path(db_path) if db_path is not None else KSS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    current_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(current_mode).lower() != "wal":
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# schema 迁移
# ---------------------------------------------------------------------------

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
) STRICT
"""

# 每个迁移是一份完整、独立可重放的 DDL 文本；新增迁移只追加，不改历史条目。
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        -- ---------------------------------------------------------------
        -- 既有 sqlite 库原样并表（kss/prediction/ledger.py / kss/backtest/factor_health.py）
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id            TEXT PRIMARY KEY,
            prediction_date          TEXT NOT NULL,
            symbol                   TEXT NOT NULL,
            strategy                 TEXT NOT NULL,
            pipeline_snapshot        TEXT,
            regime_label             TEXT,
            factor_value             REAL,
            rank_pct                 REAL,
            rank_position            INTEGER,
            planned_weight           REAL,
            status                   TEXT NOT NULL,
            t1_open                  REAL,
            t2_open                  REAL,
            realized_ret             REAL,
            outcome                  TEXT,
            settled_at               TEXT,
            attribution_category     TEXT,
            attribution_note         TEXT,
            attribution_generated_at TEXT,
            created_at               TEXT,
            updated_at               TEXT
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(prediction_date);
        CREATE INDEX IF NOT EXISTS idx_pred_symbol ON predictions(symbol);
        CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status);

        CREATE TABLE IF NOT EXISTS ic_snapshots (
            factor_id        TEXT NOT NULL,
            window_end       TEXT NOT NULL,
            source           TEXT NOT NULL,
            ic_mean          REAL,
            ic_std           REAL,
            icir             REAL,
            ic_positive_rate REAL,
            n_periods        INTEGER,
            ic_t_stat        REAL,
            half_life_1d     REAL,
            half_life_5d     REAL,
            half_life_20d    REAL,
            method           TEXT,
            created_at       TEXT,
            updated_at       TEXT,
            PRIMARY KEY (factor_id, window_end, source)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_ic_factor ON ic_snapshots(factor_id);
        CREATE INDEX IF NOT EXISTS idx_ic_window ON ic_snapshots(window_end);

        CREATE TABLE IF NOT EXISTS crashes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_id    TEXT NOT NULL,
            window_start TEXT,
            window_end   TEXT,
            crash_type   TEXT NOT NULL,
            ic_mean      REAL,
            icir         REAL,
            notes        TEXT,
            logged_at    TEXT
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_crash_factor ON crashes(factor_id);
        CREATE INDEX IF NOT EXISTS idx_crash_type ON crashes(crash_type);

        CREATE TABLE IF NOT EXISTS factor_lifecycle (
            factor_id     TEXT PRIMARY KEY,
            state         TEXT NOT NULL,
            updated_at    TEXT,
            events        TEXT
        ) STRICT;

        -- ---------------------------------------------------------------
        -- 台账/追加型域
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS paper_trade_picks (
            prediction_date  TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            generated_at     TEXT,
            strategy         TEXT,
            top_pct          REAL,
            top_n            INTEGER,
            factor_value     REAL,
            rank_pct         REAL,
            rank_position    INTEGER,
            planned_weight   REAL,
            PRIMARY KEY (prediction_date, symbol)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS app_task_runs (
            task_id      TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            title        TEXT,
            finished_at  TEXT,
            status       TEXT NOT NULL,
            exit_code    INTEGER,
            summary      TEXT,
            stdout       TEXT,
            stderr       TEXT,
            artifacts_json TEXT,
            PRIMARY KEY (task_id, started_at)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_app_run_started ON app_task_runs(started_at);

        CREATE TABLE IF NOT EXISTS intel_rewrite_items (
            item_id           TEXT NOT NULL,
            kind              TEXT NOT NULL DEFAULT 'investment',
            track_key         TEXT,
            day               TEXT,
            status            TEXT NOT NULL,
            payload_json      TEXT NOT NULL,
            created_at        TEXT,
            PRIMARY KEY (item_id, kind)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_rewrite_track ON intel_rewrite_items(track_key);
        CREATE INDEX IF NOT EXISTS idx_rewrite_day ON intel_rewrite_items(day);

        CREATE TABLE IF NOT EXISTS perilla_enrich_cache (
            ts_code      TEXT NOT NULL,
            kind         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            cached_at    TEXT,
            PRIMARY KEY (ts_code, kind)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS intraday_session_cache (
            symbol           TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            session_date     TEXT,
            payload_json     TEXT NOT NULL,
            cached_at        TEXT,
            PRIMARY KEY (symbol, interval_minutes)
        ) STRICT;

        -- ---------------------------------------------------------------
        -- 复杂嵌套域：索引列 + payload_json 原样保留
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS sector_rotation_snapshots (
            trade_date    TEXT PRIMARY KEY,
            payload_json  TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS mi_signal_packs (
            asof          TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            created_at    TEXT,
            PRIMARY KEY (asof, symbol)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS indicator_signal_packs (
            entry_id      TEXT NOT NULL,
            asof          TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            created_at    TEXT,
            PRIMARY KEY (entry_id, asof, symbol)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS intel_radar_cache (
            singleton     TEXT PRIMARY KEY DEFAULT 'default',
            payload_json  TEXT NOT NULL,
            generated_at  TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS etf_radar_snapshots (
            trade_date    TEXT PRIMARY KEY,
            payload_json  TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS etf_radar_morning_alert_state (
            singleton     TEXT PRIMARY KEY DEFAULT 'default',
            payload_json  TEXT NOT NULL,
            updated_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS news_digest_entries (
            digest_date   TEXT NOT NULL,
            scene         TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            generated_at  TEXT,
            PRIMARY KEY (digest_date, scene)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS trends_days (
            trade_date    TEXT PRIMARY KEY,
            payload_json  TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS intel_digest_notes (
            digest_date   TEXT NOT NULL,
            track_key     TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            created_at    TEXT,
            PRIMARY KEY (digest_date, track_key)
        ) STRICT;

        -- ---------------------------------------------------------------
        -- 索引表（正文文件仍留 Tier C，这里只存定位用的元数据）
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS daily_review_index (
            review_date   TEXT NOT NULL,
            ts_code       TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            created_at    TEXT,
            PRIMARY KEY (review_date, ts_code)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS reports_index (
            report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            report_name   TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            category      TEXT,
            generated_at  TEXT
        ) STRICT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_name ON reports_index(report_name);

        CREATE TABLE IF NOT EXISTS etf_radar_commentary_index (
            trade_date    TEXT PRIMARY KEY,
            file_path     TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;

        -- ---------------------------------------------------------------
        -- 注册表/静态配置（人工维护，程序化读取）
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS indicator_registry (
            entry_id       TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            kind           TEXT NOT NULL,
            family         TEXT,
            params_json    TEXT,
            rules_path     TEXT,
            signals_dir    TEXT,
            status         TEXT NOT NULL,
            solidified_at  TEXT,
            verdict_ref    TEXT,
            symbols_json   TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS indicator_lab_verdicts (
            verdict_id    TEXT PRIMARY KEY,
            entry_id      TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_verdict_entry ON indicator_lab_verdicts(entry_id);

        CREATE TABLE IF NOT EXISTS pipeline_weights (
            weight_key    TEXT PRIMARY KEY,
            weight_value  REAL NOT NULL,
            updated_at    TEXT,
            note          TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS sector_review_config (
            config_key    TEXT PRIMARY KEY DEFAULT 'default',
            config_json   TEXT NOT NULL,
            updated_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS theme_registry (
            theme_id      TEXT PRIMARY KEY,
            payload_json  TEXT NOT NULL,
            updated_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS mi_rules (
            rule_key      TEXT PRIMARY KEY,
            payload_json  TEXT NOT NULL,
            updated_at    TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS stock_names (
            ts_code       TEXT PRIMARY KEY,
            name          TEXT,
            industry      TEXT,
            concept       TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS watchlist (
            ts_code       TEXT PRIMARY KEY,
            position      INTEGER NOT NULL DEFAULT 0,
            added_at      TEXT
        ) STRICT;
        """,
    ),
)


def ensure_schema(conn: sqlite3.Connection) -> list[int]:
    """应用尚未记录的迁移；返回本次新应用的版本号列表（幂等，重复调用返回空列表）。

    多连接首次并发建库时，「查 applied → 判断未应用 → 记录」这三步不是原子的——两个连接
    都可能读到「未应用」再各自去 INSERT 同一个 version，撞主键。DDL 本身
    （``CREATE TABLE IF NOT EXISTS`` 等）天然幂等，唯一需要兜底的是这条记录 insert：
    ``OR IGNORE`` 让输的那一方安静地什么都不做，而不是抛 IntegrityError 炸调用方。
    """
    conn.execute(_MIGRATIONS_TABLE)
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    newly_applied: list[int] = []
    for version, ddl in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(ddl)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        newly_applied.append(version)
    return newly_applied


def ensure_schema_at(db_path: str | Path | None = None) -> list[int]:
    """便捷入口：开连接 + 建库 + 关闭。"""
    with connect(db_path) as conn:
        return ensure_schema(conn)
