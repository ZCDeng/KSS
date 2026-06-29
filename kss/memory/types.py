from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    id: str
    text: str
    timestamp_ms: int | None
    base_score: float = 0.0
    score: float = 0.0
