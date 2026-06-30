#!/usr/bin/env python3
"""U6a：kss-mcp —— 把 bridge 命令集包成本地 stdio MCP server。

让 Claude Code / 任意 MCP client 直接查 KSS 实时数据、跑同款逻辑（与 SwiftUI app 同一
dispatch 面，零逻辑 fork）。读命令直接成 tool；写/run/cron-mutation **paper-only by default**：
仅当 `KSS_MCP_LIVE=1`（启动读一次，防 agent 中途翻转，KTD5）才注册写 tool，且每调用须 confirm=True。

威胁模型（诚实记录）：本地 stdio，同用户进程可达。被注入的 agent 在 live 模式下经 confirm 仍能触发
写命令 —— confirm 来自 agent 自身不来自人。自用单机可接受；要更强须 app 侧一次性 token（deferred）。

运行：MCP client 以 `python scripts/kss_mcp.py` 启动（需 fastmcp，进 U0 lock / U2 bootstrap venv）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kss_app_bridge as bridge  # noqa: E402
from fastmcp import FastMCP  # noqa: E402

# KTD5：启动时读一次，运行中不重读 —— agent 无法中途把只读会话翻成可写。
_LIVE = os.environ.get("KSS_MCP_LIVE") == "1"

mcp = FastMCP("kss")


def _call(command: str, args: list[str] | None = None):
    """统一经 bridge.dispatch；与 app 同一逻辑面、同样的代码渲染数字（不经 LLM 复述）。"""
    return bridge.dispatch(command, args or [])


# ---- 读命令（ungated）----

@mcp.tool
def get_snapshot() -> dict:
    """今日总览快照：指数、推荐股、复盘/回测计数等。"""
    return _call("snapshot")


@mcp.tool
def get_stock(symbol: str) -> dict:
    """单只股票明细（日线派生指标）。symbol 形如 688114.SH。"""
    return _call("stock", [symbol])


@mcp.tool
def get_sector_rotation(date: str = "") -> dict:
    """板块热点轮动快照；date 为 YYYYMMDD，空则最新。"""
    return _call("sector-rotation", [date] if date else [])


@mcp.tool
def get_sector_rotation_history(limit: int = 30) -> dict:
    """板块轮动历史归档（近 limit 条）。"""
    return _call("sector-rotation-history", [str(limit)])


@mcp.tool
def get_theme_leaders() -> dict:
    """主题龙头梯队。"""
    return _call("theme-leaders")


@mcp.tool
def get_discovery_candidates() -> dict:
    """潜力股发现候选合并。"""
    return _call("get-discovery-candidates")


@mcp.tool
def get_perilla_enrichment(symbol: str) -> dict:
    """紫苏叶个股富化：机构持仓动态 + PE 历史分位 + 美股对标估值。

    symbol 形如 688012.SH，须在紫苏叶列表(core/main)内；否则返回
    not_in_perilla_list。各数据源独立降级（缺失标 unavailable/no_peer）。
    """
    return _call("perilla-enrichment", [symbol])


@mcp.tool
def get_paper_summary() -> dict:
    """模拟盘推荐跟踪汇总。"""
    return _call("paper-summary")


@mcp.tool
def get_report(path: str) -> dict:
    """读 storage 下某 markdown 报告（路径相对 state root，受穿越护栏约束）。"""
    return _call("report", [path])


@mcp.tool
def get_trends_month(month: str) -> dict:
    """趋势页某月日历。month 为 YYYY-MM。"""
    return _call("trends-month", [month])


@mcp.tool
def get_trends_day(date: str) -> dict:
    """趋势页某日明细。date 为 YYYY-MM-DD。"""
    return _call("trends-day", [date])


@mcp.tool
def list_cron() -> dict:
    """列出计划任务及其状态。"""
    return _call("cron-list")


@mcp.tool
def get_data_catalog() -> dict:
    """全量数据资产字典：每个数据集的 列/含义/粒度/最近日期/路径（自动反射 schema + 手维含义 overlay）。"""
    return _call("data-catalog")


@mcp.tool
def get_orientation() -> dict:
    """一次调用上手：dispatch 命令图 + run_task 白名单 + 数据目录摘要 + cron 新鲜度 + 关键文档指针。"""
    return _call("orientation")


@mcp.tool
def list_recipes() -> dict:
    """编排剧本目录(确定性复盘 DAG):每条 name/desc/write/args。选一条用 run_recipe 跑。"""
    return {"recipes": _call("recipe-list")}   # 包 dict:MCP structured_content 须非 list


@mcp.tool
def run_recipe(name: str, args: str = "") -> dict:
    """跑一条只读复盘剧本(如 explain_stock_today)。args 为 JSON 串(如 '{"symbol":"688114.SH"}')。
    只读公开;write 剧本经此拒(write 执行路径 defer 到 #4)。"""
    return _call("run-recipe", [name, args])


@mcp.tool
def research_search(query: str, limit: int = 5) -> dict:
    """搜索外部资料作为 evidence-only 背景；不得覆盖 KSS 本地工具真值。"""
    return _call("research-search", [query, str(limit)])


@mcp.tool
def research_fetch(url: str, max_chars: int = 8000) -> dict:
    """抓取一个外部 URL 的 evidence-only 摘要；带 SSRF 护栏。"""
    return _call("research-fetch", [url, str(max_chars)])


@mcp.tool
def research_bundle(query: str, limit: int = 3, max_chars_per_source: int = 3000) -> dict:
    """搜索并抓取外部证据 bundle，返回 URL/retrievedAt/sourceTier/excerpt ledger。"""
    return _call("research-bundle", [query, str(limit), str(max_chars_per_source)])


# ---- 写命令（paper-only：仅 KSS_MCP_LIVE=1 注册，且每调用须 confirm）----

if _LIVE:
    @mcp.tool
    def run_task(name: str, confirm: bool = False) -> dict:
        """执行一个数据任务（如 update_data / paper_trade）。须 confirm=True；走 bridge run 白名单。"""
        if not confirm:
            return {"error": "live_write_requires_confirm",
                    "hint": "重调并传 confirm=True 以执行写操作"}
        return _call("run", [name])

    @mcp.tool
    def cron_action(label: str, action: str, confirm: bool = False) -> dict:
        """对计划任务执行 rerun/enable/disable。须 confirm=True；走白名单。"""
        if action not in {"rerun", "enable", "disable"}:
            return {"error": "bad_action", "hint": "action ∈ rerun|enable|disable"}
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "传 confirm=True"}
        return _call(f"cron-{action}", [label])


if __name__ == "__main__":
    mcp.run()
