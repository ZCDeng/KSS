"""SQLiteStore 单元测试.

约束：所有数据写到 ``tmp_path`` 临时目录，不污染真实 storage/.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kss.data.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path) -> SQLiteStore:
    """每个测试一个全新的 db 文件，放在 tmp_path 下."""
    return SQLiteStore(tmp_path / "test_quotes.db")


def _make_stock_df(
    ts_code: str = "688017.SH",
    dates: list[str] | None = None,
    close_base: float = 100.0,
) -> pd.DataFrame:
    dates = dates or ["2024-01-02", "2024-01-03", "2024-01-04"]
    n = len(dates)
    return pd.DataFrame(
        {
            "ts_code": [ts_code] * n,
            "trade_date": dates,
            "open": [close_base + i for i in range(n)],
            "high": [close_base + i + 1 for i in range(n)],
            "low": [close_base + i - 1 for i in range(n)],
            "close": [close_base + i + 0.5 for i in range(n)],
            "pre_close": [close_base + i - 0.5 for i in range(n)],
            "change": [1.0] * n,
            "pct_chg": [0.01] * n,
            "vol": [1_000_000.0] * n,
            "amount": [5_000_000.0] * n,
            "turnover_rate": [1.5] * n,
            "volume_ratio": [1.2] * n,
            "pe": [30.0] * n,
            "pb": [3.0] * n,
            "total_mv": [1e9] * n,
        }
    )


def _make_index_df(ts_code: str = "000688.SH") -> pd.DataFrame:
    # 故意混用 YYYYMMDD 风格（index_*.csv 的真实格式）
    return pd.DataFrame(
        {
            "ts_code": [ts_code] * 3,
            "trade_date": ["20240102", "20240103", "20240104"],
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "pre_close": [100.0, 101.0, 102.0],
            "change": [1.0, 1.0, 1.0],
            "pct_chg": [0.01, 0.01, 0.01],
            "vol": [1e8, 1.1e8, 1.2e8],
            "amount": [1e9, 1.1e9, 1.2e9],
        }
    )


# ---------------------------------------------------------------------------
# 写入 + 读取 round-trip
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_and_load_single_stock(self, store: SQLiteStore) -> None:
        df_in = _make_stock_df("688017.SH")
        n = store.save_stock(df_in)
        assert n == len(df_in)

        df_out = store.load_stock("688017.SH")
        assert df_out is not None
        assert len(df_out) == len(df_in)
        # trade_date 应为 datetime64
        assert pd.api.types.is_datetime64_any_dtype(df_out["trade_date"])
        # 数值列应保持一致
        assert df_out["close"].tolist() == df_in["close"].tolist()
        assert set(df_out["ts_code"].unique()) == {"688017.SH"}

    def test_upsert_same_key_overwrites(self, store: SQLiteStore) -> None:
        df1 = _make_stock_df("688017.SH", dates=["2024-01-02"], close_base=100.0)
        df2 = _make_stock_df("688017.SH", dates=["2024-01-02"], close_base=200.0)
        store.save_stock(df1)
        # 不应抛异常
        store.save_stock(df2)

        df_out = store.load_stock("688017.SH")
        assert df_out is not None
        assert len(df_out) == 1
        # 最新写入应覆盖（close = 200.5）
        assert df_out["close"].iloc[0] == pytest.approx(200.5)

    def test_save_different_ts_codes_isolated(self, store: SQLiteStore) -> None:
        store.save_stock(_make_stock_df("688017.SH", close_base=100.0))
        store.save_stock(_make_stock_df("300750.SZ", close_base=500.0))

        a = store.load_stock("688017.SH")
        b = store.load_stock("300750.SZ")
        assert a is not None and b is not None
        assert a["close"].iloc[0] == pytest.approx(100.5)
        assert b["close"].iloc[0] == pytest.approx(500.5)
        # 互不污染
        assert set(a["ts_code"]) == {"688017.SH"}
        assert set(b["ts_code"]) == {"300750.SZ"}


# ---------------------------------------------------------------------------
# 池查询 / 不存在路径
# ---------------------------------------------------------------------------


class TestPoolAndMissing:
    def test_load_stocks_by_pool_prefix(self, store: SQLiteStore) -> None:
        store.save_stock(_make_stock_df("688017.SH"))
        store.save_stock(_make_stock_df("688322.SH"))
        store.save_stock(_make_stock_df("300750.SZ"))

        kcb = store.load_stocks(pool_prefix="688")
        assert set(kcb["ts_code"].unique()) == {"688017.SH", "688322.SH"}

        cyb = store.load_stocks(pool_prefix="300")
        assert set(cyb["ts_code"].unique()) == {"300750.SZ"}

    def test_load_stocks_explicit_list(self, store: SQLiteStore) -> None:
        store.save_stock(_make_stock_df("688017.SH"))
        store.save_stock(_make_stock_df("688322.SH"))
        store.save_stock(_make_stock_df("300750.SZ"))

        df = store.load_stocks(ts_codes=["688017.SH", "300750.SZ"])
        assert set(df["ts_code"].unique()) == {"688017.SH", "300750.SZ"}

    def test_load_stock_missing_returns_none(self, store: SQLiteStore) -> None:
        assert store.load_stock("999999.XX") is None


# ---------------------------------------------------------------------------
# 指数
# ---------------------------------------------------------------------------


class TestIndex:
    def test_save_and_load_index(self, store: SQLiteStore) -> None:
        df_in = _make_index_df("000688.SH")
        n = store.save_index(df_in)
        assert n == len(df_in)

        df_out = store.load_index("000688.SH")
        assert df_out is not None
        assert len(df_out) == 3
        assert pd.api.types.is_datetime64_any_dtype(df_out["trade_date"])
        # YYYYMMDD 字符串应被解析为 2024-01-02 等
        assert df_out["trade_date"].iloc[0] == pd.Timestamp("2024-01-02")

    def test_load_index_missing_returns_none(self, store: SQLiteStore) -> None:
        assert store.load_index("XXX.YY") is None


# ---------------------------------------------------------------------------
# 元信息 / list_*
# ---------------------------------------------------------------------------


class TestMeta:
    def test_list_symbols_and_indices(self, store: SQLiteStore) -> None:
        store.save_stock(_make_stock_df("688322.SH"))
        store.save_stock(_make_stock_df("688017.SH"))
        store.save_index(_make_index_df("000688.SH"))

        assert store.list_symbols() == ["688017.SH", "688322.SH"]
        assert store.list_indices() == ["000688.SH"]

    def test_stats_empty_and_populated(self, store: SQLiteStore) -> None:
        # empty
        empty = store.stats()
        assert empty["n_stocks"] == 0
        assert empty["n_indices"] == 0
        assert empty["n_stock_rows"] == 0
        assert empty["date_range"] == (None, None)
        assert empty["db_size_mb"] >= 0.0

        # populated
        store.save_stock(_make_stock_df("688017.SH"))
        store.save_index(_make_index_df("000688.SH"))
        s = store.stats()
        assert s["n_stocks"] == 1
        assert s["n_indices"] == 1
        assert s["n_stock_rows"] == 3
        assert s["n_index_rows"] == 3
        # date_range 应为 YYYY-MM-DD 字符串元组
        assert s["date_range"][0] == "2024-01-02"
        assert s["date_range"][1] == "2024-01-04"
