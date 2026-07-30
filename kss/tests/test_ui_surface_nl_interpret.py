"""U1: 档 A 确定性 NL 解析核。

验收黄金句（S1/S2 相关）：
- 「加上苹果」→ overnight_append AAPL
- 「苹果和英伟达」→ 两项 append draft
- 「去掉苹果」且 append 含 AAPL → remove
- 「去掉纳斯达克」且为默认 → error 不可删默认
- 「改成封板率」→ set_strip_metric limit_seal_rate
- 北向类 → ok=false
- mock probe 一成一败 → partial
"""

from __future__ import annotations

from typing import Any

from kss.ui_surface.aliases import lookup_metric, lookup_symbol
from kss.ui_surface.nl_interpret import interpret, interpret_overnight, interpret_strip_metric


def _probe_ok(code: str, kind: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "code": code.upper(),
        "name": code.upper(),
        "kind": kind or "yfinance",
        "close": 100.0,
        "pct": 1.5,
        "date": "2026-07-28",
        "source": "mock",
    }


def _probe_fail(code: str, kind: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": "no_quote", "code": code}


def test_alias_apple_and_asml() -> None:
    a = lookup_symbol("苹果")
    assert a and a["code"] == "AAPL"
    m = lookup_metric("封板率")
    assert m == "limit_seal_rate"
    assert lookup_metric("最高连板") == "limit_max_board"


def test_append_apple() -> None:
    r = interpret(
        "overnight_us",
        "加上苹果",
        config={"overnight_us": {"append": []}, "strip_metric": {"metric_id": "limit_max_board"}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is True
    assert r["action"] == "overnight_append"
    assert r["ops"][0]["code"] == "AAPL"
    assert r["ops"][0]["added_via"] == "nl"
    assert r["previews"][0]["close"] == 100.0


def test_append_multi_entities() -> None:
    # 英伟达/NVDA 在系统默认名单，不能追加；用阿斯麦+超威（候选表常见非默认）
    r = interpret_overnight(
        "苹果和阿斯麦",
        config={"overnight_us": {"append": []}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is True
    codes = [o["code"] for o in r["ops"]]
    assert codes == ["AAPL", "ASML"]


def test_remove_user_append() -> None:
    cfg = {
        "overnight_us": {
            "append": [{
                "code": "AAPL",
                "name": "苹果",
                "kind": "yfinance",
                "probe_close": 190.0,
            }],
        },
    }
    r = interpret_overnight("去掉苹果", config=cfg, probe_fn=_probe_ok)
    assert r["ok"] is True
    assert r["action"] == "overnight_remove"
    assert r["ops"] == [{"op": "overnight_remove", "code": "AAPL"}]


def test_cannot_remove_default_nasdaq() -> None:
    r = interpret_overnight(
        "去掉纳斯达克",
        config={"overnight_us": {"append": []}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is False
    assert r["error"] == "all_failed"
    assert any(i.get("error") == "cannot_remove_default" for i in r["items"])


def test_cannot_append_default() -> None:
    r = interpret_overnight(
        "加上纳指",
        config={"overnight_us": {"append": []}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is False
    assert any(i.get("error") == "is_default" for i in r["items"])


def test_partial_probe_one_ok_one_fail() -> None:
    def probe(code: str, kind: str | None = None) -> dict[str, Any]:
        if code.upper() == "AAPL":
            return _probe_ok(code, kind)
        return _probe_fail(code, kind)

    r = interpret_overnight(
        "加上苹果和ZZZZ",
        config={"overnight_us": {"append": []}},
        probe_fn=probe,
    )
    # ZZZZ 是合法 CODE_RE 但 probe 失败
    assert r["ok"] is True
    assert r.get("partial") is True
    assert len(r["ops"]) == 1
    assert r["ops"][0]["code"] == "AAPL"
    assert any(i.get("status") != "ok" for i in r["items"])


def test_unknown_symbol_fails_loud() -> None:
    r = interpret_overnight(
        "加上不存在的虚构标的XYZ",
        config={"overnight_us": {"append": []}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is False
    assert r["suggestions"]


def test_clear_mine() -> None:
    cfg = {
        "overnight_us": {
            "append": [{"code": "AAPL", "name": "苹果", "kind": "yfinance"}],
        },
    }
    r = interpret_overnight("清空我的追加", config=cfg, probe_fn=_probe_ok)
    assert r["ok"] is True
    assert r["ops"] == [{"op": "reset_overnight_append"}]


def test_set_metric_seal_rate_needs_slot() -> None:
    strip = {
        "limitBoard": {"maxBoard": 6, "sealRate": 0.55, "total": 61},
    }
    r = interpret_strip_metric("改成封板率", market_strip=strip)
    assert r["ok"] is False
    assert r["error"] == "slot_required"
    assert r["metric_id"] == "limit_seal_rate"


def test_set_metric_seal_rate_with_slot_phrase() -> None:
    strip = {
        "limitBoard": {"maxBoard": 6, "sealRate": 0.55, "total": 61},
    }
    r = interpret_strip_metric("第二张改成封板率", market_strip=strip)
    assert r["ok"] is True
    assert r["metric_id"] == "limit_seal_rate"
    assert r["ops"] == [{
        "op": "set_strip_slot",
        "slot_id": "strip_1",
        "metric_id": "limit_seal_rate",
    }]
    assert r["previews"][0].get("title")


def test_set_metric_a50_with_slot_arg() -> None:
    strip = {
        "overnightUS": [{"code": "XIN9", "name": "A50", "close": 12000.0, "pct": 0.5}],
    }
    r = interpret_strip_metric(
        "改为富时中国A50指数", market_strip=strip, slot_id="strip_2",
    )
    assert r["ok"] is True
    assert r["metric_id"] == "index_a50"
    assert r["ops"][0]["slot_id"] == "strip_2"
    assert r["previews"][0].get("valueText")


def test_metric_without_verb_needs_slot() -> None:
    r = interpret("strip_metric", "最高连板", market_strip={})
    assert r["ok"] is False
    assert r["error"] == "slot_required"


def test_north_allowed_with_slot() -> None:
    r = interpret_strip_metric("小卡显示北向", market_strip={}, slot_id="strip_0")
    assert r["ok"] is True
    assert r["metric_id"] == "north_money"
    assert r["ops"][0]["op"] == "set_strip_slot"


def test_north_five_day() -> None:
    r = interpret("strip_metric", "北向五日均", market_strip={}, slot_id="strip_0")
    assert r["ok"] is False


def test_unknown_metric_fails_loud() -> None:
    r = interpret_strip_metric("换成交额", market_strip={})
    assert r["ok"] is False
    assert r["error"] == "unknown_metric"
    assert "封板率" in (r.get("error_zh") or "")


def test_bad_region() -> None:
    r = interpret("foo", "加上苹果", probe_fn=_probe_ok)
    assert r["ok"] is False
    assert r["error"] == "bad_region"


def test_empty_text() -> None:
    r = interpret("overnight_us", "  ", probe_fn=_probe_ok)
    assert r["ok"] is False
    assert r["error"] == "empty_text"


def test_direct_ticker() -> None:
    # TSLA 是默认项；用非默认 ticker AMD
    r = interpret_overnight(
        "加上 AMD",
        config={"overnight_us": {"append": []}},
        probe_fn=_probe_ok,
    )
    assert r["ok"] is True
    assert r["ops"][0]["code"] == "AMD"
