"""U1: 上市地只读解析。门控看解析后的后缀，不以公司名或 ADR 黑名单。

跑：.venv-desktop/bin/python -m pytest kss/tests/test_listing_resolve.py kss/tests/test_bridge_orientation.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat  # noqa: E402

from kss.equity_research.listing_resolve import resolve_listing  # noqa: E402
from kss.ui_surface.bind_catalog import _item_match_score  # noqa: E402


def _item(
    code: str,
    name: str,
    *,
    market: str,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    extra_aliases = aliases or []
    return {
        "kind": "equity",
        "market": market,
        "codes": {"code": code, "primary": code},
        "names": [name],
        "aliases": [name, code, *extra_aliases],
        "overnight_kind": "hk" if code.endswith(".HK") else (
            "a_share" if "." in code else "yfinance"
        ),
    }


def _catalog_items() -> list[dict[str, Any]]:
    """复现 catalog 陷阱：精确名「阿里巴巴」打分把 BABA 排在 09988.HK 前面。"""
    return [
        _item("BABA", "阿里巴巴", market="US"),
        _item(
            "09988.HK",
            "阿里巴巴-SW",
            market="HK",
            aliases=["09988", "9988", "9988.HK"],
        ),
        _item("NVDA", "英伟达", market="US"),
        _item(
            "02318.HK",
            "中国平安",
            market="HK",
            aliases=["02318", "2318", "2318.HK"],
        ),
        _item("PDD", "拼多多", market="US"),
    ]


def _name_index() -> dict[str, Any]:
    return {
        "byName": {
            "贵州茅台": "600519.SH",
            "中国平安": "601318.SH",
        },
        "byCode": {
            "600519": "600519.SH",
            "601318": "601318.SH",
        },
        "meta": {
            "600519.SH": {"name": "贵州茅台", "kind": "stock"},
            "601318.SH": {"name": "中国平安", "kind": "stock"},
        },
    }


def _resolve(query: str) -> dict[str, Any]:
    return resolve_listing(
        query,
        catalog_items=_catalog_items(),
        name_index=_name_index(),
    )


def _codes(result: dict[str, Any]) -> set[str]:
    return {str(c["code"]) for c in result.get("candidates") or []}


def test_explicit_sh_code_single_in_scope() -> None:
    """Happy: 600519.SH → 单候选沪市，进入覆盖。"""
    out = _resolve("600519.SH")
    assert out["gate"] == "in_scope"
    assert out["enter_coverage"] is True
    assert out["picker"] is False
    assert len(out["candidates"]) == 1
    hit = out["candidates"][0]
    assert hit["code"] == "600519.SH"
    assert hit["suffix"] == ".SH"
    assert hit["gate"] == "in_scope"
    assert hit["display_name"] == "贵州茅台"


def test_alibaba_name_prefers_hk_not_baba() -> None:
    """Happy: 「阿里巴巴」进港股；BABA 不得当门控结果。KTD2 / AE8。"""
    baba, hk = _catalog_items()[0], _catalog_items()[1]
    assert _item_match_score(baba, "阿里巴巴") > _item_match_score(hk, "阿里巴巴")

    out = _resolve("阿里巴巴")
    assert out["gate"] == "in_scope"
    assert out["enter_coverage"] is True
    codes = _codes(out)
    assert "09988.HK" in codes or "9988.HK" in codes
    assert "BABA" not in codes
    assert all(c["gate"] == "in_scope" for c in out["candidates"])
    assert all(c["suffix"] in {".HK", ".SH", ".SZ", ".BJ"} for c in out["candidates"])


def test_baba_ticker_and_adr_phrase_out_of_scope() -> None:
    """Edge: 研究 BABA ADR / 仅剩美股 → us_or_adr，无 in_scope 赢家。F3 / AE2。"""
    for query in ("BABA", "研究 BABA ADR"):
        out = _resolve(query)
        assert out["gate"] == "us_or_adr", query
        assert out["enter_coverage"] is False
        assert not any(c["gate"] == "in_scope" for c in out["candidates"])
        assert "BABA" in _codes(out)


def test_unresolved_junk_does_not_guess_sz() -> None:
    """Error: 无法解析 → unresolved，不猜测 .SZ。"""
    out = _resolve("zzzznotaticker")
    assert out["gate"] == "unresolved"
    assert out["enter_coverage"] is False
    assert out["candidates"] == []
    dumped = str(out)
    assert ".SZ" not in dumped


def test_bare_six_digit_without_index_does_not_use_get_stock_heuristic() -> None:
    """无后缀 6 位且 name-index 未命中时，不得走 688→SH / 否则 SZ。"""
    out = resolve_listing(
        "688008",
        catalog_items=[],
        name_index={},
    )
    assert out["gate"] == "unresolved"
    assert out["candidates"] == []
    assert "688008.SH" not in str(out)
    assert "688008.SZ" not in str(out)


def test_bare_hk_digits_not_sz_heuristic() -> None:
    """4–5 位港股数字不得被当成 .SZ。"""
    out = _resolve("9988")
    assert out["gate"] == "in_scope"
    assert "09988.HK" in _codes(out)
    assert not any(c["code"].endswith(".SZ") for c in out["candidates"])


def test_ah_dual_returns_both_no_picker() -> None:
    """Dual: A/H 两地都 in_scope，不弹选择器。AE3。"""
    out = _resolve("中国平安")
    assert out["gate"] == "in_scope"
    assert out["picker"] is False
    codes = _codes(out)
    assert "601318.SH" in codes
    assert "02318.HK" in codes
    assert all(c["gate"] == "in_scope" for c in out["candidates"])


def test_tool_and_command_triple_registered_readonly() -> None:
    """Integration: resolve_listing / listing-resolve 三处登记且只读。"""
    names = {s["name"] for s in chat.TOOL_SPECS}
    assert "resolve_listing" in names
    spec = next(s for s in chat.TOOL_SPECS if s["name"] == "resolve_listing")
    assert spec["command"] == "listing-resolve"
    assert "listing-resolve" in bridge.COMMANDS
    assert "listing-resolve" not in bridge.WRITE_COMMANDS
    assert chat.is_write_command("listing-resolve") is False
    # 既有 WRITE resolve 不得被做成 chat 工具
    assert not any(s["name"] == "resolve_stocks" for s in chat.TOOL_SPECS)
    assert not any(s["command"] == "resolve" for s in chat.TOOL_SPECS)
    cmd, pos = chat.resolve_tool("resolve_listing", {"query": "阿里巴巴"})
    assert cmd == "listing-resolve"
    assert pos == ["阿里巴巴"]
    schema_names = {item["function"]["name"] for item in chat.build_tools_schema()}
    assert "resolve_listing" in schema_names


def test_dispatch_listing_resolve_uses_injected_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispatch 走只读命令；源可注入，不读 live bind_catalog_v1.json。"""
    monkeypatch.setattr(
        "kss.equity_research.listing_resolve._load_catalog_items",
        _catalog_items,
    )
    monkeypatch.setattr(
        "kss.equity_research.listing_resolve._load_name_index",
        _name_index,
    )
    out = bridge.dispatch("listing-resolve", ["阿里巴巴"])
    assert out["gate"] == "in_scope"
    assert "BABA" not in _codes(out)
    assert "09988.HK" in _codes(out) or "9988.HK" in _codes(out)
    # 碰写即 raise 的只读包装不得把本命令当写
    call = bridge._make_read_only_call(bridge.dispatch)
    wrapped = call("listing-resolve", ["600519.SH"])
    assert wrapped["gate"] == "in_scope"
    assert wrapped["candidates"][0]["suffix"] == ".SH"
