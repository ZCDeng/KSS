from __future__ import annotations

from .similarity import tokenize


def keyword_score(query: str | None, text: str) -> float:
    query_tokens = tokenize(query or "")
    if not query_tokens:
        return 0.0
    text_tokens = tokenize(text)
    if not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)
