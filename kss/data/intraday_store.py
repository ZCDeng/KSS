"""分时数据隔离存储 ``IntradayStore``（U2 薄前向 raw-capture 层）.

**镜像 ``SQLiteStore`` 的「形」，在并发/事务上刻意背离「实」（KTD1）**：

- 每连接加 ``PRAGMA foreign_keys=ON; journal_mode=WAL; busy_timeout``——launchd writer
  与桌面 app reader 并发是已证实的真实失败模式（学习 #7）。
- 用 ``INSERT``（绝不 ``INSERT OR REPLACE``）；blob 内容寻址去重（``INSERT OR IGNORE``
  on ``payload_sha256`` 主键）。
- ingest 走**显式单事务**（``BEGIN`` / 多写 / ``COMMIT``-or-``ROLLBACK``），不复制
  ``SQLiteStore`` 的 per-call commit 习惯。
- **写一次-PIT 守卫**：blob 一旦按内容落盘，相同内容不重复写、不覆盖（U3/U4 的
  canonical 版本守卫在其单元；U2 先保证 blob/observation 不可变 append-only）。

**凭据安全（D5/D6，从第一单 bake-in）**：``redacted_request_json`` 是请求序列化的
唯一写入点（过 :mod:`kss.security.redaction`）；blob 落盘前扫响应体，命中 token →
终止 ``credential_in_payload`` run、**无可查部分行**。

**fail-closed registry（F3）**：运行期解析要求**恰好一个**活跃映射——0 →
``mapping_unknown``、>1 → ``mapping_ambiguous``；两者该标的零 provider 调用（调用方
据此短路，见 U5）。不从前缀推断 provider secid。

KTD7 规范日期键：``trade_date`` 用横杠 ``YYYY-MM-DD`` 对齐 ``SQLiteStore``；
``*_ts`` 用带时区 Asia/Shanghai ISO-8601。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from kss.security.redaction import contains_credential, redact_request, redact_text

# busy_timeout：launchd writer 持锁时桌面 reader 等待上限（毫秒），避免立即
# SQLITE_BUSY（学习 #7）。5s 对收盘批量足够。
_BUSY_TIMEOUT_MS = 5000


class MappingStatus(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN = "mapping_unknown"  # 0 活跃映射 → 跳过该标的（零调用）
    AMBIGUOUS = "mapping_ambiguous"  # >1 活跃映射 → 终止 run（零调用）


class QualityFlag(str, Enum):
    """canonical bar 校验缺陷标记（U4）——每缺陷一个具体 flag，绝不静默丢.

    带 flag 的 bar **仍被记录**（incomplete 标记，非过滤）；缺时间戳保持 incomplete，
    **不**静默填补。
    """

    NOT_IN_PROFILE = "not_in_profile"  # 时间戳不在冻结 session profile 合法端点
    INVALID_OHLC = "invalid_ohlc"  # 违反 low<=min(o,c)<=max(o,c)<=high
    NEGATIVE_VOLUME = "negative_volume"  # 成交量为负
    NEGATIVE_AMOUNT = "negative_amount"  # 成交额为负
    MISSING_TIMESTAMP = "missing_timestamp"  # 原始行缺时间戳
    UNPARSEABLE_TIMESTAMP = "unparseable_timestamp"  # 时间戳无法解析


# 终止性失败原因（KTD2：存储/run 层失败闭合，区别于数据层取数返回 None）。
TERMINAL_FAILURES = frozenset(
    {
        "calendar_unknown",
        "retention_limit",
        "schema_drift",
        "mapping_ambiguous",
        "credential_in_payload",
    }
)


@dataclass(frozen=True)
class InstrumentResolution:
    status: MappingStatus
    instrument_id: int | None = None
    provider_symbol: str | None = None


@dataclass(frozen=True)
class ObservationInput:
    """单次 HTTP observation 的写入输入（取数发生在事务外，写入在事务内）。"""

    instrument_id: int
    provider: str
    interval_minutes: int
    request_meta: dict[str, Any]  # method/url/headers/params/body —— 序列化前脱敏
    payload_rows: list[dict[str, Any]] | None  # 观测到的行；None=错误观测无 blob
    source_asof_ts: str | None
    availability_class: str
    eligibility: str
    status_code: int | None = None
    error: str | None = None
    # A5 上下文冻结：U2 ingest 时即冻结它**当时认为生效**的 schema-hash + session
    # profile / contract 版本，使 U3 retroactive 追认按观测当时的上下文归一化，而非把
    # 事后 profile 套到所有 blob。U2-only 路径可全留 None（追认时按运行时上下文兜底）。
    frozen_schema_hash: str | None = None
    frozen_session_profile_version: str | None = None
    frozen_contract_version: str | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str  # completed / failed
    failure_reason: str | None
    observations_written: int
    blobs_written: int


@dataclass(frozen=True)
class NormalizedBar:
    """归一化后的 canonical bar 候选（确定性变换产物，校验前）。"""

    bar_end_ts: str  # 带时区 ISO-8601 Asia/Shanghai（KTD7）
    trade_date: str  # 横杠 YYYY-MM-DD（KTD7）
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    quality_flags: tuple[QualityFlag, ...] = ()


class CredentialInPayloadError(Exception):
    """响应体含凭据 → 终止 run（credential_in_payload），不持久化任何数据行。"""


class IntradayStore:
    """分时隔离库（U2 最小 schema：runs / blobs / registry / observations）。"""

    SCHEMA_INGEST_RUNS = """
    CREATE TABLE IF NOT EXISTS ingest_runs (
        run_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        mode TEXT NOT NULL,
        trade_date TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        failure_reason TEXT,
        exit_code INTEGER,
        requested_symbols INTEGER NOT NULL DEFAULT 0,
        succeeded_symbols INTEGER NOT NULL DEFAULT 0,
        error_summary TEXT
    )
    """

    SCHEMA_PAYLOAD_BLOBS = """
    CREATE TABLE IF NOT EXISTS payload_blobs (
        payload_sha256 TEXT PRIMARY KEY,
        payload BLOB NOT NULL,
        byte_size INTEGER NOT NULL,
        first_seen_at TEXT NOT NULL
    )
    """

    SCHEMA_INSTRUMENT_REGISTRY = """
    CREATE TABLE IF NOT EXISTS instrument_registry (
        instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        asset_kind TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_symbol TEXT NOT NULL,
        active_from TEXT NOT NULL,
        active_to TEXT,
        UNIQUE (symbol, provider, active_from)
    )
    """

    SCHEMA_PAYLOAD_OBSERVATIONS = """
    CREATE TABLE IF NOT EXISTS payload_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        instrument_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL,
        payload_sha256 TEXT,
        availability_class TEXT NOT NULL,
        eligibility TEXT NOT NULL,
        redacted_request_json TEXT NOT NULL,
        source_asof_ts TEXT,
        observed_at TEXT NOT NULL,
        status_code INTEGER,
        error_summary TEXT,
        frozen_schema_hash TEXT,
        frozen_session_profile_version TEXT,
        frozen_contract_version TEXT,
        FOREIGN KEY (run_id) REFERENCES ingest_runs (run_id),
        FOREIGN KEY (payload_sha256) REFERENCES payload_blobs (payload_sha256),
        FOREIGN KEY (instrument_id) REFERENCES instrument_registry (instrument_id)
    )
    """

    SCHEMA_INDEX_OBS_RUN = (
        "CREATE INDEX IF NOT EXISTS idx_obs_run ON payload_observations(run_id)"
    )
    SCHEMA_INDEX_REG_LOOKUP = (
        "CREATE INDEX IF NOT EXISTS idx_reg_lookup "
        "ON instrument_registry(symbol, provider)"
    )

    # ----- U3 canonical 层（非破坏追认；在 U2 blob 上层叠） ----- #

    # session_profiles：冻结 Asia/Shanghai 合法 bar-end 端点 + 午休 + 集合竞价 +
    # 生效区间。版本化（多 profile 可并存），后续 profile 不回溯套用先前时间戳（U11）。
    SCHEMA_SESSION_PROFILES = """
    CREATE TABLE IF NOT EXISTS session_profiles (
        profile_version TEXT PRIMARY KEY,
        timezone TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL,
        segments_json TEXT NOT NULL,
        include_segment_open INTEGER NOT NULL,
        legal_bar_ends_json TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        created_at TEXT NOT NULL
    )
    """

    # provider_bar_contracts：冻结每 provider+interval+contract-version 的时间戳语义
    # （start/end）+ publication_delay_seconds（值源 RB2 探针 p95+裕度；U3 仅存列，
    # U4/U5 校准）。
    SCHEMA_PROVIDER_BAR_CONTRACTS = """
    CREATE TABLE IF NOT EXISTS provider_bar_contracts (
        contract_version TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL,
        timestamp_basis TEXT NOT NULL,
        publication_delay_seconds INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """

    # canonical_bars：归一化后的 canonical bar，持双版本标识（profile + contract）+
    # revision。唯一索引做原子版本分配（INSERT，绝不 INSERT OR REPLACE → 写一次-PIT
    # 守卫：已写 revision 不被静默覆盖；冲突 → 更高 revision，旧 revision 仍可查）。
    SCHEMA_CANONICAL_BARS = """
    CREATE TABLE IF NOT EXISTS canonical_bars (
        canonical_id INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        bar_end_ts TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        interval_minutes INTEGER NOT NULL,
        source TEXT NOT NULL,
        revision INTEGER NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        amount REAL,
        session_profile_version TEXT NOT NULL,
        contract_version TEXT NOT NULL,
        observation_id INTEGER NOT NULL,
        quality_flags TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        certified_at TEXT NOT NULL,
        FOREIGN KEY (instrument_id) REFERENCES instrument_registry (instrument_id),
        FOREIGN KEY (observation_id)
            REFERENCES payload_observations (observation_id),
        FOREIGN KEY (session_profile_version)
            REFERENCES session_profiles (profile_version),
        FOREIGN KEY (contract_version)
            REFERENCES provider_bar_contracts (contract_version)
    )
    """

    SCHEMA_INDEX_CANON_UNIQUE = (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_version "
        "ON canonical_bars(instrument_id, bar_end_ts, interval_minutes, "
        "source, revision)"
    )
    SCHEMA_INDEX_CANON_LOOKUP = (
        "CREATE INDEX IF NOT EXISTS idx_canon_lookup "
        "ON canonical_bars(instrument_id, bar_end_ts)"
    )

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------ #
    # 连接 / schema（KTD1 背离：每连接装 pragma）
    # ------------------------------------------------------------------ #

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """读连接：装 FK/WAL/busy_timeout pragma；用于查询与单写。

        写一致性靠 :meth:`_tx`（显式单事务）；本上下文用于读与 schema init。
        """
        conn = sqlite3.connect(str(self.db_path), timeout=_BUSY_TIMEOUT_MS / 1000.0)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """显式单事务：``BEGIN`` → 多写 → ``COMMIT``，异常 ``ROLLBACK``（KTD1）。

        ``isolation_level=None`` 关掉 sqlite3 的隐式事务管理，由本块手动控制，
        保证「run+observations+blobs 原子落地，失败无可查部分行」。
        """
        conn = sqlite3.connect(str(self.db_path), timeout=_BUSY_TIMEOUT_MS / 1000.0)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(self.SCHEMA_INGEST_RUNS)
            conn.execute(self.SCHEMA_PAYLOAD_BLOBS)
            conn.execute(self.SCHEMA_INSTRUMENT_REGISTRY)
            conn.execute(self.SCHEMA_PAYLOAD_OBSERVATIONS)
            conn.execute(self.SCHEMA_INDEX_OBS_RUN)
            conn.execute(self.SCHEMA_INDEX_REG_LOOKUP)
            conn.execute(self.SCHEMA_SESSION_PROFILES)
            conn.execute(self.SCHEMA_PROVIDER_BAR_CONTRACTS)
            conn.execute(self.SCHEMA_CANONICAL_BARS)
            conn.execute(self.SCHEMA_INDEX_CANON_UNIQUE)
            conn.execute(self.SCHEMA_INDEX_CANON_LOOKUP)

    # ------------------------------------------------------------------ #
    # registry（fail-closed 解析 + 注册）
    # ------------------------------------------------------------------ #

    def register_instrument(
        self,
        symbol: str,
        asset_kind: str,
        provider: str,
        provider_symbol: str,
        active_from: str,
        active_to: str | None = None,
    ) -> int:
        """注册一条标的映射（生效区间）。返回 instrument_id。"""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO instrument_registry "
                "(symbol, asset_kind, provider, provider_symbol, active_from, active_to) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, asset_kind, provider, provider_symbol, active_from, active_to),
            )
            return int(cur.lastrowid)

    def resolve_instrument(
        self, symbol: str, provider: str, asof_date: str
    ) -> InstrumentResolution:
        """fail-closed 解析：要求 ``asof_date`` 当日**恰好一个**活跃映射.

        活跃 = ``active_from <= asof_date`` 且（``active_to`` 为空 或
        ``asof_date <= active_to``）。0 → unknown（跳过、零调用）；>1 → ambiguous
        （终止、零调用）。**不**从代码前缀猜 provider symbol。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT instrument_id, provider_symbol FROM instrument_registry "
                "WHERE symbol=? AND provider=? "
                "AND active_from <= ? AND (active_to IS NULL OR active_to >= ?)",
                (symbol, provider, asof_date, asof_date),
            ).fetchall()
        if len(rows) == 0:
            return InstrumentResolution(MappingStatus.UNKNOWN)
        if len(rows) > 1:
            return InstrumentResolution(MappingStatus.AMBIGUOUS)
        instrument_id, provider_symbol = rows[0]
        return InstrumentResolution(
            MappingStatus.RESOLVED,
            instrument_id=int(instrument_id),
            provider_symbol=str(provider_symbol),
        )

    # ------------------------------------------------------------------ #
    # ingest（显式单事务 + 凭据闭合 + 写一次 blob）
    # ------------------------------------------------------------------ #

    def ingest_run(
        self,
        *,
        provider: str,
        mode: str,
        trade_date: str | None,
        observations: list[ObservationInput],
        started_at: str | None = None,
        known_secrets: tuple[str, ...] = (),
    ) -> RunResult:
        """单事务写 run + 各 observation + 内容寻址 blob（薄 logger，无 canonical）.

        凭据闭合：任一 observation 的响应体含 token → 整事务回滚，另起一条终止
        ``credential_in_payload`` failed run（无可查数据行）。
        """
        run_id = _new_run_id()
        started = started_at or _now_iso()
        requested = len(observations)

        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT INTO ingest_runs "
                    "(run_id, provider, mode, trade_date, started_at, status, "
                    " requested_symbols) VALUES (?, ?, ?, ?, ?, 'running', ?)",
                    (run_id, provider, mode, trade_date, started, requested),
                )
                obs_written = 0
                blobs_written = 0
                succeeded = 0
                for obs in observations:
                    wrote_blob = self._write_observation(
                        conn, run_id, obs, known_secrets
                    )
                    obs_written += 1
                    blobs_written += int(wrote_blob)
                    if obs.error is None and obs.payload_rows:
                        succeeded += 1
                conn.execute(
                    "UPDATE ingest_runs SET status='completed', finished_at=?, "
                    "succeeded_symbols=?, exit_code=0 WHERE run_id=?",
                    (_now_iso(), succeeded, run_id),
                )
            return RunResult(run_id, "completed", None, obs_written, blobs_written)
        except CredentialInPayloadError as exc:
            # 整 run 回滚后，另起独立终止失败记录（无数据行）。
            self.record_terminal_failure(
                provider=provider,
                mode=mode,
                trade_date=trade_date,
                failure_reason="credential_in_payload",
                error_summary=redact_text(str(exc), known_secrets=known_secrets),
                started_at=started,
            )
            return RunResult(run_id, "failed", "credential_in_payload", 0, 0)

    def _write_observation(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        obs: ObservationInput,
        known_secrets: tuple[str, ...],
    ) -> bool:
        """事务内写单 observation：内容寻址 blob（写一次）+ observation 行.

        Returns:
            是否新写入了一个 blob（去重命中既有则 False）。
        """
        payload_sha256: str | None = None
        wrote_blob = False
        if obs.payload_rows is not None:
            blob_text = _canonical_blob_text(obs.payload_rows)
            # 落盘前扫凭据（D5/D6）：响应体回显 token → 终止整 run。
            if contains_credential(blob_text, known_secrets=known_secrets):
                raise CredentialInPayloadError(
                    f"response body for run {run_id} contains credential pattern"
                )
            payload_sha256 = _sha256_text(blob_text)
            compressed = zlib.compress(blob_text.encode("utf-8"))
            # 写一次守卫：内容寻址 PK + INSERT OR IGNORE → 相同内容不重复写、不覆盖。
            cur = conn.execute(
                "INSERT OR IGNORE INTO payload_blobs "
                "(payload_sha256, payload, byte_size, first_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    payload_sha256,
                    compressed,
                    len(blob_text.encode("utf-8")),
                    _now_iso(),
                ),
            )
            wrote_blob = cur.rowcount > 0

        # redacted_request_json：请求序列化的唯一脱敏写入点。
        redacted = redact_request(
            obs.request_meta.get("method", "GET"),
            obs.request_meta.get("url", ""),
            headers=obs.request_meta.get("headers"),
            params=obs.request_meta.get("params"),
            body=obs.request_meta.get("body"),
            known_secrets=known_secrets,
        )
        conn.execute(
            "INSERT INTO payload_observations "
            "(run_id, instrument_id, provider, interval_minutes, payload_sha256, "
            " availability_class, eligibility, redacted_request_json, source_asof_ts, "
            " observed_at, status_code, error_summary, frozen_schema_hash, "
            " frozen_session_profile_version, frozen_contract_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                obs.instrument_id,
                obs.provider,
                obs.interval_minutes,
                payload_sha256,
                obs.availability_class,
                obs.eligibility,
                json.dumps(redacted, ensure_ascii=False, sort_keys=True),
                obs.source_asof_ts,
                _now_iso(),
                obs.status_code,
                redact_text(obs.error, known_secrets=known_secrets),
                obs.frozen_schema_hash,
                obs.frozen_session_profile_version,
                obs.frozen_contract_version,
            ),
        )
        return wrote_blob

    def record_terminal_failure(
        self,
        *,
        provider: str,
        mode: str,
        trade_date: str | None,
        failure_reason: str,
        error_summary: str | None = None,
        started_at: str | None = None,
    ) -> str:
        """持久化一条终止 failed run（无可查数据行）。返回 run_id。

        用于 calendar_unknown / retention_limit / schema_drift / mapping_ambiguous /
        credential_in_payload —— 都是 KTD2 的存储/run 层失败闭合。
        """
        if failure_reason not in TERMINAL_FAILURES:
            raise ValueError(f"未知终止失败原因: {failure_reason!r}")
        run_id = _new_run_id()
        started = started_at or _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ingest_runs "
                "(run_id, provider, mode, trade_date, started_at, finished_at, "
                " status, failure_reason, exit_code, error_summary) "
                "VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, 1, ?)",
                (
                    run_id,
                    provider,
                    mode,
                    trade_date,
                    started,
                    _now_iso(),
                    failure_reason,
                    error_summary,
                ),
            )
        return run_id

    # ------------------------------------------------------------------ #
    # U3 canonical 层：session profile / contract 注册 + 追认归一化
    # ------------------------------------------------------------------ #

    def register_session_profile(
        self,
        *,
        version: str,
        interval_minutes: int,
        segments: tuple[tuple[str, str], ...],
        include_segment_open: bool,
        effective_from: str,
        effective_to: str | None = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> str:
        """注册一个版本化 session profile（冻结合法 bar-end 端点）。返回 version。

        合法端点在注册时即冻结进 ``legal_bar_ends_json``——后续 profile 上线**不回溯**
        套用先前时间戳（U11：旧 canonical 行保留其 profile version）。
        """
        legal_ends = build_legal_bar_ends(
            segments=segments,
            interval_minutes=interval_minutes,
            include_segment_open=include_segment_open,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO session_profiles "
                "(profile_version, timezone, interval_minutes, segments_json, "
                " include_segment_open, legal_bar_ends_json, effective_from, "
                " effective_to, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version,
                    timezone_name,
                    interval_minutes,
                    json.dumps(segments, ensure_ascii=False),
                    int(include_segment_open),
                    json.dumps(legal_ends, ensure_ascii=False),
                    effective_from,
                    effective_to,
                    _now_iso(),
                ),
            )
        return version

    def register_provider_bar_contract(
        self,
        *,
        version: str,
        provider: str,
        interval_minutes: int,
        timestamp_basis: str,
        publication_delay_seconds: int,
    ) -> str:
        """注册一个版本化 provider bar 契约（时间戳 start/end + 发布延迟）。返回 version。

        ``publication_delay_seconds`` 值源是 RB2 探针 p95+裕度；U3 仅存列，U4/U5 校准。
        """
        if timestamp_basis not in ("start", "end"):
            raise ValueError(f"timestamp_basis 须为 start/end，得到 {timestamp_basis!r}")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO provider_bar_contracts "
                "(contract_version, provider, interval_minutes, timestamp_basis, "
                " publication_delay_seconds, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    version,
                    provider,
                    interval_minutes,
                    timestamp_basis,
                    publication_delay_seconds,
                    _now_iso(),
                ),
            )
        return version

    def get_session_profile(self, version: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM session_profiles WHERE profile_version=?", (version,)
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["segments"] = json.loads(out["segments_json"])
        out["legal_bar_ends"] = json.loads(out["legal_bar_ends_json"])
        return out

    def get_provider_bar_contract(self, version: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM provider_bar_contracts WHERE contract_version=?",
                (version,),
            ).fetchone()
        return dict(row) if row else None

    def certify_observations_to_canonical(
        self,
        *,
        run_id: str,
        expected_schema_hash: str | None = None,
    ) -> dict[str, Any]:
        """retroactive 追认：从已存 blob 建 canonical bar，读 observation 冻结上下文.

        非破坏：在 U2 blob 上层叠 canonical（A5）。每 observation 按它**冻结当时**的
        schema-hash / session_profile / contract 版本归一化——不把事后 profile 套到所有
        blob。

        - **schema-hash 漂移（S4）**：``expected_schema_hash`` 非空且与 observation 冻结
          ``frozen_schema_hash`` 不符 → 终止 ``schema_drift`` run、无 canonical 行、告警。
          区别于空/坏响应（payload 为空只是无行可追认，不终止）。
        - **写一次-PIT（KTD1）**：用 ``(instrument_id, bar_end_ts, interval, source,
          revision)`` 唯一索引 + ``INSERT``（绝不 ``INSERT OR REPLACE``）原子分配 revision。
          同内容（content_sha256 命中既有 revision）→ 幂等不 bump；内容变 → 更高 revision，
          旧 revision 保持可查。

        Returns:
            ``{"status", "failure_reason", "canonical_written"}``。
        """
        observations = self.list_observations(run_id)
        # schema 漂移检查：任一 observation 的冻结 hash 与期望不符即终止整 run。
        if expected_schema_hash is not None:
            for obs in observations:
                frozen = obs.get("frozen_schema_hash")
                if frozen is not None and frozen != expected_schema_hash:
                    self.record_terminal_failure(
                        provider=obs["provider"],
                        mode="certify",
                        trade_date=None,
                        failure_reason="schema_drift",
                        error_summary=(
                            f"frozen schema_hash {frozen!r} != expected "
                            f"{expected_schema_hash!r} (observation "
                            f"{obs['observation_id']})"
                        ),
                    )
                    return {
                        "status": "failed",
                        "failure_reason": "schema_drift",
                        "canonical_written": 0,
                    }

        written = 0
        with self._tx() as conn:
            for obs in observations:
                if obs.get("payload_sha256") is None:
                    continue  # 空/错误响应：无行可追认（非 schema_drift）
                rows = self._load_blob_rows(conn, obs["payload_sha256"])
                profile = self._profile_for(conn, obs["frozen_session_profile_version"])
                contract = self._contract_for(conn, obs["frozen_contract_version"])
                if profile is None or contract is None:
                    continue  # 无冻结上下文则跳过（U2-only blob）
                legal_ends = tuple(json.loads(profile["legal_bar_ends_json"]))
                for raw in rows:
                    written += self._certify_one_bar(
                        conn,
                        obs=obs,
                        raw=raw,
                        profile=profile,
                        contract=contract,
                        legal_ends=legal_ends,
                    )
        return {
            "status": "completed",
            "failure_reason": None,
            "canonical_written": written,
        }

    def _certify_one_bar(
        self,
        conn: sqlite3.Connection,
        *,
        obs: dict[str, Any],
        raw: dict[str, Any],
        profile: dict[str, Any],
        contract: dict[str, Any],
        legal_ends: tuple[str, ...],
    ) -> int:
        """归一+校验单条原始行并原子分配 revision 写 canonical。返回新写行数(0/1)。"""
        trade_date = _trade_date_from_obs(obs, raw)
        norm = normalize_bar(
            raw,
            timestamp_basis=contract["timestamp_basis"],
            interval_minutes=int(contract["interval_minutes"]),
            trade_date=trade_date,
            legal_bar_ends=legal_ends,
        )
        if not norm.bar_end_ts:
            return 0  # 缺/不可解析时间戳：保持 incomplete，不静默填（U5）
        flags = validate_bar(norm)
        content_sha = _sha256_text(
            json.dumps(
                [
                    norm.bar_end_ts, norm.open, norm.high, norm.low, norm.close,
                    norm.volume, norm.amount, sorted(f.value for f in flags),
                ],
                ensure_ascii=False, default=str,
            )
        )
        instrument_id = int(obs["instrument_id"])
        source = obs["provider"]
        interval = int(obs["interval_minutes"])

        existing = conn.execute(
            "SELECT revision, content_sha256 FROM canonical_bars "
            "WHERE instrument_id=? AND bar_end_ts=? AND interval_minutes=? "
            "AND source=? ORDER BY revision",
            (instrument_id, norm.bar_end_ts, interval, source),
        ).fetchall()
        # 写一次幂等：相同内容已有某 revision → 不重复写。
        if any(row[1] == content_sha for row in existing):
            return 0
        next_revision = (max((row[0] for row in existing), default=0)) + 1

        symbol = _symbol_for_instrument(conn, instrument_id)
        conn.execute(
            "INSERT INTO canonical_bars "
            "(instrument_id, symbol, bar_end_ts, trade_date, interval_minutes, "
            " source, revision, open, high, low, close, volume, amount, "
            " session_profile_version, contract_version, observation_id, "
            " quality_flags, content_sha256, certified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                instrument_id,
                symbol,
                norm.bar_end_ts,
                norm.trade_date,
                interval,
                source,
                next_revision,
                norm.open,
                norm.high,
                norm.low,
                norm.close,
                norm.volume,
                norm.amount,
                profile["profile_version"],
                contract["contract_version"],
                int(obs["observation_id"]),
                ",".join(f.value for f in flags),
                content_sha,
                _now_iso(),
            ),
        )
        return 1

    @staticmethod
    def _load_blob_rows(
        conn: sqlite3.Connection, payload_sha256: str
    ) -> list[dict[str, Any]]:
        row = conn.execute(
            "SELECT payload FROM payload_blobs WHERE payload_sha256=?",
            (payload_sha256,),
        ).fetchone()
        if row is None:
            return []
        text = zlib.decompress(row[0]).decode("utf-8")
        return json.loads(text)

    @staticmethod
    def _profile_for(
        conn: sqlite3.Connection, version: str | None
    ) -> dict[str, Any] | None:
        if version is None:
            return None
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM session_profiles WHERE profile_version=?", (version,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _contract_for(
        conn: sqlite3.Connection, version: str | None
    ) -> dict[str, Any] | None:
        if version is None:
            return None
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM provider_bar_contracts WHERE contract_version=?",
            (version,),
        ).fetchone()
        return dict(row) if row else None

    def list_canonical_bars(
        self,
        *,
        instrument_id: int | None = None,
        bar_end_ts: str | None = None,
    ) -> list[dict[str, Any]]:
        """列 canonical bar（含全部 revision；可按 instrument / bar_end_ts 过滤）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if instrument_id is not None:
            clauses.append("instrument_id=?")
            params.append(instrument_id)
        if bar_end_ts is not None:
            clauses.append("bar_end_ts=?")
            params.append(bar_end_ts)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM canonical_bars" + where
                + " ORDER BY bar_end_ts, revision",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 只读查询（测试 / 可观测性）
    # ------------------------------------------------------------------ #

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM ingest_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def count_observations(self, run_id: str | None = None) -> int:
        with self._conn() as conn:
            if run_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM payload_observations"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM payload_observations WHERE run_id=?",
                    (run_id,),
                ).fetchone()
        return int(row[0])

    def count_blobs(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM payload_blobs").fetchone()
        return int(row[0])

    def list_registered_symbols(
        self, provider: str
    ) -> list[tuple[str, str]]:
        """该 provider 下注册过的 distinct ``(symbol, asset_kind)``（采集器迭代源）.

        返回去重符号；采集器对每个再走 :meth:`resolve_instrument` 做 fail-closed
        解析（unknown 跳过 / ambiguous 终止），故此处含历史/重复映射也无妨。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol, asset_kind FROM instrument_registry "
                "WHERE provider=? ORDER BY symbol",
                (provider,),
            ).fetchall()
        return [(str(s), str(k)) for s, k in rows]

    def list_observations(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if run_id is None:
                rows = conn.execute(
                    "SELECT * FROM payload_observations ORDER BY observation_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM payload_observations WHERE run_id=? "
                    "ORDER BY observation_id",
                    (run_id,),
                ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# 模块级工具
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    """当前时刻的 Asia/Shanghai ISO-8601（带时区，KTD7）。"""
    from zoneinfo import ZoneInfo

    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def _canonical_blob_text(rows: list[dict[str, Any]]) -> str:
    """把观测行确定性序列化（排序键），使「相同数据 → 相同 sha256」内容寻址成立。"""
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# U3 归一化 / 校验（确定性纯函数 —— test-first，测意图）
# --------------------------------------------------------------------------- #

_SHANGHAI_TZ = "Asia/Shanghai"

# 东财(akshare)分钟响应原始中文列 → canonical 字段。与 intraday_client.EM_COLUMN_MAP
# 同源（只取归一化需要的 OHLCV+amount 子集；时间另走 _CANON_TIME_KEY）。
_CANON_TIME_KEY = "时间"
_CANON_FIELD_MAP: dict[str, str] = {
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
}


def build_legal_bar_ends(
    *,
    segments: tuple[tuple[str, str], ...],
    interval_minutes: int,
    include_segment_open: bool,
) -> tuple[str, ...]:
    """从 session 段生成合法 bar-end 端点列表（``HH:MM``），**不硬编码 240/241**.

    每段 ``(open, close)``（含端点）按 ``interval_minutes`` 步进。end-stamped bar 的
    合法端点是 ``open+interval .. close``；若 ``include_segment_open`` 为真，再纳入
    **当日开盘**集合竞价 bar（仅首段开盘，如 09:30 零成交 bar）。这使东财 1m 的 241 形
    成立（``1 + 120 + 120``：09:30 + 09:31..11:30 + 13:01..15:00），并据此区分零成交
    开盘 bar vs 缺记录。午休首分钟（如 13:00）**不**单独成 bar。

    Args:
        segments: 交易段 ``((HH:MM, HH:MM), ...)``，含午休则分两段；首段为当日开盘段。
        interval_minutes: 分钟周期。
        include_segment_open: 是否纳入当日开盘时刻（开盘集合竞价 bar，仅首段）。

    Returns:
        升序去重的合法 bar-end 端点元组（``HH:MM``）。
    """
    ends: list[str] = []
    seen: set[str] = set()
    for seg_index, (seg_open, seg_close) in enumerate(segments):
        start_min = _hhmm_to_minutes(seg_open)
        end_min = _hhmm_to_minutes(seg_close)
        # 仅当日首段开盘竞价 bar 入合法端点（东财 241 形：13:00 不单独成 bar）。
        if include_segment_open and seg_index == 0 and seg_open not in seen:
            ends.append(seg_open)
            seen.add(seg_open)
        cur = start_min + interval_minutes
        while cur <= end_min:
            label = _minutes_to_hhmm(cur)
            if label not in seen:
                ends.append(label)
                seen.add(label)
            cur += interval_minutes
    return tuple(sorted(ends))


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_bar(
    raw: dict[str, Any],
    *,
    timestamp_basis: str,
    interval_minutes: int,
    trade_date: str,
    legal_bar_ends: tuple[str, ...],
) -> NormalizedBar:
    """把单条原始行归一化为 canonical bar 候选（确定性变换）.

    - ``start`` 戳 → 推到合法 bar-end（``+interval_minutes``）。
    - ``end`` 戳 → 保留。
    - 归一后的 ``HH:MM`` 不在 ``legal_bar_ends`` → 置 ``NOT_IN_PROFILE`` flag（不丢）。
    - 缺/不可解析时间戳 → 置 ``MISSING_TIMESTAMP`` / ``UNPARSEABLE_TIMESTAMP``。

    ``bar_end_ts`` 用带时区 ISO-8601 Asia/Shanghai；``trade_date`` 横杠 YYYY-MM-DD（KTD7）。
    """
    flags: list[QualityFlag] = []
    raw_ts = raw.get(_CANON_TIME_KEY)

    parsed = _parse_naive_ts(raw_ts) if raw_ts is not None else None
    if raw_ts is None:
        flags.append(QualityFlag.MISSING_TIMESTAMP)
    elif parsed is None:
        flags.append(QualityFlag.UNPARSEABLE_TIMESTAMP)

    bar_end_ts = ""
    if parsed is not None:
        from datetime import timedelta

        if timestamp_basis == "start":
            parsed = parsed + timedelta(minutes=interval_minutes)
        hhmm = parsed.strftime("%H:%M")
        if hhmm not in legal_bar_ends:
            flags.append(QualityFlag.NOT_IN_PROFILE)
        bar_end_ts = _localize_shanghai(parsed)

    fields = {v: raw.get(k) for k, v in _CANON_FIELD_MAP.items()}
    return NormalizedBar(
        bar_end_ts=bar_end_ts,
        trade_date=trade_date,
        open=_as_float(fields.get("open")),
        high=_as_float(fields.get("high")),
        low=_as_float(fields.get("low")),
        close=_as_float(fields.get("close")),
        volume=_as_float(fields.get("volume")),
        amount=_as_float(fields.get("amount")),
        quality_flags=tuple(flags),
    )


def validate_bar(bar: NormalizedBar) -> tuple[QualityFlag, ...]:
    """校验 canonical bar，返回**全部**缺陷 flag（含归一化阶段已置的，去重保序）.

    校验（test-spec U4）：合法端点（归一化已标 ``NOT_IN_PROFILE``）、
    ``low<=min(open,close)<=max(open,close)<=high``、非负 volume/amount。每缺陷一个
    具体 flag；合法完整 bar 返回 ``()``。零成交（volume==0）合法，不置 flag。
    """
    flags: list[QualityFlag] = list(bar.quality_flags)

    def _add(flag: QualityFlag) -> None:
        if flag not in flags:
            flags.append(flag)

    o, h, low, c = bar.open, bar.high, bar.low, bar.close
    if None not in (o, h, low, c):
        oc_min = min(o, c)
        oc_max = max(o, c)
        if not (low <= oc_min <= oc_max <= h):
            _add(QualityFlag.INVALID_OHLC)

    if bar.volume is not None and bar.volume < 0:
        _add(QualityFlag.NEGATIVE_VOLUME)
    if bar.amount is not None and bar.amount < 0:
        _add(QualityFlag.NEGATIVE_AMOUNT)

    return tuple(flags)


def _parse_naive_ts(raw_ts: Any):
    from datetime import datetime

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(raw_ts).strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _localize_shanghai(naive_dt) -> str:
    from zoneinfo import ZoneInfo

    return naive_dt.replace(tzinfo=ZoneInfo(_SHANGHAI_TZ)).isoformat()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _trade_date_from_obs(obs: dict[str, Any], raw: dict[str, Any]) -> str:
    """从原始行时间戳推导横杠 trade_date；不可解析则回落 observed_at 日期。"""
    parsed = _parse_naive_ts(raw.get(_CANON_TIME_KEY))
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    observed = str(obs.get("observed_at") or "")
    return observed[:10]


def _symbol_for_instrument(conn: sqlite3.Connection, instrument_id: int) -> str:
    row = conn.execute(
        "SELECT symbol FROM instrument_registry WHERE instrument_id=?",
        (instrument_id,),
    ).fetchone()
    return str(row[0]) if row else ""


__all__ = [
    "CredentialInPayloadError",
    "InstrumentResolution",
    "IntradayStore",
    "MappingStatus",
    "NormalizedBar",
    "ObservationInput",
    "QualityFlag",
    "RunResult",
    "TERMINAL_FAILURES",
    "build_legal_bar_ends",
    "normalize_bar",
    "validate_bar",
]
