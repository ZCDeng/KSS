"""LLM 驱动的板块复盘投顾文字生成.

数据 → prompt → LLM → HTML 文本，失败兜底走结构化纯文本.

调用入口：:func:`generate_commentary`.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from kss.llm import LLMClient, LLMUnavailable
from kss.sector.data_fetcher import SectorSnapshot
from kss.sector.kcb_overlay import KcbOverlay
from kss.sector.themes import ThemeBucket

logger = logging.getLogger(__name__)


# Telegram HTML 模式允许的标签白名单（其他标签会被 strip）.
_ALLOWED_HTML_TAGS: frozenset[str] = frozenset({"b", "i", "u", "code", "s"})

# Telegram 单条消息上限 4096 字符；留 96 字给推送 metadata.
_MAX_MSG_LEN: int = 4000

# 行业 / 概念 prompt context 中保留的 top 行数 —— 控制 token 量级.
_TOP_N_INDUSTRY: int = 15
_TOP_N_CONCEPT: int = 15

# Commentary system role —— 角色 + 格式 + 长度 + 内容约束.
_SYSTEM_PROMPT: str = """\
你是一位资深 A 股投资顾问，关注中线（5-20 个交易日）持仓配置。\
请基于下方结构化数据写一段当日 A 股复盘短文，作为给客户的盘后通讯.

