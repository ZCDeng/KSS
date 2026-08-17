"""Local comboSearch provider for the external-research adapter.

``KSS_RESEARCH_PROVIDER=combosearch`` is not a built-in adapter id yet. This
module patches ``research_search`` / ``research_fetch`` / ``research_status``
so scheduled research can use the Homebrew/CLI service already used by news
collect. Failures stay fail-soft (``research_unavailable``), never raise.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from kss.research.combosearch_client import DEFAULT_TIMEOUT, _bin, is_alive


def _node() -> str:
    configured = (os.environ.get("KSS_COMBOSEARCH_NODE") or os.environ.get("KSS_HARNESS_NODE") or "").strip()
    for candidate in (
        configured,
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "node"
from kss.research.evidence import cap as _cap
from kss.research.evidence import source_tier
from kss.research.evidence import warning_from_text as _warning_from_text


def _retrieved_at() -> str:
    from kss.research.adapter import _retrieved_at as _shared

    return _shared()


def _run_json(argv: list[str], *, timeout: float) -> dict[str, Any]:
    if not os.path.exists(argv[0]):
        return {"ok": False, "error": f"combosearch CLI not found: {argv[0]}", "results": []}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"TimeoutExpired: {timeout}s", "results": []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}
    # 0 = ok, 2 = degraded/partial; both may still carry results.
    if proc.returncode not in {0, 2}:
        return {
            "ok": False,
            "error": f"exit {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
            "results": [],
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSONDecodeError: {exc}", "results": []}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "combosearch payload is not an object", "results": []}
    results = payload.get("results") or []
    if not isinstance(results, list):
        results = []
    return {"ok": True, "error": None, "results": results, "degraded": proc.returncode == 2}


def search(query: str, *, limit: int = 5) -> dict[str, Any]:
    from kss.research.adapter import _unavailable

    limit = max(1, min(int(limit or 5), 10))
    if not is_alive():
        return _unavailable(
            "search",
            query=query,
            provider="combosearch",
            hint="combosearch CLI is not available",
        )
    timeout = float(os.environ.get("KSS_COMBOSEARCH_TIMEOUT") or DEFAULT_TIMEOUT)
    out = _run_json([_node(), _bin(), "search", query, "--json", "--limit", str(limit)], timeout=timeout)
    if not out.get("ok"):
        return _unavailable(
            "search",
            query=query,
            provider="combosearch",
            hint=str(out.get("error") or "combosearch search failed"),
        )
    retrieved = _retrieved_at()
    results: list[dict[str, Any]] = []
    for index, item in enumerate(out.get("results") or [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "")
        excerpt = str(item.get("content") or item.get("snippet") or "")
        snippet = str(item.get("snippet") or excerpt[:500])
        if not (url or title or snippet):
            continue
        results.append(
            {
                "title": title or snippet[:40],
                "url": url,
                "snippet": snippet[:500],
                "excerpt": excerpt,
                "sourceTier": source_tier(url, title),
                "retrievedAt": str(item.get("ts") or retrieved),
                "cacheStatus": "live",
                "cacheTtlSeconds": None,
                "rank": item.get("rank") if item.get("rank") is not None else index,
            }
        )
        if len(results) >= limit:
            break
    return {
        "query": query,
        "provider": "combosearch",
        "retrievedAt": retrieved,
        "results": results,
        "partial": bool(out.get("degraded")) or not results,
        "failedSteps": [] if results else ["search"],
    }


def fetch(url: str, *, max_chars: int = 8000) -> dict[str, Any]:
    from kss.research.adapter import ResearchSafetyError, _unavailable, _unsafe_fetch_payload, validate_url

    max_chars = max(200, min(int(max_chars or 8000), 20000))
    try:
        safe_url = validate_url(url, resolve_dns=False)
    except ResearchSafetyError as exc:
        return _unsafe_fetch_payload(url, provider="combosearch", detail=str(exc))
    if not is_alive():
        return _unavailable(
            "fetch",
            url=safe_url,
            provider="combosearch",
            hint="combosearch CLI is not available",
        )
    timeout = float(os.environ.get("KSS_COMBOSEARCH_TIMEOUT") or DEFAULT_TIMEOUT)
    out = _run_json([_node(), _bin(), "scrape", safe_url, "--json"], timeout=timeout)
    if not out.get("ok"):
        return _unavailable(
            "fetch",
            url=safe_url,
            provider="combosearch",
            hint=str(out.get("error") or "combosearch scrape failed"),
        )
    hit = next((item for item in out.get("results") or [] if isinstance(item, dict)), {})
    excerpt = _cap(str(hit.get("content") or hit.get("snippet") or ""), max_chars)
    warning = _warning_from_text(excerpt)
    return {
        "url": str(hit.get("url") or safe_url),
        "provider": "combosearch",
        "retrievedAt": str(hit.get("ts") or _retrieved_at()),
        "status": 200 if excerpt else None,
        "sourceTier": source_tier(str(hit.get("url") or safe_url), str(hit.get("title") or "")),
        "title": str(hit.get("title") or ""),
        "excerpt": excerpt,
        "contentChars": len(str(hit.get("content") or hit.get("snippet") or "")),
        "cacheStatus": "live",
        "cacheTtlSeconds": None,
        "warnings": [warning] if warning else [],
    }


def status_available() -> bool:
    return is_alive()


def install(adapter: Any) -> None:
    """Wrap adapter entry points so ``combosearch`` is a first-class provider."""
    if getattr(adapter, "_kss_combosearch_installed", False):
        return
    original_search = adapter.research_search
    original_fetch = adapter.research_fetch
    original_status = adapter.research_status

    def research_search(query: str, *, limit: int = 5, locale: str = "zh-CN") -> dict[str, Any]:
        if (os.environ.get("KSS_RESEARCH_PROVIDER") or "").strip().lower() == "combosearch":
            return search(query, limit=limit)
        return original_search(query, limit=limit, locale=locale)

    def research_fetch(url: str, *, max_chars: int = 8000) -> dict[str, Any]:
        provider = (
            os.environ.get("KSS_RESEARCH_FETCH_PROVIDER")
            or os.environ.get("KSS_RESEARCH_PROVIDER")
            or ""
        ).strip().lower()
        if provider == "combosearch":
            return fetch(url, max_chars=max_chars)
        return original_fetch(url, max_chars=max_chars)

    def research_status() -> dict[str, Any]:
        body = original_status()
        if (os.environ.get("KSS_RESEARCH_PROVIDER") or "").strip().lower() == "combosearch":
            body["provider"] = "combosearch"
            body["available"] = status_available()
            if not body.get("fetchProvider"):
                body["fetchProvider"] = "combosearch"
        return body

    adapter.research_search = research_search
    adapter.research_fetch = research_fetch
    adapter.research_status = research_status
    adapter._kss_combosearch_installed = True
