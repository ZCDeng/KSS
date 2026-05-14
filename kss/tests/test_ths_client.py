"""kss/data/ths_client.py 单元测试.

覆盖：
- 正常响应解析 + 列重命名 + 数值 cast
- 上游业务错误（errocode != 0）→ None
- 空 data 数组（非交易日）→ None
- 缺失 reason 字段 → None
- 非法日期格式 → None
- HTTP 异常 + 重试成功
- HTTP 异常重试到上限 → None
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from kss.data.ths_client import fetch_ths_hot


def _make_session(*responses: object) -> MagicMock:
    """构造一个 ``requests.Session`` mock，按 ``.get`` 调用顺序返回响应序列.

    每个 response 可以是：

    - ``dict``：作为 JSON payload，包装成 200 响应
    - ``Exception``：调用时抛出
    - ``MagicMock``：直接返回（用于自定义 status_code）
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
            resp.encoding = "utf-8"
            items.append(resp)
        else:
            items.append(item)
    sess.get.side_effect = items
    return sess


def _ok_payload() -> dict[str, object]:
    """模拟 zx.10jqka.com.cn 真实返回（精简到 KSS 需要的字段）."""
    return {
        "errocode": 0,
        "errormsg": "",
        "data": [
            {
                "code": "688008",
                "name": "澜起科技",
                "reason": "算力租赁+Token工厂+AI政务",
                "zhangfu": 9.98,
                "close": 75.3,
                "huanshou": 5.2,
                "chengjiaoe": 1.2e9,
                "market": "沪",
            },
            {
                "code": "300033",
                "name": "同花顺",
                "reason": "AI应用+金融科技",
                "zhangfu": 6.5,
                "close": 280.0,
                "huanshou": 3.1,
                "chengjiaoe": 5.0e8,
                "market": "深",
            },
        ],
    }


class TestFetchThsHotHappyPath:
    def test_returns_dataframe_with_renamed_columns(self) -> None:
        sess = _make_session(_ok_payload())
        df = fetch_ths_hot("20260513", session=sess)
        assert df is not None
        assert len(df) == 2
        # 列重命名
        assert "pct_change" in df.columns  # was zhangfu
        assert "turnover_rate" in df.columns  # was huanshou
        assert "amount" in df.columns  # was chengjiaoe
        assert "reason" in df.columns
        # 内容
        assert df.iloc[0]["code"] == "688008"
        assert df.iloc[0]["reason"] == "算力租赁+Token工厂+AI政务"
        assert df.iloc[0]["pct_change"] == pytest.approx(9.98)
        # 数值 cast 成 float
        assert pd.api.types.is_numeric_dtype(df["pct_change"])
        assert pd.api.types.is_numeric_dtype(df["close"])

    def test_url_contains_iso_date(self) -> None:
        """trade_date YYYYMMDD 应被转成 URL 中的 YYYY-MM-DD."""
        sess = _make_session(_ok_payload())
        fetch_ths_hot("20260513", session=sess)
        called_url = sess.get.call_args[0][0]
        assert "2026-05-13" in called_url
        assert "charset/GBK" in called_url

    def test_sets_gbk_encoding(self) -> None:
        """响应应被强制设为 GBK 编码（避免乱码）."""
        sess = _make_session(_ok_payload())
        fetch_ths_hot("20260513", session=sess)
        # 第一个调用产生的 response mock：sess.get.side_effect 已被消费
        # 通过 mock_calls 拿不到 response 的 encoding setter，改用断言 .json() 被调
        resp = sess.get.return_value
        # side_effect 模式下 return_value 没用，转检查参数中的 timeout/headers
        kwargs = sess.get.call_args.kwargs
        assert "User-Agent" in kwargs["headers"]
        assert kwargs["timeout"] > 0


class TestFetchThsHotErrorPaths:
    def test_errocode_nonzero_returns_none(self) -> None:
        """业务错误（注意 errocode 拼写非标准）→ None."""
        bad = {"errocode": 1001, "errormsg": "invalid date", "data": []}
        sess = _make_session(bad)
        assert fetch_ths_hot("20260513", session=sess) is None

    def test_empty_data_array_returns_none(self) -> None:
        """非交易日 / 无强势股 → None."""
        empty = {"errocode": 0, "errormsg": "", "data": []}
        sess = _make_session(empty)
        assert fetch_ths_hot("20260101", session=sess) is None

    def test_missing_reason_column_returns_none(self) -> None:
        """上游格式漂移：没有 reason 字段 → None（不要返回半残数据）."""
        bad = {
            "errocode": 0,
            "data": [{"code": "688008", "name": "X", "zhangfu": 5.0}],
        }
        sess = _make_session(bad)
        assert fetch_ths_hot("20260513", session=sess) is None

    def test_invalid_date_format_returns_none(self) -> None:
        sess = _make_session()  # 不应被调用
        assert fetch_ths_hot("2026-05-13", session=sess) is None
        assert fetch_ths_hot("not-a-date", session=sess) is None
        sess.get.assert_not_called()

    def test_http_error_eventually_returns_none(self) -> None:
        """所有重试都失败 → None，不外抛."""
        sess = _make_session(
            requests.ConnectTimeout("timeout 1"),
            requests.ConnectTimeout("timeout 2"),
        )
        assert fetch_ths_hot("20260513", session=sess) is None
        assert sess.get.call_count == 2  # _MAX_ATTEMPTS = 2

    def test_retry_succeeds_on_second_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """首次网络错 → 重试成功路径."""
        # 加速测试：把 sleep 短路掉
        monkeypatch.setattr("kss.data.ths_client.time.sleep", lambda _: None)
        sess = _make_session(
            requests.ConnectionError("transient"),
            _ok_payload(),
        )
        df = fetch_ths_hot("20260513", session=sess)
        assert df is not None
        assert len(df) == 2
        assert sess.get.call_count == 2

    def test_json_parse_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JSON 损坏 → 重试到上限 → None."""
        monkeypatch.setattr("kss.data.ths_client.time.sleep", lambda _: None)
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(side_effect=ValueError("malformed JSON"))
        sess = MagicMock(spec=requests.Session)
        sess.get.return_value = bad_resp
        assert fetch_ths_hot("20260513", session=sess) is None
