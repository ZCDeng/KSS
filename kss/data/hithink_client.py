"""Official HiThink Financial-API thin REST client.

Official contract: https://github.com/HiThink-Tech/Financial-API/tree/main/docs/api
Base: ``https://fuyao.aicubes.cn``. Auth: header ``X-api-key``.

KTD1: REST only — no official Python SDK / CLI as a runtime dependency.
Failures return ``None`` (never raise). Envelope: HTTP 200 and ``code==0``
and ``data`` is not null. Retry network / ``4001`` / ``5xxx`` at most 3 times;
do not retry ``1xxx`` / ``2xxx``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import requests

from kss.config.paths import STATE_ROOT
from kss.security.redaction import redact_text

logger = logging.getLogger(__name__)

BASE_URL: str = "https://fuyao.aicubes.cn"
API_KEY_ENV: str = "HITHINK_FINANCE_API_KEY"
SECRET_FILENAME: str = "hithink_finance_api_key"
FUYAO_HOST: str = "fuyao.aicubes.cn"

_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 0.4
_TIMEOUT_SECONDS: float = 20.0
_RETRYABLE_CODES: frozenset[int] = frozenset({4001, 5001, 5002, 5003})
_AUCTION_STAGES: frozenset[str] = frozenset({"live", "final"})
_THSCODE_LIMIT: int = 100


def resolve_api_key() -> str:
    """Env-first, then ``$KSS_STATE_ROOT/secrets/hithink_finance_api_key``.

    No historical file fallback (plan U1).
    """
    env = os.environ.get(API_KEY_ENV, "").strip()
    if env:
        return env
    path = STATE_ROOT / "secrets" / SECRET_FILENAME
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def ymd_to_date_ms(ymd: str) -> int:
    """``YYYYMMDD`` → Asia/Shanghai 00:00 Unix milliseconds."""
    dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(dt.timestamp() * 1000)


def ymd_to_iso(ymd: str) -> str:
    """``YYYYMMDD`` → ``YYYY-MM-DD``."""
    return datetime.strptime(ymd, "%Y%m%d").strftime("%Y-%m-%d")


def join_thscodes(thscodes: Sequence[str], *, limit: int = _THSCODE_LIMIT) -> str:
    """Normalize, de-dupe, cap at official 100-token limit."""
    from kss.data.longbridge_coverage import normalize_symbol  # noqa: PLC0415

    out: list[str] = []
    seen: set[str] = set()
    for raw in thscodes:
        token = str(raw or "").strip()
        if not token:
            continue
        code = normalize_symbol(token)
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= limit:
            break
    return ",".join(out)


def _bypass_system_proxy() -> None:
    """Append ``fuyao.aicubes.cn`` to NO_PROXY / no_proxy (Tushare pattern)."""
    existing = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    hosts = [h.strip() for h in existing.split(",") if h.strip()]
    if FUYAO_HOST not in hosts:
        hosts.append(FUYAO_HOST)
    merged = ",".join(hosts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def _as_int_code(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_retryable_code(code: int) -> bool:
    return code in _RETRYABLE_CODES or 5000 <= code <= 5999


class HithinkClient:
    """Thin GET client. Inject ``session`` / ``api_key`` in tests; no live net in CI."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        api_key: str | None = None,
    ) -> None:
        self._api_key = resolve_api_key() if api_key is None else api_key.strip()
        self._session = session
        self._owns_session = session is None
        self.last_error: dict[str, Any] | None = None

    @property
    def api_key(self) -> str:
        return self._api_key

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _secrets(self) -> tuple[str, ...]:
        return (self._api_key,) if self._api_key else ()

    def _redact(self, text: str | None) -> str:
        return redact_text(text, known_secrets=self._secrets()) or ""

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET ``path``. Returns the envelope ``data`` field, or ``None``."""
        _bypass_system_proxy()
        self.last_error = None
        if not self._api_key:
            self.last_error = {"code": None, "message": "missing_key"}
            logger.warning("hithink: missing HITHINK_FINANCE_API_KEY")
            return None
        rel = path if path.startswith("/") else f"/{path}"
        url = f"{BASE_URL}{rel}"
        query = dict(params or {})
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = self._http().get(
                    url,
                    headers={
                        "X-api-key": self._api_key,
                        "Accept": "application/json",
                    },
                    params=query,
                    timeout=_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                msg = self._redact(str(exc))
                if attempt >= _MAX_ATTEMPTS:
                    logger.warning(
                        "hithink GET %s network failed after %d: %s",
                        rel, _MAX_ATTEMPTS, msg,
                    )
                    return None
                wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.info(
                    "hithink GET %s network retry %d: %s; wait %.1fs",
                    rel, attempt, msg, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500 or resp.status_code == 429:
                if attempt >= _MAX_ATTEMPTS:
                    logger.warning(
                        "hithink GET %s HTTP %s after %d",
                        rel, resp.status_code, _MAX_ATTEMPTS,
                    )
                    return None
                wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.info(
                    "hithink GET %s HTTP %s retry %d; wait %.1fs",
                    rel, resp.status_code, attempt, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.warning("hithink GET %s HTTP %s", rel, resp.status_code)
                return None

            try:
                payload = resp.json()
            except ValueError:
                logger.warning("hithink GET %s non-json body", rel)
                return None
            if not isinstance(payload, dict):
                logger.warning("hithink GET %s envelope not an object", rel)
                return None

            code = _as_int_code(payload.get("code"))
            if code is None:
                logger.warning("hithink GET %s missing/invalid code", rel)
                return None
            if code == 0:
                if "data" not in payload or payload["data"] is None:
                    self.last_error = {"code": 0, "message": "null_data"}
                    logger.warning("hithink GET %s code=0 but data is null", rel)
                    return None
                self.last_error = None
                return payload["data"]

            msg = self._redact(str(payload.get("message") or ""))
            self.last_error = {"code": code, "message": msg}
            if _is_retryable_code(code) and attempt < _MAX_ATTEMPTS:
                wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.info(
                    "hithink GET %s code=%s retry %d; wait %.1fs",
                    rel, code, attempt, wait,
                )
                time.sleep(wait)
                continue
            logger.warning("hithink GET %s business code=%s msg=%s", rel, code, msg)
            return None
        return None

    def get_prices_snapshot(self, thscodes: Sequence[str]) -> Any:
        joined = join_thscodes(thscodes)
        if not joined:
            return None
        return self.get("/api/a-share/prices/snapshot", {"thscodes": joined})

    def get_auction_snapshot(
        self, thscodes: Sequence[str], *, stage: str = "live",
    ) -> Any:
        joined = join_thscodes(thscodes)
        if not joined:
            return None
        st = (stage or "live").strip().lower()
        if st not in _AUCTION_STAGES:
            logger.warning("hithink auction: invalid stage %r", stage)
            return None
        return self.get(
            "/api/a-share/auction/snapshot",
            {"thscodes": joined, "stage": st},
        )

    def get_limit_up_pool(
        self,
        *,
        date_ms: int | None = None,
        page: int = 1,
        size: int = 200,
        sort_field: str = "limit_up_time",
        sort_dir: str = "asc",
    ) -> Any:
        params: dict[str, Any] = {
            "page": page,
            "size": size,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
        }
        if date_ms is not None:
            params["date_ms"] = date_ms
        return self.get("/api/a-share/special-data/limit-up-pool", params)

    def get_anomaly_analysis_list(
        self, *, tag_codes: str = "LIMIT_UP,SHARP_RISE",
    ) -> Any:
        params: dict[str, Any] = {}
        tags = (tag_codes or "").strip()
        if tags:
            params["tag_codes"] = tags
        return self.get("/api/a-share/special-data/anomaly-analysis-list", params)

    def get_dragon_tiger_list(
        self,
        *,
        board_type: str = "all",
        date: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"board_type": board_type or "all"}
        if date:
            params["date"] = date
        return self.get("/api/a-share/special-data/dragon-tiger-list", params)

    def search_tickers(self, q: str, *, limit: int = 1) -> Any:
        query = (q or "").strip()
        if not query:
            return None
        return self.get("/api/meta/tickers/search", {"q": query, "limit": int(limit)})
