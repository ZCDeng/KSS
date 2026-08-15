"""comboSearch CLI 客户端 —— 接替已下线的 seek MCP(2026-08-15)。

seek 容器(``127.0.0.1:8643``)连同 agentos-stack 一起下线,本模块以同样的接口
(``reach`` / ``is_alive``)接住 ``kss.news.collect`` 的调用,底层换成本机
``combosearch`` CLI(Homebrew 装,内部按 exa/tavily/anspire/bocha 阶梯路由)。

设计要点(与 ``seek_client`` 保持同一契约,调用方无需感知换源):
- ``reach(tool, **args)``:**永不因传输/超时抛异常**,失败返回 ``ok=False`` 的降级
  结构,让 ``collect_news`` 记 ``failedSteps`` 而不连坐其余源。
- ``is_alive(timeout)``:探 CLI 是否可执行,不可用返回 ``False`` 而非抛。
- 输出**格式化成旧 seek 的文本形态**,好让 ``kss.news.collect`` 里既有的
  ``parse_search`` / ``parse_weibo`` 原样复用,不动解析层。
- CLI 路径 / 超时 / 每源条数走环境变量(``KSS_COMBOSEARCH_BIN`` /
  ``KSS_COMBOSEARCH_TIMEOUT`` / ``KSS_COMBOSEARCH_LIMIT``),不硬编码。launchd
  的 PATH 极简,故默认解析绝对路径。

工具映射::

    bocha_web_search / anspire_web_search  -> combosearch search  <query>
    reach_weibo_search                     -> combosearch social  <query> --platform weibo
    reach_twitter_search                   -> combosearch social  <query> --platform x
    reach_weibo_hot                        -> combosearch social  <财经宽词> --platform weibo

``reach_weibo_hot`` 是近似:comboSearch 没有"热榜"接口,改用财经宽词搜微博,因此
**拿不到热度值**(evidence-item 的 ``heat`` 为空),集中度只按出现次数算。宽词可用
``KSS_COMBOSEARCH_WEIBO_HOT_QUERY`` 覆盖。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

DEFAULT_BIN = "/opt/homebrew/bin/combosearch"
DEFAULT_TIMEOUT = 45.0
DEFAULT_LIMIT = 10
DEFAULT_WEIBO_HOT_QUERY = "A股 股市 板块 行情"


def _bin() -> str:
    return os.environ.get("KSS_COMBOSEARCH_BIN") or shutil.which("combosearch") or DEFAULT_BIN


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _degraded(tool: str, error: str) -> dict[str, Any]:
    return {"ok": False, "text": "", "structured": None, "error": error, "tool": tool}


# ---- 输出格式化(对齐 kss.news.collect 里既有 parser 的期望格式) ----

# 抓回来的正文里若自带"来源："/"标题："字样,会被 parse_search 的正则抢先命中,
# 把 URL 解析成一段中文(实测:摘要含"来源：中国科学院..."时 url 字段就成了那串中文)。
# 字段值里一律把这些标签的冒号换成空格,只留我们自己写的那三行当分隔锚点。
_FIELD_LABELS = re.compile(r"(标题|摘要|来源)[:：]")
# 分块锚点是独占一行的 ---,正文里的连字符行同样会误切块。
_HR_LINE = re.compile(r"(?m)^\s*-{3,}\s*$")
# 微博正文自带"热度：1234"时会被 parse_weibo 当成真热度读走并从标题里抹掉。
_HEAT_LABEL = re.compile(r"(热度)[:：]")


def _sanitize_field(text: str) -> str:
    """清掉会干扰 ``parse_search`` 分块/取值的标签与分隔线,并压平换行。"""
    s = _HR_LINE.sub(" ", text or "")
    s = _FIELD_LABELS.sub(r"\1 ", s)
    return " ".join(s.split())


def _sanitize_weibo_body(text: str) -> str:
    """微博正文清洗:压平换行(否则破坏 ``N.`` 行结构),并让正文里自带的"热度:"
    不被 ``parse_weibo`` 当成真热度读走。"""
    return _HEAT_LABEL.sub(r"\1 ", " ".join((text or "").split()))


def _fmt_search(results: list[dict[str, Any]]) -> str:
    """格式化成 ``parse_search`` 认的 ``标题：/摘要：/来源：`` 块,``---`` 分隔。"""
    blocks: list[str] = []
    for r in results:
        title = _sanitize_field(r.get("title") or "")
        summary = _sanitize_field(r.get("snippet") or "")
        if not summary:
            # search 结果常把正文放 content、snippet 留空,截前 300 字当摘要
            summary = _sanitize_field(r.get("content") or "")[:300]
        url = (r.get("url") or "").strip()
        if not (title or summary):
            continue
        blocks.append(f"标题：{title or summary[:40]}\n摘要：{summary}\n来源：{url}")
    return "\n---\n".join(blocks)


def _fmt_weibo(results: list[dict[str, Any]]) -> str:
    """格式化成 ``parse_weibo`` 认的 ``N. <正文>`` + 次行 URL。

    注意:微博条目的 ``title`` 是**博主名**、``snippet`` 才是正文,故取 snippet 上行,
    取不到才回落 title,否则采回来一堆人名。热度 comboSearch 不提供,整行不写"热度:",
    ``parse_weibo`` 对缺失热度返回 ``None``,契约允许。
    """
    lines: list[str] = []
    n = 0
    for r in results:
        body = _sanitize_weibo_body(r.get("snippet") or "")
        if not body:
            body = _sanitize_weibo_body(r.get("title") or "")
        if not body:
            continue
        n += 1
        lines.append(f"{n}. {body}")
        url = (r.get("url") or "").strip()
        if url:
            lines.append(f"   {url}")
    return "\n".join(lines)


# ---- 工具映射 ----

def _argv_for(tool: str, args: dict[str, Any], limit: int) -> tuple[list[str], str] | None:
    """返回 ``(argv, kind)``;kind 决定用哪个 formatter。未知工具返回 ``None``。"""
    query = str(args.get("query") or "").strip()

    if tool in ("bocha_web_search", "anspire_web_search"):
        if not query:
            return None
        return ([_bin(), "search", query, "--json", "--limit", str(limit)], "search")

    if tool == "reach_weibo_search":
        if not query:
            return None
        return ([_bin(), "social", query, "--platform", "weibo", "--json", "--limit", str(limit)], "weibo")

    if tool == "reach_twitter_search":
        if not query:
            return None
        return ([_bin(), "social", query, "--platform", "x", "--json", "--limit", str(limit)], "search")

    if tool == "reach_weibo_hot":
        q = os.environ.get("KSS_COMBOSEARCH_WEIBO_HOT_QUERY") or DEFAULT_WEIBO_HOT_QUERY
        return ([_bin(), "social", q, "--platform", "weibo", "--json", "--limit", str(limit)], "weibo")

    return None


def reach(
    tool: str,
    *,
    timeout: float | None = None,
    init_timeout: float | None = None,  # noqa: ARG001 - 兼容 seek_client 签名,CLI 无握手阶段
    **args: Any,
) -> dict[str, Any]:
    """同步调一个采集工具,签名与 ``seek_client.reach`` 一致。

    返回 ``{"ok": bool, "text": str, "structured": Any, "error": str | None,
    "tool": str}``。CLI 缺失 / 超时 / 非零退出 / JSON 解析失败一律降级,不抛。
    """
    to = _float_env("KSS_COMBOSEARCH_TIMEOUT", DEFAULT_TIMEOUT) if timeout is None else timeout
    limit = _int_env("KSS_COMBOSEARCH_LIMIT", DEFAULT_LIMIT)

    plan = _argv_for(tool, args, limit)
    if plan is None:
        return _degraded(tool, f"unsupported tool or missing query: {tool}")
    argv, kind = plan

    if not os.path.exists(argv[0]):
        return _degraded(tool, f"combosearch CLI not found: {argv[0]}")

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=to, check=False)
    except subprocess.TimeoutExpired:
        return _degraded(tool, f"TimeoutExpired: {to}s")
    except Exception as exc:  # noqa: BLE001 - 进程层任何失败都降级,不外抛
        return _degraded(tool, f"{type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        return _degraded(tool, f"exit {proc.returncode}: {(proc.stderr or '').strip()[:200]}")

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return _degraded(tool, f"JSONDecodeError: {exc}")

    results = payload.get("results") or []
    if not isinstance(results, list):
        results = []
    text = _fmt_weibo(results) if kind == "weibo" else _fmt_search(results)

    return {
        "ok": True,
        "text": text,
        "structured": payload,
        "error": None,
        "tool": tool,
    }


def is_alive(timeout: float = 5.0) -> bool:  # noqa: ARG001 - 无网络往返,留参数兼容 seek_client 签名
    """探活:CLI 存在且可执行。不可用返回 ``False``,永不抛。

    只查可执行位,不跑子命令——comboSearch 没有 ``--version``,任何无效子命令都以
    退出码 1 打 usage(实测),拿它当探活会恒判不可用;而真跑一次搜索既慢又费配额。
    单源真实失败由 ``reach`` 的降级结构接住,不需要探活替它把关。
    """
    b = _bin()
    return bool(b) and os.path.isfile(b) and os.access(b, os.X_OK)
