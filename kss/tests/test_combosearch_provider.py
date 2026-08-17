"""Local comboSearch provider for external research tools."""

from __future__ import annotations

from kss.research import adapter
from kss.research import combosearch_provider as provider


def test_search_maps_cli_results(monkeypatch) -> None:
    monkeypatch.setattr(provider, "is_alive", lambda: True)
    monkeypatch.setattr(
        provider,
        "_run_json",
        lambda *_a, **_k: {
            "ok": True,
            "error": None,
            "degraded": False,
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "政策 A",
                    "snippet": "摘要",
                    "content": "正文",
                    "ts": "2026-08-14T00:00:00+08:00",
                    "rank": 1,
                }
            ],
        },
    )
    out = provider.search("半导体 政策", limit=3)
    assert out["provider"] == "combosearch"
    assert out["error"] is None if "error" in out else True
    assert out["results"][0]["url"] == "https://example.com/a"
    assert out["results"][0]["title"] == "政策 A"
    assert out["partial"] is False


def test_search_unavailable_when_cli_missing(monkeypatch) -> None:
    monkeypatch.setattr(provider, "is_alive", lambda: False)
    out = provider.search("半导体 政策")
    assert out["error"] == "research_unavailable"
    assert out["provider"] == "combosearch"
    assert out["results"] == []


def test_install_routes_adapter_when_provider_is_combosearch(monkeypatch) -> None:
    monkeypatch.setenv("KSS_RESEARCH_PROVIDER", "combosearch")
    monkeypatch.setattr(provider, "is_alive", lambda: True)
    monkeypatch.setattr(
        provider,
        "search",
        lambda query, limit=5: {"query": query, "provider": "combosearch", "results": [{"url": "https://example.com"}]},
    )
    adapter._kss_combosearch_installed = False
    provider.install(adapter)
    out = adapter.research_search("AI 政策", limit=1)
    assert out["provider"] == "combosearch"
    assert out["results"][0]["url"] == "https://example.com"


def test_install_leaves_other_providers_alone(monkeypatch) -> None:
    monkeypatch.setenv("KSS_RESEARCH_PROVIDER", "disabled")
    adapter._kss_combosearch_installed = False
    provider.install(adapter)
    out = adapter.research_search("AI 政策")
    assert out["provider"] == "disabled"
    assert out["error"] == "research_unavailable"
