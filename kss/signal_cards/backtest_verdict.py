"""回测裁决卡：predictions + ic_snapshots，按 strategy 分别门控。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kss.config.paths import KSS_DB, PROJECT_ROOT
from kss.signal_cards.common import base_card
from kss.storage.db import connect, ensure_schema
from kss.storage.signal_cards import _to_compact

_THRESHOLDS_PATH = PROJECT_ROOT / "kss" / "config" / "factor_health_thresholds.yaml"

# predictions.strategy → ic_snapshots.factor_id（命名空间不同，禁止直接 join）
STRATEGY_TO_FACTOR_ID: dict[str, str] = {
    "log_mv_reverse": "pipeline:log_mv",
    "sr": "sr",
}


def resolve_factor_id(strategy: str) -> str | None:
    """显式映射；未知 strategy 尝试同名，否则 None。"""
    if strategy in STRATEGY_TO_FACTOR_ID:
        return STRATEGY_TO_FACTOR_ID[strategy]
    return strategy  # 允许新策略以同名尝试；读不到 n_periods 再标 insufficient


def load_realized_ic_min_n(path: Path | None = None) -> int:
    p = path or _THRESHOLDS_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return int(raw["factor_health"]["realized_ic_min_n"])


def generate_for_date(
    trade_date: str,
    *,
    db_path: str | Path | None = None,
    thresholds_path: Path | None = None,
) -> list[dict[str, Any]]:
    """对 predictions 中出现的每个 strategy 产一张裁决卡（动态分组，不硬编码策略名）。"""
    td = _to_compact(trade_date) if "-" in trade_date else trade_date
    # predictions 用横杠日期
    dashed = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
    path = Path(db_path) if db_path is not None else KSS_DB
    min_n = load_realized_ic_min_n(thresholds_path)

    with connect(path) as conn:
        ensure_schema(conn)
        strategies = [
            r["strategy"]
            for r in conn.execute(
                "SELECT DISTINCT strategy FROM predictions ORDER BY strategy"
            ).fetchall()
        ]
        if not strategies:
            return []

        cards: list[dict[str, Any]] = []
        for strategy in strategies:
            factor_id = resolve_factor_id(strategy)
            ic_row = None
            if factor_id is not None:
                ic_row = conn.execute(
                    "SELECT factor_id, window_end, n_periods, ic_mean, icir, ic_t_stat "
                    "FROM ic_snapshots WHERE factor_id=? "
                    "ORDER BY window_end DESC LIMIT 1",
                    (factor_id,),
                ).fetchone()

            # 当日预测（可选，供 outcome 展示）
            day_preds = conn.execute(
                "SELECT symbol, status, outcome, realized_ret, prediction_date "
                "FROM predictions WHERE strategy=? AND prediction_date=?",
                (strategy, dashed),
            ).fetchall()

            n_periods = int(ic_row["n_periods"]) if ic_row and ic_row["n_periods"] is not None else None
            metrics: dict[str, Any] = {
                "strategy": strategy,
                "factor_id": factor_id,
                "n_periods": n_periods,
                "realized_ic_min_n": min_n,
                "ic_mean": ic_row["ic_mean"] if ic_row else None,
                "icir": ic_row["icir"] if ic_row else None,
                "ic_window_end": ic_row["window_end"] if ic_row else None,
                "day_prediction_count": len(day_preds),
                "day_settled": sum(1 for p in day_preds if p["status"] == "settled"),
            }

            if factor_id is None or ic_row is None or n_periods is None:
                cards.append(
                    base_card(
                        card_type="backtest_verdict",
                        trade_date=td,
                        subject=strategy,
                        rule_id="backtest_verdict_unmapped_or_missing_ic",
                        metrics={
                            **metrics,
                            "deficit_days": None,
                            "reason": "no_ic_snapshot_for_factor_id",
                        },
                        threshold_source="gated",
                        coverage="insufficient_data",
                        data_as_of=td,
                        direction=None,
                    )
                )
                continue

            if n_periods < min_n:
                deficit = min_n - n_periods
                cards.append(
                    base_card(
                        card_type="backtest_verdict",
                        trade_date=td,
                        subject=strategy,
                        rule_id="backtest_verdict_below_min_n",
                        metrics={
                            **metrics,
                            "deficit_days": deficit,
                            "reason": f"n_periods={n_periods} < min_n={min_n} (差 {deficit} 天)",
                        },
                        threshold_source="gated",
                        coverage="insufficient_data",
                        data_as_of=str(ic_row["window_end"] or td),
                        direction=None,
                    )
                )
                continue

            # 过门控：写实际裁决（IC 读数 + 当日 settled outcomes 摘要）
            outcomes = [p["outcome"] for p in day_preds if p["status"] == "settled" and p["outcome"]]
            metrics["outcomes"] = outcomes
            metrics["gate"] = "pass"
            cards.append(
                base_card(
                    card_type="backtest_verdict",
                    trade_date=td,
                    subject=strategy,
                    rule_id="backtest_verdict_pass",
                    metrics=metrics,
                    threshold_source="gated",
                    coverage="covered",
                    data_as_of=str(ic_row["window_end"] or td),
                    direction=None,
                )
            )
        return cards
