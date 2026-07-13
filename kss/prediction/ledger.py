"""预测生命周期账本（Prediction Lifecycle Ledger）—— SQLite 单表.

记录每条预测从"入选 (F1) → 结算 (F2) → 结构化归因 (F3)"的完整生命周期，
下游复盘 / IC 统计 / 管道 alpha 从这一源头取数（U2，回测闭环计划）。

设计纪律（见 docs/plans/2026-06-21-001-backtest-loop-closure-plan.md）：

- **金融数字一律代码确定性渲染**：``realized_ret`` / ``t1_open`` / ``t2_open`` /
  ``outcome`` / ``attribution_category`` 全部由代码判定，LLM 不参与数值；
  ``attribution_note`` 由 LLM 出（仅自然语言），其 prompt 严禁注入价格字段。
- **PIT 红线**：账本数据严禁回流回测；结算价做快照（``t1_open`` / ``t2_open``），
  防止复盘 csv 被更新后漂移。
- **SQLite**：与 ``storage/kss_quotes.db`` 先例一致（sqlite3 标准库，无新依赖），
  按 ``date`` / ``symbol`` / ``status`` 可索引查询，滚动 IC 查询需索引。
- **fail loud**：结算缺数据保持 ``status="open"``，不幻觉填值；超 ``stale_settle_days``
  个交易日仍缺则升级 ``data_missing``，不回溯改 ``realized_ret``。

主键 = ``prediction_id = "{prediction_date}_{symbol}"``；Writer 去重（同 ``force=False``）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

from kss.config.paths import KSS_DB

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = KSS_DB

# 状态机
STATUS_OPEN = "open"
STATUS_SETTLED = "settled"
STATUS_DATA_MISSING = "data_missing"

# 归因类别（优先级顺序，与 F3 路由一致）
ATTR_DATA_MISSING = "data_missing"
ATTR_FACTOR_STALE = "factor_stale"
ATTR_REGIME_SHIFT = "regime_shift"
ATTR_EXECUTION_FRICTION = "execution_friction"
ATTR_FACTOR_VALID = "factor_valid"

# F3 阈值（外化默认；可由调用方覆盖，不硬编码进路由逻辑）
DEFAULT_FACTOR_STALE_IC = 0.02       # 入账前 20 日截面 IC 滚动均值 < 此值 → factor_stale
DEFAULT_EXECUTION_FRICTION_BPS = 30.0  # |realized_ret| < 此 bps → execution_friction
DEFAULT_STALE_SETTLE_DAYS = 7        # status=open 超 N 个交易日仍缺数据 → data_missing


@dataclass
class AttributionContext:
    """F3 归因规则路由的输入上下文（纯数据，无副作用）.

    ``factor_stale`` / ``regime_shift`` 走 hook：调用方提供 ``rolling_ic``
    （依赖 U1 可信回测的滚动截面 IC）与 ``settle_regime_label``（结算日 regime）；
    缺失时该类别不触发，路由继续向下（fail-safe，绝不幻觉）。
    """

    outcome: str | None
    realized_ret: float | None
    has_prices: bool
    entry_regime_label: str | None = None
    settle_regime_label: str | None = None
    rolling_ic: float | None = None
    factor_stale_ic_th: float = DEFAULT_FACTOR_STALE_IC
    execution_friction_bps: float = DEFAULT_EXECUTION_FRICTION_BPS


@dataclass
class PredictionRecord:
    """一条账本记录（F1 入账时的最小字段集 + 结算 / 归因可空字段）."""

    prediction_id: str
    prediction_date: str
    symbol: str
    strategy: str = "log_mv_reverse"
    pipeline_snapshot: dict[str, Any] = field(default_factory=dict)
    regime_label: str | None = None
    factor_value: float | None = None
    rank_pct: float | None = None
    rank_position: int | None = None
    planned_weight: float | None = None
    status: str = STATUS_OPEN


# ---------------------------------------------------------------------------
# F3: 归因规则路由（纯函数，LLM 不参与；价格字段不进 LLM）
# ---------------------------------------------------------------------------


def classify_attribution(ctx: AttributionContext) -> str:
    """按优先级路由归因类别（代码判定，非 LLM）.

    优先级（高 → 低）::

        data_missing > factor_stale > regime_shift > execution_friction > factor_valid

    factor_stale / regime_shift 为 hook：缺上下文（``rolling_ic`` /
    ``settle_regime_label``）时不触发，继续向下，绝不静默猜测（fail-safe）。
    """
    # 1. data_missing —— T+1/T+2 open 缺失
    if not ctx.has_prices or ctx.outcome is None or ctx.realized_ret is None:
        return ATTR_DATA_MISSING
    # 2. factor_stale —— 入账前 20 日截面 IC 滚动均值 < 阈值（依赖 U1 可信回测）
    if ctx.rolling_ic is not None and ctx.rolling_ic < ctx.factor_stale_ic_th:
        return ATTR_FACTOR_STALE
    # 3. regime_shift —— 入账 regime 与结算 regime 不一致（hook：两者皆有才判）
    if (
        ctx.entry_regime_label is not None
        and ctx.settle_regime_label is not None
        and ctx.entry_regime_label != ctx.settle_regime_label
    ):
        return ATTR_REGIME_SHIFT
    # 4. execution_friction —— |realized_ret| < 单程成本估算（bps）
    if abs(ctx.realized_ret) * 1e4 < ctx.execution_friction_bps:
        return ATTR_EXECUTION_FRICTION
    # 5. factor_valid —— 其余（因子正常工作）
    return ATTR_FACTOR_VALID


def attribution_note_payload(record: dict[str, Any]) -> dict[str, Any]:
    """构造给 LLM 生成 ``attribution_note`` 的 payload —— 严禁含价格字段.

    只注入定性标签，真值由代码渲染（龙虎榜事故先例）。返回 dict 保证
    ``t1_open`` / ``t2_open`` / ``realized_ret`` 三个价格字段绝不出现。
    """
    return {
        "attribution_category": record.get("attribution_category"),
        "outcome": record.get("outcome"),
        "regime_label": record.get("regime_label"),
        "factor_value": record.get("factor_value"),
        "rank_pct": record.get("rank_pct"),
    }


def classify_outcome(realized_ret: float | None) -> str | None:
    """``realized_ret`` → outcome（代码判定）：>0 win / <0 loss / ==0 flat."""
    if realized_ret is None:
        return None
    if realized_ret > 0:
        return "win"
    if realized_ret < 0:
        return "loss"
    return "flat"


# ---------------------------------------------------------------------------
# 账本（SQLite 单表）
# ---------------------------------------------------------------------------


class PredictionLedger:
    """预测生命周期账本（SQLite 单表，按 date/symbol/status 索引）."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id        TEXT PRIMARY KEY,
        prediction_date      TEXT NOT NULL,
        symbol               TEXT NOT NULL,
        strategy             TEXT NOT NULL,
        pipeline_snapshot    TEXT,
        regime_label         TEXT,
        factor_value         REAL,
        rank_pct             REAL,
        rank_position        INTEGER,
        planned_weight       REAL,
        status               TEXT NOT NULL,
        t1_open              REAL,
        t2_open              REAL,
        realized_ret         REAL,
        outcome              TEXT,
        settled_at           TEXT,
        attribution_category TEXT,
        attribution_note     TEXT,
        attribution_generated_at TEXT,
        created_at           TEXT,
        updated_at           TEXT
    )
    """
    INDEXES = (
        "CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(prediction_date)",
        "CREATE INDEX IF NOT EXISTS idx_pred_symbol ON predictions(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status)",
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_LEDGER_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # 单机双写（cron 结算 + app 读写）会 database is locked：connect timeout +
        # busy_timeout 让写锁竞争等待而非立刻抛错；WAL 让读写不互斥（fail loud 保留）。
        # journal_mode=WAL 只在库尚未是 WAL 模式时才发——U15 割接后本库与 kss.db
        # 其它域共用同一文件，冷启动并发首连都发该 pragma 会互相 database is locked
        # （该 pragma 不吃 busy_timeout）；读探测仍有 TOCTOU 窗口，短重试兜底
        # （见 kss/storage/db.py:connect() 同款修法 + 实测记录）。
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
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

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(self.SCHEMA)
            for idx in self.INDEXES:
                conn.execute(idx)

    # ------------------------------------------------------------------ #
    # F1: 入账
    # ------------------------------------------------------------------ #

    def record_prediction(self, record: PredictionRecord, force: bool = False) -> bool:
        """F1 入账一条预测；prediction_id 已存在时默认跳过（去重）.

        Args:
            record: 待入账记录.
            force: ``True`` 时覆盖已存在记录（回放 / 修订用）.

        Returns:
            ``True`` 写入新记录；``False`` 因已存在且未 force 而跳过.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT prediction_id FROM predictions WHERE prediction_id = ?",
                (record.prediction_id,),
            ).fetchone()
            if existing is not None and not force:
                logger.warning("账本已存在记录, 跳过 (force=True 强制): %s", record.prediction_id)
                return False
            if existing is not None:
                # force=True：只改 F1 入账列，绝不整行 REPLACE（否则抹掉
                # t1_open/t2_open/realized_ret/outcome/status 等 settlement 列）。
                conn.execute(
                    """
                    UPDATE predictions SET
                        prediction_date = ?, symbol = ?, strategy = ?,
                        pipeline_snapshot = ?, regime_label = ?, factor_value = ?,
                        rank_pct = ?, rank_position = ?, planned_weight = ?,
                        updated_at = ?
                    WHERE prediction_id = ?
                    """,
                    (
                        record.prediction_date,
                        record.symbol,
                        record.strategy,
                        json.dumps(record.pipeline_snapshot, ensure_ascii=False),
                        record.regime_label,
                        record.factor_value,
                        record.rank_pct,
                        record.rank_position,
                        record.planned_weight,
                        now,
                        record.prediction_id,
                    ),
                )
                return True
            # 新行：INSERT OR IGNORE（existing 检查已处理 force 语义，主键冲突即跳过）。
            conn.execute(
                """
                INSERT OR IGNORE INTO predictions (
                    prediction_id, prediction_date, symbol, strategy,
                    pipeline_snapshot, regime_label, factor_value, rank_pct,
                    rank_position, planned_weight, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.prediction_id,
                    record.prediction_date,
                    record.symbol,
                    record.strategy,
                    json.dumps(record.pipeline_snapshot, ensure_ascii=False),
                    record.regime_label,
                    record.factor_value,
                    record.rank_pct,
                    record.rank_position,
                    record.planned_weight,
                    record.status,
                    now,
                    now,
                ),
            )
        return True

    # ------------------------------------------------------------------ #
    # F2: 结算 + F3: 归因
    # ------------------------------------------------------------------ #

    def settle(
        self,
        prediction_id: str,
        *,
        t1_open: float | None,
        t2_open: float | None,
        attribution_ctx_overrides: dict[str, Any] | None = None,
        attribution_note: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """F2 结算（真实开盘价 → realized_ret / outcome）+ F3 归因.

        ``realized_ret = t2_open / t1_open - 1``，与 ``_horizon_return(hold=1)`` 一致。
        T+2 数据未到（任一 open 为 None/0）→ 保持 ``status="open"``，不误结算。

        Args:
            prediction_id: 主键.
            t1_open / t2_open: 快照原始开盘价（PIT，防 csv 漂移）；缺失则不结算.
            attribution_ctx_overrides: 注入 ``rolling_ic`` / ``settle_regime_label``
                等 F3 hook 上下文（缺失时对应类别不触发）.
            attribution_note: LLM 生成的自然语言说明（不含价格数字）；None 不阻塞.
            force: 已结算记录默认短路返回现有行（防 cs_data 价覆盖 PIT 快照漂移）；
                ``True`` 才允许用新价覆盖（修订 / 回放修复用）.

        Returns:
            结算后该记录的完整 dict.
        """
        from datetime import datetime, timezone

        row = self.get(prediction_id)
        if row is None:
            raise KeyError(f"账本无此记录: {prediction_id}")

        # 重结算守卫：已 SETTLED 且非 force → 短路返回现有行，不让新价覆盖 PIT 快照。
        if row.get("status") == STATUS_SETTLED and not force:
            logger.warning(
                "记录已结算, 拒绝重结算覆盖 PIT 快照 (force=True 强制): %s",
                prediction_id,
            )
            return row

        has_prices = (
            t1_open is not None and t2_open is not None and t1_open != 0
        )
        if has_prices:
            realized_ret = t2_open / t1_open - 1
            outcome = classify_outcome(realized_ret)
            status = STATUS_SETTLED
        else:
            realized_ret = None
            outcome = None
            status = STATUS_OPEN  # T+2 未到 → 不误结算

        overrides = attribution_ctx_overrides or {}
        ctx = AttributionContext(
            outcome=outcome,
            realized_ret=realized_ret,
            has_prices=has_prices,
            entry_regime_label=row.get("regime_label"),
            settle_regime_label=overrides.get("settle_regime_label"),
            rolling_ic=overrides.get("rolling_ic"),
            factor_stale_ic_th=overrides.get(
                "factor_stale_ic_th", DEFAULT_FACTOR_STALE_IC
            ),
            execution_friction_bps=overrides.get(
                "execution_friction_bps", DEFAULT_EXECUTION_FRICTION_BPS
            ),
        )
        # 未结算（status=open）不写归因，等数据到齐再判
        attribution_category = classify_attribution(ctx) if has_prices else None

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE predictions SET
                    t1_open = ?, t2_open = ?, realized_ret = ?, outcome = ?,
                    status = ?, settled_at = ?,
                    attribution_category = ?, attribution_note = ?,
                    attribution_generated_at = ?, updated_at = ?
                WHERE prediction_id = ?
                """,
                (
                    t1_open if has_prices else None,
                    t2_open if has_prices else None,
                    realized_ret,
                    outcome,
                    status,
                    now if has_prices else None,
                    attribution_category,
                    attribution_note if attribution_category else None,
                    now if attribution_note and attribution_category else None,
                    now,
                    prediction_id,
                ),
            )
        return self.get(prediction_id)  # type: ignore[return-value]

    def mark_data_missing(self, prediction_id: str) -> dict[str, Any]:
        """F2 兜底：超期仍缺数据 → 升级 data_missing（不回溯改 realized_ret）.

        已 SETTLED 行拒绝降级（防把已带 realized_ret 的有效结算 clobber 成 missing）。
        """
        from datetime import datetime, timezone

        row = self.get(prediction_id)
        if row is None:
            raise KeyError(f"账本无此记录: {prediction_id}")
        if row.get("status") == STATUS_SETTLED:
            logger.warning(
                "记录已结算, 拒绝降级为 data_missing: %s", prediction_id
            )
            return row

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE predictions SET
                    status = ?, attribution_category = ?, updated_at = ?
                WHERE prediction_id = ?
                """,
                (STATUS_DATA_MISSING, ATTR_DATA_MISSING, now, prediction_id),
            )
        return self.get(prediction_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #

    def get(self, prediction_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM predictions WHERE prediction_id = ?",
                (prediction_id,),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def query(
        self,
        *,
        status: str | None = None,
        symbol: str | None = None,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 status / symbol / 日期区间查询（索引覆盖）."""
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if since_date is not None:
            clauses.append("prediction_date >= ?")
            params.append(since_date)
        if until_date is not None:
            clauses.append("prediction_date <= ?")
            params.append(until_date)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM predictions" + where + " ORDER BY prediction_date, symbol"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def open_due_for_settle(self, cutoff_date: str) -> list[dict[str, Any]]:
        """status=open 且 prediction_date <= cutoff_date（T+2 语义结算队列）."""
        return self.query(status=STATUS_OPEN, until_date=cutoff_date)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        snap = out.get("pipeline_snapshot")
        if isinstance(snap, str) and snap:
            try:
                out["pipeline_snapshot"] = json.loads(snap)
            except json.JSONDecodeError:
                pass
        return out


