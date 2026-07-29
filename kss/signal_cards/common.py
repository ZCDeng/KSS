"""信号卡共用 helper：稳定 card_id、统一 payload 形状。"""

from __future__ import annotations

import hashlib
from typing import Any

from kss.storage.signal_cards import _to_compact

CARD_TYPES = frozenset({
    "etf_flow",
    "sector_move",
    "theme_leader",
    "volume_spike",
    "valuation",
    "backtest_verdict",
})


def make_card_id(card_type: str, trade_date: str, subject: str) -> str:
    """稳定哈希：card_type + trade_date + subject。"""
    raw = f"{card_type}|{trade_date}|{subject}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def base_card(
    *,
    card_type: str,
    trade_date: str,
    subject: str,
    rule_id: str,
    metrics: dict[str, Any],
    threshold_source: str,
    coverage: str = "covered",
    data_as_of: str | None = None,
    direction: str | None = None,
    dose_bucket: str | None = None,
    hist_forward_ret: float | None = None,
    win_rate: float | None = None,
    effective_n: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装统一卡形态。direction 非 null 时强制要求 win_rate + effective_n。"""
    if direction is not None and (win_rate is None or effective_n is None):
        raise ValueError(
            f"direction={direction!r} 要求 win_rate 与 effective_n 同时非 null"
        )
    td = _to_compact(trade_date) if "-" in str(trade_date) else str(trade_date)
    card: dict[str, Any] = {
        "card_id": make_card_id(card_type, td, subject),
        "card_type": card_type,
        "trade_date": td,
        "data_as_of": data_as_of if data_as_of is not None else td,
        "subject": subject,
        "rule_id": rule_id,
        "metrics": metrics,
        "direction": direction,
        "dose_bucket": dose_bucket,
        "hist_forward_ret": hist_forward_ret,
        "win_rate": win_rate,
        "effective_n": effective_n,
        "threshold_source": threshold_source,
        "coverage": coverage,
    }
    if extra:
        card.update(extra)
    return card
