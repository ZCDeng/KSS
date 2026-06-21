# -*- coding: utf-8 -*-
"""U2: ensure_history 新股全量回填的单测 (mock TushareClient, 不联网)。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


uc = _load("update_cs_data", "scripts/update_cs_data.py")


class _FakeClient:
    """记录被请求的 ts_code, 回放固定 daily/daily_basic。"""

    def __init__(self, *, list_date="20210101", rows=3, empty=False):
        self.list_date = list_date
        self.rows = rows
        self.empty = empty
        self.daily_calls: list[tuple[str, str, str]] = []
        self.basic_exchange: list[str | None] = []

    def fetch_stock_basic(self, exchange=None, **_):
        self.basic_exchange.append(exchange)
        return pd.DataFrame([
            {"ts_code": "688322.SH", "name": "奥比中光", "list_date": self.list_date},
            {"ts_code": "300750.SZ", "name": "宁德时代", "list_date": self.list_date},
            {"ts_code": "920819.BJ", "name": "颖泰生物", "list_date": self.list_date},
        ])

    def fetch_daily(self, ts_code, start, end):
        self.daily_calls.append((ts_code, start, end))
        if self.empty:
            return pd.DataFrame()
        dates = ["20210104", "20210105", "20210106"][: self.rows]
        return pd.DataFrame({
            "ts_code": ts_code, "trade_date": dates,
            "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05,
            "pre_close": 1.0, "change": 0.05, "pct_chg": 5.0,
            "vol": 100.0, "amount": 105.0,
        })

    def fetch_daily_basic(self, ts_code, start, end):
        if self.empty:
            return pd.DataFrame()
        dates = ["20210104", "20210105", "20210106"][: self.rows]
        return pd.DataFrame({
            "trade_date": dates, "turnover_rate": 1.0, "volume_ratio": 1.0,
            "pe": 10.0, "pb": 2.0, "total_mv": 1e6,
        })


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(uc, "_KSS_STATE", tmp_path)
    return tmp_path


def test_existing_csv_is_noop(state):
    (state / "cs_data_688322.csv").write_text("ts_code,trade_date\n688322.SH,2021-01-04\n")
    client = _FakeClient()
    n, status = uc.ensure_history("688322", "SH", client=client)
    assert (n, status) == (0, "exists")
    assert client.daily_calls == []  # 未联网


def test_new_symbol_backfills_and_writes(state):
    client = _FakeClient(rows=3)
    n, status = uc.ensure_history("688322", "SH", client=client)
    assert status == "written" and n == 3
    out = state / "cs_data_688322.csv"
    assert out.exists()
    df = pd.read_csv(out)
    assert list(df.columns) == uc.EXPECTED_COLS
    assert len(df) == 3
    # 无半截 .tmp 残留
    assert not (state / "cs_data_688322.csv.tmp").exists()


def test_suffix_threads_into_ts_code(state):
    for code, exch, expect in [("688322", "SH", "688322.SH"),
                               ("300750", "SZ", "300750.SZ"),
                               ("920819", "BJ", "920819.BJ")]:
        client = _FakeClient()
        uc.ensure_history(code, exch, client=client)
        assert client.daily_calls[0][0] == expect


def test_bj_exchange_maps_to_bse(state):
    client = _FakeClient()
    uc.ensure_history("920819", "BJ", client=client)
    assert client.basic_exchange == ["BSE"]


def test_empty_fetch_writes_nothing(state):
    client = _FakeClient(empty=True)
    n, status = uc.ensure_history("688322", "SH", client=client)
    assert (n, status) == (0, "empty")
    assert not (state / "cs_data_688322.csv").exists()


def test_illegal_exchange_raises(state):
    with pytest.raises(ValueError):
        uc.ensure_history("688322", "XX", client=_FakeClient())


def test_update_one_recovers_exchange_from_ts_code_column(state):
    # finding 3: ensure_history 回填的 BJ/SZ 股, update_one 增量须从 ts_code 列恢复后缀,
    # 不能从文件名推成 .SH。
    import pandas as pd
    csv = state / "cs_data_920819.csv"
    pd.DataFrame({
        "ts_code": ["920819.BJ"], "trade_date": ["2021-01-04"],
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05],
        "pre_close": [1.0], "change": [0.05], "pct_chg": [5.0],
        "vol": [100.0], "amount": [105.0], "turnover_rate": [1.0],
        "volume_ratio": [1.0], "pe": [10.0], "pb": [2.0], "total_mv": [1e6],
    }).to_csv(csv, index=False)
    client = _FakeClient()
    uc.update_one(csv, client, end_date="20210106")
    assert client.daily_calls[0][0] == "920819.BJ"  # 非 920819.SH


def test_update_one_fallback_when_ts_code_malformed(state):
    import pandas as pd
    csv = state / "cs_data_300750.csv"
    # ts_code 列存在但值无后缀(畸形) → 走文件名前缀推断兜底
    cols = {c: [1.0] for c in uc.EXPECTED_COLS}
    cols["ts_code"] = ["300750"]  # 缺 .SZ
    cols["trade_date"] = ["2021-01-04"]
    pd.DataFrame(cols).to_csv(csv, index=False)
    client = _FakeClient()
    uc.update_one(csv, client, end_date="20210106")
    assert client.daily_calls[0][0] == "300750.SZ"  # 文件名推断兜底 300→SZ


def test_list_date_falls_back_when_basic_fails(state):
    class _NoBasic(_FakeClient):
        def fetch_stock_basic(self, exchange=None, **_):
            raise RuntimeError("down")

    client = _NoBasic()
    uc.ensure_history("688322", "SH", client=client)
    # 回退固定早期日 20180101 进 fetch_daily start
    assert client.daily_calls[0][1] == "20180101"
