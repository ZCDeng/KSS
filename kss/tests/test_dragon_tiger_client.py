"""kss/data/dragon_tiger_client.py 单元测试.

覆盖（不打真网，全 mock session）：
- 正常响应解析 + 列重命名 + 数值 cast + 按净买入降序
- 非交易日空 result（result=null）→ None
- 必需列缺失（无 EXPLANATION/reason）→ None
- 非法日期格式 → None（不发请求）
- HTTP 异常重试到上限 → None
- 首次网络错 → 重试成功
- 请求参数携带 ISO 日期 filter + headers
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from kss.data.dragon_tiger_client import fetch_dragon_tiger


def _make_session(*responses: object) -> MagicMock:
    """构造 ``requests.Session`` mock，按 ``.get`` 调用顺序返回响应序列.

    每个 response 可以是：

    - ``dict``：作为 JSON payload，包装成 200 响应
    - ``Exception``：调用时抛出
    - ``MagicMock``：直接返回（用于自定义行为）
    """
    sess = MagicMock(spec=requests.Session)
    items: list[object] = []
    for item in responses:
        if isinstance(item, Exception):
            items.append(item)
        elif isinstance(item, dict):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=item)
            items.append(resp)
        else:
            items.append(item)
    sess.get.side_effect = items
    return sess


def _ok_payload() -> dict[str, object]:
    """模拟东财 RPT_DAILYBILLBOARD_DETAILS 真实返回（精简到 KSS 需要的字段）."""
    return {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "300001",
                    "SECURITY_NAME_ABBR": "特锐德",
                    "BILLBOARD_NET_AMT": "1.0e8",
                    "EXPLANATION": "日涨幅偏离值达7%的证券",
                    "TURNOVERRATE": "12.3",
                    "ACCUM_AMOUNT": "8.0e8",
                },
                {
                    "SECURITY_CODE": "688002",
                    "SECURITY_NAME_ABBR": "睿创微纳",
                    "BILLBOARD_NET_AMT": "3.0e8",
                    "EXPLANATION": "换手率达20%的证券",
                    "TURNOVERRATE": "21.0",
                    "ACCUM_AMOUNT": "9.0e8",
                },
            ]
        },
    }


class TestFetchDragonTigerHappyPath:
    def test_returns_dataframe_with_renamed_columns(self) -> None:
        sess = _make_session(_ok_payload())
        df = fetch_dragon_tiger("20260612", session=sess)
        assert df is not None
        assert len(df) == 2
        # 列重命名
        assert "net_amount" in df.columns  # was BILLBOARD_NET_AMT
        assert "reason" in df.columns  # was EXPLANATION
        assert "turnover_rate" in df.columns  # was TURNOVERRATE
        assert "amount" in df.columns  # was ACCUM_AMOUNT
        # 数值 cast 成 float
        assert pd.api.types.is_numeric_dtype(df["net_amount"])
        assert pd.api.types.is_numeric_dtype(df["turnover_rate"])

    def test_sorted_desc_by_net_amount(self) -> None:
        """按净买入额降序排列（席位抢筹力度优先）."""
        sess = _make_session(_ok_payload())
        df = fetch_dragon_tiger("20260612", session=sess)
        assert df is not None
        assert list(df["name"]) == ["睿创微纳", "特锐德"]
        assert df.iloc[0]["net_amount"] == pytest.approx(3.0e8)
        assert df.iloc[0]["reason"] == "换手率达20%的证券"

    def test_request_carries_iso_date_and_headers(self) -> None:
        """trade_date YYYYMMDD 应转成 filter 里的 YYYY-MM-DD；带 UA/timeout."""
        sess = _make_session(_ok_payload())
        fetch_dragon_tiger("20260612", session=sess)
        kwargs = sess.get.call_args.kwargs
        assert "2026-06-12" in kwargs["params"]["filter"]
        assert kwargs["params"]["reportName"] == "RPT_DAILYBILLBOARD_DETAILS"
        assert "User-Agent" in kwargs["headers"]
        assert kwargs["timeout"] > 0


class TestFetchDragonTigerErrorPaths:
    def test_null_result_returns_none(self) -> None:
        """非交易日：success=true 但 result=null → None."""
        sess = _make_session({"success": True, "result": None})
        assert fetch_dragon_tiger("20260613", session=sess) is None

    def test_empty_data_array_returns_none(self) -> None:
        sess = _make_session({"success": True, "result": {"data": []}})
        assert fetch_dragon_tiger("20260101", session=sess) is None

    def test_missing_required_column_returns_none(self) -> None:
        """上游格式漂移：无 EXPLANATION(reason) → None（不返回半残数据）."""
        bad = {
            "result": {
                "data": [
                    {
                        "SECURITY_CODE": "300001",
                        "SECURITY_NAME_ABBR": "特锐德",
                        "BILLBOARD_NET_AMT": "1.2e8",
                    }
                ]
            }
        }
        sess = _make_session(bad)
        assert fetch_dragon_tiger("20260612", session=sess) is None

    def test_invalid_date_format_returns_none(self) -> None:
        sess = _make_session()  # 不应被调用
        assert fetch_dragon_tiger("2026-06-12", session=sess) is None
        assert fetch_dragon_tiger("not-a-date", session=sess) is None
        sess.get.assert_not_called()

    def test_non_dict_response_returns_none(self) -> None:
        """响应 JSON 不是对象（如返回裸列表）→ None."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=["x"])
        sess = MagicMock(spec=requests.Session)
        sess.get.return_value = resp
        assert fetch_dragon_tiger("20260612", session=sess) is None

    def test_http_error_eventually_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kss.data.dragon_tiger_client.time.sleep", lambda _: None
        )
        sess = _make_session(
            requests.ConnectTimeout("timeout 1"),
            requests.ConnectTimeout("timeout 2"),
        )
        assert fetch_dragon_tiger("20260612", session=sess) is None
        assert sess.get.call_count == 2  # _MAX_ATTEMPTS = 2

    def test_retry_succeeds_on_second_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kss.data.dragon_tiger_client.time.sleep", lambda _: None
        )
        sess = _make_session(
            requests.ConnectionError("transient"),
            _ok_payload(),
        )
        df = fetch_dragon_tiger("20260612", session=sess)
        assert df is not None
        assert len(df) == 2
        assert sess.get.call_count == 2

    def test_json_parse_error_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kss.data.dragon_tiger_client.time.sleep", lambda _: None
        )
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(side_effect=ValueError("malformed JSON"))
        sess = MagicMock(spec=requests.Session)
        sess.get.return_value = bad_resp
        assert fetch_dragon_tiger("20260612", session=sess) is None
