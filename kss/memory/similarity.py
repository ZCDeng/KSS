from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> set[str]:
    lowered = (text or "").lower()
    tokens = set(_WORD_RE.findall(lowered))
    compact = re.sub(r"\s+", "", lowered)
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", compact)
    if len(compact) == 1:
        tokens.add(compact)
    elif len(compact) >= 2:
        tokens.update(compact[i:i + 2] for i in range(len(compact) - 1))
    return tokens


def jaccard(a: str | set[str], b: str | set[str]) -> float:
    left = a if isinstance(a, set) else tokenize(a)
    right = b if isinstance(b, set) else tokenize(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
