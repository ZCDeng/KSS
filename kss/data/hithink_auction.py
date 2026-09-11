"""集合竞价 universe、落盘与只读 payload（U6 / KTD6）。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from kss.config.paths import STORAGE_ROOT
from kss.data.hithink_client import HithinkClient, join_thscodes
from kss.data.longbridge_coverage import normalize_symbol

logger = logging.getLogger(__name__)

AUCTION_STAGES = frozenset({"live", "final"})
AUCTION_MAX_CODES = 100
_UNKNOWN_DROP_CAP = 8
_UNKNOWN_THSCODE_RE = re.compile(
    r"Unknown(?: A-share)? thscode[:\s`']+([0-9]{6}\.(?:SH|SZ|BJ))",
    re.I,
)
_CACHE_FLAG = frozenset({"1", "true", "yes", "from-cache", "from_cache", "cache"})


def auction_dir() -> Path:
    return STORAGE_ROOT / "auction"


def shanghai_today_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def auction_path(trade_date: str | None = None, stage: str = "live") -> Path:
    day = trade_date or shanghai_today_iso()
    st = (stage or "live").strip().lower()
    if st not in AUCTION_STAGES:
        st = "live"
    return auction_dir() / f"{day}-{st}.json"


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_a_share_stock(thscode: str) -> bool:
    """Auction endpoint rejects funds/indices; keep A-share stocks only."""
    n = normalize_symbol(thscode)
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", n):
        return False
    ticker = n[:6]
    if n.endswith(".BJ"):
        return ticker[0] in {"4", "8", "9"}
    if n.endswith(".SH"):
        return ticker.startswith(("60", "68"))
    # SZ stocks: 000/001/002/003/300 — not 159/399 ETFs and indices
    return ticker.startswith(("000", "001", "002", "003", "300"))


def _bj_pool_symbols() -> list[str]:
    scan_dir = STORAGE_ROOT / "reports" / "bj50_scan"
    cache_dir = STORAGE_ROOT / "bj_cache"
    scans = sorted(scan_dir.glob("scan_*.csv"))
    if scans:
        try:
            import pandas as pd  # noqa: PLC0415

            df = pd.read_csv(scans[-1])
            if "ts_code" in df.columns:
                return [s for s in df["ts_code"].tolist() if isinstance(s, str) and s]
        except Exception as exc:  # noqa: BLE001
            logger.warning("hithink auction: bj50 scan read failed: %s", exc)
    return [p.name.replace("_daily.csv", "") for p in cache_dir.glob("*_daily.csv")]


def load_auction_universe(*, limit: int = AUCTION_MAX_CODES) -> list[str]:
    from kss.storage.watchlist import load_watchlist  # noqa: PLC0415

    ordered: list[str] = []
    try:
        ordered.extend(load_watchlist())
    except Exception as exc:  # noqa: BLE001
        logger.warning("hithink auction: watchlist unavailable: %s", exc)
    ordered.extend(_bj_pool_symbols())
    out: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        if not is_a_share_stock(str(raw)):
            continue
        code = normalize_symbol(str(raw))
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= limit:
            break
    return out


def unknown_thscode_from_error(err: dict[str, Any] | None) -> str | None:
    """One unknown token 1002s the whole auction/snapshot request."""
    if not isinstance(err, dict):
        return None
    m = _UNKNOWN_THSCODE_RE.search(str(err.get("message") or ""))
    if not m:
        return None
    return normalize_symbol(m.group(1))


def _normalize_stage(stage: str | None) -> str:
    st = (stage or "live").strip().lower()
    return st if st in AUCTION_STAGES else "live"


def _use_cache(flag: str | None) -> bool:
    return str(flag or "").strip().lower() in _CACHE_FLAG


def fetch_auction_snapshot(
    *,
    stage: str = "live",
    thscodes: Sequence[str] | None = None,
    persist: bool = False,
    client: HithinkClient | None = None,
) -> dict[str, Any]:
    st = _normalize_stage(stage)
    codes = list(thscodes) if thscodes else load_auction_universe()
    if not codes:
        return {
            "error": "empty_universe",
            "hint": "自选 ∪ 北证池过滤后无 A 股股票",
            "stage": st,
            "thscodes": [],
        }
    cli = client or HithinkClient()
    if not cli.api_key:
        return {"error": "not_configured", "hint": "未配置 HITHINK_FINANCE_API_KEY", "stage": st}
    remaining = list(codes)
    dropped: list[str] = []
    data: Any = None
    for _ in range(_UNKNOWN_DROP_CAP + 1):
        data = cli.get_auction_snapshot(remaining, stage=st)
        if data is not None:
            break
        unknown = unknown_thscode_from_error(getattr(cli, "last_error", None))
        if not unknown or unknown not in remaining:
            break
        remaining = [c for c in remaining if c != unknown]
        dropped.append(unknown)
        if not remaining:
            break
    if data is None:
        return {
            "error": "empty_response",
            "hint": "HiThink auction/snapshot 无数据",
            "stage": st,
            "thscodes": remaining,
            "dropped_thscodes": dropped,
        }
    payload = {
        "stage": st,
        "trade_date": shanghai_today_iso(),
        "thscodes": join_thscodes(remaining).split(","),
        "dropped_thscodes": dropped,
        "source": "hithink",
        "eligibility": "forward_observed",
        "data": data,
    }
    if persist:
        atomic_write_json(auction_path(payload["trade_date"], st), payload)
    return payload


def load_auction_payload(
    *,
    stage: str = "live",
    from_cache: str = "",
    thscodes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Bridge entry: live API by default; ``from-cache`` reads today's file."""
    st = _normalize_stage(stage)
    if _use_cache(from_cache):
        path = auction_path(stage=st)
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {"error": "cache_unreadable", "hint": str(exc), "stage": st, "path": str(path)}
            if isinstance(cached, dict):
                cached.setdefault("from_cache", True)
                cached.setdefault("path", str(path))
                return cached
        return {"error": "cache_missing", "hint": f"无落盘 {path.name}", "stage": st, "path": str(path)}
    return fetch_auction_snapshot(stage=st, thscodes=thscodes, persist=False)
