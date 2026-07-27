"""Agent 只读实时盘面上下文服务."""

from __future__ import annotations

import re
from hashlib import sha256
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from kss.data.longbridge_coverage import normalize_symbol

ReadCall = Callable[[str, list[str]], Any]

_SYMBOL_RE = re.compile(r"\b(?:[0345689]\d{5})(?:\.(?:SH|SZ|BJ|sh|sz|bj))?\b")
_FORBIDDEN_INTENT_RE = re.compile(
    r"\b(buy|sell|order|trade|cancel|replace)\b|买入|卖出|下单|撤单|交易"
)


@dataclass(frozen=True)
class LiveContextScope:
    """一次 agent-turn 的实时上下文预取范围."""

    scope: str = "symbols"
    symbols: tuple[str, ...] = ()
    enabled: bool = True
    reason: str = "explicit"
    max_symbols: int = 12
    include_intraday_snapshot: bool = True
    intent: str = "explain"
    warnings: tuple[str, ...] = ()
    rejected: bool = False
    rejection_reason: str | None = None

    @classmethod
    def from_payload(
        cls,
        payload: Any,
        *,
        user_text: str = "",
    ) -> "LiveContextScope | None":
        """解析 wire payload；缺省/关闭时返回 None."""
        if payload in (None, False, "", {}):
            return None
        if isinstance(payload, str):
            payload = {"symbols": payload}
        if isinstance(payload, list):
            payload = {"symbols": payload}
        if not isinstance(payload, Mapping):
            return cls(
                enabled=False,
                rejected=True,
                rejection_reason="invalid_live_context_scope",
            )
        if payload.get("enabled") is False:
            return None
        scope = str(payload.get("scope") or "symbols").strip().lower()
        if scope not in {"market", "watchlist", "symbols"}:
            return cls(
                enabled=False,
                rejected=True,
                rejection_reason="invalid_live_context_scope",
            )
        max_symbols = _bounded_int(payload.get("max_symbols"), default=12, low=1, high=24)
        symbols = _symbols_from_any(payload.get("symbols"))
        reason = str(payload.get("reason") or "explicit")
        if not symbols and bool(payload.get("infer_from_input")):
            symbols = _symbols_from_text(user_text)
            reason = "input_symbols"
        intent = str(payload.get("intent") or "explain")
        warnings: list[str] = []
        rejected = False
        rejection_reason: str | None = None
        if _FORBIDDEN_INTENT_RE.search(intent):
            warnings.append("trade_intent_rejected")
            rejected = True
            rejection_reason = "trade_intent_not_allowed"
        return cls(
            scope=scope,
            symbols=tuple(dict.fromkeys(symbols))[:max_symbols],
            enabled=True,
            reason=reason,
            max_symbols=max_symbols,
            include_intraday_snapshot=bool(payload.get("include_intraday_snapshot", True)),
            intent=intent,
            warnings=tuple(warnings),
            rejected=rejected,
            rejection_reason=rejection_reason,
        )

    def to_payload(self) -> dict[str, Any]:
        """转换为可持久化/事件化 payload."""
        return {
            "scope": self.scope,
            "symbols": list(self.symbols),
            "enabled": self.enabled,
            "reason": self.reason,
            "max_symbols": self.max_symbols,
            "include_intraday_snapshot": self.include_intraday_snapshot,
            "intent": self.intent,
            "warnings": list(self.warnings),
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class LiveMarketContextService:
    """复用 bridge 只读命令组装 agent 可消费的实时上下文."""

    read_call: ReadCall
    now: Callable[[], datetime] = field(default_factory=lambda: datetime.now)

    def get_context(
        self,
        *,
        symbols: list[str] | tuple[str, ...] | str,
        intent: str = "explain",
        reason: str = "tool_call",
        include_intraday_snapshot: bool = True,
        max_symbols: int = 12,
    ) -> dict[str, Any]:
        """返回只读实时上下文；失败按标的结构化呈现，不抛到 agent loop."""
        if _FORBIDDEN_INTENT_RE.search(intent or ""):
            return {
                "error": "trade_intent_not_allowed",
                "is_error": True,
                "intent": intent,
                "policy": _policy(),
                "warnings": ["禁止把实时上下文用于交易执行或个性化买卖建议"],
            }
        normalized = tuple(dict.fromkeys(_symbols_from_any(symbols)))[:max_symbols]
        if not normalized:
            return {
                "error": "no_symbols",
                "is_error": True,
                "intent": intent,
                "policy": _policy(),
                "warnings": ["live context requires at least one symbol"],
            }

        quote_payload = self._safe_read("longbridge-quotes", [",".join(normalized)])
        quote_rows = _quote_rows(quote_payload, normalized)
        snapshot_rows: list[dict[str, Any]] = []
        if include_intraday_snapshot:
            for symbol in normalized:
                snapshot_rows.append(
                    self._snapshot_row(symbol, self._safe_read("intraday-snapshot", [symbol]))
                )

        rows: list[dict[str, Any]] = []
        quotes_by_symbol = {
            str(row.get("symbol") or ""): row for row in quote_rows if isinstance(row, Mapping)
        }
        snapshots_by_symbol = {
            str(row.get("symbol") or ""): row
            for row in snapshot_rows
            if isinstance(row, Mapping)
        }
        for symbol in normalized:
            quote = dict(quotes_by_symbol.get(symbol) or {"symbol": symbol, "error": "empty"})
            snapshot = snapshots_by_symbol.get(symbol)
            rows.append({
                "symbol": symbol,
                "quote": quote,
                "intraday_snapshot": snapshot,
                "routed_provider": quote.get("routed_provider")
                or (snapshot or {}).get("routed_provider"),
                "manifest_stale": bool(
                    quote.get("manifest_stale")
                    or (snapshot or {}).get("manifest_stale", False)
                ),
                "eligibility": "forward_observed",
                "provenance": "kss_live_market_context",
            })
        errors = [
            {
                "symbol": row["symbol"],
                "quote_error": (row["quote"] or {}).get("error"),
                "snapshot_error": (row.get("intraday_snapshot") or {}).get("error")
                if isinstance(row.get("intraday_snapshot"), Mapping)
                else None,
            }
            for row in rows
            if (row["quote"] or {}).get("error")
            or (
                isinstance(row.get("intraday_snapshot"), Mapping)
                and row["intraday_snapshot"].get("error")
            )
        ]
        newest = _newest_asof(rows)
        retrieved_at = self.now().astimezone().isoformat(timespec="seconds")
        snapshot_seed = "|".join([
            retrieved_at,
            newest or "",
            ",".join(normalized),
            ",".join(str((row.get("quote") or {}).get("last_done") or "") for row in rows),
        ])
        return {
            "kind": "market_live_context",
            "snapshot_id": f"lmc-{sha256(snapshot_seed.encode('utf-8')).hexdigest()[:16]}",
            "intent": intent or "explain",
            "reason": reason,
            "symbols": list(normalized),
            "rows": rows,
            "errors": errors,
            "source_asof_ts": newest,
            "retrieved_at": retrieved_at,
            "eligibility": "forward_observed",
            "provenance": "kss_live_market_context",
            "policy": _policy(),
            "warnings": _warnings(rows, errors),
        }

    def _safe_read(self, command: str, args: list[str]) -> Any:
        try:
            result = self.read_call(command, args)
        except Exception as exc:  # noqa: BLE001
            return {"error": "read_failed", "detail": f"{type(exc).__name__}: {exc}"}
        return result

    def _snapshot_row(self, symbol: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {"symbol": symbol, "error": "invalid_snapshot_payload"}
        return dict(payload)


def scope_context_text(payload: Mapping[str, Any]) -> str:
    """把 live context 压成短上下文段，供 Agent Core 注入模型."""
    lines = [
        "实时盘面上下文（只读，forward_observed，非 PIT 回测数据，禁止交易执行/个性化买卖建议）："
    ]
    for row in payload.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        quote = row.get("quote") if isinstance(row.get("quote"), Mapping) else {}
        snap = (
            row.get("intraday_snapshot")
            if isinstance(row.get("intraday_snapshot"), Mapping)
            else {}
        )
        parts = [
            f"- {row.get('symbol')}",
            f"route={row.get('routed_provider')}",
            f"asof={quote.get('source_asof_ts') or snap.get('source_asof_ts')}",
            f"last={quote.get('last_done')}",
            f"pct_source=tool_only",
        ]
        if quote.get("error"):
            parts.append(f"quote_error={quote.get('error')}")
        if snap.get("bar"):
            bar = snap.get("bar")
            if isinstance(bar, Mapping):
                parts.append(f"bar_close={bar.get('close')}")
        elif snap.get("error"):
            parts.append(f"snapshot_error={snap.get('error')}")
        lines.append(" ".join(parts))
    if payload.get("warnings"):
        lines.append("warnings=" + ",".join(str(x) for x in payload["warnings"]))
    return "\n".join(lines)


def _symbols_from_any(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value.strip())
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = []
    symbols: list[str] = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        norm = normalize_symbol(item)
        if _SYMBOL_RE.fullmatch(norm):
            symbols.append(norm)
    return symbols


def _symbols_from_text(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_symbol(match.group(0)) for match in _SYMBOL_RE.finditer(text)))


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(low, min(high, out))


def _quote_rows(payload: Any, symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return [{"symbol": symbol, "error": "invalid_quotes_payload"} for symbol in symbols]
    rows = payload.get("quotes")
    if not isinstance(rows, list):
        err = str(payload.get("error") or "invalid_quotes_payload")
        return [{"symbol": symbol, "error": err} for symbol in symbols]
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _newest_asof(rows: list[dict[str, Any]]) -> str | None:
    values: list[str] = []
    for row in rows:
        quote = row.get("quote") if isinstance(row.get("quote"), Mapping) else {}
        snap = row.get("intraday_snapshot") if isinstance(row.get("intraday_snapshot"), Mapping) else {}
        for candidate in (quote.get("source_asof_ts"), snap.get("source_asof_ts")):
            if candidate:
                values.append(str(candidate))
    return max(values) if values else None


def _warnings(rows: list[dict[str, Any]], errors: list[dict[str, Any]]) -> list[str]:
    warnings = ["forward_observed_non_pit", "no_trade_advice"]
    if errors:
        warnings.append("partial_live_context")
    if any(row.get("manifest_stale") for row in rows):
        warnings.append("longbridge_manifest_stale")
    return warnings


def _policy() -> dict[str, Any]:
    return {
        "read_only": True,
        "eligibility": "forward_observed",
        "pit_backtest_eligible": False,
        "trade_execution_allowed": False,
        "personalized_trade_advice_allowed": False,
        "source_precedence": "kss_tool_truth",
    }


__all__ = [
    "LiveContextScope",
    "LiveMarketContextService",
    "scope_context_text",
]
