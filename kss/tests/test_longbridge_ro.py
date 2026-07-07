"""U9 单测：CLI 只读代理硬闸（R10 / KTD3 / KTD7）.

约束：proxy 是唯一入口，白名单硬编码，交易子命令直接 exit(非零)，symbol 校验防注入，
entitlement 门拒 trade scope token。
"""

from __future__ import annotations

import pytest
from scripts.longbridge_ro import _accept, _check_entitlement


# --------------------------------------------------------------------------- #
# 子命令 allowlist
# --------------------------------------------------------------------------- #


def test_accept_allowed_read_commands():
    assert _accept("quote", ["688008.SH"]) is True
    assert _accept("kline", ["688008.SH"]) is True
    assert _accept("static-info", ["600519.SH"]) is True


def test_reject_trade_commands():
    for cmd in ("buy", "sell", "cancel", "replace", "order", "trade", "submit"):
        assert _accept(cmd, ["688008.SH"]) is False, f"trade cmd '{cmd}' should reject"


def test_reject_unknown_commands():
    assert _accept("positions", ["688008.SH"]) is False
    assert _accept("account", []) is False


# --------------------------------------------------------------------------- #
# symbol 校验（防注入 / 命令拼接）
# --------------------------------------------------------------------------- #


def test_accept_valid_symbols():
    assert _accept("quote", ["688008.SH"]) is True
    assert _accept("quote", ["300750.SZ"]) is True
    assert _accept("quote", ["830799.BJ"]) is True


def test_reject_shell_metachar_in_args():
    bad = [
        "688008.SH; rm -rf /",
        "688008.SH|cat /etc/passwd",
        "$(whoami)",
        "`id`",
        "688008.SH'",
        '688008.SH"',
        "688008.SH<",
        "688008.SH>",
    ]
    for s in bad:
        assert _accept("quote", [s]) is False, f"should reject: {s!r}"


def test_reject_invalid_symbol_format():
    assert _accept("quote", ["bad"]) is False
    assert _accept("quote", ["688008"]) is False  # 缺后缀
    assert _accept("quote", ["688008.XX"]) is False  # 后缀非法


# --------------------------------------------------------------------------- #
# 凭据 entitlement 门（KTD7）
# --------------------------------------------------------------------------- #


def test_entitlement_no_token():
    ok, reason = _check_entitlement()
    # 无 env token → blocked
    assert ok is False
    assert reason == "no_token"


def test_entitlement_paper_token_ok(monkeypatch):
    # 构造 paper trading JWT payload
    import base64, json

    payload = {"ac": "lb_papertrading", "mid": 12345}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    token = f"header.{encoded}.sig"
    monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", token)
    ok, reason = _check_entitlement()
    assert ok is True
    assert "lb_papertrading" in reason


def test_entitlement_live_token_blocked(monkeypatch):
    import base64, json

    payload = {"ac": "lb_live_trading", "mid": 12345}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    token = f"header.{encoded}.sig"
    monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", token)
    ok, reason = _check_entitlement()
    assert ok is False
    assert "lb_live_trading" in reason
