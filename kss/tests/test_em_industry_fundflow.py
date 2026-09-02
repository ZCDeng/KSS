"""kss/data/em_industry_fundflow.py 单元测试（全 mock，不打真网）.

覆盖：
- 当日走 push2delay，映射 f14/f3/f184/f66/f69 → Tushare 行业列
- 隔日走 datacenter RPT_INDUSTRY_FUNDFLOW
- 当日 push2delay 失败 → 降到 datacenter
- 翻页合并
- 非法日期 / 空响应 / 必需列缺失 → None
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from kss.data.em_industry_fundflow import (
    SOURCE_DATACENTER,
    SOURCE_PUSH2DELAY,
    _CLIST_URL,
    _DC_URL,
    fetch_industry_fundflow_em,
)


def _resp(payload: object) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=payload)
    return r


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


def _clist_row(
    code: str = "BK0420",
    name: str = "航空机场",
    *,
    pct: float = 0.77,
    net: float = 87096805.0,
    net_rate: float = 3.19,
    elg: float = 68179470.0,
    elg_rate: float = 2.5,
    close: float = 4018.02,
) -> dict:
    return {
        "f12": code,
        "f14": name,
        "f2": close,
        "f3": pct,
        "f62": net,
        "f184": net_rate,
        "f66": elg,
        "f69": elg_rate,
    }


def _clist_payload(rows: list[dict], *, total: int | None = None) -> dict:
    return {"data": {"total": total if total is not None else len(rows), "diff": rows}}


def _dc_row(
    code: str = "BK0420",
    name: str = "航空机场",
    *,
    pct: float = 0.77,
    net: float = 87096805.0,
    net_rate: float = 3.189,
    elg: float = 68179470.0,
    elg_rate: float = 2.496,
) -> dict:
    return {
        "BOARD_CODE": code,
        "BOARD_NAME": name,
        "CHANGE_RATE": pct,
        "NET_INFLOW": net,
        "NET_INFLOW_RATIO": net_rate,
        "SUPERDEAL_NET": elg,
        "SUPERDEAL_NET_RATIO": elg_rate,
        "TRADE_DATE": "2026-09-01 00:00:00",
    }


def _dc_payload(rows: list[dict], *, pages: int = 1) -> dict:
    return {
        "success": True,
        "result": {"data": rows, "pages": pages, "count": len(rows)},
    }


class TestSameDayPush2delay:
    def test_maps_clist_fields_to_tushare_schema(self) -> None:
        sess = _make_session(_clist_payload([_clist_row()]))
        df = fetch_industry_fundflow_em(
            "20260901", session=sess, now_ymd="20260901"
        )
        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["name"] == "航空机场"
        assert df.iloc[0]["ts_code"] == "BK0420.DC"
        assert df.iloc[0]["content_type"] == "行业"
        assert df.iloc[0]["trade_date"] == "20260901"
        assert df.iloc[0]["em_source"] == SOURCE_PUSH2DELAY
        assert df.iloc[0]["pct_change"] == pytest.approx(0.77)
        assert df.iloc[0]["net_amount"] == pytest.approx(87096805.0)
        assert df.iloc[0]["net_amount_rate"] == pytest.approx(3.19)
        assert df.iloc[0]["buy_elg_amount_rate"] == pytest.approx(2.5)
        assert sess.get.call_args.kwargs["params"]["fs"] == "m:90 t:2"
        assert sess.get.call_args.args[0] == _CLIST_URL

    def test_paginates_clist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kss.data.em_industry_fundflow.time.sleep", lambda _: None
        )
        page1 = _clist_payload(
            [_clist_row("BK0001", "甲")], total=101
        )
        page2 = _clist_payload(
            [_clist_row("BK0002", "乙")], total=101
        )
        sess = _make_session(page1, page2)
        df = fetch_industry_fundflow_em(
            "20260901", session=sess, now_ymd="20260901"
        )
        assert df is not None
        assert set(df["name"]) == {"甲", "乙"}
        assert sess.get.call_count == 2


class TestDatedDatacenter:
    def test_past_day_hits_datacenter_not_clist(self) -> None:
        sess = _make_session(_dc_payload([_dc_row()]))
        df = fetch_industry_fundflow_em(
            "20260901", session=sess, now_ymd="20260902"
        )
        assert df is not None
        assert df.iloc[0]["em_source"] == SOURCE_DATACENTER
        assert df.iloc[0]["name"] == "航空机场"
        assert df.iloc[0]["ts_code"] == "BK0420.DC"
        assert df.iloc[0]["pct_change"] == pytest.approx(0.77)
        assert df.iloc[0]["net_amount_rate"] == pytest.approx(3.189)
        assert sess.get.call_args.args[0] == _DC_URL
        assert "2026-09-01" in sess.get.call_args.kwargs["params"]["filter"]
        assert sess.get.call_args.kwargs["params"]["reportName"] == (
            "RPT_INDUSTRY_FUNDFLOW"
        )

    def test_same_day_clist_fail_falls_back_to_datacenter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kss.data.em_industry_fundflow.time.sleep", lambda _: None
        )
        sess = _make_session(
            requests.ConnectionError("push2delay down"),
            requests.ConnectionError("push2delay down"),
            _dc_payload([_dc_row()]),
        )
        df = fetch_industry_fundflow_em(
            "20260901", session=sess, now_ymd="20260901"
        )
        assert df is not None
        assert df.iloc[0]["em_source"] == SOURCE_DATACENTER
        urls = [c.args[0] for c in sess.get.call_args_list]
        assert urls[0] == _CLIST_URL
        assert urls[-1] == _DC_URL


class TestErrorPaths:
    def test_invalid_date_does_not_request(self) -> None:
        sess = _make_session()
        assert fetch_industry_fundflow_em("2026-09-01", session=sess) is None
        sess.get.assert_not_called()

    def test_empty_clist_then_empty_datacenter_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kss.data.em_industry_fundflow.time.sleep", lambda _: None
        )
        sess = _make_session(
            {"data": {"total": 0, "diff": []}},
            {"success": True, "result": {"data": [], "pages": 1}},
        )
        assert (
            fetch_industry_fundflow_em(
                "20260901", session=sess, now_ymd="20260901"
            )
            is None
        )

    def test_datacenter_missing_required_numeric_drops_to_none(self) -> None:
        bad = _dc_row()
        del bad["NET_INFLOW_RATIO"]
        del bad["CHANGE_RATE"]
        sess = _make_session(_dc_payload([bad]))
        # pct_change / net_amount_rate become NA → dropna 清空
        assert (
            fetch_industry_fundflow_em(
                "20260901", session=sess, now_ymd="20260902"
            )
            is None
        )
