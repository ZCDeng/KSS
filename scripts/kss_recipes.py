#!/usr/bin/env python3
"""U3(plan 003)：编排剧本注册表 —— 确定性复盘 DAG。

把高频多步复盘固化成 Python 函数剧本,agent 选剧本不自由发挥(省 token、可复现步骤、可测)。
设计见 docs/plans/2026-06-22-003-feat-mcp-orchestration-recipes-plan.md。

约定(KTD-1):
  - 每条 recipe 是 fn(call, **args) -> dict;`call` 由 bridge 注入(read 路径注入受限 call:
    碰 WRITE_COMMANDS 即 raise,见 bridge._make_read_only_call,KTD-3)。
  - 本模块**不 import bridge**(收注入的 call,避免循环 import,KTD-4)。纯 stdlib。
  - recipe **绝不调 LLM**(KTD-2):只串 read 命令、打包真值;叙事交调用方 agent。
  - 透传子命令返回的 LLM 自由文本(commentary 等)须标 provenance:"llm_prior"(KTD-2),
    用 tag_llm_text() 包裹,下游当未验证叙事而非可引真值。
  - 部分失败:某步抛 → 该区 {error,hint} + 顶层 partial/failedSteps(KTD-7,见 _gather)。

注册:RECIPES[name] = {"desc", "write": bool, "args": [..], "fn": callable}。
  write 字段本轮仅作前向兼容 + orientation 路由提示(无 write 执行路径,defer 到 #4)。
"""
from __future__ import annotations

from typing import Any, Callable

# 透传 LLM 自由文本字段的标记 key + 已知 LLM 文本字段名(KTD-2)。
LLM_TEXT_FIELDS = ("commentary",)


def tag_llm_text(value: Any) -> dict:
    """把既有 LLM 文本包成带 provenance 标的对象,下游当未验证叙事处理。"""
    return {"text": value, "provenance": "llm_prior"}


def _scrub_llm_fields(obj: Any) -> Any:
    """递归把已知 LLM 文本字段(commentary)标 provenance:llm_prior。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in LLM_TEXT_FIELDS and not (isinstance(v, dict) and v.get("provenance")):
                out[k] = tag_llm_text(v)
            else:
                out[k] = _scrub_llm_fields(v)
        return out
    if isinstance(obj, list):
        return [_scrub_llm_fields(v) for v in obj]
    return obj


def _gather(steps: dict[str, Callable[[], Any]]) -> dict:
    """跑各区采集步骤,单步失败降级 + 顶层 partial/failedSteps(KTD-7)。
    steps: 区名 → 取值 thunk(内部调注入的 call)。call 抛(含受限 call 的 PermissionError /
    归一化后的错误)即该区标 error,不连坐其余区。"""
    out: dict[str, Any] = {}
    failed: list[str] = []
    for region, thunk in steps.items():
        try:
            out[region] = _scrub_llm_fields(thunk())
        except Exception as exc:  # noqa: BLE001  受限 call 已把 SystemExit 归一为普通异常
            out[region] = {"error": str(exc), "hint": f"{region} 采集失败,该区降级"}
            failed.append(region)
    if failed:
        out["partial"] = True
        out["failedSteps"] = failed
    return out


# ---------------------------------------------------------------------------
# 种子只读剧本(U2)
# ---------------------------------------------------------------------------

def _explain_stock_today(call: Callable, symbol: str) -> dict:
    """解释这只今天为什么上榜:个股(含当日 move)+ 板块上下文(含其板块归属)+ 主题龙头 +
    发现候选命中(含 reason/score)。一束足以回答「为什么上榜」,agent 无需追加调用(R7)。"""
    def _discovery_hit() -> dict:
        merged = call("get-discovery-candidates")
        cands = merged.get("candidates", merged) if isinstance(merged, dict) else merged
        cands = cands if isinstance(cands, list) else []
        hit = next((c for c in cands if isinstance(c, dict) and c.get("ts_code") == symbol), None)
        if hit is None:
            return {"hit": None, "queried": True}   # 未命中 ≠ 出错(KTD-7)
        return {"hit": hit, "queried": True}

    return _gather({
        "stock": lambda: call("stock", [symbol]),
        "sectorContext": lambda: call("sector-rotation"),
        "themeLeaders": lambda: call("theme-leaders"),
        "discoveryHit": _discovery_hit,
    })


def _sector_context(call: Callable, date: str = "") -> dict:
    """板块上下文:当前(或指定日)轮动快照 + 近期历史 + 主题龙头梯队。"""
    return _gather({
        "rotation": lambda: call("sector-rotation", [date] if date else []),
        "history": lambda: call("sector-rotation-history", ["30"]),
        "themeLeaders": lambda: call("theme-leaders"),
    })


def _news_collect(call: Callable, scene: str = "盘前") -> dict:
    """舆情采集:多源(微博/雪球/财联社/格隆汇/X)→ 带 provenance 的结构化证据。

    走 seek HTTP MCP(非 bridge),故不用注入的 ``call``;**不调 LLM**(R2)。
    采集真值交下游 pipeline(去重→集中度→情绪/催化→渲染)。延迟 import 避免
    把 fastmcp 常驻进 recipe 模块(本模块保持轻)。"""
    from kss.news.collect import collect_news

    return collect_news(scene)


RECIPES: dict[str, dict] = {
    "news_collect": {
        "desc": "舆情采集:微博/雪球/财联社/格隆汇/X → 带 provenance 的结构化证据(不调 LLM)",
        "write": False,
        "args": ["scene"],
        "fn": _news_collect,
    },
    "explain_stock_today": {
        "desc": "解释这只今天为什么上榜:个股+板块上下文+主题龙头+发现候选命中(含 reason/score)",
        "write": False,
        "args": ["symbol"],
        "fn": _explain_stock_today,
    },
    "sector_context": {
        "desc": "板块上下文:轮动快照+近期历史+主题龙头",
        "write": False,
        "args": ["date"],
        "fn": _sector_context,
    },
}
