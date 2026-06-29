from __future__ import annotations

from kss.memory.mmr import mmr_rerank
from kss.memory.types import Candidate


def test_mmr_empty_and_single():
    assert mmr_rerank([]) == []
    one = Candidate("a", "横盘震荡", None, score=0.5)
    assert mmr_rerank([one]) == [one]


def test_lambda_one_is_score_sort():
    items = [
        Candidate("low", "低分", None, score=0.1),
        Candidate("high", "高分", None, score=0.9),
        Candidate("mid", "中分", None, score=0.5),
    ]
    assert [item.id for item in mmr_rerank(items, lambda_=1.0)] == ["high", "mid", "low"]


def test_mmr_separates_near_duplicates():
    items = [
        Candidate("a", "仍横盘震荡 持仓保留", None, score=1.0),
        Candidate("b", "仍横盘震荡 持仓保留", None, score=0.95),
        Candidate("c", "放量突破 观察前高", None, score=0.9),
    ]
    ranked = mmr_rerank(items, lambda_=0.7)
    assert [item.id for item in ranked[:2]] == ["a", "c"]
