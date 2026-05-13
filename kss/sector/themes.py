"""「十五五」主题映射加载器 —— 板块名 → 主题桶反向索引.

YAML 配置驻留 :file:`storage/themes_15th_5y.yaml`，用户可热改.
commentary 模块每次跑加载一次，按主题 bucket 聚合行业 / 概念资金流，
作为 LLM prompt 的「主题」段落输入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_YAML = Path(__file__).resolve().parents[2] / "storage" / "themes_15th_5y.yaml"


class ThemeConfigError(ValueError):
    """YAML 文件存在但格式不合法（不是文件缺失）."""


@dataclass
class ThemeBucket:
    """单个主题桶.

    Attributes:
        name: 主题中文名（如「半导体」/「AI算力」），用于 LLM prompt 标题.
        industries: 东财行业 ``name`` 候选列表（与 ``snapshot.industry['name']`` 匹配）.
        concepts: 同花顺概念 ``name`` 候选列表（与 ``snapshot.concept['name']`` 匹配）.
    """

    name: str
    industries: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)


def load_themes(path: Path | str | None = None) -> dict[str, ThemeBucket]:
    """加载 themes YAML，返回主题名 → ThemeBucket 字典.

    Args:
        path: YAML 文件路径；``None`` 走默认 ``storage/themes_15th_5y.yaml``.

    Returns:
        主题字典；文件缺失 → 空字典（commentary 仍可跑，只是失去主题维度）.

    Raises:
        ThemeConfigError: YAML 语法错误或顶层不是 ``themes:`` 字典.
    """
    p = Path(path) if path is not None else _DEFAULT_YAML
    if not p.exists():
        logger.warning("themes YAML 不存在: %s（commentary 将无主题段）", p)
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
        industries = _coerce_str_list(body.get("industries"))
        concepts = _coerce_str_list(body.get("concepts"))
        if not industries and not concepts:
            logger.warning(
                "主题 %s industries 与 concepts 均为空，跳过", theme_name,
            )
            continue
        buckets[theme_name] = ThemeBucket(
            name=theme_name,
            industries=industries,
            concepts=concepts,
        )

    if not buckets:
        logger.warning("themes YAML 未加载到任何有效主题: %s", p)

    return buckets


def _coerce_str_list(val: object) -> list[str]:
    """把 ``industries`` / ``concepts`` 字段转成 ``list[str]``，过滤掉空 / 非字符串项."""
    if val is None:
        return []
    if not isinstance(val, list):
        logger.warning("期望 list，得到 %s，跳过", type(val).__name__)
        return []
    out: list[str] = []
    for item in val:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        else:
            logger.warning("非字符串板块名，跳过: %r", item)
    return out
