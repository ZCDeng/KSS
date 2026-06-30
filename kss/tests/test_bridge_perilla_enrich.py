"""U6: bridge perilla-enrichment 命令分发测试 (mock aggregate, 不触网)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_dispatch_routes_to_enrich(monkeypatch) -> None:
    captured = {}

    def fake_enrich(symbol, **kw):
        captured["symbol"] = symbol
        return {"symbol": symbol, "status": "ok", "name": "中微公司"}

    import kss.perilla_enrich.aggregate as agg
    monkeypatch.setattr(agg, "enrich", fake_enrich)

    out = b.dispatch("perilla-enrichment", ["688012.SH"])
    assert out["status"] == "ok"
    assert captured["symbol"] == "688012.SH"


def test_empty_symbol_degrades() -> None:
    out = b.dispatch("perilla-enrichment", [])
    assert out["status"] == "invalid_symbol"


def test_enrich_exception_degrades(monkeypatch) -> None:
    import kss.perilla_enrich.aggregate as agg
    monkeypatch.setattr(agg, "enrich", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = b.dispatch("perilla-enrichment", ["688012.SH"])
    assert out["status"] == "unavailable"
    assert "boom" in out["reason"]


def test_command_registered_in_commands() -> None:
    # 漂移守卫: dispatch 命令须登记 COMMANDS
    assert "perilla-enrichment" in b.COMMANDS
