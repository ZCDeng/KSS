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
