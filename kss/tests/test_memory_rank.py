from __future__ import annotations

from kss.memory.rank import rank
from kss.memory.temporal_decay import timestamp_ms_for_date
from kss.memory.types import Candidate


def _cand(id_: str, text: str, date: str) -> Candidate:
    return Candidate(id_, text, timestamp_ms_for_date(date))


def test_rank_query_match_beats_unmatched_recent_item():
    now = timestamp_ms_for_date("2026-06-20")
    items = [
        _cand("match", "MACD极值 放量突破", "2026-06-01"),
        _cand("recent", "缩量观望", "2026-06-19"),
    ]
    out = rank(items, query="MACD极值 放量", now_ms=now, top_k=1)
    assert out[0].id == "match"


def test_rank_mmr_collapses_repetitive_flat_days():
    now = timestamp_ms_for_date("2026-06-20")
    repetitive = [
        _cand(f"flat-{i}", "仍横盘震荡 持仓继续保留 不建议追高", f"2026-06-{10+i:02d}")
        for i in range(6)
    ]
    changes = [
        _cand("breakout", "放量突破 前高 带量持有", "2026-06-09"),
        _cand("risk", "MACD缩柱 温和回落 止损位上移", "2026-06-08"),
        _cand("fund", "大单净流入 资金10d改善", "2026-06-07"),
        _cand("support", "回踩MA20 支撑有效", "2026-06-06"),
    ]
    out = rank(repetitive + changes, query="横盘震荡 MACD极值 量能极值", now_ms=now, top_k=5)
    flat_count = sum(1 for item in out if item.id.startswith("flat-"))
    assert flat_count == 1
    assert {"breakout", "risk", "fund", "support"}.issubset({item.id for item in out})


def test_rank_without_query_uses_decay_and_mmr():
    now = timestamp_ms_for_date("2026-06-20")
    out = rank([
        _cand("old", "横盘震荡", "2026-05-01"),
        _cand("new", "放量突破", "2026-06-19"),
    ], query=None, now_ms=now, top_k=5)
    assert [item.id for item in out] == ["new", "old"]


def test_rank_short_and_empty_inputs():
    now = timestamp_ms_for_date("2026-06-20")
    one = _cand("one", "横盘震荡", "2026-06-19")
    assert rank([], query=None, now_ms=now) == []
    assert rank([one], query=None, now_ms=now, top_k=5) == [rank([one], query=None, now_ms=now, top_k=5)[0]]
