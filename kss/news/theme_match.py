"""舆情热词 → 「十五五」题材库主题的确定性匹配层（U7）.

设计契约（KTD3）：**纯确定性，无 embedding / LLM**。配置的 LLM 是 DeepSeek
（chat-only，无 `/embeddings`），且语义匹配会制造「假直达」（把「固态电池」错对到
「锂电池」→ 错票，违背 AE1）。故主路径只有两条：

1. **精确匹配**：热词本身就是库内主题名 / 行业名 / 概念名。
2. **同义词表匹配**：热词经人工维护的 :data:`_SYNONYMS` 映射到一个库内规范名。

命中以上任一即「直达」（``direct_hit=True``）；否则按 R8 降级链路由
（``board`` → ``keyword`` → ``none``），**绝不臆造主题**。

匹配用的「主题库」走 :func:`load_theme_library`：它与 :func:`kss.sector.themes.load_themes`
同源，但**保留 industries/concepts 皆空的纯宏观主题**（如「降息受益」），
使这类主题仍能按主题名被舆情热词直达——板块缺失留给下游 U8 落 R8 降级。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from kss.sector.themes import (
    ThemeBucket,
    ThemeConfigError,
    _coerce_str_list,
    _DEFAULT_YAML,
)

logger = logging.getLogger(__name__)

# 同义词表：舆情常见说法（左）→ 库内规范名（右，主题名 / 行业名 / 概念名之一）.
#
# 维护规范：右值必须是 themes_15th_5y.yaml 里真实出现的主题名或板块名，
# 否则 match_theme 解析不到、自动降级（fail loud：解析不到不会假装命中）。
# 只收「精确匹配漏掉、但语义等价确定」的说法，不收模糊近义（防假直达 / AE1）。
_SYNONYMS: dict[str, str] = {
    # —— AI / 算力 ——
    "ai": "AI算力",
    "人工智能": "AI算力",
    "算力": "AI算力",
    "大模型": "AI算力",
    "光模块": "AI算力",
    "cpo": "AI算力",
    "液冷": "AI算力",
    "英伟达": "AI算力",
    "昇腾": "AI算力",
    "chatgpt": "AI算力",
    # —— 半导体 ——
    "芯片": "半导体",
    "光刻机": "半导体",
    "存储芯片": "半导体",
    "国产芯片": "半导体",
    # —— 新能源 / 储能 ——
    "锂电池": "新能源储能",
    "锂电": "新能源储能",
    "储能": "新能源储能",
    "钠电池": "新能源储能",
    "光伏": "新能源储能",
    "风电": "新能源储能",
    "氢能": "新能源储能",
    "氢能源": "新能源储能",
    # —— 生物医药 ——
    "创新药": "生物医药",
    "生物医药": "生物医药",
    "cxo": "生物医药",
    "减肥药": "生物医药",
    "中药": "生物医药",
    "疫苗": "生物医药",
    # —— 具身智能 ——
    "机器人": "具身智能",
    "人形机器人": "具身智能",
    "减速器": "具身智能",
    # —— 工业母机·低空·航天 ——
    "低空经济": "工业母机·低空·航天",
    "evtol": "工业母机·低空·航天",
    "飞行汽车": "工业母机·低空·航天",
    "无人机": "工业母机·低空·航天",
    "商业航天": "工业母机·低空·航天",
    # —— 贵金属 ——
    "黄金": "贵金属",
    "金价": "贵金属",
    "金矿": "贵金属",
    "白银": "贵金属",
    # —— 能源（油气） ——
    "石油": "能源",
    "原油": "能源",
    "油价": "能源",
    "油气": "能源",
    "天然气": "能源",
    "页岩气": "能源",
    "opec": "能源",
    # —— 煤炭 ——
    "煤": "煤炭",
    "煤炭": "煤炭",
    "动力煤": "煤炭",
    "焦煤": "煤炭",
    # —— 有色金属 ——
    "有色": "有色金属",
    "铜": "有色金属",
    "铝": "有色金属",
    "稀土": "有色金属",
    "锂矿": "有色金属",
    "小金属": "有色金属",
    # —— 钢铁 ——
    "钢铁": "钢铁",
    "螺纹钢": "钢铁",
    "特钢": "钢铁",
    # —— 化工 ——
    "化工": "化工",
    "钛白粉": "化工",
    # —— 房地产 ——
    "地产": "房地产",
    "楼市": "房地产",
    "房企": "房地产",
    "保交楼": "房地产",
    # —— 消费 ——
    "消费": "消费",
    "白酒": "消费",
    "免税": "消费",
    "预制菜": "消费",
    # —— 农业 ——
    "农业": "农业",
    "猪": "农业",
    "猪肉": "农业",
    "养殖": "农业",
    "种业": "农业",
    "粮食": "农业",
    # —— 船舶航运 ——
    "船舶": "船舶航运",
    "造船": "船舶航运",
    "中船": "船舶航运",
    "航运": "船舶航运",
    "集运": "船舶航运",
    "海运": "船舶航运",
    # —— 银行 ——
    "银行": "银行",
    "国有大行": "银行",
    # —— 非银金融 ——
    "券商": "非银金融",
    "证券": "非银金融",
    "保险": "非银金融",
    "牛市旗手": "非银金融",
    # —— 稳定币 / 数字货币 ——
    "稳定币": "稳定币数字货币",
    "数字货币": "稳定币数字货币",
    "加密货币": "稳定币数字货币",
    "比特币": "稳定币数字货币",
    "区块链": "稳定币数字货币",
    "rwa": "稳定币数字货币",
    # —— 降息受益（纯宏观，库内无板块，仍可直达主题名） ——
    "降息": "降息受益",
    "降准": "降息受益",
    "加息": "降息受益",
    "美联储": "降息受益",
    "宽松": "降息受益",
    "利率": "降息受益",
}


def load_theme_library(path: Path | str | None = None) -> dict[str, ThemeBucket]:
    """加载题材库（匹配层专用），**保留 industries/concepts 皆空的宏观主题**.

    与 :func:`kss.sector.themes.load_themes` 同源同契约，唯一差异：后者会把
    空板块主题跳过（commentary 用不到空桶），本函数保留它们的主题名，
    使「降息受益」这类纯宏观主题仍能被舆情热词按名直达（板块缺失留给 U8 降级）。

    Args:
        path: YAML 文件路径；``None`` 走默认 ``storage/themes_15th_5y.yaml``.

    Returns:
        主题名 → ThemeBucket 字典；文件缺失 → 空字典.

    Raises:
        ThemeConfigError: YAML 语法错误或顶层不是 ``themes:`` 字典.
    """
    p = Path(path) if path is not None else _DEFAULT_YAML
    if not p.exists():
        logger.warning("themes YAML 不存在: %s（匹配层无题材库）", p)
        return {}

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ThemeConfigError(f"themes YAML 解析失败: {exc}") from exc

    if not isinstance(raw, dict) or "themes" not in raw:
        raise ThemeConfigError(
            f"themes YAML 顶层必须是含 'themes' 键的 dict，实际: {type(raw).__name__}"
        )

    themes_raw = raw["themes"]
    if not isinstance(themes_raw, dict):
        raise ThemeConfigError(
            f"themes 字段必须是 dict，实际: {type(themes_raw).__name__}"
        )

    buckets: dict[str, ThemeBucket] = {}
    for theme_name, body in themes_raw.items():
        if not theme_name or not isinstance(theme_name, str):
            logger.warning("主题名为空 / 非字符串，跳过: %r", theme_name)
            continue
        if not isinstance(body, dict):
            logger.warning("主题 %s 内容不是 dict，跳过", theme_name)
            continue
        buckets[theme_name] = ThemeBucket(
            name=theme_name,
            industries=_coerce_str_list(body.get("industries")),
            concepts=_coerce_str_list(body.get("concepts")),
        )
    return buckets


def _build_index(themes: dict[str, ThemeBucket]) -> dict[str, str]:
    """规范名（主题名 / 行业名 / 概念名）→ 所属主题名的反向索引（casefold 归一）.

    主题名优先，故主题名→自身的映射后写、不被板块名覆盖；板块名跨主题冲突时先到先得.
    """
    index: dict[str, str] = {}
    for name, bucket in themes.items():
        for board in list(bucket.industries) + list(bucket.concepts):
            key = board.strip().casefold()
            if key and key not in index:
                index[key] = name
    # 主题名最后写入，确保「主题名」永远解析到自身（即便与某板块名撞）.
    for name in themes:
        index[name.strip().casefold()] = name
    return index


def _all_boards(themes: dict[str, ThemeBucket]) -> list[str]:
    """题材库全部行业 + 概念板块名（用于降级链 board 路由）."""
    boards: list[str] = []
    for bucket in themes.values():
        boards.extend(bucket.industries)
        boards.extend(bucket.concepts)
    return boards


def _fallback_route(norm: str, themes: dict[str, ThemeBucket]) -> str:
    """非直达时按 R8 降级链给路由标签：``board`` → ``keyword`` → ``none``.

    ``board``：热词与某板块名互为子串（≥2 字），可退到板块层粗对；
    ``keyword``：仍是有效热词，退关键词搜索；``none``：空热词。
    """
    if not norm:
        return "none"
    for board in _all_boards(themes):
        b = board.strip()
        if len(b) >= 2 and (b in norm or norm in b):
            return "board"
    return "keyword"


def match_theme(
    hotword: str,
    themes: dict[str, ThemeBucket] | None = None,
) -> dict:
    """把单个舆情热词确定性匹配到库内主题.

    Args:
        hotword: 自然语言舆情热词 / 方向（如「黄金」「光模块」「固态电池」）.
        themes: 题材库；``None`` 走 :func:`load_theme_library`（保留空板块宏观主题）.

    Returns:
        dict::

            {
              "hotword": 原始热词,
              "theme": 命中主题名 | None,
              "matched_on": "exact" | "synonym" | "none",
              "direct_hit": bool,          # 是否直达库内主题
              "fallback": "board" | "keyword" | "none",  # 非直达时的 R8 降级路由
            }

        直达时 ``fallback`` 恒为 ``"none"``（无需降级）.
    """
    norm = (hotword or "").strip()
    base = {
        "hotword": hotword,
        "theme": None,
        "matched_on": "none",
        "direct_hit": False,
        "fallback": "none",
    }
    if not norm:
        return base

    if themes is None:
        themes = load_theme_library()
    if not themes:
        # 题材库为空：无法直达，退关键词.
        base["fallback"] = "keyword"
        return base

    index = _build_index(themes)
    key = norm.casefold()

    # 1) 精确匹配：热词本身是主题名 / 行业名 / 概念名.
    if key in index:
        return {
            "hotword": hotword,
            "theme": index[key],
            "matched_on": "exact",
            "direct_hit": True,
            "fallback": "none",
        }

    # 2) 同义词表匹配：热词 → 规范名 → 主题.
    canon = _SYNONYMS.get(key)
    if canon is not None:
        target = index.get(canon.strip().casefold())
        if target is not None:
            return {
                "hotword": hotword,
                "theme": target,
                "matched_on": "synonym",
                "direct_hit": True,
                "fallback": "none",
            }
        # 同义词右值不在库里：配置漂移，告警并降级（不假装命中）.
        logger.warning("同义词 %r → %r 不在题材库，降级处理", norm, canon)

    # 3) 非直达：按 R8 降级链路由.
    base["fallback"] = _fallback_route(norm, themes)
    return base
