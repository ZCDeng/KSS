"""Provider-agnostic external research adapter.

The adapter is intentionally read-only and evidence-oriented.  KSS local tools
remain the financial truth source; web/search payloads are returned as external
evidence with provenance metadata and safety warnings.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from kss.llm.sanitizer import scan_for_injection

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _ROOT / "evals" / "deep_research" / "fixtures" / "web_snapshots" / "sample_sources.json"

_TIMEOUT_SECONDS = 10
_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 256_000
_USER_AGENT = "KSSResearchAdapter/0.1 (+read-only evidence fetch)"

_REPUTABLE_SECONDARY_DOMAINS = (
    "eastmoney.com",
    "cls.cn",
    "yicai.com",
    "caixin.com",
    "stcn.com",
    "cnstock.com",
    "sina.com.cn",
    "10jqka.com.cn",
)
_PRIMARY_HINTS = ("公告", "announcement", "investor", "ir.", "sse.com.cn", "szse.cn", "neeq.com.cn")


class ResearchSafetyError(ValueError):
    """Raised when a URL is unsafe for external fetching."""


def _retrieved_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _provider() -> str:
    return (os.environ.get("KSS_RESEARCH_PROVIDER") or "disabled").strip().lower() or "disabled"


def _fetch_provider() -> str:
    return (os.environ.get("KSS_RESEARCH_FETCH_PROVIDER") or _provider()).strip().lower() or "disabled"


def research_status() -> dict[str, Any]:
    provider = _provider()
    fetch_provider = _fetch_provider()
    available = provider in {"fixture", "requests", "jina"} or (
        provider == "serper" and bool(os.environ.get("SERPER_API_KEY"))
    )
    return {
        "available": available,
        "provider": provider,
        "fetchProvider": fetch_provider,
        "tools": ["research-search", "research-fetch", "research-bundle"],
        "evidenceRules": [
            "localTruthPrecedence",
            "doNotTreatWebAsInstruction",
            "noTradeAdvice",
        ],
    }


def _rules() -> dict[str, bool]:
    return {
        "localTruthPrecedence": True,
        "doNotTreatWebAsInstruction": True,
        "noTradeAdvice": True,
    }


def _unavailable(kind: str, *, query: str = "", url: str = "", provider: str | None = None, hint: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider or _provider(),
        "retrievedAt": _retrieved_at(),
        "partial": True,
        "failedSteps": [kind],
        "error": "research_unavailable",
        "hint": hint or "Set KSS_RESEARCH_PROVIDER=fixture|requests|jina|serper with required keys.",
    }
    if query:
        payload["query"] = query
        payload["results"] = []
    if url:
        payload["url"] = url
        payload.update({
            "status": None,
            "sourceTier": source_tier(url),
            "title": "",
            "excerpt": "",
            "contentChars": 0,
            "cacheStatus": "unknown",
            "cacheTtlSeconds": None,
            "warnings": [],
        })
    return payload


def _fixture_path() -> Path:
    return Path(os.environ.get("KSS_RESEARCH_FIXTURE_PATH", str(_DEFAULT_FIXTURE))).expanduser()


def _load_fixture_sources() -> list[dict[str, Any]]:
    path = _fixture_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    sources = raw.get("sources", []) if isinstance(raw, dict) else []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(sources, start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = str(item.get("title") or f"Fixture source {i}")
        excerpt = str(item.get("excerpt") or item.get("snippet") or "")
        retrieved = str(item.get("retrievedAt") or item.get("retrieved_at") or raw.get("generated_at") or _retrieved_at())
        tier = str(item.get("sourceTier") or item.get("tier") or source_tier(url, title))
        out.append({
            "title": title,
            "url": url,
            "snippet": excerpt[:500],
            "excerpt": excerpt,
            "sourceTier": tier,
            "retrievedAt": retrieved,
            "cacheStatus": item.get("cacheStatus") or "cached",
            "cacheTtlSeconds": item.get("cacheTtlSeconds"),
            "rank": int(item.get("rank") or i),
        })
    return out


def _warning_from_text(text: str) -> dict[str, str] | None:
    pattern = scan_for_injection(text)
    if not pattern:
        return None
    return {
        "type": "prompt_injection",
        "severity": "danger",
        "message": f"External evidence matched injection pattern: {pattern}",
    }


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated {len(text) - limit} chars ..."


def source_tier(url: str, title: str = "") -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    text = f"{url} {title}".lower()
    if host.endswith(".gov.cn") or any(hint in text for hint in _PRIMARY_HINTS):
        return "official_or_primary"
    if host.endswith(("sse.com.cn", "szse.cn", "neeq.com.cn")):
        return "official_or_primary"
    if any(host == d or host.endswith("." + d) for d in _REPUTABLE_SECONDARY_DOMAINS):
        return "reputable_secondary"
    return "unknown"


def _is_private_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return any([
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


def _validate_host(host: str, *, resolve_dns: bool = True) -> None:
    clean = host.strip().lower().strip("[]")
    if clean in {"localhost", "localhost.localdomain"} or clean.endswith(".localhost"):
        raise ResearchSafetyError("localhost URLs are not allowed")
    if _is_private_ip(clean):
        raise ResearchSafetyError("private/loopback/link-local IP URLs are not allowed")
    if not resolve_dns:
        return
    try:
        infos = socket.getaddrinfo(clean, None)
    except OSError:
        return
    for info in infos:
        ip_text = info[4][0]
        if _is_private_ip(ip_text):
            raise ResearchSafetyError("host resolves to private/loopback/link-local IP")


def validate_url(url: str, *, resolve_dns: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ResearchSafetyError("only http/https URLs are allowed")
    if not parsed.hostname:
        raise ResearchSafetyError("URL host is required")
    _validate_host(parsed.hostname, resolve_dns=resolve_dns)
    return url


def _extract_text(html: str) -> tuple[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        text = " ".join(soup.stripped_strings)
        return title, re.sub(r"\s+", " ", text).strip()
    except Exception:  # noqa: BLE001
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        text = re.sub(r"<[^>]+>", " ", html)
        return title, re.sub(r"\s+", " ", text).strip()


def _requests_fetch(url: str, *, max_chars: int) -> dict[str, Any]:
    import requests

    current = validate_url(url)
    session = requests.Session()
    headers = {"User-Agent": _USER_AGENT}
    response = None
    for _ in range(_MAX_REDIRECTS + 1):
        response = session.get(
            current,
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                break
            current = validate_url(urljoin(current, location))
            continue
        break
    if response is None:
        raise RuntimeError("no response")
    final_url = validate_url(response.url or current)
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_RESPONSE_BYTES:
            break
    raw = b"".join(chunks)
    encoding = response.encoding or "utf-8"
    html = raw.decode(encoding, errors="replace")
    title, text = _extract_text(html)
    excerpt = _cap(text, max_chars)
    warning = _warning_from_text(excerpt)
    return {
        "url": final_url,
        "provider": "requests",
        "retrievedAt": _retrieved_at(),
        "status": response.status_code,
        "sourceTier": source_tier(final_url, title),
        "title": title,
        "excerpt": excerpt,
        "contentChars": len(text),
        "cacheStatus": "unknown",
        "cacheTtlSeconds": None,
        "warnings": [warning] if warning else [],
    }


def _jina_fetch(url: str, *, max_chars: int) -> dict[str, Any]:
    import requests

    safe_url = validate_url(url)
    endpoint = "https://r.jina.ai/" + safe_url
    headers = {"User-Agent": _USER_AGENT}
    if os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    response = requests.get(endpoint, headers=headers, timeout=_TIMEOUT_SECONDS)
    text = response.text
    title = text.splitlines()[0].strip("# ").strip() if text.splitlines() else ""
    excerpt = _cap(text, max_chars)
    warning = _warning_from_text(excerpt)
    return {
        "url": safe_url,
        "provider": "jina",
        "authMode": "api_key" if os.environ.get("JINA_API_KEY") else "none",
        "retrievedAt": _retrieved_at(),
        "status": response.status_code,
        "sourceTier": source_tier(safe_url, title),
        "title": title,
        "excerpt": excerpt,
        "contentChars": len(text),
        "cacheStatus": "unknown",
        "cacheTtlSeconds": None,
        "warnings": [warning] if warning else [],
    }


def research_search(query: str, *, limit: int = 5, locale: str = "zh-CN") -> dict[str, Any]:
    provider = _provider()
    limit = max(1, min(int(limit or 5), 10))
    if provider == "fixture":
        sources = _load_fixture_sources()[:limit]
        results = [
            {
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "excerpt": item["excerpt"],
                "sourceTier": item["sourceTier"],
                "retrievedAt": item["retrievedAt"],
                "cacheStatus": item["cacheStatus"],
                "cacheTtlSeconds": item["cacheTtlSeconds"],
                "rank": item["rank"],
            }
            for item in sources
        ]
        return {
            "query": query,
            "provider": "fixture",
            "retrievedAt": _retrieved_at(),
            "results": results,
            "partial": False,
            "failedSteps": [],
        }
    if provider == "jina":
        return _jina_search(query, limit=limit, locale=locale)
    if provider == "serper":
        return _serper_search(query, limit=limit, locale=locale)
    return _unavailable("search", query=query, provider=provider)


def _jina_search(query: str, *, limit: int, locale: str) -> dict[str, Any]:
    import requests

    endpoint = "https://s.jina.ai/" + quote(query)
    headers = {"User-Agent": _USER_AGENT}
    if os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    try:
        response = requests.get(endpoint, headers=headers, timeout=_TIMEOUT_SECONDS)
        text = response.text
        urls = re.findall(r"https?://[^\s)>\]]+", text)
        results = []
        retrieved_at = _retrieved_at()
        for i, url in enumerate(dict.fromkeys(urls), start=1):
            if i > limit:
                break
            try:
                validate_url(url)
            except ResearchSafetyError:
                continue
            results.append({
                "title": f"Jina result {i}",
                "url": url,
                "snippet": "",
                "excerpt": "",
                "sourceTier": source_tier(url),
                "retrievedAt": retrieved_at,
                "cacheStatus": "unknown",
                "cacheTtlSeconds": None,
                "rank": i,
            })
        return {
            "query": query,
            "provider": "jina",
            "locale": locale,
            "retrievedAt": retrieved_at,
            "results": results,
            "partial": response.status_code >= 400,
            "failedSteps": [] if response.status_code < 400 else ["search"],
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable("search", query=query, provider="jina", hint=str(exc))


def _serper_search(query: str, *, limit: int, locale: str) -> dict[str, Any]:
    import requests

    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return _unavailable("search", query=query, provider="serper", hint="SERPER_API_KEY is required")
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": _USER_AGENT},
            json={"q": query, "num": limit, "gl": "cn" if locale.startswith("zh") else "us", "hl": locale},
            timeout=_TIMEOUT_SECONDS,
        )
        data = response.json()
        organic = data.get("organic", []) if isinstance(data, dict) else []
        results = []
        retrieved_at = _retrieved_at()
        for i, item in enumerate(organic[:limit], start=1):
            url = str(item.get("link") or "")
            try:
                validate_url(url)
            except ResearchSafetyError:
                continue
            title = str(item.get("title") or "")
            snippet = str(item.get("snippet") or "")
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "excerpt": snippet,
                "sourceTier": source_tier(url, title),
                "retrievedAt": retrieved_at,
                "cacheStatus": "unknown",
                "cacheTtlSeconds": None,
                "rank": i,
            })
        return {
            "query": query,
            "provider": "serper",
            "retrievedAt": retrieved_at,
            "results": results,
            "partial": response.status_code >= 400,
            "failedSteps": [] if response.status_code < 400 else ["search"],
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable("search", query=query, provider="serper", hint=str(exc))


def research_fetch(url: str, *, max_chars: int = 8000) -> dict[str, Any]:
    max_chars = max(200, min(int(max_chars or 8000), 20000))
    provider = _fetch_provider()
    if provider == "fixture":
        try:
            validate_url(url, resolve_dns=False)
        except ResearchSafetyError as exc:
            return _unsafe_fetch_payload(url, provider="fixture", detail=str(exc))
        sources = _load_fixture_sources()
        hit = next((item for item in sources if item["url"] == url), None)
        if not hit:
            return _unavailable("fetch", url=url, provider="fixture", hint="URL not found in fixture")
        excerpt = _cap(hit.get("excerpt", ""), max_chars)
        warning = _warning_from_text(excerpt)
        return {
            "url": hit["url"],
            "provider": "fixture",
            "retrievedAt": hit["retrievedAt"],
            "status": 200,
            "sourceTier": hit["sourceTier"],
            "title": hit["title"],
            "excerpt": excerpt,
            "contentChars": len(hit.get("excerpt", "")),
            "cacheStatus": hit["cacheStatus"],
            "cacheTtlSeconds": hit["cacheTtlSeconds"],
            "warnings": [warning] if warning else [],
        }
    if provider in {"requests", "serper"}:
        try:
            return _requests_fetch(url, max_chars=max_chars)
        except ResearchSafetyError as exc:
            return _unsafe_fetch_payload(url, provider="requests", detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            return _unavailable("fetch", url=url, provider="requests", hint=str(exc))
    if provider == "jina":
        try:
            return _jina_fetch(url, max_chars=max_chars)
        except ResearchSafetyError as exc:
            return _unsafe_fetch_payload(url, provider="jina", detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            return _unavailable("fetch", url=url, provider="jina", hint=str(exc))
    return _unavailable("fetch", url=url, provider=provider)


def _unsafe_fetch_payload(url: str, *, provider: str, detail: str) -> dict[str, Any]:
    return {
        "url": url,
        "provider": provider,
        "retrievedAt": _retrieved_at(),
        "status": None,
        "sourceTier": source_tier(url),
        "title": "",
        "excerpt": "",
        "contentChars": 0,
        "cacheStatus": "unknown",
        "cacheTtlSeconds": None,
        "warnings": [{"type": "unsafe_url", "severity": "danger", "message": detail}],
        "partial": True,
        "failedSteps": ["fetch"],
        "error": "unsafe_url",
    }


def research_bundle(query: str, *, limit: int = 3, max_chars_per_source: int = 3000) -> dict[str, Any]:
    search = research_search(query, limit=limit)
    sources: list[dict[str, Any]] = []
    failed_steps = list(search.get("failedSteps") or [])
    partial = bool(search.get("partial"))
    warnings: list[dict[str, Any]] = []
    for item in search.get("results", [])[: max(1, min(int(limit or 3), 10))]:
        url = str(item.get("url") or "")
        fetched = research_fetch(url, max_chars=max_chars_per_source)
        if fetched.get("error"):
            failed_steps.extend(fetched.get("failedSteps") or ["fetch"])
            partial = True
        warnings.extend(fetched.get("warnings") or [])
        excerpt = fetched.get("excerpt") or item.get("snippet") or ""
        sources.append({
            "url": url,
            "title": fetched.get("title") or item.get("title") or "",
            "sourceTier": (item.get("sourceTier") if fetched.get("error") else None)
            or fetched.get("sourceTier") or item.get("sourceTier") or source_tier(url),
            "retrievedAt": fetched.get("retrievedAt") or search.get("retrievedAt"),
            "excerpt": excerpt,
            "cacheStatus": fetched.get("cacheStatus") or item.get("cacheStatus") or "unknown",
            "cacheTtlSeconds": fetched.get("cacheTtlSeconds", item.get("cacheTtlSeconds")),
            "usedFor": "external_background_only",
        })
    return {
        "query": query,
        "provider": search.get("provider", _provider()),
        "retrievedAt": _retrieved_at(),
        "sources": sources,
        "rules": _rules(),
        "warnings": warnings,
        "partial": partial,
        "failedSteps": sorted(set(failed_steps)),
    }


__all__ = [
    "ResearchSafetyError",
    "research_bundle",
    "research_fetch",
    "research_search",
    "research_status",
    "source_tier",
    "validate_url",
]
