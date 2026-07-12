"""因子健康度闭环 —— 滚动 IC/ICIR + 崩盘登记库 + IC 双源仲裁状态机（U3）.

把 KSS 各自孤立的回测/校验诊断接成「会自我纠偏」的闭环。本模块负责度量与
状态机两件事：

- :class:`FactorHealthTracker` —— 逐期 Rank-IC 落库，算 ICIR / 1d-5d-20d 衰减
  半衰期，提供 60 日滚动 ICIR 查询（SQLite，复用 storage 风格）。
- :class:`FactorCrashRegistry` —— 把「哪个因子 + 哪窗口 + 崩成什么样」结构化入库，
  含 ``IC_SOURCE_DIVERGENCE`` 事件类型；并托管 ``ACTIVE → PENDING_REVIEW →
  RETIRED`` 状态机（剔除留人工确认）。

设计纪律（见 docs/plans/2026-06-21-001-backtest-loop-closure-plan.md）：

- **有效 n = 去重交易日数**（``ic_series.index.nunique()``），不是事件数。A 股横截面
  高相关，事件数严重高估 t-stat（etf_flow / 龙虎榜先例）。IC 显著性沿用
  :class:`~kss.backtest.significance.Significance` 的 ``t_stat`` / ``newey_west_se``。
- **IC 双源仲裁（Global Decision #8）**：本模块算的是**实盘账本 IC**（读 U2
  ``realized_ret``）= 后验/更新，**不是先验**。状态迁移必须走 #8：
  * 实盘 IC 单独**只能降权 / 置 PENDING_REVIEW**（fail-safe 偏保守）；
  * **升权 / 置 ACTIVE / RETIRE 需与回测 IC 先验一致** —— 回测先验由 U5（CPCV）
    提供，本模块做成**可注入接口** ``backtest_ic_source``；U5 未就绪（None）时升权
    路径不放行（保守不放行）；
  * 实盘 IC 低于其有效 n 时**仅供参考**，不得单独翻转状态，回退先验；
  * 实盘与回测**符号分歧** → 记 ``IC_SOURCE_DIVERGENCE``，人工复核，绝不静默。
- **严禁回流回测**：实盘 IC 读账本做**因子诊断**可以；诊断结果（阈值/状态）**不得
  直接改已验证回测参数 / 特征工程**（要改须独立 walk-forward 重标定）。账本数据
  绝不注入 ``factor_df``。本模块只读账本、只写自己的健康度/崩盘库。
- **阈值外化**：全部阈值读 ``kss/config/factor_health_thresholds.yaml``；删文件即
  fail loud（FileNotFoundError），不回退硬编码（R5/SC6）。
- **fail loud**：拟合失败 / 样本不足返回 null，不强行输出；跳过即说跳过。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

from kss.backtest.significance import Significance
from kss.config.paths import KSS_DB

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HEALTH_DB = KSS_DB
DEFAULT_THRESHOLDS_PATH = (
    PROJECT_ROOT / "kss" / "config" / "factor_health_thresholds.yaml"
)

# 因子状态机（单向：ACTIVE → PENDING_REVIEW → RETIRED）
STATE_ACTIVE = "ACTIVE"
STATE_PENDING_REVIEW = "PENDING_REVIEW"
STATE_RETIRED = "RETIRED"
_STATE_ORDER = {STATE_ACTIVE: 0, STATE_PENDING_REVIEW: 1, STATE_RETIRED: 2}

# 崩盘 / 生命周期事件类型
CRASH_IC_DECAY = "IC_DECAY"                    # IC 绝对值连续 N 期 < 阈值
CRASH_ICIR_BELOW_BASELINE = "ICIR_BELOW_BASELINE"  # 60 日滚动 ICIR 跌破基线
CRASH_IC_SOURCE_DIVERGENCE = "IC_SOURCE_DIVERGENCE"  # 实盘 vs 回测 IC 符号分歧（#8）

# 仲裁裁决（arbitrate 返回的动作标签，代码判定非 LLM）
VERDICT_PROMOTE = "promote"          # 升权 / 置 ACTIVE（需双源一致）
VERDICT_HOLD = "hold"               # 维持现状
VERDICT_DEMOTE = "demote"           # 降权 / 置 PENDING_REVIEW（实盘单源即可）
VERDICT_DIVERGENCE = "divergence"   # 符号分歧 → 人工复核
VERDICT_INSUFFICIENT_N = "insufficient_n"  # 有效 n 不足 → 仅供参考，不翻转
VERDICT_METHOD_MISMATCH = "method_mismatch"  # 两源 IC 口径不同 → 拒绝跨口径比较

# IC 计量口径（防把符号代理与 Rank-IC 当同轴比；写入点须显式标注）
IC_METHOD_RANK_IC = "rank_ic"        # 截面 Spearman Rank-IC（cross_section / pipeline）
IC_METHOD_SIGN_PROXY = "sign_proxy"  # 单股时序符号一致性代理（cpcv / wfc）


# ---------------------------------------------------------------------------
# 阈值外化加载（删文件即 fail loud）
# ---------------------------------------------------------------------------


def load_thresholds(path: str | Path | None = None) -> dict[str, Any]:
    """从 ``factor_health_thresholds.yaml`` 读阈值；文件缺失即抛错（R5/SC6）.

    不回退硬编码默认 —— 阈值缺失是配置错误，必须 fail loud 让用户察觉，而非
    静默用一组未经 walk-forward 标定的数字做告警/退役判定。
    """
    import yaml

    cfg_path = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"因子健康度阈值配置缺失: {cfg_path}（阈值须外化 + walk-forward 标定，"
            f"不回退硬编码值）"
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    fh = cfg.get("factor_health")
    if not fh:
        raise FileNotFoundError(
            f"{cfg_path} 缺少 factor_health 段（阈值未配置，拒绝静默回退）"
        )
    return dict(fh)


# ---------------------------------------------------------------------------
# 纯函数：Rank-IC 统计量 / 半衰期 / ICIR
# ---------------------------------------------------------------------------


@dataclass
class ICSnapshot:
    """一条 IC 快照（落库字段集；半衰期可空若拟合失败）."""

    factor_id: str
    window_end: str
    ic_mean: float
    ic_std: float
    icir: float
    ic_positive_rate: float
    n_periods: int            # 有效 n = 去重交易日数（非事件数）
    ic_t_stat: float
    half_life_1d: float | None = None
    half_life_5d: float | None = None
    half_life_20d: float | None = None
    source: str = "realized"  # realized=实盘账本（U3 后验）/ backtest=回测（U5 先验）
    method: str = IC_METHOD_RANK_IC  # IC 口径：rank_ic / sign_proxy（防跨口径比较）


def _dedup_daily_ic(ic_series: pd.Series) -> pd.Series:
    """把逐事件 IC 折成**逐去重交易日**的 IC（有效 n = 去重日期数，非事件数）.

    A 股横截面高相关：同一日多支股票的 IC 不是独立观测。``ic_series`` 的 index
    可能含重复日期（每日一个截面 IC 已是聚合，但调用方若误传事件级序列，这里按
    日期 groupby-mean 折叠），确保 ``n_periods == index.nunique()``。
    """
    s = ic_series.dropna()
    if s.empty:
        return s
    # 同一日期多条 → 取均值，保证一日一观测
    if s.index.has_duplicates:
        s = s.groupby(level=0).mean()
    return s.sort_index()


def _half_life(daily_ic: pd.Series, smooth_window: int, min_periods: int) -> float | None:
    """IC 半衰期：对滚动 IC 均值的 |·| 取 log 后线性拟合，half_life = ln2 / 衰减斜率.

    样本不足（< min_periods）或斜率非负（无衰减）→ 返回 None（不强行输出，R12）。
    """
    s = daily_ic.dropna()
    if len(s) < max(min_periods, smooth_window + 1):
        return None
    rolling = s.rolling(smooth_window, min_periods=smooth_window).mean().dropna()
    if len(rolling) < min_periods:
        return None
    y = np.log(np.abs(rolling.values) + 1e-12)
    x = np.arange(len(y), dtype=float)
    if np.std(x) == 0:
        return None
    slope = np.polyfit(x, y, 1)[0]
    # slope < 0 表示 |IC| 在衰减；斜率非负 = 不衰减 → None
    if slope >= 0:
        return None
    hl = float(np.log(2.0) / (-slope))
    return hl if np.isfinite(hl) and hl > 0 else None


def compute_ic_snapshot(
    factor_id: str,
    window_end: str,
    ic_series: pd.Series,
    *,
    thresholds: dict[str, Any],
    source: str = "realized",
    method: str = IC_METHOD_RANK_IC,
) -> ICSnapshot:
    """从逐日 Rank-IC 序列算一条快照（纯函数，代码确定性渲染所有数字）.

    Args:
        factor_id: 因子 ID（与 FactorPipeline 输出列名一致命名空间）.
        window_end: 窗口结束日（主键的一半）.
        ic_series: index = 交易日、值 = 当日截面 Rank-IC 的序列.
        thresholds: ``load_thresholds()`` 的结果（半衰期 min_periods 等）.
        source: ``realized``（实盘账本）/ ``backtest``（回测先验）.
        method: IC 口径 ``rank_ic`` / ``sign_proxy``（防仲裁跨口径比较）.

    Returns:
        :class:`ICSnapshot`；有效 n = 去重交易日数，t_stat 用该 n。
    """
    daily = _dedup_daily_ic(ic_series)
    n = int(daily.index.nunique())
    if n == 0:
        return ICSnapshot(
            factor_id=factor_id, window_end=window_end, ic_mean=0.0, ic_std=0.0,
            icir=0.0, ic_positive_rate=0.0, n_periods=0, ic_t_stat=0.0,
            source=source, method=method,
        )
    ic_mean = float(daily.mean())
    ic_std = float(daily.std(ddof=1)) if n >= 2 else 0.0
    icir = float(ic_mean / ic_std) if ic_std > 0 else 0.0
    pos_rate = float((daily > 0).mean())
    # t_stat = ic_mean / ic_std * sqrt(n)（有效 n = 去重日数）。复用 significance 口径：
    # 对去重后的逐日 IC 序列做单样本 t 检验，等价于 ic_mean/se，se=ic_std/sqrt(n)。
    t_stat, _ = Significance.t_stat(daily)

    hl_min = int(thresholds.get("half_life_min_periods", 20))
    return ICSnapshot(
        factor_id=factor_id,
        window_end=window_end,
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        ic_positive_rate=pos_rate,
        n_periods=n,
        ic_t_stat=float(t_stat),
        half_life_1d=_half_life(daily, smooth_window=1, min_periods=hl_min),
        half_life_5d=_half_life(daily, smooth_window=5, min_periods=hl_min),
        half_life_20d=_half_life(daily, smooth_window=20, min_periods=hl_min),
        source=source,
        method=method,
    )


# ---------------------------------------------------------------------------
# IC 双源仲裁（Global Decision #8）—— 纯函数，代码判定
# ---------------------------------------------------------------------------


@dataclass
class ArbitrationInput:
    """仲裁输入（实盘后验 + 可注入的回测先验）.

    ``backtest_ic`` / ``backtest_icir`` 由 ``backtest_ic_source`` 回调提供；U5 未就绪
    时为 None → 升权路径不放行（保守）。
    """

    realized_icir: float
    realized_ic_mean: float
    realized_n: int                       # 实盘有效 n（去重交易日数）
    backtest_ic_mean: float | None = None  # 回测先验 IC 均值（U5）；None=先验缺失
    backtest_icir: float | None = None
    realized_method: str = IC_METHOD_RANK_IC   # 实盘 IC 口径
    backtest_method: str = IC_METHOD_RANK_IC   # 回测 IC 口径（须与实盘同口径才可比）


@dataclass
class ArbitrationResult:
    verdict: str
    reason: str
    divergence: bool = False


def arbitrate(inp: ArbitrationInput, thresholds: dict[str, Any]) -> ArbitrationResult:
    """按 Global Decision #8 裁决状态迁移动作（纯函数，绝不静默）.

    规则优先级：
      1. 实盘有效 n < min_n → ``insufficient_n``（仅供参考，不翻转）；
      2. 实盘 ICIR 跌破降权门 → ``demote``（fail-safe，单源即可置 PENDING_REVIEW）；
      3. 实盘想升权（ICIR ≥ 升权门）：
         - 回测先验缺失（U5 未就绪）→ 不放行升权，``hold``（保守）；
         - 两源 IC 口径不同（rank_ic vs sign_proxy）→ ``method_mismatch``（不跨口径比）；
         - 回测先验在但**符号分歧**（两侧 |ic| 均超 epsilon 且符号相反）→ ``divergence``；
         - 双源符号一致 → ``promote``；
      4. 其余 → ``hold``。

    divergence 幅度 epsilon（``ic_divergence_min_abs``）：两侧 |ic| 都须超此下限才判
    分歧，避免零附近噪声把"两个接近 0 的反号小数"误判成真分歧。
    """
    min_n = int(thresholds["realized_ic_min_n"])
    promote_th = float(thresholds["realized_icir_promote"])
    demote_th = float(thresholds["realized_icir_demote"])
    div_eps = float(thresholds.get("ic_divergence_min_abs", 0.0))

    # 1) 有效 n 不足 → 仅供参考，不翻转状态（回退先验）
    if inp.realized_n < min_n:
        return ArbitrationResult(
            VERDICT_INSUFFICIENT_N,
            f"实盘有效 n={inp.realized_n} < {min_n}（去重交易日数），仅供参考不翻转状态",
        )

    # 2) 降权门：实盘单源即可置 PENDING_REVIEW（fail-safe 偏保守）
    if inp.realized_icir < demote_th:
        return ArbitrationResult(
            VERDICT_DEMOTE,
            f"实盘 ICIR={inp.realized_icir:.3f} < 降权门 {demote_th}（fail-safe 降权）",
        )

    # 3) 升权门：需与回测先验一致才放行
    if inp.realized_icir >= promote_th:
        if inp.backtest_ic_mean is None:
            return ArbitrationResult(
                VERDICT_HOLD,
                "回测 IC 先验缺失（U5 未就绪），升权路径不放行（保守）",
            )
        # 跨口径守卫：rank_ic 与 sign_proxy 不在同一坐标轴，符号/量级不可直接比。
        if inp.realized_method != inp.backtest_method:
            return ArbitrationResult(
                VERDICT_METHOD_MISMATCH,
                f"实盘 IC 口径={inp.realized_method} 与回测口径={inp.backtest_method} "
                f"不同（rank_ic vs sign_proxy 不同轴），拒绝跨口径比较，不放行升权",
            )
        # 符号分歧检测：两侧 |ic| 均超 epsilon（避免零附近噪声）且符号相反
        both_material = (
            abs(inp.realized_ic_mean) > div_eps
            and abs(inp.backtest_ic_mean) > div_eps
        )
        opposite_sign = (
            np.sign(inp.realized_ic_mean) != np.sign(inp.backtest_ic_mean)
            and inp.realized_ic_mean != 0
            and inp.backtest_ic_mean != 0
        )
        if both_material and opposite_sign:
            return ArbitrationResult(
                VERDICT_DIVERGENCE,
                f"实盘 IC={inp.realized_ic_mean:.4f} 与回测 IC="
                f"{inp.backtest_ic_mean:.4f} 符号分歧（均超 epsilon {div_eps}），"
                f"记 IC_SOURCE_DIVERGENCE 人工复核",
                divergence=True,
            )
        # 一侧或两侧接近 0（量级低于 epsilon）→ 非真分歧，但也无一致先验支撑升权 → hold
        if not both_material:
            return ArbitrationResult(
                VERDICT_HOLD,
                f"实盘 IC={inp.realized_ic_mean:.4f} 或回测 IC="
                f"{inp.backtest_ic_mean:.4f} 量级低于 epsilon {div_eps}（零附近噪声），"
                f"不判分歧也不放行升权（保守）",
            )
        return ArbitrationResult(
            VERDICT_PROMOTE,
            f"实盘 ICIR={inp.realized_icir:.3f} ≥ 升权门 {promote_th} 且与回测先验符号一致",
        )

    return ArbitrationResult(VERDICT_HOLD, "实盘 ICIR 在升降权门之间，维持现状")


# ---------------------------------------------------------------------------
# FactorHealthTracker —— 滚动 IC/ICIR 落库（SQLite）
# ---------------------------------------------------------------------------


class FactorHealthTracker:
    """滚动 Rank-IC / ICIR / 半衰期落库 + 60 日滚动 ICIR 查询.

    主键 ``(factor_id, window_end, source)``；幂等写入（已存在跳过，除非 force）。
    """

    SCHEMA = """
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
    )
    """
    INDEXES = (
        "CREATE INDEX IF NOT EXISTS idx_ic_factor ON ic_snapshots(factor_id)",
        "CREATE INDEX IF NOT EXISTS idx_ic_window ON ic_snapshots(window_end)",
    )

    def __init__(
        self,
        db_path: str | Path | None = None,
        thresholds: dict[str, Any] | None = None,
        thresholds_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_HEALTH_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 阈值外化：调用方可注入（测试用），否则从 YAML 读（删文件即 fail loud）
        self.thresholds = thresholds if thresholds is not None else load_thresholds(
            thresholds_path
        )
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # 单机双写（cron 回测 hook + app 读）会 database is locked：connect timeout +
        # busy_timeout 让写锁竞争等待而非立刻抛错；WAL 让读写不互斥。
        # journal_mode=WAL 只在库尚未是 WAL 模式时才发；读探测仍有 TOCTOU 窗口，
        # 短重试兜底（见 kss/storage/db.py:connect() 同款修法 + 实测记录）。
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
            # 旧库迁移：method 列后加（CREATE IF NOT EXISTS 不会补列），缺则补。
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(ic_snapshots)")}
            if "method" not in cols:
                conn.execute("ALTER TABLE ic_snapshots ADD COLUMN method TEXT")

    def record_ic_snapshot(
        self,
        factor_id: str,
        window_end: str,
        ic_series: pd.Series,
        *,
        source: str = "realized",
        method: str = IC_METHOD_RANK_IC,
        force: bool = False,
    ) -> ICSnapshot:
        """算一条 IC 快照并落库（幂等；已存在且未 force → 直接返回旧值，不重算）.

        Args:
            factor_id: 因子 ID.
            window_end: 窗口结束日.
            ic_series: index=交易日、值=当日截面 Rank-IC.
            source: ``realized``（实盘）/ ``backtest``（回测先验）.
            method: IC 口径 ``rank_ic`` / ``sign_proxy``（防仲裁跨口径比较）.
            force: True 时覆盖重算（``--force-recompute`` 语义）.

        Returns:
            写入（或既存）的 :class:`ICSnapshot`。
        """
        if not force:
            existing = self.get_snapshot(factor_id, window_end, source=source)
            if existing is not None:
                return existing

        snap = compute_ic_snapshot(
            factor_id, window_end, ic_series,
            thresholds=self.thresholds, source=source, method=method,
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ic_snapshots (
                    factor_id, window_end, source, ic_mean, ic_std, icir,
                    ic_positive_rate, n_periods, ic_t_stat,
                    half_life_1d, half_life_5d, half_life_20d, method,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.factor_id, snap.window_end, snap.source,
                    snap.ic_mean, snap.ic_std, snap.icir, snap.ic_positive_rate,
                    snap.n_periods, snap.ic_t_stat,
                    snap.half_life_1d, snap.half_life_5d, snap.half_life_20d,
                    snap.method, now, now,
                ),
            )
        return snap

    def get_snapshot(
        self, factor_id: str, window_end: str, source: str = "realized"
    ) -> ICSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ic_snapshots WHERE factor_id=? AND window_end=? "
                "AND source=?",
                (factor_id, window_end, source),
            ).fetchone()
        return self._row_to_snap(row) if row is not None else None

    def rolling_icir(
        self, factor_id: str, window_days: int | None = None, source: str = "realized"
    ) -> pd.Series:
        """该因子按 ``window_days`` 滚动的 ICIR 时间序列（按 window_end 升序）.

        以已落库快照的 ``ic_mean`` 序列做滚动 mean/std → ICIR；窗口默认取配置的
        ``rolling_icir_window``（60）。有效观测 = 去重 window_end 数。
        """
        if window_days is None:
            window_days = int(self.thresholds.get("rolling_icir_window", 60))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT window_end, ic_mean FROM ic_snapshots WHERE factor_id=? "
                "AND source=? ORDER BY window_end",
                (factor_id, source),
            ).fetchall()
        if not rows:
            return pd.Series(dtype=float, name="rolling_icir")
        s = pd.Series(
            [r["ic_mean"] for r in rows],
            index=[r["window_end"] for r in rows],
            dtype=float,
        )
        roll_mean = s.rolling(window_days, min_periods=2).mean()
        roll_std = s.rolling(window_days, min_periods=2).std(ddof=1)
        icir = (roll_mean / roll_std).replace([np.inf, -np.inf], np.nan)
        icir.name = "rolling_icir"
        return icir

    @staticmethod
    def _row_to_snap(row: sqlite3.Row) -> ICSnapshot:
        return ICSnapshot(
            factor_id=row["factor_id"],
            window_end=row["window_end"],
            ic_mean=row["ic_mean"],
            ic_std=row["ic_std"],
            icir=row["icir"],
            ic_positive_rate=row["ic_positive_rate"],
            n_periods=row["n_periods"],
            ic_t_stat=row["ic_t_stat"],
            half_life_1d=row["half_life_1d"],
            half_life_5d=row["half_life_5d"],
            half_life_20d=row["half_life_20d"],
            source=row["source"],
            method=(row["method"] if row["method"] else IC_METHOD_RANK_IC),
        )


# ---------------------------------------------------------------------------
# FactorCrashRegistry —— 崩盘登记库 + 状态机
# ---------------------------------------------------------------------------


@dataclass
class CrashRecord:
    factor_id: str
    window_start: str
    window_end: str
    crash_type: str
    ic_mean: float | None = None
    icir: float | None = None
    notes: str = ""
    logged_at: str = ""


class FactorCrashRegistry:
    """因子崩盘登记库 + 生命周期状态机（SQLite）.

    两张表：``crashes``（每次崩盘/分歧事件结构化记录）、``factor_lifecycle``
    （当前状态 + 单向迁移 ACTIVE→PENDING_REVIEW→RETIRED）。
    """

    SCHEMA_CRASH = """
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
    )
    """
    SCHEMA_LIFECYCLE = """
    CREATE TABLE IF NOT EXISTS factor_lifecycle (
        factor_id     TEXT PRIMARY KEY,
        state         TEXT NOT NULL,
        updated_at    TEXT,
        events        TEXT
    )
    """
    INDEXES = (
        "CREATE INDEX IF NOT EXISTS idx_crash_factor ON crashes(factor_id)",
        "CREATE INDEX IF NOT EXISTS idx_crash_type ON crashes(crash_type)",
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_HEALTH_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # 单机双写（cron 回测 hook + app 读）会 database is locked：connect timeout +
        # busy_timeout 让写锁竞争等待而非立刻抛错；WAL 让读写不互斥。
        # journal_mode=WAL 只在库尚未是 WAL 模式时才发；读探测仍有 TOCTOU 窗口，
        # 短重试兜底（见 kss/storage/db.py:connect() 同款修法 + 实测记录）。
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
            conn.execute(self.SCHEMA_CRASH)
            conn.execute(self.SCHEMA_LIFECYCLE)
            for idx in self.INDEXES:
                conn.execute(idx)

    # -------------------------------------------------------------- #
    # 崩盘登记
    # -------------------------------------------------------------- #

    def log_crash(
        self,
        factor_id: str,
        *,
        window_start: str,
        window_end: str,
        crash_type: str,
        ic_mean: float | None = None,
        icir: float | None = None,
        notes: str = "",
    ) -> CrashRecord:
        """写一条崩盘 / 分歧事件记录（结构化，可查询）."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crashes (
                    factor_id, window_start, window_end, crash_type,
                    ic_mean, icir, notes, logged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (factor_id, window_start, window_end, crash_type,
                 ic_mean, icir, notes, now),
            )
        return CrashRecord(
            factor_id=factor_id, window_start=window_start, window_end=window_end,
            crash_type=crash_type, ic_mean=ic_mean, icir=icir, notes=notes,
            logged_at=now,
        )

    def query(
        self,
        factor_id: str | None = None,
        since: str | None = None,
        crash_type: str | None = None,
    ) -> list[CrashRecord]:
        """按因子 / 起始日 / 类型查询崩盘记录."""
        clauses: list[str] = []
        params: list[Any] = []
        if factor_id is not None:
            clauses.append("factor_id = ?")
            params.append(factor_id)
        if since is not None:
            clauses.append("window_end >= ?")
            params.append(since)
        if crash_type is not None:
            clauses.append("crash_type = ?")
            params.append(crash_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crashes" + where + " ORDER BY window_end, id", params
            ).fetchall()
        return [
            CrashRecord(
                factor_id=r["factor_id"], window_start=r["window_start"],
                window_end=r["window_end"], crash_type=r["crash_type"],
                ic_mean=r["ic_mean"], icir=r["icir"], notes=r["notes"],
                logged_at=r["logged_at"],
            )
            for r in rows
        ]

    # -------------------------------------------------------------- #
    # 状态机：ACTIVE → PENDING_REVIEW → RETIRED（单向）
    # -------------------------------------------------------------- #

    def get_state(self, factor_id: str) -> str:
        """当前状态；未登记默认 ACTIVE."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM factor_lifecycle WHERE factor_id=?",
                (factor_id,),
            ).fetchone()
        return row["state"] if row is not None else STATE_ACTIVE

    def set_state(
        self, factor_id: str, new_state: str, *, note: str = ""
    ) -> str:
        """迁移状态（单向，不可回退）；返回迁移后状态.

        - ACTIVE → PENDING_REVIEW → RETIRED 单向；试图回退（如 RETIRED→ACTIVE）
          被拒并保持原状（fail loud：warning）。
        - RETIRE 是终态，剔除留人工确认（本方法只在调用方确认后调用）。
        """
        if new_state not in _STATE_ORDER:
            raise ValueError(f"未知状态: {new_state}")
        cur = self.get_state(factor_id)
        if _STATE_ORDER[new_state] < _STATE_ORDER[cur]:
            logger.warning(
                "[FactorLifecycle] 拒绝回退 %s: %s → %s（状态机单向）",
                factor_id, cur, new_state,
            )
            return cur
        now = datetime.now(timezone.utc).isoformat()
        events = self._load_events(factor_id)
        events.append({"at": now, "from": cur, "to": new_state, "note": note})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO factor_lifecycle (
                    factor_id, state, updated_at, events
                ) VALUES (?, ?, ?, ?)
                """,
                (factor_id, new_state, now, json.dumps(events, ensure_ascii=False)),
            )
        return new_state

    def _load_events(self, factor_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT events FROM factor_lifecycle WHERE factor_id=?",
                (factor_id,),
            ).fetchone()
        if row is None or not row["events"]:
            return []
        try:
            return list(json.loads(row["events"]))
        except json.JSONDecodeError:
            return []

    def list_pending_review(self) -> list[str]:
        """当前 PENDING_REVIEW 因子（供人工剔除确认）."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT factor_id FROM factor_lifecycle WHERE state=? ORDER BY factor_id",
                (STATE_PENDING_REVIEW,),
            ).fetchall()
        return [r["factor_id"] for r in rows]

    def active_factors(self, candidates: list[str]) -> list[str]:
        """从候选里过滤掉非 ACTIVE（RETIRED/PENDING_REVIEW）因子.

        供 ``WalkForwardCombiner`` 构造 ``feature_cols`` 时跳过已退役因子（R11）。
        仅退役 RETIRED 强制剔除；PENDING_REVIEW 是否参与由调用方决定，这里保守
        只保留 ACTIVE。
        """
        return [c for c in candidates if self.get_state(c) == STATE_ACTIVE]


# ---------------------------------------------------------------------------
# 回测结束 hook —— surgical，默认不破坏现有回测
# ---------------------------------------------------------------------------


# 回测 IC 先验注入接口（Global Decision #8）：U5（CPCV）提供；签名 = factor_id →
# (backtest_ic_mean, backtest_icir) | None。U5 未就绪时传 None，升权路径不放行。
BacktestICSource = Callable[[str], "tuple[float, float] | None"]


@dataclass
class FactorHealthHook:
    """回测结束钩子：把因子 IC 快照入库、跑崩盘判定 + #8 仲裁、衰减告警.

    Surgical：默认参数下不改变现有回测行为；调用方在回测结束点显式构造并调用
    :meth:`on_backtest_end`，不传则现有回测完全不受影响。

    Attributes:
        tracker / registry: 落库与状态机.
        backtest_ic_source: #8 回测先验注入接口（U5 未就绪 → None，升权不放行）.
        notify_channel: 衰减告警通道（``console`` / ``telegram`` / ``all``）；默认 console.
        notify: 是否发告警（测试可关）.
    """

    tracker: FactorHealthTracker
    registry: FactorCrashRegistry
    backtest_ic_source: BacktestICSource | None = None
    # 回测先验的 IC 口径（CPCV/WFC = sign_proxy；rank_ic 来源须显式覆盖）。
    # 仲裁据此与实盘口径比对，不同则 method_mismatch 不跨口径比。
    backtest_ic_method: str = IC_METHOD_SIGN_PROXY
    notify_channel: str = "console"
    notify: bool = True
    thresholds: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.thresholds:
            self.thresholds = self.tracker.thresholds

    def on_backtest_end(
        self,
        factor_id: str,
        window_start: str,
        window_end: str,
        ic_series: pd.Series,
        *,
        source: str = "realized",
        method: str = IC_METHOD_RANK_IC,
    ) -> dict[str, Any]:
        """回测/结算结束时调用：落库 → 崩盘判定 → #8 仲裁 → 告警.

        返回摘要 dict（snapshot / verdict / crashes / state），供调用方/测试断言。
        实盘 IC（``source="realized"``）走 #8 仲裁；回测 IC（``source="backtest"``）
        只落库不驱动状态机（它是先验，由 U5 自己产生）。

        ``method``：该 ic_series 的口径（cross_section=rank_ic / wfc=sign_proxy）；
        仲裁拿它与回测先验 ``backtest_ic_method`` 比，不同则 method_mismatch 不跨口径比。
        """
        snap = self.tracker.record_ic_snapshot(
            factor_id, window_end, ic_series, source=source, method=method
        )
        out: dict[str, Any] = {"snapshot": snap, "crashes": [], "verdict": None}

        # 回测先验只落库，不驱动实盘状态机
        if source != "realized":
            out["state"] = self.registry.get_state(factor_id)
            return out

        th = self.thresholds
        daily = _dedup_daily_ic(ic_series)

        # --- 崩盘判定 1：IC 绝对值连续 N 期 < ic_floor ---
        n_consec = int(th["crash_consecutive_n"])
        ic_floor = float(th["ic_floor"])
        if _max_consecutive_below(daily, ic_floor) >= n_consec:
            rec = self.registry.log_crash(
                factor_id, window_start=window_start, window_end=window_end,
                crash_type=CRASH_IC_DECAY, ic_mean=snap.ic_mean, icir=snap.icir,
                notes=f"IC 绝对值连续 ≥{n_consec} 期 < {ic_floor}",
            )
            out["crashes"].append(rec)

        # --- #8 仲裁：实盘后验 + 回测先验 ---
        prior = self.backtest_ic_source(factor_id) if self.backtest_ic_source else None
        bt_ic_mean = prior[0] if prior is not None else None
        bt_icir = prior[1] if prior is not None else None
        result = arbitrate(
            ArbitrationInput(
                realized_icir=snap.icir,
                realized_ic_mean=snap.ic_mean,
                realized_n=snap.n_periods,
                backtest_ic_mean=bt_ic_mean,
                backtest_icir=bt_icir,
                realized_method=snap.method,
                backtest_method=self.backtest_ic_method,
            ),
            th,
        )
        out["verdict"] = result

        # --- 符号分歧：记 IC_SOURCE_DIVERGENCE，人工复核（不静默）---
        if result.divergence:
            rec = self.registry.log_crash(
                factor_id, window_start=window_start, window_end=window_end,
                crash_type=CRASH_IC_SOURCE_DIVERGENCE,
                ic_mean=snap.ic_mean, icir=snap.icir, notes=result.reason,
            )
            out["crashes"].append(rec)

        # --- 状态迁移（#8）---
        if result.verdict == VERDICT_DEMOTE:
            # ICIR 跌破基线也记一条 ICIR_BELOW_BASELINE
            self.registry.log_crash(
                factor_id, window_start=window_start, window_end=window_end,
                crash_type=CRASH_ICIR_BELOW_BASELINE,
                ic_mean=snap.ic_mean, icir=snap.icir,
                notes=result.reason,
            )
            self.registry.set_state(
                factor_id, STATE_PENDING_REVIEW, note=result.reason
            )
            self._alert(factor_id, snap, result)
        elif result.verdict == VERDICT_PROMOTE:
            # 双源一致才放行升权 / 回 ACTIVE（仅当当前不是 RETIRED 终态）
            self.registry.set_state(factor_id, STATE_ACTIVE, note=result.reason)
        # insufficient_n / hold / divergence → 不翻转状态（保守）

        out["state"] = self.registry.get_state(factor_id)
        return out

    def _alert(
        self, factor_id: str, snap: ICSnapshot, result: ArbitrationResult
    ) -> None:
        """衰减告警走 send_to_channels（console/telegram）；数字代码渲染."""
        if not self.notify:
            return
        baseline = float(self.thresholds["realized_icir_demote"])
        message = (
            f"[FACTOR ALERT] {factor_id} ICIR={snap.icir:.2f} 低于基线 {baseline:.2f}"
            f"（{snap.n_periods}期，去重交易日数）→ PENDING_REVIEW；{result.reason}"
        )
        try:
            from kss.notifications.manager import send_to_channels

            send_to_channels(
                message, channel=self.notify_channel,
                title=f"因子健康度告警 {factor_id}",
            )
        except Exception as exc:  # noqa: BLE001 —— 告警失败不打断回测
            logger.error("因子健康度告警发送失败: %s", exc)


def _max_consecutive_below(daily_ic: pd.Series, floor: float) -> int:
    """``|IC| < floor`` 的最长连续期数（崩盘判定 IC_DECAY）."""
    s = daily_ic.dropna()
    if s.empty:
        return 0
    below = (s.abs() < floor).astype(int).values
    best = cur = 0
    for b in below:
        cur = cur + 1 if b else 0
        best = max(best, cur)
    return best
