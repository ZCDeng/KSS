from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat_loop  # noqa: E402
from kss.agent import KSSAgentService, LiveMarketContextService  # noqa: E402


def _fake_read(command: str, args: list[str]):
    if command == "longbridge-quotes":
        return {
            "quotes": [
                {
                    "symbol": "688008.SH",
                    "last_done": 253.2,
                    "prev_close": 250.0,
                    "source_asof_ts": "2026-07-27T10:31:00+08:00",
                    "eligibility": "forward_observed",
                    "routed_provider": "longbridge",
                    "manifest_stale": False,
                },
                {
                    "symbol": "830799.BJ",
                    "error": "no_realtime_snapshot",
                    "eligibility": "forward_observed",
                    "routed_provider": "eastmoney_akshare",
                    "manifest_stale": False,
                },
            ],
            "count": 2,
        }
    if command == "intraday-snapshot":
        symbol = args[0]
        if symbol == "688008.SH":
            return {
                "symbol": symbol,
                "bar": {"close": 253.0, "volume": 1000},
                "source_asof_ts": "2026-07-27T10:31:00+08:00",
                "eligibility": "forward_observed",
                "routed_provider": "longbridge",
                "manifest_stale": False,
            }
        return {
            "symbol": symbol,
            "error": "unreachable",
            "eligibility": "forward_observed",
            "routed_provider": "eastmoney_akshare",
            "manifest_stale": False,
        }
    raise AssertionError(f"unexpected command {command}")


def test_live_market_context_reuses_read_bridge_and_keeps_forward_observed_policy():
    service = LiveMarketContextService(_fake_read)

    payload = service.get_context(symbols="688008.SH,830799.BJ", intent="explain")

    assert payload["kind"] == "market_live_context"
    assert payload["snapshot_id"].startswith("lmc-")
    assert payload["eligibility"] == "forward_observed"
    assert payload["policy"]["read_only"] is True
    assert payload["policy"]["pit_backtest_eligible"] is False
    assert payload["policy"]["trade_execution_allowed"] is False
    assert [row["symbol"] for row in payload["rows"]] == ["688008.SH", "830799.BJ"]
    assert payload["rows"][0]["quote"]["last_done"] == 253.2
    assert payload["rows"][0]["intraday_snapshot"]["bar"]["close"] == 253.0
    assert payload["rows"][1]["routed_provider"] == "eastmoney_akshare"
    assert "partial_live_context" in payload["warnings"]


def test_live_market_context_blocks_trade_intent_before_reading():
    calls = []

    def fail_read(command: str, args: list[str]):
        calls.append((command, args))
        raise AssertionError("trade-intent guard should preflight before reads")

    payload = LiveMarketContextService(fail_read).get_context(
        symbols="688008.SH",
        intent="buy now",
    )

    assert payload["error"] == "trade_intent_not_allowed"
    assert payload["is_error"] is True
    assert calls == []


def test_bridge_registers_market_live_context_as_read_only(monkeypatch):
    monkeypatch.setattr(bridge, "_market_live_context", lambda *args: {"ok": True})
    assert "market-live-context" in bridge.COMMANDS
    assert "market-live-context" not in bridge.WRITE_COMMANDS
    assert "get_market_live_context" in {
        spec["name"] for spec in chat_loop.TOOL_SPECS
    }
    out = bridge._make_read_only_call(bridge.dispatch)(
        "market-live-context",
        ["688008.SH", "explain"],
    )
    assert out == {"ok": True}


def test_agent_turn_preloads_live_context_and_persists_event(monkeypatch, tmp_path):
    async def scenario():
        service = KSSAgentService(tmp_path, tmp_path)
        monkeypatch.setattr(bridge, "dispatch", _fake_read)
        captured = {}

        async def fake_run_turn(messages, emit, request_write, **kwargs):
            effective = kwargs["transform_context"](messages)
            captured["context"] = json.dumps(effective, ensure_ascii=False)
            await emit({"type": "chunk", "text": "答复"})
            await emit({"type": "done", "reason": "stop"})
            return chat_loop.TurnTranscript(
                messages=[*effective, {"role": "assistant", "content": "答复"}],
                run_state={"status": "done", "reason": "stop"},
            )

        monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)
        events = []

        async def no_write(**kwargs):
            raise AssertionError("live context preflight is read-only")

        result = await service.run_turn(
            "live-context",
            "client-1",
            "688008 今天为什么动",
            events.append,
            no_write,
        live_context_scope={
            "scope": "watchlist",
            "symbols": ["688008.SH"],
            "intent": "explain",
            "reason": "visible_watchlist",
            },
        )

        assert result.status == "completed"
        live_event = next(event for event in events if event.type == "live_context")
        assert live_event.payload["live_context"]["rows"][0]["symbol"] == "688008.SH"
        assert live_event.payload["live_context"]["scope"]["scope"] == "watchlist"
        assert "实时盘面上下文" in captured["context"]
        entries = service.sessions._read_entries("live-context")
        persisted = next(entry for entry in entries if entry["type"] == "live_context")
        assert persisted["payload"]["items"][0]["policy"]["source_precedence"] == "kss_tool_truth"
        messages = service.sessions.read_messages("live-context")
        tool_results = [
            call.result
            for message in messages
            for call in message.tool_calls
            if message.role == "tool"
        ]
        assert tool_results == []

    asyncio.run(scenario())
