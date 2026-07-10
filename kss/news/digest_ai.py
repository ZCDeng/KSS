"""资讯雷达 AI digest —— 单赛道 LLM 要点提炼。

薄壳封装 ``kss.llm.openai_client.LLMClient.complete``，把 25 条 RSS 资讯打包成
prompt 调 LLM 生成 3-5 条要点。Sync fire-and-forget（不流式、不走 chat-turn）。

设计要点：
- 不复用 chat-turn 流式长连（chat-turn 是 streaming + confirm gate 的交互协议，
  digest 是单轮 sync 调用，shape 完全不同；我们只复用底层 LLMClient 与凭据）
- 超时由 LLMClient(timeout=…, max_retries=0) 强制；pro 模型默认 90s
- 25 条截断 + 字符数兜底（≤12k chars）
- 沉淀库写入完全由调用方触发，本模块只返回 text
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from kss.llm.openai_client import LLMClient, LLMUnavailable

logger = logging.getLogger(__name__)

_MAX_ITEMS = 25
_MAX_CHARS = 12_000  # prompt 上限兜底（25 条平均 ~480 char，留余量给指令）
# deepseek-v4-pro + 25 条列表，30s 偏紧；与 rewrite 中文/投研超时对齐
_TIMEOUT_SEC = 90.0
_MAX_RETRIES = 0

_SYSTEM_PROMPT = """你是「资讯雷达」要点提炼助手。任务：从用户提供的资讯列表中提炼「今日要点」。

要求：
1. 输出 3-5 条要点，每条 ≤ 40 个中文字符或 ≤ 80 个英文字符
2. 用「- 」开头列点，不要多余前后缀、不要编号
3. 只客观陈述重要事件 / 趋势，不推荐标的、不预测涨跌、不构成投资建议
4. 优先选取跨多个独立来源印证的事件；孤立或单源事件可不收录
5. 如资讯质量过低（标题含糊、无来源），输出「该赛道近期无重大事件」一条即可
"""

_USER_TEMPLATE = """以下是「{track_name}」赛道近期资讯（{n} 条，已按时间倒序）：

{items}

请提炼「今日要点」。"""


def _format_items(items: list[dict[str, Any]]) -> str:
    """把 [{title,url,time,source,summary}] 格式化为可读文本块."""
    lines: list[str] = []
    for it in items[:_MAX_ITEMS]:
        time = (it.get("time") or "—").strip() or "—"
        source = (it.get("source") or "—").strip() or "—"
        title = (it.get("title") or "").strip()
        if not title:
            continue
        line = f"[{time}] {source}｜{title}"
        lines.append(line)
    return "\n".join(lines)


def build_prompt(track_name: str, items: list[dict[str, Any]]) -> tuple[str, str]:
    """构造 (system, user) prompt。

    Returns:
        (system_prompt, user_prompt)。
    """
    formatted = _format_items(items)
    # 字符数兜底：超过 _MAX_CHARS 时按比例再截断
    if len(formatted) > _MAX_CHARS:
        keep = max(_MAX_ITEMS // 2, _MAX_ITEMS * len(formatted) // _MAX_CHARS // _MAX_CHARS)
        formatted = _format_items(items[:keep])
    user_prompt = _USER_TEMPLATE.format(
        track_name=track_name,
        n=min(len(items), _MAX_ITEMS),
        items=formatted,
    )
    return _SYSTEM_PROMPT, user_prompt


def run_digest(
    track_key: str,
    track_name: str,
    items: list[dict[str, Any]],
    *,
    force: bool = False,
    items_already_truncated: bool = False,
) -> dict[str, Any]:
    """调 LLM 提炼要点。**不写沉淀库**，由调用方决定是否写入。

    Args:
        track_key: 赛道 key（如 "ai"）。
        track_name: 赛道显示名（如 "AI / 大模型"）。
        items: 资讯列表，每条 {title,url,time,source,summary?}.
        force: True 时强制重新生成（否则 25 条同 track 同日会复用之前的 result）。
        items_already_truncated: True 时跳过 items[:25] 截断（Swift 端已截）。

    Returns:
        dict: 至少含 {text, model, generated_at}；失败时含 {error, error_type}.
    """
    # 截断（如果调用方没做）
    if not items_already_truncated:
        items = items[:_MAX_ITEMS]

    # items 为空
    if not items:
        return {
            "text": "",
            "skipped": True,
            "model": "",
            "generated_at": "",
        }

    # 缓存命中（force=False 且 items 数量一致时复用）
    if not force:
        from kss.storage.notes import intel_digest_exists, read_intel_digest_response

        existing = intel_digest_exists(track_key)
        if existing is not None:
            cached_text = read_intel_digest_response(track_key)
            if cached_text:
                return {
                    "text": cached_text,
                    "model": "(cached)",
                    "generated_at": "",
                    "from_cache": True,
                    "cached_path": str(existing),
                }

    # 调 LLM
    system_prompt, user_prompt = build_prompt(track_name, items)
    try:
        client = LLMClient(timeout=_TIMEOUT_SEC, max_retries=_MAX_RETRIES)
        text = client.complete(system=system_prompt, user=user_prompt)
    except LLMUnavailable as exc:
        msg = str(exc)
        # 简单分类 error_type 给 UI 用
        lower = msg.lower()
        if "auth" in lower or "401" in msg or "403" in msg:
            err_type = "auth"
        elif "429" in msg or "rate" in lower:
            err_type = "rate_limit"
        elif "timeout" in lower or "timed out" in lower:
            err_type = "timeout"
        else:
            err_type = "server"
        logger.warning("[digest-ai] %s/%s failed: %s", track_key, track_name, exc)
        return {
            "error": msg,
            "error_type": err_type,
            "text": "",
        }

    import datetime as _dt
    return {
        "text": text,
        "model": os.getenv("KSS_LLM_MODEL") or "(unknown)",
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "item_count": min(len(items), _MAX_ITEMS),
        "prompt": user_prompt,  # 留给沉淀库写入时记录
    }


def parse_items_payload(raw: str | bytes) -> list[dict[str, Any]]:
    """bridge 调用时解析 args[0]（JSON 字符串）为 items 列表."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    obj = json.loads(raw)
    if not isinstance(obj, list):
        raise ValueError("items payload must be a JSON array")
    return obj


