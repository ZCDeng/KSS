"""个股放量卡：读仓库根 cs_data_*.csv（不读 cs_data/ 陈旧副本）。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from kss.config.paths import STATE_ROOT
from kss.signal_cards.common import base_card
from kss.storage.signal_cards import _to_compact

logger = logging.getLogger(__name__)

# 未经回测，threshold_source=convention
VOLUME_RATIO_TH = 2.0
PCT_CHG_ABS_TH = 3.0


def _discover_cs_files(root: Path | None = None) -> list[Path]:
    base = Path(root) if root is not None else Path(STATE_ROOT)
    # 仅扫仓库根，不扫 cs_data/ 子目录
    return sorted(base.glob("cs_data_*.csv"))


def _read_row_for_date(path: Path, trade_date_compact: str) -> dict[str, str] | None:
    dashed = f"{trade_date_compact[:4]}-{trade_date_compact[4:6]}-{trade_date_compact[6:8]}"
    try:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # 文件按日期升序；从后往前找更快
            rows = list(reader)
    except OSError as exc:
        logger.warning("volume_spike: 无法读 %s: %s", path, exc)
        return None
    for row in reversed(rows):
        d = (row.get("trade_date") or "").strip()
        if d == dashed or d == trade_date_compact:
            return row
    return None


def generate_for_date(
    trade_date: str,
    *,
    root: Path | None = None,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    td = _to_compact(trade_date) if "-" in trade_date else trade_date
    files = _discover_cs_files(root)
    if symbols is not None:
        wanted = {s.split(".")[0].replace("cs_data_", "") for s in symbols}
        files = [p for p in files if p.stem.replace("cs_data_", "") in wanted]

    cards: list[dict[str, Any]] = []
    for path in files:
        row = _read_row_for_date(path, td)
        if row is None:
            continue
        ts_code = (row.get("ts_code") or "").strip()
        if not ts_code:
            code = path.stem.replace("cs_data_", "")
            ts_code = f"{code}.SH"  # 兜底；正常 CSV 自带 ts_code
        vr_raw = (row.get("volume_ratio") or "").strip()
        pct_raw = (row.get("pct_chg") or "").strip()

        if vr_raw == "":
            # 空 volume_ratio → insufficient_data 卡，不静默跳过
            cards.append(
                base_card(
                    card_type="volume_spike",
                    trade_date=td,
                    subject=ts_code,
                    rule_id="volume_spike_empty_ratio",
                    metrics={
                        "volume_ratio": None,
                        "pct_chg": float(pct_raw) if pct_raw else None,
                        "turnover_rate": row.get("turnover_rate") or None,
                        "total_mv": row.get("total_mv") or None,
                        "volume_ratio_th": VOLUME_RATIO_TH,
                        "pct_chg_abs_th": PCT_CHG_ABS_TH,
                    },
                    threshold_source="convention",
                    coverage="insufficient_data",
                    data_as_of=td,
                    direction=None,
                )
            )
            continue

        try:
            vr = float(vr_raw)
        except ValueError:
            cards.append(
                base_card(
                    card_type="volume_spike",
                    trade_date=td,
                    subject=ts_code,
                    rule_id="volume_spike_bad_ratio",
                    metrics={"volume_ratio_raw": vr_raw},
                    threshold_source="convention",
                    coverage="insufficient_data",
                    data_as_of=td,
                    direction=None,
                )
            )
            continue

        try:
            pct = float(pct_raw) if pct_raw else 0.0
        except ValueError:
            pct = 0.0

        if vr < VOLUME_RATIO_TH or abs(pct) < PCT_CHG_ABS_TH:
            continue

        cards.append(
            base_card(
                card_type="volume_spike",
                trade_date=td,
                subject=ts_code,
                rule_id="volume_spike_dual_threshold",
                metrics={
                    "volume_ratio": vr,
                    "pct_chg": pct,
                    "turnover_rate": float(row["turnover_rate"])
                    if (row.get("turnover_rate") or "").strip()
                    else None,
                    "total_mv": float(row["total_mv"])
                    if (row.get("total_mv") or "").strip()
                    else None,
                    "close": float(row["close"])
                    if (row.get("close") or "").strip()
                    else None,
                    "volume_ratio_th": VOLUME_RATIO_TH,
                    "pct_chg_abs_th": PCT_CHG_ABS_TH,
                },
                threshold_source="convention",
                coverage="covered",
                data_as_of=td,
                direction=None,
            )
        )
    return cards
