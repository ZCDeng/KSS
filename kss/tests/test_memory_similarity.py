from __future__ import annotations

from kss.memory.similarity import jaccard, tokenize


def test_tokenize_chinese_bigrams():
    assert tokenize("横盘震荡") == {"横盘", "盘震", "震荡"}


def test_tokenize_mixed_english_and_chinese():
    tokens = tokenize("MACD缩柱")
    assert "macd" in tokens
    assert "缩柱" in tokens


def test_jaccard_edges_and_overlap():
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"横盘"}, set()) == 0.0
    assert jaccard("横盘震荡", "横盘震荡") == 1.0
    score = jaccard("横盘震荡", "横盘整理")
    assert 0 < score < 1
