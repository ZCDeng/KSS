"""离线批处理推理（U4）—— 冻结模型对全 universe 前向预测，落 SQLite.

只读、与 ``log_mv`` 实盘**零连接**：本模块不 import 任何实盘决策 / paper_trade
路径，预测写独立 ``storage/kronos/predictions.sqlite``（与 ``storage/*.db`` 行情库
分离）。每个 base_date 对每票产出点估计 + 蒙特卡洛不确定带。

隔离守卫（``Covers AE1``）：被预测 bar 必须严格晚于冻结截断日 D；输入窗口末尾
必须严格早于被预测 bar（特征级 look-ahead）。单票失败 → 跳过 + 记录，不中断整批
（exit-code 语义沿用 paper_trade：有产出即 0）。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from kss.kronos.adapter import build_inference_input, normalize_ohlcv
from kss.kronos.config import (
    BAND_LOWER_Q,
    BAND_UPPER_Q,
    INFER_LOOKBACK_BARS,
    PREDICT_LEN,
    PREDICTIONS_DB,
    PREDICTIONS_TABLE,
    SAMPLE_COUNT,
)
from kss.kronos.isolation import assert_after_cutoff, assert_window_precedes_target

logger = logging.getLogger(__name__)


# 预测表 schema（Deferred-to-Implementation：在此定型）。
PREDICTION_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {PREDICTIONS_TABLE} (
    ts_code TEXT NOT NULL,
    base_date TEXT NOT NULL,       -- 推理基准日（输入窗口末尾 bar）
    target_date TEXT NOT NULL,     -- 被预测 bar 日期（严格 > base_date 且 > D）
    horizon INTEGER NOT NULL,      -- 1..pred_len
    point_close REAL,              -- close 点估计（MC 样本均值）
    lower_close REAL,              -- 不确定带下分位
    upper_close REAL,              -- 不确定带上分位
    point_ret REAL,                -- 相对 base close 的累计收益点估计
    base_close REAL,               -- 基准日 close（回溯锚点）
    cutoff_d TEXT NOT NULL,        -- 冻结截断日 D
    model_id TEXT NOT NULL,        -- artifact 标识
    created_at TEXT,
    PRIMARY KEY (ts_code, base_date, target_date, model_id)
)
"""

_COLUMNS = (
    "ts_code", "base_date", "target_date", "horizon", "point_close",
    "lower_close", "upper_close", "point_ret", "base_close",
    "cutoff_d", "model_id", "created_at",
)


