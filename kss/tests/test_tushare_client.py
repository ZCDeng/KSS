"""U3: TushareClient 机构持仓 + PE 历史取数方法 (mock _pro, 不触网)."""

from __future__ import annotations

import pandas as pd
import pytest

from kss.data import tushare_client as tc


def _bare_client(pro) -> tc.TushareClient:
    """绕过单例 __new__/__init__(免 token), 直接注入假 _pro."""
    c = object.__new__(tc.TushareClient)
    c._pro = pro
    return c


class _FakePro:
    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    def top10_floatholders(self, **kw):
        self.calls.append(kw)
        return self._maybe()

    def daily_basic(self, **kw):
        self.calls.append(kw)
        return self._maybe()

    def _maybe(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_top10_floatholders_happy() -> None:
    df = pd.DataFrame({
        "ts_code": ["688012.SH"], "end_date": ["20260331"],
        "holder_name": ["香港中央结算有限公司"], "hold_ratio": [10.5],
        "hold_change": [1.0e7], "holder_type": ["一般企业"],
    })
    pro = _FakePro(df)
    c = _bare_client(pro)
    out = c.fetch_top10_floatholders("688012.SH", "20240101", "20260630")
    assert out is not None and len(out) == 1
    assert pro.calls[0]["ts_code"] == "688012.SH"
    assert pro.calls[0]["start_date"] == "20240101"


def test_daily_basic_history_happy() -> None:
    df = pd.DataFrame({"trade_date": ["20260630"], "pe_ttm": [162.6]})
    c = _bare_client(_FakePro(df))
    out = c.fetch_daily_basic_history("688012.SH", "20240101", "20260630")
    assert out is not None and out.iloc[0]["pe_ttm"] == 162.6


def test_empty_returns_none() -> None:
    c = _bare_client(_FakePro(pd.DataFrame()))
    assert c.fetch_top10_floatholders("688012.SH", "20240101", "20260630") is None
    assert c.fetch_daily_basic_history("688012.SH", "20240101", "20260630") is None


def test_exception_degrades_to_none(monkeypatch) -> None:
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)  # 不真睡
    c = _bare_client(_FakePro(ConnectionError("boom")))
    assert c.fetch_top10_floatholders("688012.SH", "20240101", "20260630") is None
    assert c.fetch_daily_basic_history("688012.SH", "20240101", "20260630") is None
