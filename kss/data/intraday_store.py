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
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str  # completed / failed
    failure_reason: str | None
    observations_written: int
    blobs_written: int


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
            " observed_at, status_code, error_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


__all__ = [
    "CredentialInPayloadError",
    "InstrumentResolution",
    "IntradayStore",
    "MappingStatus",
    "ObservationInput",
    "RunResult",
    "TERMINAL_FAILURES",
]