class KronosPredictionStore:
    """Kronos 预测的 SQLite 仓库（镜像 SQLiteStore：stdlib + 新连接 + commit + close）.

    与 ``log_mv`` 行情库物理分离，体现"零连接"。
    """

    def __init__(self, db_path: str | Path = PREDICTIONS_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(PREDICTION_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, rows: list[dict]) -> int:
        """Upsert 预测行；返回写入条数."""
        if not rows:
            return 0
        placeholders = ",".join("?" for _ in _COLUMNS)
        cols = ",".join(_COLUMNS)
        sql = f"INSERT OR REPLACE INTO {PREDICTIONS_TABLE} ({cols}) VALUES ({placeholders})"
        tuples = [tuple(r.get(c) for c in _COLUMNS) for r in rows]
        with self._conn() as conn:
            conn.executemany(sql, tuples)
        return len(tuples)

    def load(self, ts_code: str | None = None) -> pd.DataFrame:
        """读回预测（可按 ts_code 过滤）."""
        with self._conn() as conn:
            if ts_code is None:
                return pd.read_sql_query(f"SELECT * FROM {PREDICTIONS_TABLE}", conn)
            return pd.read_sql_query(
                f"SELECT * FROM {PREDICTIONS_TABLE} WHERE ts_code = ?", conn, params=(ts_code,)
            )


@dataclass
class BatchResult:
    """一次批处理的结果摘要."""

    n_predictions: int = 0
    n_symbols_ok: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _mc_close_paths(
    predictor, ki, *, pred_len: int, mc_samples: int, T: float, top_p: float
) -> np.ndarray:
    """跑 ``mc_samples`` 次随机采样预测，返回 ``[mc_samples, pred_len]`` 的 close 路径."""
    paths = []
    for _ in range(mc_samples):
        out = predictor.predict(
            df=ki.df, x_timestamp=ki.x_timestamp, y_timestamp=ki.y_timestamp,
            pred_len=pred_len, T=T, top_p=top_p, sample_count=1, verbose=False,
        )
        paths.append(np.asarray(out["close"].values, dtype=float))
    return np.vstack(paths)


def infer_symbol(
    predictor,
    df: pd.DataFrame,
    *,
    cutoff,
    model_id: str,
    symbol: str,
    base_date=None,
    lookback_bars: int = INFER_LOOKBACK_BARS,
    pred_len: int = PREDICT_LEN,
    mc_samples: int = SAMPLE_COUNT,
    T: float = 1.0,
    top_p: float = 0.9,
) -> list[dict] | None:
    """对单票产出 D 之后预测行；历史不足 / 越界 / NaN 时返回 ``None``.

    Args:
        predictor: 冻结 KronosPredictor.
        df: 该票 cs_data.
        cutoff: 冻结截断日 D（被预测 bar 必须严格晚于 D）.
        model_id: artifact 标识，写入每行.
        symbol: ts_code.
        base_date: 推理基准日；缺省用最新可用 bar.
        lookback_bars / pred_len / mc_samples / T / top_p: 推理超参.

    Returns:
        预测行 dict 列表，或 ``None``（含原因日志）.

    Raises:
        LeakageError: 隔离守卫被违反（向上抛由 :func:`run_batch` 记录为 skip）.
    """
    norm = normalize_ohlcv(df)
    if base_date is not None:
        norm = norm[norm["trade_date"] <= pd.Timestamp(base_date)].reset_index(drop=True)
    if len(norm) <= lookback_bars:
        logger.info("跳过 %s：历史不足", symbol)
        return None

    # 真实未来交易日（base 之后）当 y_timestamp；不足则外推工作日。
    base_idx = len(norm) - 1
    base_close = float(norm["close"].iloc[base_idx])
    base_dt = norm["trade_date"].iloc[base_idx]

    ki = build_inference_input(norm, lookback_bars=lookback_bars, pred_len=pred_len, symbol=symbol)
    if ki is None:
        return None

    # 隔离：输入窗口末尾 < 被预测 bar；被预测 bar 严格 > D（Covers AE1）。
    assert_window_precedes_target(ki.x_timestamp, ki.y_timestamp)
    assert_after_cutoff(ki.y_timestamp, cutoff)

    paths = _mc_close_paths(predictor, ki, pred_len=pred_len, mc_samples=mc_samples, T=T, top_p=top_p)
    point = paths.mean(axis=0)
    lower = np.quantile(paths, BAND_LOWER_Q, axis=0)
    upper = np.quantile(paths, BAND_UPPER_Q, axis=0)

    created = datetime.now(timezone.utc).isoformat()
    cutoff_s = str(pd.Timestamp(cutoff).date())
    rows: list[dict] = []
    for h in range(pred_len):
        rows.append({
            "ts_code": symbol,
            "base_date": str(pd.Timestamp(base_dt).date()),
            "target_date": str(pd.Timestamp(ki.y_timestamp.iloc[h]).date()),
            "horizon": h + 1,
            "point_close": float(point[h]),
            "lower_close": float(lower[h]),
            "upper_close": float(upper[h]),
            "point_ret": float(point[h] / base_close - 1.0) if base_close else None,
            "base_close": base_close,
            "cutoff_d": cutoff_s,
            "model_id": model_id,
            "created_at": created,
        })
    return rows


def run_batch(
    frames: dict[str, pd.DataFrame],
    predictor,
    *,
    cutoff,
    model_id: str,
    store: KronosPredictionStore | None = None,
    base_date=None,
    lookback_bars: int = INFER_LOOKBACK_BARS,
    pred_len: int = PREDICT_LEN,
    mc_samples: int = SAMPLE_COUNT,
) -> BatchResult:
    """对全 universe 批量推理并落库；单票失败跳过不中断整批.

    Args:
        frames: ``{symbol: cs_data}``.
        predictor: 冻结 KronosPredictor.
        cutoff: 冻结截断日 D.
        model_id: artifact 标识.
        store: 预测仓库；缺省新建默认库.
        base_date / lookback_bars / pred_len / mc_samples: 推理超参.

    Returns:
        :class:`BatchResult`.
    """
    store = store or KronosPredictionStore()
    result = BatchResult()
    all_rows: list[dict] = []
    for symbol, df in frames.items():
        try:
            rows = infer_symbol(
                predictor, df, cutoff=cutoff, model_id=model_id, symbol=symbol,
                base_date=base_date, lookback_bars=lookback_bars,
                pred_len=pred_len, mc_samples=mc_samples,
            )
        except Exception as exc:  # noqa: BLE001 - 单票失败不中断整批
            logger.warning("跳过 %s：%s", symbol, exc)
            result.skipped.append((symbol, str(exc)))
            continue
        if rows is None:
            result.skipped.append((symbol, "历史不足/无输出"))
            continue
        all_rows.extend(rows)
        result.n_symbols_ok += 1

    result.n_predictions = store.save(all_rows)
    logger.info(
        "批处理完成：%d 票产出 %d 条预测，跳过 %d 票",
        result.n_symbols_ok, result.n_predictions, len(result.skipped),
    )
    return result
