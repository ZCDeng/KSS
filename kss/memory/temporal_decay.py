from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .types import Candidate

_DATED_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_.+\.md$")
MS_PER_DAY = 86_400_000


def decay_lambda(half_life_days: float) -> float:
    if half_life_days <= 0:
        return 0.0
    return math.log(2.0) / half_life_days


def decay_multiplier(age_days: float, half_life_days: float = 30.0) -> float:
    lam = decay_lambda(half_life_days)
    if age_days <= 0 or lam <= 0:
        return 1.0
    return math.exp(-lam * age_days)


def parse_date_from_filename(name: str | Path) -> datetime | None:
    filename = Path(name).name
    match = _DATED_RE.match(filename)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def timestamp_ms_for_date(value: str | datetime) -> int:
    if isinstance(value, str):
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def is_evergreen(candidate: Candidate) -> bool:
    return candidate.timestamp_ms is None


def apply_decay(
    candidates: Iterable[Candidate],
    *,
    now_ms: int,
    half_life_days: float = 30.0,
) -> list[Candidate]:
    out: list[Candidate] = []
    for cand in candidates:
        if cand.timestamp_ms is None:
            out.append(cand)
            continue
        age_days = (now_ms - cand.timestamp_ms) / MS_PER_DAY
        score = cand.score * decay_multiplier(age_days, half_life_days)
        out.append(replace(cand, score=score))
    return out
