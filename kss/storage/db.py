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
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kss.config.paths import KSS_DB

try:
    import fcntl
except ImportError:  # pragma: no cover - KSSDesktop currently targets macOS
    fcntl = None  # type: ignore[assignment]

_MIGRATION_THREAD_LOCK = threading.RLock()

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
    对常规读写锁生效，不对 journal-mode 切换生效）。读探测把「谁去切」的次数降到最低，
    但读-判-写之间仍有 TOCTOU 窗口（多线程同时读到「未 WAL」再都去切）——加一层短重试：
    输的一方重试时会看到赢家已经切完，直接走「已是 WAL」分支跳过，而非在第一次撞锁就
    直接向上抛错（见 U15 域割接期间实测：8 次重跑偶发 ~2/5 失败率，纯读探测不够）。
    """
    path = Path(db_path) if db_path is not None else KSS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    for attempt in range(5):
        current_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current_mode).lower() == "wal":
            break
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
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
    (
        2,
        """
        -- ---------------------------------------------------------------
        -- 资讯雷达正文缓存（plan 2026-07-22-001 U2）：读穿缓存，结构化 markdown-lite 正文
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS intel_article_items (
            item_key     TEXT NOT NULL PRIMARY KEY,
            url          TEXT NOT NULL,
            title        TEXT,
            mode         TEXT NOT NULL,
            body         TEXT,
            body_md      TEXT,
            char_count   INTEGER,
            extractor    TEXT,
            fetched_at   TEXT
        ) STRICT;
        """,
    ),
    (
        3,
        """
        -- ---------------------------------------------------------------
        -- Deep Research execution stack (plan 2026-07-26):
        -- contract + evidence ledger + durable DAG + artifact/publication audit.
        -- Large content stays in storage/agent/research/objects; SQLite holds
        -- indexes, state, immutable evidence metadata and durable events.
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS research_goals (
            goal_id             TEXT PRIMARY KEY,
            session_id          TEXT,
            profile_id          TEXT NOT NULL,
            objective           TEXT NOT NULL,
            status              TEXT NOT NULL,
            inputs_json         TEXT NOT NULL,
            snapshot_json       TEXT,
            budget_json         TEXT NOT NULL,
            usage_json          TEXT NOT NULL,
            termination_reason  TEXT,
            client_request_id   TEXT UNIQUE,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            started_at          TEXT,
            finished_at         TEXT
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_goals_status ON research_goals(status);
        CREATE INDEX IF NOT EXISTS idx_research_goals_session ON research_goals(session_id);

        CREATE TABLE IF NOT EXISTS research_criteria (
            criterion_id        TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            label               TEXT NOT NULL,
            required            INTEGER NOT NULL,
            min_verified_evidence INTEGER NOT NULL,
            allowed_tiers_json  TEXT NOT NULL,
            freshness_days      INTEGER,
            validator           TEXT,
            status              TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_criteria_goal ON research_criteria(goal_id);

        CREATE TABLE IF NOT EXISTS research_claims (
            claim_id            TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            task_id             TEXT,
            criterion_id        TEXT,
            content             TEXT NOT NULL,
            status              TEXT NOT NULL,
            confidence          REAL,
            evidence_ids_json   TEXT NOT NULL,
            contradiction_ids_json TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_claims_goal ON research_claims(goal_id);
        CREATE INDEX IF NOT EXISTS idx_research_claims_status ON research_claims(status);

        CREATE TABLE IF NOT EXISTS research_evidence (
            evidence_id         TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            criterion_id        TEXT,
            task_id             TEXT,
            attempt_id          TEXT,
            run_id              TEXT,
            tool_call_id        TEXT,
            source_tool         TEXT NOT NULL,
            provider            TEXT,
            uri                 TEXT,
            artifact_id         TEXT,
            data_as_of          TEXT,
            method              TEXT,
            scope               TEXT,
            hash                TEXT,
            source_tier         TEXT NOT NULL,
            caveat              TEXT,
            verified            INTEGER NOT NULL,
            check_count         INTEGER NOT NULL,
            metadata_json       TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_evidence_goal ON research_evidence(goal_id);
        CREATE INDEX IF NOT EXISTS idx_research_evidence_criterion ON research_evidence(criterion_id);
        CREATE INDEX IF NOT EXISTS idx_research_evidence_freshness ON research_evidence(data_as_of);

        CREATE TABLE IF NOT EXISTS research_evidence_checks (
            check_id            TEXT PRIMARY KEY,
            evidence_id         TEXT NOT NULL,
            goal_id             TEXT NOT NULL,
            status              TEXT NOT NULL,
            checker             TEXT NOT NULL,
            detail_json         TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(evidence_id) REFERENCES research_evidence(evidence_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_checks_evidence ON research_evidence_checks(evidence_id);

        CREATE TABLE IF NOT EXISTS research_tasks (
            task_id             TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            profile_id          TEXT NOT NULL,
            kind                TEXT NOT NULL,
            title               TEXT NOT NULL,
            status              TEXT NOT NULL,
            required            INTEGER NOT NULL,
            sequence_index      INTEGER NOT NULL,
            payload_json        TEXT NOT NULL,
            lease_owner         TEXT,
            lease_expires_at    TEXT,
            current_attempt_id  TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            started_at          TEXT,
            finished_at         TEXT,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_tasks_goal ON research_tasks(goal_id);
        CREATE INDEX IF NOT EXISTS idx_research_tasks_status ON research_tasks(status);

        CREATE TABLE IF NOT EXISTS research_task_dependencies (
            goal_id             TEXT NOT NULL,
            task_id             TEXT NOT NULL,
            depends_on_task_id  TEXT NOT NULL,
            required            INTEGER NOT NULL,
            PRIMARY KEY (goal_id, task_id, depends_on_task_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_deps_task ON research_task_dependencies(task_id);

        CREATE TABLE IF NOT EXISTS research_attempts (
            attempt_id          TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            task_id             TEXT NOT NULL,
            run_id              TEXT,
            status              TEXT NOT NULL,
            attempt_no          INTEGER NOT NULL,
            trigger             TEXT NOT NULL,
            usage_json          TEXT NOT NULL,
            result_json         TEXT,
            error               TEXT,
            lease_owner         TEXT,
            lease_expires_at    TEXT,
            created_at          TEXT NOT NULL,
            started_at          TEXT,
            finished_at         TEXT,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id),
            FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_attempts_task ON research_attempts(task_id);
        CREATE INDEX IF NOT EXISTS idx_research_attempts_status ON research_attempts(status);

        CREATE TABLE IF NOT EXISTS research_artifacts (
            artifact_id         TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            task_id             TEXT,
            attempt_id          TEXT,
            kind                TEXT NOT NULL,
            name                TEXT NOT NULL,
            object_hash         TEXT NOT NULL,
            size_bytes          INTEGER NOT NULL,
            media_type          TEXT NOT NULL,
            metadata_json       TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_artifacts_goal ON research_artifacts(goal_id);

        CREATE TABLE IF NOT EXISTS research_audits (
            audit_id            TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            status              TEXT NOT NULL,
            coverage_json       TEXT NOT NULL,
            findings_json       TEXT NOT NULL,
            artifact_id         TEXT,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_audits_goal ON research_audits(goal_id);

        CREATE TABLE IF NOT EXISTS research_publications (
            publication_id      TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            artifact_id         TEXT NOT NULL,
            destination         TEXT NOT NULL,
            object_hash         TEXT NOT NULL,
            status              TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_publications_goal ON research_publications(goal_id);

        CREATE TABLE IF NOT EXISTS research_events (
            event_id            TEXT PRIMARY KEY,
            goal_id             TEXT NOT NULL,
            sequence            INTEGER NOT NULL,
            event_type          TEXT NOT NULL,
            task_id             TEXT,
            attempt_id          TEXT,
            run_id              TEXT,
            payload_json        TEXT NOT NULL,
            mirrored_at         TEXT,
            created_at          TEXT NOT NULL,
            UNIQUE(goal_id, sequence),
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_events_goal_seq ON research_events(goal_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_research_events_mirror ON research_events(mirrored_at);
        """,
    ),
    (
        4,
        """
        -- ---------------------------------------------------------------
        -- Research multi-agent pilot (plan 2026-07-27):
        -- role-bound attempts and an opt-in execution mode. Historical
        -- research rows remain single-agent by default.
        -- ---------------------------------------------------------------
        ALTER TABLE research_goals
            ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'single';
        ALTER TABLE research_tasks
            ADD COLUMN agent_id TEXT;
        ALTER TABLE research_attempts
            ADD COLUMN agent_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_research_tasks_agent
            ON research_tasks(goal_id, agent_id, status);
        CREATE INDEX IF NOT EXISTS idx_research_attempts_agent
            ON research_attempts(goal_id, agent_id, status);
        """,
    ),
    (
        5,
        """
        -- ---------------------------------------------------------------
        -- Investment analysis archive (plan 2026-07-28): manual and
        -- scheduled research share the same immutable ledger, with a small
        -- index for daily/weekly report browsing.
        -- ---------------------------------------------------------------
        ALTER TABLE research_goals
            ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual';
        ALTER TABLE research_goals
            ADD COLUMN cadence TEXT;
        CREATE INDEX IF NOT EXISTS idx_research_goals_origin_cadence_created
            ON research_goals(origin, cadence, created_at DESC);
        """,
    ),
    (
        6,
        """
        -- ---------------------------------------------------------------
        -- Controlled analyst corpus and verified precision cards
        -- (plan 2026-07-28). Private source text stays in the content-
        -- addressed artifact store; SQLite only indexes provenance,
        -- immutable hashes, checker results and reproducible formula inputs.
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS research_source_records (
            source_id            TEXT PRIMARY KEY,
            goal_id              TEXT NOT NULL,
            source_message_id    TEXT NOT NULL,
            analyst_id           TEXT NOT NULL,
            published_at         TEXT NOT NULL,
            source_uri           TEXT NOT NULL,
            content_hash         TEXT NOT NULL,
            corpus_artifact_id   TEXT NOT NULL,
            line_number          INTEGER NOT NULL,
            provenance_json      TEXT NOT NULL,
            attachment_refs_json TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            UNIQUE(goal_id, source_message_id),
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id),
            FOREIGN KEY(corpus_artifact_id) REFERENCES research_artifacts(artifact_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_sources_goal_published
            ON research_source_records(goal_id, published_at);
        CREATE INDEX IF NOT EXISTS idx_research_sources_hash
            ON research_source_records(content_hash);

        CREATE TABLE IF NOT EXISTS research_precision_cards (
            card_id                 TEXT PRIMARY KEY,
            goal_id                 TEXT NOT NULL,
            source_id               TEXT NOT NULL,
            evidence_id             TEXT,
            analyst_id              TEXT NOT NULL,
            trading_date            TEXT NOT NULL,
            symbols_json            TEXT NOT NULL,
            themes_json             TEXT NOT NULL,
            stance_label            TEXT NOT NULL,
            stance_score            INTEGER NOT NULL,
            conviction_label        TEXT,
            conviction_weight       REAL,
            risks_json              TEXT NOT NULL,
            catalysts_json          TEXT NOT NULL,
            date_anchor             TEXT,
            evidence_grade          TEXT NOT NULL,
            quote_start             INTEGER NOT NULL,
            quote_end               INTEGER NOT NULL,
            quote_hash              TEXT NOT NULL,
            sell_side_forward       INTEGER NOT NULL,
            extractor_json          TEXT NOT NULL,
            checker_json            TEXT NOT NULL,
            verified                INTEGER NOT NULL,
            exclusion_reason        TEXT,
            created_at              TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id),
            FOREIGN KEY(source_id) REFERENCES research_source_records(source_id),
            FOREIGN KEY(evidence_id) REFERENCES research_evidence(evidence_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_cards_goal_date
            ON research_precision_cards(goal_id, trading_date);
        CREATE INDEX IF NOT EXISTS idx_research_cards_source
            ON research_precision_cards(source_id);
        CREATE INDEX IF NOT EXISTS idx_research_cards_verified
            ON research_precision_cards(goal_id, verified, sell_side_forward);

        CREATE TABLE IF NOT EXISTS research_formula_runs (
            formula_run_id       TEXT PRIMARY KEY,
            goal_id              TEXT NOT NULL,
            snapshot_id          TEXT NOT NULL,
            formula_version      TEXT NOT NULL,
            config_hash          TEXT NOT NULL,
            input_hash           TEXT NOT NULL,
            result_artifact_id   TEXT,
            model_versions_json  TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            UNIQUE(goal_id, snapshot_id, formula_version, config_hash, input_hash),
            FOREIGN KEY(goal_id) REFERENCES research_goals(goal_id),
            FOREIGN KEY(result_artifact_id) REFERENCES research_artifacts(artifact_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_research_formula_goal
            ON research_formula_runs(goal_id, created_at);
        """,
    ),
    (
        7,
        """
        -- ---------------------------------------------------------------
        -- Signal card layer (plan 2026-07-28-002): deterministic daily cards
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS signal_cards (
            trade_date    TEXT NOT NULL,
            card_type     TEXT NOT NULL,
            card_id       TEXT PRIMARY KEY,
            subject       TEXT,
            payload_json  TEXT NOT NULL,
            created_at    TEXT
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_signal_cards_date_type
            ON signal_cards(trade_date, card_type);
        CREATE INDEX IF NOT EXISTS idx_signal_cards_subject
            ON signal_cards(subject, trade_date);
        """,
    ),
    (
        8,
        """
        -- ---------------------------------------------------------------
        -- Recommendation style contrast + shadow paper trade
        -- (plan 2026-07-31-003 U2/U4): formal paper_trade_picks PK cannot
        -- hold multi-strategy same day; shadow and contrast are separate.
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS style_contrast_snapshots (
            prediction_date  TEXT NOT NULL,
            style_id         TEXT NOT NULL,
            status           TEXT NOT NULL,
            gate_label       TEXT,
            error            TEXT,
            source_tags_json TEXT,
            name             TEXT,
            payload_json     TEXT NOT NULL,
            generated_at     TEXT,
            PRIMARY KEY (prediction_date, style_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_style_contrast_date
            ON style_contrast_snapshots(prediction_date);

        CREATE TABLE IF NOT EXISTS paper_trade_shadow_picks (
            prediction_date  TEXT NOT NULL,
            strategy_id      TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            generated_at     TEXT,
            top_n            INTEGER,
            factor_value     REAL,
            rank_pct         REAL,
            rank_position    INTEGER,
            planned_weight   REAL,
            selection_reason TEXT,
            PRIMARY KEY (prediction_date, strategy_id, symbol)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_shadow_picks_date_strategy
            ON paper_trade_shadow_picks(prediction_date, strategy_id);
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
    database_row = conn.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2]) if database_row and database_row[2] else ""
    lock_handle = None
    with _MIGRATION_THREAD_LOCK:
        try:
            if database_path and fcntl is not None:
                lock_path = Path(database_path + ".migration.lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_handle = lock_path.open("a+b")
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            conn.execute(_MIGRATIONS_TABLE)
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
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
            if newly_applied:
                # Publish the version row before another process is allowed to
                # inspect and replay non-idempotent ALTER TABLE migrations.
                conn.commit()
            return newly_applied
        finally:
            if lock_handle is not None:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()


def ensure_schema_at(db_path: str | Path | None = None) -> list[int]:
    """便捷入口：开连接 + 建库 + 关闭。"""
    with connect(db_path) as conn:
        return ensure_schema(conn)
