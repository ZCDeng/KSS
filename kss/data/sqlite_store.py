"""SQLite 数据存储层 —— CSV 的高性能替代品（向后兼容，不强制）.

设计原则：
- 旧 CSV 路径完全保留；本模块作为可选加速通道.
- 表 schema 与现有 cs_data_*.csv 列对齐：ts_code, trade_date, open, high, low, close,
  pre_close, change, pct_chg, vol, amount, turnover_rate, volume_ratio, pe, pb, total_mv.
- 写入用 INSERT OR REPLACE 支持增量更新.
- 不引入新依赖（sqlite3 是标准库），每次新连接 + commit + close（简单胜复杂）.

用例:

    store = SQLiteStore("storage/kss_quotes.db")
    df = store.load_stocks(pool_prefix="688")       # 读科创板全板
    df1 = store.load_stock("688017.SH")             # 读单股
    store.save_stock(df, ts_code="688017.SH")       # 写入 / 更新
    df_idx = store.load_index("000688.SH")
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量：列顺序 / schema
# ---------------------------------------------------------------------------

# 与 cs_data_*.csv 完全对齐
STOCK_COLUMNS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "pe",
    "pb",
    "total_mv",
)

# 与 index_*.csv / cs_index_*.csv 对齐（无估值列）
INDEX_COLUMNS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)


class SQLiteStore:
    """KSS 行情数据 SQLite 仓库."""

    SCHEMA_STOCK_QUOTES = """
    CREATE TABLE IF NOT EXISTS stock_quotes (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        pre_close REAL, change REAL, pct_chg REAL,
        vol REAL, amount REAL,
        turnover_rate REAL, volume_ratio REAL,
        pe REAL, pb REAL, total_mv REAL,
        PRIMARY KEY (ts_code, trade_date)
    )
    """

    SCHEMA_INDEX_QUOTES = """
    CREATE TABLE IF NOT EXISTS index_quotes (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        pre_close REAL, change REAL, pct_chg REAL,
        vol REAL, amount REAL,
        PRIMARY KEY (ts_code, trade_date)
    )
    """

    SCHEMA_INDEX_DATE = (
        "CREATE INDEX IF NOT EXISTS idx_stock_date ON stock_quotes(trade_date)"
    )
    SCHEMA_INDEX_TS = (
        "CREATE INDEX IF NOT EXISTS idx_idx_ts ON index_quotes(ts_code)"
    )

    # ------------------------------------------------------------------
    # init / connection
    # ------------------------------------------------------------------

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        # 父目录可能不存在（首次使用 storage/kss_quotes.db）
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(self.SCHEMA_STOCK_QUOTES)
            conn.execute(self.SCHEMA_INDEX_QUOTES)
            conn.execute(self.SCHEMA_INDEX_DATE)
            conn.execute(self.SCHEMA_INDEX_TS)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """上下文管理器：每次都开新连接 → commit → close.

        简单胜复杂；不做连接池.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_df(
        df: pd.DataFrame,
        columns: Iterable[str],
        ts_code: str | None,
    ) -> pd.DataFrame:
        """对齐列、回填 ts_code、统一 trade_date 为 ``YYYY-MM-DD`` 字符串."""
        if df is None or df.empty:
            return pd.DataFrame(columns=list(columns))

        df = df.copy()

        # 缺失 ts_code 列时回填（CSV 自带，但调用方可能传裸 df）
        if "ts_code" not in df.columns:
            if ts_code is None:
                raise ValueError("ts_code 缺失且未提供回填值")
            df["ts_code"] = ts_code
        else:
            if ts_code is not None:
                df["ts_code"] = ts_code

        # 标准化日期 → 字符串 YYYY-MM-DD（SQLite TEXT 利于范围查询排序）
        if "trade_date" not in df.columns:
            raise ValueError("DataFrame 缺少 trade_date 列")
        td = df["trade_date"]
        if pd.api.types.is_datetime64_any_dtype(td):
            df["trade_date"] = td.dt.strftime("%Y-%m-%d")
        else:
            # 既兼容 "2023-01-03" 也兼容 "20230103"
            parsed = pd.to_datetime(td.astype(str), errors="coerce")
            # 8 位纯数字时 pandas 也能 parse；fallback 用 format
            mask_null = parsed.isna()
            if mask_null.any():
                parsed.loc[mask_null] = pd.to_datetime(
                    td.astype(str).loc[mask_null], format="%Y%m%d", errors="coerce"
                )
            df["trade_date"] = parsed.dt.strftime("%Y-%m-%d")

        # 仅保留 schema 内的列；缺失列补 None
        for col in columns:
            if col not in df.columns:
                df[col] = None
        df = df[list(columns)]

        return df

    @staticmethod
    def _parse_dates(df: pd.DataFrame) -> pd.DataFrame:
        if "trade_date" in df.columns and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        return df

    def _executemany_upsert(
        self,
        table: str,
        columns: tuple[str, ...],
        rows: list[tuple],
    ) -> int:
        if not rows:
            return 0
        placeholders = ",".join(["?"] * len(columns))
        col_list = ",".join(columns)
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        with self._conn() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def save_stock(self, df: pd.DataFrame, ts_code: str | None = None) -> int:
        """写入股票行情；同 (ts_code, trade_date) 自动覆盖. 返回写入行数."""
        normalized = self._normalize_df(df, STOCK_COLUMNS, ts_code)
        rows = [tuple(x) for x in normalized.itertuples(index=False, name=None)]
        # 把 NaN 替换为 None（sqlite3 不接受 NaN）
        rows = [tuple(None if _is_nan(v) else v for v in row) for row in rows]
        return self._executemany_upsert("stock_quotes", STOCK_COLUMNS, rows)

    def save_index(self, df: pd.DataFrame, ts_code: str | None = None) -> int:
        """写入指数行情；同 (ts_code, trade_date) 自动覆盖. 返回写入行数."""
        normalized = self._normalize_df(df, INDEX_COLUMNS, ts_code)
        rows = [tuple(x) for x in normalized.itertuples(index=False, name=None)]
        rows = [tuple(None if _is_nan(v) else v for v in row) for row in rows]
        return self._executemany_upsert("index_quotes", INDEX_COLUMNS, rows)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def load_stock(self, ts_code: str) -> pd.DataFrame | None:
        """读单股；不存在返回 None."""
        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM stock_quotes WHERE ts_code = ? ORDER BY trade_date",
                conn,
                params=(ts_code,),
            )
        if df.empty:
            return None
        return self._parse_dates(df)

    def load_stocks(
        self,
        ts_codes: list[str] | None = None,
        pool_prefix: str | None = None,
    ) -> pd.DataFrame:
        """读多股. 支持显式列表或前缀筛选（如 "688" → 全部科创板）.

        - ``ts_codes``: 显式列表，优先级最高.
        - ``pool_prefix``: 前缀（匹配 ts_code 的前 N 位）.
        - 都为 None: 返回全表.
        """
        with self._conn() as conn:
            if ts_codes:
                placeholders = ",".join(["?"] * len(ts_codes))
                sql = (
                    f"SELECT * FROM stock_quotes WHERE ts_code IN ({placeholders}) "
                    "ORDER BY ts_code, trade_date"
                )
                df = pd.read_sql_query(sql, conn, params=tuple(ts_codes))
            elif pool_prefix:
                df = pd.read_sql_query(
                    "SELECT * FROM stock_quotes WHERE ts_code LIKE ? "
                    "ORDER BY ts_code, trade_date",
                    conn,
                    params=(f"{pool_prefix}%",),
                )
            else:
                df = pd.read_sql_query(
                    "SELECT * FROM stock_quotes ORDER BY ts_code, trade_date",
                    conn,
                )
        return self._parse_dates(df)

    def load_index(self, ts_code: str) -> pd.DataFrame | None:
        """读单个指数；不存在返回 None."""
        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM index_quotes WHERE ts_code = ? ORDER BY trade_date",
                conn,
                params=(ts_code,),
            )
        if df.empty:
            return None
        return self._parse_dates(df)

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ts_code FROM stock_quotes ORDER BY ts_code"
            ).fetchall()
        return [r[0] for r in rows]

    def list_indices(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ts_code FROM index_quotes ORDER BY ts_code"
            ).fetchall()
        return [r[0] for r in rows]

    def stats(self) -> dict:
        """返回 db 状态：``{n_stocks, n_indices, n_stock_rows, n_index_rows,
        date_range, db_size_mb}``."""
        with self._conn() as conn:
            n_stocks = conn.execute(
                "SELECT COUNT(DISTINCT ts_code) FROM stock_quotes"
            ).fetchone()[0]
            n_indices = conn.execute(
                "SELECT COUNT(DISTINCT ts_code) FROM index_quotes"
            ).fetchone()[0]
            n_stock_rows = conn.execute(
                "SELECT COUNT(*) FROM stock_quotes"
            ).fetchone()[0]
            n_index_rows = conn.execute(
                "SELECT COUNT(*) FROM index_quotes"
            ).fetchone()[0]
            date_min, date_max = conn.execute(
                "SELECT MIN(trade_date), MAX(trade_date) FROM stock_quotes"
            ).fetchone()

        size_mb = (
            self.db_path.stat().st_size / (1024 * 1024)
            if self.db_path.exists()
            else 0.0
        )
        return {
            "n_stocks": int(n_stocks or 0),
            "n_indices": int(n_indices or 0),
            "n_stock_rows": int(n_stock_rows or 0),
            "n_index_rows": int(n_index_rows or 0),
            "date_range": (date_min, date_max),
            "db_size_mb": round(size_mb, 2),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_nan(v: object) -> bool:
    """安全 NaN 检测：仅 float 走 pd.isna，其他类型不当 NaN."""
    if v is None:
        return True
    try:
        if isinstance(v, float):
            return pd.isna(v)
    except Exception:
        return False
    return False