# ---------------------------------------------------------------------------
# 12 赛道全景热点（独立 LLM，与单赛道 digest 分离）
# ---------------------------------------------------------------------------

_PANORAMA_TIMEOUT_SEC = 90.0
_PANORAMA_MAX_TITLES_PER_TRACK = 4
_PANORAMA_MAX_CHARS = 10_000

_PANORAMA_SYSTEM = """你是「资讯雷达」全景热点助手。根据用户提供的 12 个赛道近期头条，归纳**跨赛道**当日热点。

要求：
1. 输出 4-7 条短 bullet，用「- 」开头；每条点明赛道或跨赛道主题
2. 只客观陈述，不荐股、不预测涨跌
3. 优先跨源/跨赛道共振；避免逐赛道机械复读标题
4. 全文控制在约 200-350 中文字；不要寒暄与前后缀
"""

_PANORAMA_USER = """以下为各赛道最新标题采样（已截断）：

{body}

请输出今日 12 赛道全景热点（「- 」列表）。"""


def run_panorama(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """跨赛道全景摘要。

    tracks: ``[{key, name, titles: [str, ...]}, ...]``
    """
    if not tracks:
        return {"text": "", "error": "empty tracks", "error_type": "client"}

    blocks: list[str] = []
    for t in tracks:
        name = str(t.get("name") or t.get("key") or "—")
        titles = t.get("titles") or []
        if not isinstance(titles, list):
            titles = []
        lines = [f"  · {str(x).strip()}" for x in titles[:_PANORAMA_MAX_TITLES_PER_TRACK] if str(x).strip()]
        if not lines:
            lines = ["  · （无标题）"]
        blocks.append(f"【{name}】\n" + "\n".join(lines))
    body = "\n\n".join(blocks)[:_PANORAMA_MAX_CHARS]
    user = _PANORAMA_USER.format(body=body)

    try:
        client = LLMClient(timeout=_PANORAMA_TIMEOUT_SEC, max_retries=_MAX_RETRIES)
        text = client.complete(system=_PANORAMA_SYSTEM, user=user)
    except LLMUnavailable as exc:
        msg = str(exc)
        lower = msg.lower()
        if "timeout" in lower or "timed out" in lower:
            err_type = "timeout"
        elif "auth" in lower or "401" in msg or "403" in msg:
            err_type = "auth"
        else:
            err_type = "server"
        logger.warning("[digest-ai] panorama failed: %s", exc)
        return {"text": "", "error": msg, "error_type": err_type}

    import datetime as _dt

    return {
        "text": text,
        "model": os.getenv("KSS_LLM_MODEL") or "(unknown)",
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "track_count": len(tracks),
    }