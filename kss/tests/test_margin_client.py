"""kss/data/margin_client.py 单元测试（全 mock，不打真网）.

覆盖：
- 单页解析 + 列重命名 + 数值 cast + 按融资余额降序
- 多页翻页合并（pages=2）
- 非交易日首页空 → None
- 必需列缺失 → None
- 非法日期 → None（不发请求）
- HTTP 异常重试到上限 → None
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from kss.data.margin_client import fetch_kcb_margin


def _resp(payload: object) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=payload)
    return r


def _page(rows: list[dict], pages: int = 1) -> dict:
    return {"success": True, "result": {"data": rows, "pages": pages}}


def _make_session(*responses: object) -> MagicMock:
    sess = MagicMock(spec=requests.Session)
    items: list[object] = []
    for item in responses:
        if isinstance(item, Exception):
            items.append(item)
        elif isinstance(item, dict):
            items.append(_resp(item))
        else:
            items.append(item)
    sess.get.side_effect = items
    return sess


def _rows(*specs: tuple[str, str, float, float]) -> list[dict]:
    return [
        {"SCODE": c, "SECNAME": n, "RZYE": bal, "RZJME": net, "KCB": "1"}
        for c, n, bal, net in specs
    ]


class TestFetchKcbMarginHappyPath:
    def test_single_page_renames_and_sorts(self) -> None:
        sess = _make_session(_page(_rows(
            ("688256", "寒武纪", 2.3e10, -1.2e8),
            ("688008", "澜起科技", 1.5e10, 3.8e8),
        ), pages=1))
        df = fetch_kcb_margin("20260612", session=sess)
        assert df is not None
        assert list(df.columns) == ["code", "name", "fin_balance", "fin_net_buy"]
        assert list(df["code"]) == ["688256", "688008"]  # 按融资余额降序
        assert pd.api.types.is_numeric_dtype(df["fin_net_buy"])
        assert sess.get.call_count == 1  # pages=1 不翻页

    def test_paginates_when_multiple_pages(self) -> None:
        """pages=2 → 翻第 2 页合并."""
        sess = _make_session(
            _page(_rows(("688256", "寒武纪", 2.3e10, -1.2e8)), pages=2),
            _page(_rows(("688009", "中国通号", 1.0e9, 5.0e6)), pages=2),
        )
        df = fetch_kcb_margin("20260612", session=sess)
        assert df is not None
        assert len(df) == 2
        assert set(df["code"]) == {"688256", "688009"}
        assert sess.get.call_count == 2

    def test_request_filter_carries_date_and_kcb(self) -> None:
        sess = _make_session(_page(_rows(("688008", "澜起科技", 1.5e10, 3.8e8))))
        fetch_kcb_margin("20260612", session=sess)
        params = sess.get.call_args.kwargs["params"]
        assert "2026-06-12" in params["filter"]
        assert 'KCB="1"' in params["filter"]


class TestFetchKcbMarginErrorPaths:
    def test_non_trading_day_empty_first_page_returns_none(self) -> None:
        sess = _make_session({"success": True, "result": {"data": [], "pages": 0}})
        assert fetch_kcb_margin("20260613", session=sess) is None

    def test_null_result_returns_none(self) -> None:
        sess = _make_session({"success": True, "result": None})
        assert fetch_kcb_margin("20260613", session=sess) is None

    def test_missing_required_column_returns_none(self) -> None:
        bad = _page([{"SCODE": "688008", "SECNAME": "澜起科技"}])  # 无 RZYE/RZJME
        sess = _make_session(bad)
        assert fetch_kcb_margin("20260612", session=sess) is None

    def test_invalid_date_returns_none_without_request(self) -> None:
        sess = _make_session()
        assert fetch_kcb_margin("2026-06-12", session=sess) is None
        sess.get.assert_not_called()

    def test_http_error_eventually_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("kss.data.margin_client.time.sleep", lambda _: None)
        sess = _make_session(
            requests.ConnectTimeout("t1"), requests.ConnectTimeout("t2"),
        )
        assert fetch_kcb_margin("20260612", session=sess) is None
        assert sess.get.call_count == 2  # _MAX_ATTEMPTS

    def test_second_page_failure_keeps_first_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第 2 页挂掉不应丢第 1 页数据（降级而非全失败）."""
        monkeypatch.setattr("kss.data.margin_client.time.sleep", lambda _: None)
        sess = _make_session(
            _page(_rows(("688256", "寒武纪", 2.3e10, -1.2e8)), pages=2),
            requests.ConnectionError("p2 fail 1"),
            requests.ConnectionError("p2 fail 2"),
        )
        df = fetch_kcb_margin("20260612", session=sess)
        assert df is not None
        assert len(df) == 1  # 仅第 1 页
