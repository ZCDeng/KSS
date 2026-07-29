"""U8: 提示词中每个工具名都在 TOOL_SPECS；无断裂引用。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_chat_loop as chat  # noqa: E402


def test_prompt_tools_subset_of_tool_specs() -> None:
    prompt = chat.load_system_prompt()
    # 匹配 `get_xxx` / `run_xxx` / `research_xxx` 等反引号工具名
    names = set(re.findall(r"`([a-z][a-z0-9_]{2,})`", prompt))
    tool_names = {s["name"] for s in chat.TOOL_SPECS}
    # 仅保留像工具名的（含下划线、常见前缀）
    candidates = {
        n
        for n in names
        if n.startswith(
            (
                "get_",
                "run_",
                "list_",
                "research_",
                "backtest_",
                "suggest_",
                "solidify_",
                "retire_",
                "propose_",
                "apply_",
                "surface_",
                "sync_",
                "cron_",
                "load_",
                "read_",
            )
        )
        or n in tool_names
    }
    # 过滤明显非工具：explain_stock_today 是 recipe 名
    recipe_like = {"explain_stock_today", "sector_context"}
    candidates -= recipe_like
    missing = sorted(candidates - tool_names)
    assert not missing, f"prompt 引用了 TOOL_SPECS 中不存在的工具: {missing}"
    assert "get_perilla_enrichment" not in prompt


def test_five_dimensions_point_to_callable_tools() -> None:
    prompt = chat.load_system_prompt()
    assert "get_signal_cards" in prompt
    assert "get_etf_radar" in prompt or "get_stock" in prompt
    assert "get_daily_review_archive" in prompt or "get_report" in prompt
    # 不设硬编码最少工具数
    assert not re.search(r"至少\s*\d+\s*个工具", prompt)
    assert not re.search(r"最少\s*\d+\s*次", prompt)
