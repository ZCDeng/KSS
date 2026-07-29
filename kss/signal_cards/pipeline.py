"""每日信号卡编排：六生成器串行，单失败不中断其余。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kss.signal_cards import (
    backtest_verdict,
    etf_flow,
    sector_move,
    theme_leader,
    valuation,
    volume_spike,
)
from kss.storage.signal_cards import _to_compact, write_cards

logger = logging.getLogger(__name__)

GeneratorFn = Callable[..., list[dict[str, Any]]]

GENERATORS: list[tuple[str, GeneratorFn]] = [
    ("etf_flow", etf_flow.generate_for_date),
    ("sector_move", sector_move.generate_for_date),
    ("theme_leader", theme_leader.generate_for_date),
    ("volume_spike", volume_spike.generate_for_date),
    ("valuation", valuation.generate_for_date),
    ("backtest_verdict", backtest_verdict.generate_for_date),
]


@dataclass
class BuildResult:
    trade_date: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)
    failed_generators: list[dict[str, str]] = field(default_factory=list)
    written: int = 0

    @property
    def partial_ok(self) -> bool:
        """至少一个生成器成功，或全部空但无异常。"""
        return bool(self.cards) or not self.failed_generators


def build_for_date(
    trade_date: str,
    *,
    db_path: str | Path | None = None,
    write: bool = True,
    generators: list[tuple[str, GeneratorFn]] | None = None,
) -> BuildResult:
    td = _to_compact(trade_date) if "-" in trade_date else trade_date
    result = BuildResult(trade_date=td)
    gens = generators if generators is not None else GENERATORS
    all_cards: list[dict[str, Any]] = []

    for name, fn in gens:
        try:
            if name == "volume_spike":
                cards = fn(td)
            else:
                cards = fn(td, db_path=db_path)
            all_cards.extend(cards)
            result.by_type[name] = len(cards)
        except Exception as exc:  # noqa: BLE001 — 单生成器失败隔离
            logger.exception("signal_cards generator %s failed on %s", name, td)
            result.failed_generators.append(
                {"generator": name, "error": f"{type(exc).__name__}: {exc}"}
            )
            result.by_type[name] = 0

    result.cards = all_cards
    if write and all_cards:
        result.written = write_cards(all_cards, db_path=db_path)
    return result


def backfill(
    start: str,
    end: str,
    *,
    db_path: str | Path | None = None,
    dates: list[str] | None = None,
) -> list[BuildResult]:
    """逐日回补 [start, end] 或显式 dates 列表。"""
    if dates is None:
        from kss.storage import etf_radar, sector_rotation

        start_c = _to_compact(start) if "-" in start else start
        end_c = _to_compact(end) if "-" in end else end
        etf_dates = {
            s["trade_date"]
            for s in etf_radar.read_all_ascending(db_path=db_path)
            if start_c <= s["trade_date"] <= end_c
        }
        sector_dates = {
            s["tradeDate"]
            for s in sector_rotation.read_all_ascending(db_path=db_path)
            if start_c <= s["tradeDate"] <= end_c
        }
        dates = sorted(etf_dates | sector_dates)
    return [build_for_date(d, db_path=db_path, write=True) for d in dates]
