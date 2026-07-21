"""yupi-hot-monitor HTTP 旁路客户端（stdlib urllib，无 Node 依赖）。

默认 ``YUPI_BASE_URL=http://127.0.0.1:3001``。所有方法 fail-soft：网络/HTTP 错误
返回结构化结果，不抛到调用方（调用方也可捕获 ``YupiError``）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class YupiError(Exception):
    """yupi 旁路不可用或响应异常。"""


def base_url() -> str:
    """优先 ``YUPI_BASE_URL``；否则走 KSS 托管运行时默认端口（见 ``yupi_runtime``）。"""
    explicit = (os.environ.get("YUPI_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        from kss.news.yupi_runtime import base_url as managed_base_url

        return managed_base_url()
    except Exception:
        return "http://127.0.0.1:18765"


class YupiClient:
    def __init__(self, base: str | None = None, timeout: float = 30.0):
        self.base = (base or base_url()).rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        query: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout if timeout is not None else self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                detail = str(e)
            raise YupiError(f"HTTP {e.code} {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            raise YupiError(f"{method} {path}: {e}") from e

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health", timeout=min(5.0, self.timeout))

    def list_keywords(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/keywords")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def create_keyword(self, text: str, category: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if category is not None:
            body["category"] = category
        return self._request("POST", "/api/keywords", body=body)

    def update_keyword(
        self,
        kid: str,
        *,
        text: str | None = None,
        category: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
        if category is not None:
            body["category"] = category
        if is_active is not None:
            body["isActive"] = is_active
        return self._request("PUT", f"/api/keywords/{kid}", body=body)

    def check_hotspots(self, timeout: float = 180.0) -> dict[str, Any]:
        return self._request("POST", "/api/check-hotspots", body={}, timeout=timeout)

    def list_hotspots(
        self,
        *,
        keyword_id: str | None = None,
        page: int = 1,
        limit: int = 50,
        time_range: str | None = "7d",
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {"page": str(page), "limit": str(limit)}
        if keyword_id:
            q["keywordId"] = keyword_id
        if time_range:
            q["timeRange"] = time_range
        data = self._request("GET", "/api/hotspots", query=q)
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, list):
            return data
        return []
