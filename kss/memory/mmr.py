from __future__ import annotations

from typing import Sequence

from .similarity import jaccard
from .types import Candidate


def _normalize_scores(items: Sequence[Candidate]) -> dict[str, float]:
    if not items:
        return {}
    scores = [item.score for item in items]
    if all(0.0 <= score <= 1.0 for score in scores):
        return {item.id: item.score for item in items}
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return {item.id: 1.0 for item in items}
    return {item.id: (item.score - lo) / (hi - lo) for item in items}


def mmr_rerank(items: Sequence[Candidate], *, lambda_: float = 0.7) -> list[Candidate]:
    if not items:
        return []
    if len(items) == 1:
        return list(items)
    lam = min(1.0, max(0.0, lambda_))
    remaining = sorted(items, key=lambda item: (item.score, item.id), reverse=True)
    if lam >= 1.0:
        return remaining

    normalized = _normalize_scores(remaining)
    selected: list[Candidate] = []
    while remaining:
        best_idx = 0
        best_value: tuple[float, float, str] | None = None
        for idx, item in enumerate(remaining):
            max_sim = max((jaccard(item.text, picked.text) for picked in selected), default=0.0)
            mmr_score = lam * normalized[item.id] - (1.0 - lam) * max_sim
            value = (mmr_score, item.score, item.id)
            if best_value is None or value > best_value:
                best_value = value
                best_idx = idx
        selected.append(remaining.pop(best_idx))
    return selected


def dedupe_near_duplicates(
    items: Sequence[Candidate],
    *,
    threshold: float = 0.8,
) -> list[Candidate]:
    deduped: list[Candidate] = []
    for item in items:
        if any(jaccard(item.text, kept.text) >= threshold for kept in deduped):
            continue
        deduped.append(item)
    return deduped
