"""涨停板聚合：maxBoard / sealRate / tiers（纯函数，可单测）。"""

from __future__ import annotations

from typing import Any


def aggregate_limit_board(df: Any) -> dict[str, Any] | None:
    """从 limit_list_d DataFrame 聚合 limitBoard 结构。

    - maxBoard: 涨停侧（limit=='U'）limit_times 最大值
    - sealRate: open_times==0 的涨停 / 全部涨停（若无 open_times 则 None）
    - tiers: [{level, count}] 按连板高度
    """
    if df is None or getattr(df, "empty", True):
        return None
    if "limit_times" not in df.columns:
        return None

    work = df
    if "limit" in work.columns:
        up = work[work["limit"] == "U"]
    else:
        up = work
    if up is None or getattr(up, "empty", True):
        return {
            "maxBoard": 0,
            "tiers": [],
            "total": 0,
            "sealRate": None,
            "breakRate": None,
        }

    times = up["limit_times"].fillna(0).astype(float)
    max_board = int(times.max()) if len(times) else 0
    tiers_map: dict[int, int] = {}
    for v in times:
        level = int(v)
        if level <= 0:
            continue
        tiers_map[level] = tiers_map.get(level, 0) + 1
    tiers = [
        {"level": level, "count": tiers_map[level]}
        for level in sorted(tiers_map.keys())
    ]
    total = int(len(up))
    seal_rate = None
    break_rate = None
    if "open_times" in up.columns and total > 0:
        sealed = int((up["open_times"].fillna(0).astype(float) == 0).sum())
        seal_rate = sealed / total
        break_rate = 1.0 - seal_rate

    return {
        "maxBoard": max_board,
        "tiers": tiers,
        "total": total,
        "sealRate": seal_rate,
        "breakRate": break_rate,
    }
