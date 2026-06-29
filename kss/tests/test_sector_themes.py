"""kss/sector/themes.py 单元测试."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kss.sector.themes import ThemeBucket, ThemeConfigError, load_themes


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "themes.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_happy_path_six_themes(tmp_path: Path) -> None:
    """6 个主题，每个含 industries + concepts。"""
    p = _write_yaml(tmp_path, """\
        themes:
          半导体:
            industries: [半导体]
            concepts: [半导体概念, 集成电路概念]
          AI算力:
            industries: [软件开发]
            concepts: [算力概念, AI服务器]
          新能源储能:
            industries: [电池]
            concepts: [储能概念]
          生物医药:
            industries: [化学制药]
            concepts: [创新药]
          高端制造:
            industries: [通用设备]
            concepts: [机器人概念]
          数字经济:
            industries: [计算机设备]
            concepts: [信创概念]
        """)
    themes = load_themes(p)
    assert len(themes) == 6
    assert isinstance(themes["半导体"], ThemeBucket)
    assert themes["半导体"].industries == ["半导体"]
    assert "集成电路概念" in themes["半导体"].concepts


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    """文件不存在 → 返回空 dict（commentary 走无主题降级），不抛."""
    themes = load_themes(tmp_path / "nonexistent.yaml")
    assert themes == {}


def test_load_yaml_syntax_error_raises(tmp_path: Path) -> None:
    """YAML 语法错 → 抛 ThemeConfigError."""
    p = tmp_path / "bad.yaml"
    p.write_text("themes:\n  半导体: [unclosed", encoding="utf-8")
    with pytest.raises(ThemeConfigError):
        load_themes(p)


def test_load_top_level_must_have_themes_key(tmp_path: Path) -> None:
    """顶层缺 themes 键 → 抛."""
    p = _write_yaml(tmp_path, """\
        wrong_key:
          x: 1
        """)
    with pytest.raises(ThemeConfigError, match="themes"):
        load_themes(p)


def test_load_skips_empty_theme(tmp_path: Path) -> None:
    """industries 与 concepts 均为空的主题 → 跳过，不出现在结果里."""
    p = _write_yaml(tmp_path, """\
        themes:
          有效:
            industries: [半导体]
            concepts: []
          空主题:
            industries: []
            concepts: []
        """)
    themes = load_themes(p)
    assert "有效" in themes
    assert "空主题" not in themes


def test_load_skips_non_string_board_name(tmp_path: Path) -> None:
    """concepts 列表里有非字符串项（如 null/int）→ 跳过该项，主题保留."""
    p = _write_yaml(tmp_path, """\
        themes:
          AI算力:
            industries: []
            concepts:
              - 算力概念
              - null
              - 123
              - AI服务器
        """)
    themes = load_themes(p)
    assert themes["AI算力"].concepts == ["算力概念", "AI服务器"]


def test_load_duplicate_concept_name_across_themes_allowed(tmp_path: Path) -> None:
    """同一概念名出现在多主题（如 数据中心 既算 AI 又算 数字经济）→ 都保留."""
    p = _write_yaml(tmp_path, """\
        themes:
          AI算力:
            industries: []
            concepts: [数据中心]
          数字经济:
            industries: []
            concepts: [数据中心]
        """)
    themes = load_themes(p)
    assert "数据中心" in themes["AI算力"].concepts
    assert "数据中心" in themes["数字经济"].concepts


def test_load_real_default_yaml_exists() -> None:
    """仓库内 storage/themes_15th_5y.yaml 真实加载（防 PR 回归）。

    舆情 digest 的 R15 把题材库从 7 个十五五科技主题扩域到含宏观/商品/消费域;
    其中 降息受益 无对应 board(industries/concepts 均空)被 load_themes 跳过,
    故实际加载 20 个。原 7 个科技主题须仍在(不被扩域挤掉)。"""
    themes = load_themes()
    assert len(themes) == 20  # 21 定义 − 1 个空主题(降息受益,R8 降级案)
    original_seven = {"半导体", "AI算力", "新能源储能", "生物医药", "具身智能", "工业母机·低空·航天", "数字经济"}
    assert original_seven <= set(themes.keys())
    assert {"贵金属", "能源", "房地产"} <= set(themes.keys())  # 扩域新主题抽样在位