【硬性输出格式约束】
- 总长 1000-1500 中文字
- 不要使用任何 Markdown 语法（不要 **/__/```/[]）
- 只能使用 4 个 HTML 标签：<b>（重点）、<i>（板块名/概念名）、<u>（关键数字）、<code>（代码）
- 不要在散文中夹任何裸的 < 或 > 字符
- 段落之间空一行
- 严禁编造数据；所有数字必须复述输入 JSON 里的数值；输入没有的字段不要提及

【必须包含的 6 段】
1. <b>大盘总览</b>：沪深主板 / 创业板 / 科创板 各自涨跌幅 + 成交额（如有量比一并提及）
2. <b>热点行业</b>：今天最强的 2-3 个行业 + 资金有没有跟（净流入率、大单买入率）
3. <b>概念轮动</b>：今天发动的概念 vs 滞涨/退潮的概念，简单点评
4. <b>科技板块资金走向</b>：聚焦半导体 / AI / 算力 / 新能源 / 生物医药 / 高端制造 / 数字经济等科技主线
5. <b>「十五五」六大主题</b>：按下方 themes 数据逐个点评（哪些主题资金涌入、哪些资金离场）
6. <b>加减仓建议</b>：明确给出 2-3 条可执行建议，每条格式：「板块名/概念名 + 加仓/减仓/观望/持有 + 一句理由」.
   注：只到板块/概念粒度，不要给具体股票代码；建议要谨慎，标注「中线视角」.

【风险口径】
- 建议偏稳健，避免「强烈推荐」「必涨」「全仓」等用词
- 数据缺失维度（input 标注 "数据未到位"）→ 文中诚实说出，不要编造
- KCB 池子（科创板活跃股池子）持仓数大的板块更值得用户关注，在叙述中带一句
"""


# ====================================================================== #
# 公开入口
# ====================================================================== #


def generate_commentary(
    date_yyyymmdd: str,
    snapshot: SectorSnapshot,
    indices: dict[str, dict[str, float]],
    themes: dict[str, ThemeBucket],
    overlay: KcbOverlay,
    client: LLMClient | None = None,
) -> str:
    """生成单日板块复盘投顾文字.

    Args:
        date_yyyymmdd: 复盘日期，``YYYYMMDD``.
        snapshot: 板块数据快照（行业 / 概念 / 北向）.
        indices: 大盘指数量价（沪深 / 创业 / 科创），:func:`fetch_market_indices` 输出.
        themes: 主题映射，:func:`load_themes` 输出.
        overlay: KCB 池子覆盖度（用于"科创池子有 N 只持仓"标注）.
        client: LLM 客户端；``None`` 时按需 lazy 初始化。失败 → fallback 文本.

    Returns:
        HTML 格式投顾文本，长度 ≤ ``_MAX_MSG_LEN``.
        LLM 失败 → 返回结构化 fallback 文本（仍含日期 + Top 数据 + ⚠️ 标记）.
    """
    context = build_context(snapshot, indices, themes, overlay)
    theme_metrics = compute_theme_metrics(snapshot, themes, overlay)

    system, user = render_prompt(date_yyyymmdd, context, theme_metrics)

    try:
        if client is None:
            client = LLMClient()
        text = client.complete(system=system, user=user)
    except LLMUnavailable as exc:
        logger.warning("[commentary] LLM 不可用，走 fallback: %s", exc)
        return fallback_text(date_yyyymmdd, snapshot, indices, reason=str(exc))

    text = _sanitize_html(text)
    text = clip_to_max_len(text)
    return text


# ====================================================================== #
# Prompt 上下文构造
# ====================================================================== #


def build_context(
    snapshot: SectorSnapshot,
    indices: dict[str, dict[str, float]],
    themes: dict[str, ThemeBucket],
    overlay: KcbOverlay,
) -> dict[str, Any]:
    """把 snapshot + indices + themes 序列化成紧凑 dict（喂给 LLM）.

    Returns:
        含 ``indices`` / ``top_industries`` / ``top_concepts`` / ``northbound``
        / ``missing`` 五个键的 dict.
    """
    ctx: dict[str, Any] = {
        "indices": indices,  # 已是 dict[alias -> metrics]
        "top_industries": _top_industries(snapshot, overlay, _TOP_N_INDUSTRY),
        "top_concepts": _top_concepts(snapshot, overlay, _TOP_N_CONCEPT),
        "northbound": snapshot.northbound,
        "missing": list(snapshot.missing),
    }
    # indices 全缺 → 在 missing 显式标注，让 LLM 知道这个维度没数据
    if not indices:
        ctx["missing"].append("market_indices")
    # themes 全缺 → 标记，让 LLM 不要硬写「十五五」段
    if not themes:
        ctx["missing"].append("themes")
    return ctx


def _top_industries(
    snapshot: SectorSnapshot,
    overlay: KcbOverlay,
    n: int,
) -> list[dict[str, Any]]:
    """取行业涨幅 Top-N，附带资金流指标 + KCB 池子持仓数."""
    if snapshot.industry is None or snapshot.industry.empty:
        return []
    df = snapshot.industry.copy()
    if "pct_change" not in df.columns:
        return []
    df = df.sort_values("pct_change", ascending=False).head(n)
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "pct_change": _safe_float(r.get("pct_change")),
            "net_amount_rate": _safe_float(r.get("net_amount_rate")),
            "buy_elg_amount_rate": _safe_float(r.get("buy_elg_amount_rate")),
            "kcb_count": overlay.count_for_industry(name),
        })
    return rows


def _top_concepts(
    snapshot: SectorSnapshot,
    overlay: KcbOverlay,
    n: int,
) -> list[dict[str, Any]]:
    """取概念涨幅 Top-N，附带净流入 + KCB 池子持仓数."""
    if snapshot.concept is None or snapshot.concept.empty:
        return []
    df = snapshot.concept.copy()
    if "pct_change" not in df.columns:
        return []
    df = df.sort_values("pct_change", ascending=False).head(n)
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "pct_change": _safe_float(r.get("pct_change")),
            "net_amount": _safe_float(r.get("net_amount")),  # 单位「亿」
            "kcb_count": overlay.count_for_concept(name),
        })
    return rows


def _safe_float(v: object) -> float | None:
    """容错 cast；None / 不可 cast → None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # pandas NaN
    if f != f:  # type: ignore[comparison-overlap]
        return None
    return f


# ====================================================================== #
# 主题维度聚合
# ====================================================================== #


def compute_theme_metrics(
    snapshot: SectorSnapshot,
    themes: dict[str, ThemeBucket],
    overlay: KcbOverlay,
) -> dict[str, dict[str, Any]]:
    """按主题桶聚合行业 + 概念的当日表现.

    每个主题返回：

    - ``avg_industry_pct``: 主题对应行业的平均涨跌幅（无匹配 → None）
    - ``avg_concept_pct``: 主题对应概念的平均涨跌幅（无匹配 → None）
    - ``matched_industries``: 在 snapshot 里真的找到的行业名 list
    - ``matched_concepts``: 在 snapshot 里真的找到的概念名 list
    - ``kcb_count``: 主题对应板块在 KCB 池子的累计持仓数（不去重）
    """
    if not themes:
        return {}

    out: dict[str, dict[str, Any]] = {}
    ind_df = snapshot.industry
    cnt_df = snapshot.concept

    for theme_name, bucket in themes.items():
        # 行业匹配
        matched_inds: list[dict[str, Any]] = []
        if ind_df is not None and not ind_df.empty and "name" in ind_df.columns:
            sub = ind_df[ind_df["name"].isin(bucket.industries)]
            for _, r in sub.iterrows():
                matched_inds.append({
                    "name": str(r["name"]),
                    "pct_change": _safe_float(r.get("pct_change")),
                    "net_amount_rate": _safe_float(r.get("net_amount_rate")),
                })

        # 概念匹配
        matched_concs: list[dict[str, Any]] = []
        if cnt_df is not None and not cnt_df.empty and "name" in cnt_df.columns:
            sub = cnt_df[cnt_df["name"].isin(bucket.concepts)]
            for _, r in sub.iterrows():
                matched_concs.append({
                    "name": str(r["name"]),
                    "pct_change": _safe_float(r.get("pct_change")),
                    "net_amount": _safe_float(r.get("net_amount")),
                })

        kcb_count = sum(overlay.count_for_industry(n) for n in bucket.industries)
        kcb_count += sum(overlay.count_for_concept(n) for n in bucket.concepts)

        out[theme_name] = {
            "matched_industries": matched_inds,
            "matched_concepts": matched_concs,
            "avg_industry_pct": _mean_pct(matched_inds),
            "avg_concept_pct": _mean_pct(matched_concs),
            "kcb_count": kcb_count,
        }
    return out


def _mean_pct(rows: list[dict[str, Any]]) -> float | None:
    vals = [r["pct_change"] for r in rows if r.get("pct_change") is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ====================================================================== #
# Prompt 渲染
# ====================================================================== #


def render_prompt(
    date_yyyymmdd: str,
    context: dict[str, Any],
    theme_metrics: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """组装 system + user prompt 字符串.

    Returns:
        ``(system, user)`` —— system 走 :data:`_SYSTEM_PROMPT`，user 是 JSON
        化的当日数据 + 任务指令.
    """
    import json
    payload = {
        "date": date_yyyymmdd,
        "indices": context["indices"],
        "top_industries": context["top_industries"],
        "top_concepts": context["top_concepts"],
        "northbound": context["northbound"],
        "themes_15th_5y": theme_metrics,
        "missing_dimensions": context["missing"],
    }
    # ensure_ascii=False 保留中文；indent 让 LLM 容易解析
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)

    user = (
        f"以下是 {date_yyyymmdd} A 股板块复盘的结构化数据，"
        f"请按 system 指定的 6 段格式写一段投顾复盘短文。"
        f"missing_dimensions 列出的维度数据未到位，相关段落请简短带过或说明.\n\n"
        f"```json\n{payload_json}\n```"
    )
    return _SYSTEM_PROMPT, user


# ====================================================================== #
# Fallback —— LLM 不可用时的结构化兜底
# ====================================================================== #


def fallback_text(
    date_yyyymmdd: str,
    snapshot: SectorSnapshot,
    indices: dict[str, dict[str, float]],
    reason: str = "",
) -> str:
    """LLM 不可用时的兜底文本.

    Returns:
        HTML 格式的简化复盘：日期 + 指数 + Top 行业/概念名 + ⚠️ 标记.
    """
    date_disp = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"
    parts: list[str] = [
        f"<b>📊 板块复盘 {html.escape(date_disp)}</b>（自动复盘失败，降级为简表）",
        "",
    ]

    if indices:
        idx_line = "<b>大盘指数</b>："
        idx_line += " / ".join(
            f"{html.escape(alias)} {_fmt_pct(m.get('pct_chg'))}"
            for alias, m in indices.items()
        )
        parts.append(idx_line)
    else:
        parts.append("<b>大盘指数</b>：数据未到位")

    parts.append("")

    if snapshot.industry is not None and not snapshot.industry.empty:
        top_inds = snapshot.industry.sort_values(
            "pct_change", ascending=False,
        ).head(5)
        names = " / ".join(
            f"{html.escape(str(r['name']))} {_fmt_pct(r.get('pct_change'))}"
            for _, r in top_inds.iterrows()
        )
        parts.append(f"<b>Top 行业</b>：{names}")
    else:
        parts.append("<b>Top 行业</b>：行业数据未到位")

    if snapshot.concept is not None and not snapshot.concept.empty:
        top_cs = snapshot.concept.sort_values(
            "pct_change", ascending=False,
        ).head(5)
        names = " / ".join(
            f"{html.escape(str(r['name']))} {_fmt_pct(r.get('pct_change'))}"
            for _, r in top_cs.iterrows()
        )
        parts.append(f"<b>Top 概念</b>：{names}")
    else:
        parts.append("<b>Top 概念</b>：概念数据未到位")

    if snapshot.missing:
        parts.append("")
        parts.append(
            f"⚠️ 缺失数据：<code>{html.escape(', '.join(snapshot.missing))}</code>"
        )

    parts.append("")
    parts.append("⚠️ <i>LLM 复盘生成失败，请人工查看完整数据.</i>")
    if reason:
        parts.append(f"<i>原因：{html.escape(reason[:120])}</i>")

    return "\n".join(parts)


def _fmt_pct(v: object) -> str:
    f = _safe_float(v)
    if f is None:
        return "—"
    sign = "+" if f >= 0 else ""
    return f"{sign}{f:.2f}%"


# ====================================================================== #
# HTML 清洗 + 长度兜底
# ====================================================================== #


def _sanitize_html(text: str) -> str:
    """LLM 输出后处理：去 markdown 字符 + 只保留白名单 HTML 标签.

    防御性措施，防止 LLM 偶尔输出 `**bold**` / `<script>` 等导致 Telegram
    HTML parser 拒收（参考 ``docs/solutions/telegram_markdown_v1_silent_drop.md``
    的踩坑经验：silent failure 难诊断，最好提前 sanitize）.
    """
    # 1) 去掉 Markdown 强调符号
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<u>\1</u>", text)
    # 单星 / 单下划线 italic：转 <i>
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    # 去除 markdown code fence（LLM 偶尔会包整段）
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)

    # 2) 白名单标签过滤：未在白名单的 <tag>...</tag> 转义掉
    text = re.sub(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", _filter_tag, text)
    return text


def _filter_tag(match: re.Match[str]) -> str:
    """re.sub 回调：白名单标签放行，其他转义为字面文本."""
    full = match.group(0)
    tag = match.group(2).lower()
    if tag in _ALLOWED_HTML_TAGS:
        return full
    return html.escape(full)


def clip_to_max_len(text: str, limit: int = _MAX_MSG_LEN) -> str:
    """超长后置裁剪：按段落优先丢中间段，保留首尾.

    简化实现：先用 4000 字硬截，从尾部回退找到上一个换行符；
    实际场景里 LLM 受 prompt 长度约束极少超.
    """
    if len(text) <= limit:
        return text
    suffix = "\n…（已截断）"
    target = limit - len(suffix)
    truncated = text[:target]
    # 回退到最近的双换行（段落边界）
    cut = truncated.rfind("\n\n")
    if cut > target - 200:  # 段落边界还在合理范围内
        truncated = truncated[:cut]
    return truncated + suffix
