"""入选理由确定性合成单测。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_reason_with_rank_and_industry() -> None:
    s = b._selection_reason(
        {"strategy": "log_mv", "rank_position": 2},
        rank=2,
        industry="电子",
    )
    assert s == "log_mv 截面排名 #2 · 电子"


def test_reason_without_industry() -> None:
    s = b._selection_reason({"strategy": "log_mv"}, rank=1, industry="")
    assert s == "log_mv 截面排名 #1"
    assert "·" not in s


def test_reason_missing_rank() -> None:
    assert b._selection_reason({}, rank=None, industry="电子") == "—"
    assert b._selection_reason({"rank_position": 0}, industry="x") == "—"