# ---------------------------------------------------------------------------
# 账本安全网：paper_trade_picks（kss.db）回放回填（幂等）
# ---------------------------------------------------------------------------


def replay_paper_trade(
    ledger: PredictionLedger,
    db_path: str | Path | None = None,
    *,
    settle: bool = False,
    horizon_return=None,
) -> dict[str, int]:
    """把 ``paper_trade_picks``（kss.db，U15 割接自 storage/paper_trade/*.json）
    回填进账本（幂等）。

    主写入路径（``_record_ledger_entry``）已在预测当天同步写账本；本函数是
    每日结算 cron 的安全网，兜底任何漏写记录（force=False 使重复回放不改写
    已结算记录）。

    Args:
        ledger: 目标账本.
        db_path: kss.db 路径覆盖（测试用）；``None`` 走默认解析.
        settle: ``True`` 时用 ``horizon_return`` 回放结算（对齐 ``_horizon_return``）.
        horizon_return: 可注入的结算价函数 ``(symbol, prediction_date) -> (t1_open, t2_open) | None``.

    Returns:
        ``{"records": n_in, "settled": n_settled, "skipped": n_skip}``.
    """
    from kss.storage.paper_trade import read_all_days

    n_in = n_settled = n_skip = 0
    for log in read_all_days(db_path):
        date = log.get("prediction_date")
        if not date:
            continue
        snapshot = {
            "factor_col": "log_mv",
            "top_n": log.get("top_n"),
            "top_pct": log.get("top_pct"),
        }
        for pick in log.get("picks", []):
            symbol = pick.get("symbol", "")
            if not symbol:
                continue
            pred_id = f"{date}_{symbol}"
            wrote = ledger.record_prediction(
                PredictionRecord(
                    prediction_id=pred_id,
                    prediction_date=date,
                    symbol=symbol,
                    strategy=log.get("strategy", "log_mv_reverse"),
                    pipeline_snapshot=snapshot,
                    factor_value=pick.get("factor_value"),
                    rank_pct=pick.get("rank_pct"),
                    rank_position=pick.get("rank_position"),
                    planned_weight=pick.get("planned_weight"),
                ),
                force=False,
            )
            if wrote:
                n_in += 1
            else:
                n_skip += 1
            if settle and horizon_return is not None:
                row = ledger.get(pred_id)
                if row is not None and row.get("status") == STATUS_OPEN:
                    prices = horizon_return(symbol, date)
                    if prices is not None:
                        t1, t2 = prices
                        ledger.settle(pred_id, t1_open=t1, t2_open=t2)
                        n_settled += 1
    return {"records": n_in, "settled": n_settled, "skipped": n_skip}
