#!/usr/bin/env python3
"""U2(plan 004)：薄·工具调用 loop —— 寄居 sidecar 的多轮复盘 loop。

模型 → tool_calls → 读经 #3 受限 call、写**只发意图**(request_write,由 sidecar reader 任务执行)
→ 喂回 → 多轮至无 tool_call 或达上限。金融真值由 bridge 代码渲染,loop 不复述。

安全核心(KTD-4,doc-review round-1+2):**本模块代码路径里没有任何写 `dispatch` 调用**。
碰 `WRITE_COMMANDS` 只 `await request_write(...)`(意图帧)并 await 结果;真正的写 `bridge.dispatch`
由 sidecar 的 reader 任务在收到 Swift 人工 `approved` 后亲自执行(U3)。比「loop 持 token 受限执行器」更硬:
无 token 可伪造、无写执行代码可被 LLM tool_call 误触。读路径走 #3 `_make_read_only_call`(碰写命令即 raise)。

红线(KTD-2/4 code-review checklist):本模块 **不得 import kss_sidecar**(保调用图边界);
**不得调 bridge.dispatch 做写**(只读经受限 call)。AUTO_TASKS 默认空,免确认与否由 reader 定(见下注)。

AUTO_TASKS 设计取舍(doc-review 张力消解):plan R3 文字说 AUTO「放行」、KTD-4 说 loop 无写 dispatch。
两者冲突时取**更强的、过两轮 review 的 KTD-4**:AUTO 成员也走 `request_write`,由 reader **跳过人工 tap
自动批准**(仍查 _CHAT_LOOP_LIVE、仍 reader 执行)。loop 路径因此对所有写零 dispatch,可静态断言。
AUTO_TASKS 默认空 → 该自动路径默认休眠;准入 = 人工调用图审计「文件系统只读」(KTD-4)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

# bridge:读经受限 call;WRITE_COMMANDS 分级。**绝不**用 bridge.dispatch 做写(KTD-4)。
import kss_app_bridge as bridge
import kss_recipes  # provenance 标记复用(#3)
from kss.llm.chat_client import ChatClient
from kss.llm.sanitizer import scan_for_injection

logger = logging.getLogger(__name__)

# 准入「文件系统只读」的 run 任务可由 reader 自动批准(免人工 tap)。默认空(KTD-4 R3)。
AUTO_TASKS: frozenset[str] = frozenset()

_DEFAULT_MAX_STEPS = 8          # 步数上限(KTD-6 Q2 初始值)
_DEFAULT_TURN_TIMEOUT = 240.0   # 单轮总超时秒(多轮放宽,KTD-6)


# ---------------------------------------------------------------------------
# 工具目录 —— 映射 LLM function-calling 工具 → bridge 命令(与 MCP 同源面)。
# command ∈ WRITE_COMMANDS 即视为写(走 request_write);否则读(走受限 call)。
# ---------------------------------------------------------------------------

def _spec(name, command, desc, params=None, order=()):
    return {"name": name, "command": command, "desc": desc,
            "params": params or {}, "order": list(order)}


_STR = {"type": "string"}
TOOL_SPECS: list[dict[str, Any]] = [
    _spec("get_orientation", "orientation", "一次上手:命令图+数据目录+剧本+文档指针(建议首调)"),
    _spec("get_snapshot", "snapshot", "今日总览快照:指数/推荐股/复盘计数"),
    _spec("get_stock", "stock", "单只股票明细(日线派生指标)。symbol 如 688008.SH",
          {"symbol": _STR}, ["symbol"]),
    _spec("get_sector_rotation", "sector-rotation", "板块热点轮动快照;date 为 YYYYMMDD 空则最新",
          {"date": _STR}, ["date"]),
    _spec("get_sector_rotation_history", "sector-rotation-history", "板块轮动历史近 limit 条",
          {"limit": {"type": "string", "description": "条数,如 30"}}, ["limit"]),
    _spec("get_theme_leaders", "theme-leaders", "主题龙头梯队"),
    _spec("get_discovery_candidates", "get-discovery-candidates", "潜力股发现候选合并"),
    _spec("get_paper_summary", "paper-summary", "模拟盘推荐跟踪汇总"),
    _spec("get_report", "report", "读 storage 下 markdown 报告(相对 state root,受穿越护栏)",
          {"path": _STR}, ["path"]),
    _spec("get_data_catalog", "data-catalog", "全量数据资产字典:列/含义/粒度/最近日期/路径"),
    _spec("get_trends_month", "trends-month", "趋势页某月日历。month 为 YYYY-MM",
          {"month": _STR}, ["month"]),
    _spec("get_trends_day", "trends-day", "趋势页某日明细。date 为 YYYY-MM-DD",
          {"date": _STR}, ["date"]),
    _spec("list_cron", "cron-list", "列出计划任务及状态"),
    _spec("list_recipes", "recipe-list", "编排剧本目录(确定性复盘 DAG)"),
    _spec("run_recipe", "run-recipe",
          "跑只读复盘剧本(如 explain_stock_today)。args 为 JSON 串如 {\"symbol\":\"688008.SH\"}",
          {"name": _STR, "args": _STR}, ["name", "args"]),
    # ---- 写工具:经 request_write,loop 不执行(KTD-4)----
    _spec("run_task", "run",
          "执行数据任务(白名单,如 update-cs-data / refresh-sector-rotation / paper-summary)。**写操作,须人工确认**",
          {"task": _STR}, ["task"]),
    _spec("cron_rerun", "cron-rerun", "重跑计划任务。**写操作,须人工确认**", {"label": _STR}, ["label"]),
    _spec("cron_enable", "cron-enable", "启用计划任务。**写操作,须人工确认**", {"label": _STR}, ["label"]),
    _spec("cron_disable", "cron-disable", "停用计划任务。**写操作,须人工确认**", {"label": _STR}, ["label"]),
]
_SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}


def build_tools_schema() -> list[dict[str, Any]]:
    """OpenAI function-calling tools 数组(DeepSeek 兼容)。"""
    out = []
    for s in TOOL_SPECS:
        required = [p for p in s["order"] if p in s["params"]
                    and "date" not in p and "args" != p and "limit" not in p]
        out.append({
            "type": "function",
            "function": {
                "name": s["name"], "description": s["desc"],
                "parameters": {"type": "object", "properties": s["params"],
                               "required": required},
            },
        })
    return out


def resolve_tool(name: str, args: dict[str, Any]) -> tuple[str, list[str]]:
    """工具名+args dict → (bridge command, 位置 args 列表)。未知工具 raise KeyError。"""
    spec = _SPEC_BY_NAME[name]
    pos: list[str] = []
    for key in spec["order"]:
        if key in args and args[key] is not None and str(args[key]) != "":
            pos.append(str(args[key]))
        else:
            pos.append("")   # 占位,bridge 命令多数容空(空 date→最新等)
    while pos and pos[-1] == "":   # 去尾部空,避免传无谓空参
        pos.pop()
    return spec["command"], pos


def is_write_command(command: str) -> bool:
    return command in bridge.WRITE_COMMANDS


def is_auto_task(command: str, args: list[str]) -> bool:
    """reader 用:run + 任务 ∈ AUTO_TASKS 可免人工 tap 自动批准(默认空→恒 False)。"""
    return command == "run" and bool(args) and args[0] in AUTO_TASKS


# ---------------------------------------------------------------------------
# 数字 provenance 守卫(KTD-5/R7)
# ---------------------------------------------------------------------------

import re as _re

_NUM = _re.compile(r"\d[\d,]*\.?\d*%?")


def number_guard(assistant_text: str, tool_results_text: str) -> list[str]:
    """扫 loop 自产正文里的数字,凡未在本轮任一 tool 结果出现 → 列为「未核实」(KTD-5)。
    纯检测 + 返回列表供上层 fail-loud 标记/日志;不静默改写正文。"""
    in_text = {m.group(0) for m in _NUM.finditer(assistant_text)}
    in_tools = tool_results_text
    unverified = sorted(n for n in in_text
                        if len(n.strip(".,%")) >= 2 and n not in in_tools
                        and n.rstrip("%") not in in_tools)
    return unverified


# ---------------------------------------------------------------------------
# loop 主体
# ---------------------------------------------------------------------------

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]
RequestWriteFn = Callable[..., Awaitable[dict[str, Any]]]


async def run_turn(
    messages: list[dict[str, Any]],
    emit: EmitFn,
    request_write: RequestWriteFn,
    *,
    chat_client: ChatClient | None = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
    turn_timeout: float = _DEFAULT_TURN_TIMEOUT,
) -> None:
    """跑一轮多步对话。emit 逐帧(await drain by caller);写经 request_write(loop 不 dispatch 写)。

    messages 末尾应含本轮 user 消息(调用方已 sanitize_user_text)。emit 的帧:
    chunk / tool_call / tool_done / confirm_required(由 request_write 内部 emit) / done / error。
    """
    client = chat_client or ChatClient()
    tools = build_tools_schema()
    read_call = bridge._make_read_only_call(bridge.dispatch)   # 读受限 call(碰写即 raise)
    convo = list(messages)
    deadline = time.monotonic() + turn_timeout
    tool_results_text: list[str] = []   # 本轮所有 tool 结果文本(数字守卫用)

    for step in range(max_steps):
        if time.monotonic() > deadline:
            await emit({"type": "done", "reason": "timeout",
                        "note": "已达单轮总超时,优雅终止"})
            return

        assistant_text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        had_error = False

        gen = client.stream_turn(convo, tools)
        # 阻塞 SDK 流在线程里逐事件取出 → 每次 await 让出事件循环,reader 任务得以并发收 confirm。
        while True:
            ev = await asyncio.to_thread(_next_event, gen)
            if ev is None:
                break
            etype = ev["type"]
            if etype == "text":
                assistant_text_parts.append(ev["text"])
                await emit({"type": "chunk", "text": ev["text"]})
            elif etype == "tool_call":
                tool_calls.append(ev)
            elif etype == "error":
                had_error = True
                await emit({"type": "error", "error": ev["error"]})
            # finish 事件:不额外处理,循环靠 None(StopIteration)收尾

        if had_error:
            await emit({"type": "done", "reason": "error"})
            return

        if not tool_calls:
            # 无工具 → 本轮终。数字守卫:loop 自产数字 vs 本轮 tool 结果。
            full_text = "".join(assistant_text_parts)
            unverified = number_guard(full_text, "\n".join(tool_results_text))
            if unverified:
                logger.warning("[chat-loop] 未核实数字(非 tool 真值): %s", unverified)
            await emit({"type": "done", "reason": "stop",
                        "numberGuard": {"unverified": unverified}})
            return

        # 把本轮 assistant(含 tool_calls)记入对话,再逐个执行工具。
        convo.append(_assistant_msg(assistant_text_parts, tool_calls))
        for tc in tool_calls:
            result = await _exec_tool(tc, read_call, request_write, emit)
            tool_results_text.append(result)
            convo.append({"role": "tool", "tool_call_id": tc.get("id") or tc["name"],
                          "name": tc["name"], "content": result})

    await emit({"type": "done", "reason": "max_steps",
                "note": f"已达步数上限 {max_steps},优雅终止"})


def _next_event(gen) -> dict[str, Any] | None:
    try:
        return next(gen)
    except StopIteration:
        return None


def _assistant_msg(text_parts: list[str], tool_calls: list[dict]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    msg["tool_calls"] = [
        {"id": tc.get("id") or tc["name"], "type": "function",
         "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args") or {})}}
        for tc in tool_calls
    ]
    return msg


async def _exec_tool(tc, read_call, request_write, emit) -> str:
    """执行单个 tool_call,返回喂回 LLM 的字符串结果。写经 request_write(loop 不 dispatch 写)。"""
    name = tc.get("name") or ""
    args = tc.get("args") or {}
    await emit({"type": "tool_call", "name": name, "args": args})
    try:
        command, pos = resolve_tool(name, args)
    except KeyError:
        return json.dumps({"error": "unknown_tool", "tool": name}, ensure_ascii=False)

    try:
        if is_write_command(command):
            # 写:只发意图,reader 任务执行(KTD-4)。loop 不调 dispatch 写。
            result = await request_write(command=command, args=pos,
                                         tool_name=name, tool_args=args)
        else:
            result = read_call(command, pos)   # #3 受限 call(碰写命令即 raise)
    except PermissionError as exc:
        result = {"error": "blocked_write", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001  单工具失败降级,不崩整轮
        logger.warning("[chat-loop] 工具 %s 执行失败: %s", name, exc)
        result = {"error": "tool_failed", "detail": str(exc)}

    scrubbed = kss_recipes._scrub_llm_fields(result)   # commentary 标 provenance:llm_prior
    text = json.dumps(scrubbed, ensure_ascii=False, default=str)
    scan_for_injection(text)   # R8:pattern 扫描(只告警,不截断,完整透传)
    await emit({"type": "tool_done", "name": name})
    return text


__all__ = [
    "run_turn", "build_tools_schema", "resolve_tool", "is_write_command",
    "is_auto_task", "number_guard", "AUTO_TASKS", "TOOL_SPECS",
]
