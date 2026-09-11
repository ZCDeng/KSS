"""U1: HithinkClient transport tests. Mock HTTP only — no live network in CI."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
import requests

from kss.data.hithink_client import (
    FUYAO_HOST,
    HithinkClient,
    join_thscodes,
    resolve_api_key,
    ymd_to_date_ms,
    ymd_to_iso,
)

_FAKE_KEY = "sk-fuyao-FAKESECRET-not-a-real-key"


class _FakeResp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self) -> Any:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str] | None, Any]] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected extra GET")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(data: Any) -> _FakeResp:
    return _FakeResp(200, {"code": 0, "message": "ok", "request_id": "r1", "data": data})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kss.data.hithink_client.time.sleep", lambda _s: None)


def test_missing_key_returns_none_without_http(caplog: pytest.LogCaptureFixture) -> None:
    sess = _FakeSession([])
    client = HithinkClient(session=sess, api_key="")
    with caplog.at_level("WARNING"):
        assert client.get("/api/meta/tickers/search") is None
    assert sess.calls == []
    assert "missing HITHINK_FINANCE_API_KEY" in caplog.text


def test_http_200_code_0_returns_data() -> None:
    payload = {"item": [{"thscode": "600519.SH"}]}
    sess = _FakeSession([_ok(payload)])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/a-share/prices/snapshot", {"thscodes": "600519.SH"}) == payload
    assert len(sess.calls) == 1
    assert sess.calls[0]["headers"]["X-api-key"] == _FAKE_KEY


def test_code_1002_sets_last_error_no_retry() -> None:
    sess = _FakeSession([
        _FakeResp(200, {"code": 1002, "message": "Unknown thscode `830799.BJ`", "data": None}),
        _ok({"should": "not-run"}),
    ])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/a-share/auction/snapshot") is None
    assert len(sess.calls) == 1
    assert client.last_error is not None
    assert client.last_error["code"] == 1002
    assert "830799.BJ" in str(client.last_error["message"])
    assert _FAKE_KEY not in str(client.last_error)
    sess = _FakeSession([
        _FakeResp(200, {"code": 2003, "message": "invalid key", "data": None}),
        _ok({"should": "not-run"}),
    ])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/meta/tickers/search") is None
    assert len(sess.calls) == 1


def test_code_4001_retries_then_success() -> None:
    limited = _FakeResp(200, {"code": 4001, "message": "rate limited", "data": None})
    sess = _FakeSession([limited, limited, _ok({"item": []})])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/a-share/special-data/limit-up-pool") == {"item": []}
    assert len(sess.calls) == 3


def test_code_0_null_data_is_none() -> None:
    sess = _FakeSession([_FakeResp(200, {"code": 0, "message": "ok", "data": None})])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/a-share/prices/snapshot") is None


def test_exception_log_redacts_key(caplog: pytest.LogCaptureFixture) -> None:
    boom = requests.RequestException(f"proxy failed X-api-key: {_FAKE_KEY}")
    sess = _FakeSession([boom, boom, boom])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    with caplog.at_level("INFO"):
        assert client.get("/api/meta/tickers/search") is None
    blob = caplog.text
    assert _FAKE_KEY not in blob
    assert "X-api-key" not in blob or _FAKE_KEY not in blob


def test_bypass_proxy_appends_fuyao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "api.tushare.pro")
    monkeypatch.delenv("no_proxy", raising=False)
    sess = _FakeSession([_ok({"ok": True})])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get("/api/meta/tickers/search") == {"ok": True}
    assert FUYAO_HOST in os.environ["NO_PROXY"]
    assert FUYAO_HOST in os.environ["no_proxy"]


def test_resolve_api_key_env_first(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "hithink_finance_api_key").write_text("from-file", encoding="utf-8")
    monkeypatch.setattr("kss.data.hithink_client.STATE_ROOT", tmp_path)
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "from-env")
    assert resolve_api_key() == "from-env"
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY")
    assert resolve_api_key() == "from-file"


def test_join_thscodes_normalizes_and_caps() -> None:
    joined = join_thscodes(["600519", "600519.SH", "300750.SZ"], limit=2)
    assert joined == "600519.SH,300750.SZ"


def test_ymd_helpers() -> None:
    assert ymd_to_iso("20260912") == "2026-09-12"
    ms = ymd_to_date_ms("20260912")
    # 2026-09-12 00:00 Asia/Shanghai
    assert ms == 1789142400000


def test_wrappers_skip_empty_thscodes() -> None:
    sess = _FakeSession([])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    assert client.get_prices_snapshot([]) is None
    assert client.get_auction_snapshot([], stage="live") is None
    assert sess.calls == []


def test_get_prices_snapshot_wrapper() -> None:
    sess = _FakeSession([_ok({"item": [{"thscode": "600519.SH", "last_price": 1400.0}]})])
    client = HithinkClient(session=sess, api_key=_FAKE_KEY)
    data = client.get_prices_snapshot(["600519.SH"])
    assert data["item"][0]["last_price"] == 1400.0
    assert "thscodes" in sess.calls[0]["params"]
