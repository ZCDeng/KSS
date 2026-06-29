from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from .mmr import dedupe_near_duplicates, mmr_rerank
from .score import keyword_score
from .temporal_decay import apply_decay
from .types import Candidate


def rank(
    candidates: Sequence[Candidate],
    *,
    query: str | None,
    now_ms: int,
    half_life_days: float = 30.0,
    mmr_lambda: float = 0.7,
    top_k: int = 5,
) -> list[Candidate]:
    if top_k <= 0 or not candidates:
        return []

    scored: list[Candidate] = []
    has_query = bool((query or "").strip())
    for cand in candidates:
        base = keyword_score(query, cand.text) if has_query else cand.base_score
        if not has_query and base == 0.0:
            base = 1.0
        scored.append(replace(cand, base_score=base, score=base))

    decayed = apply_decay(scored, now_ms=now_ms, half_life_days=half_life_days)
    decayed.sort(key=lambda item: (item.score, item.timestamp_ms or 0, item.id), reverse=True)
    pool = decayed[:max(top_k * 3, top_k)]
    reranked = mmr_rerank(pool, lambda_=mmr_lambda)
    diverse = dedupe_near_duplicates(reranked)

    if len(diverse) < top_k:
        kept = {item.id for item in diverse}
        diverse.extend(item for item in reranked if item.id not in kept)
    return diverse[:top_k]
