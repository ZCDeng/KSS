"""板块启发式评分 —— 热度 / 资金持续性 / 轮动信号.

三个独立函数，纯输入 → 输出，不做 IO. 配置从 kss.db 的 ``sector_review_config`` 表
加载（表/库缺失走默认；plan 2026-07-12-005 / U15 域割接自 storage/sector_review_config.json）.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from kss.storage.db import connect, ensure_schema

logger = logging.getLogger(__name__)

# 拒绝硬编码权重在代码里 —— 配置走文件，权重调优不改代码.
DEFAULT_CONFIG: dict[str, Any] = {
    "industry_heat_weights": {
        "pct_change": 0.5,
        "net_amount_rate": 0.3,
        "buy_elg_amount_rate": 0.2,
    },
    "concept_heat_weights": {
        "pct_change": 0.6,
        "net_amount": 0.4,
    },
    "top_n_industry": 5,
    "top_n_concept": 5,
    "top_n_flow": 3,
    "top_n_rotation": 5,
    "persistence_days": 3,
    "rotation_lookback_days": 3,
    "rotation_rank_jump_threshold": 50,
}


def load_config(db_path: str | Path | None = None) -> dict[str, Any]:
    """从 kss.db 加载评分配置，缺失/损坏走默认 + warning.

    Returns:
        合并了默认值的完整配置 dict（用户配置中缺失的键以 DEFAULT_CONFIG 补全）.
    """
    config = dict(DEFAULT_CONFIG)
    try:
        with connect(db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                "SELECT config_json FROM sector_review_config WHERE config_key='default'"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载 sector_review_config 失败: %s，回退到默认权重", exc)
        return config
    if row is None:
        logger.info("sector_review_config 未配置，使用默认权重")
        return config
    try:
        user = json.loads(row["config_json"])
        # 浅合并：用户键覆盖默认键
        config.update(user)
    except json.JSONDecodeError as exc:
        logger.warning("解析 sector_review_config 失败: %s，回退到默认权重", exc)
    return config


# ====================================================================== #
# Heat score
# ====================================================================== #


def _minmax_normalize(s: pd.Series) -> pd.Series:
    """将 Series 缩放到 [0, 1]；常数序列返回 0.5（中性占位）."""
    smin, smax = s.min(), s.max()
    if pd.isna(smin) or pd.isna(smax) or smax == smin:
        return pd.Series(0.5, index=s.index)
    return (s - smin) / (smax - smin)


def compute_heat_score(
    df: pd.DataFrame,
    weights: dict[str, float],
    name_col: str = "name",
) -> pd.DataFrame:
    """加权热度评分（线性 min-max 归一化后加权求和）.

    避免量纲偏置：原始 pct_change 量纲 ~ ±5，net_amount_rate 量纲 ~ ±5，
    但直接加权会让最大值的列主导分数；先归一化到 [0, 1] 再加权.

    Args:
        df: 板块单日数据，含 ``name_col`` 和 ``weights`` 的所有 key 列.
        weights: ``{column_name: weight}`` 字典. 权重不必和为 1（最终 score
            数值不影响排序）.
        name_col: 板块名列名.

    Returns:
        含原始列 + ``heat_score`` 列的新 DataFrame，按 ``heat_score`` 降序.
        输入为空 / 缺列时返回空 DataFrame（容错，不外抛）.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    missing = [c for c in weights if c not in df.columns]
    if missing:
        logger.warning("compute_heat_score 输入缺列 %s，返回空结果", missing)
        return pd.DataFrame()

    out = df.copy()
    score = pd.Series(0.0, index=out.index)
    for col, w in weights.items():
        score = score + _minmax_normalize(out[col]) * w
    out["heat_score"] = score
    return out.sort_values("heat_score", ascending=False).reset_index(drop=True)


# ====================================================================== #
# Flow persistence
# ====================================================================== #


def compute_flow_persistence(
    history: list[pd.DataFrame],
    name_col: str = "name",
    flow_col: str = "net_amount_rate",
) -> pd.DataFrame:
    """统计每个板块的「累计净流入 + 连续净流入天数」.

    Args:
        history: 历史板块单日 DataFrame 列表，**最近日在最后**.
            空列表 / 全 None 视为无历史，返回空结果.
        name_col: 板块名列名.
        flow_col: 资金净流入列名（默认 ``net_amount_rate``）.

    Returns:
        含 ``name`` / ``cum_inflow`` / ``persist_days`` 的 DataFrame，按
        ``cum_inflow`` 降序. 连续天数从最近日往回数，遇到 <= 0 即停.
    """
    if not history:
        return pd.DataFrame(columns=[name_col, "cum_inflow", "persist_days"])

    valid = [df for df in history if df is not None and not df.empty]
    if not valid:
        return pd.DataFrame(columns=[name_col, "cum_inflow", "persist_days"])

    # 累计净流入：所有日子按 name 聚合 sum
    long = pd.concat(
        [df[[name_col, flow_col]] for df in valid if flow_col in df.columns],
        ignore_index=True,
    )
    cum = long.groupby(name_col)[flow_col].sum().rename("cum_inflow")

    # 每板块时序流值（按 valid 顺序：旧 → 新）
    flows: dict[str, list[float]] = {}
    for df in valid:
        if flow_col not in df.columns:
            continue
        for _, row in df.iterrows():
            name = row[name_col]
            val = row[flow_col]
            flows.setdefault(name, []).append(
                float(val) if pd.notna(val) else 0.0
            )

    # 连续天数：从最近日（list 尾部）反向走，遇 <= 0 即停
    persist: dict[str, int] = {}
    for name, series in flows.items():
        count = 0
        for v in reversed(series):
            if v > 0:
                count += 1
            else:
                break
        persist[name] = count

    persist_series = pd.Series(persist, name="persist_days")
    persist_series.index.name = name_col  # 必须显式命名，否则 concat + reset_index 列名会丢
    result = pd.concat([cum, persist_series], axis=1).reset_index()
    result = result.fillna({"persist_days": 0, "cum_inflow": 0.0})
    result["persist_days"] = result["persist_days"].astype(int)
    return result.sort_values("cum_inflow", ascending=False).reset_index(drop=True)


# ====================================================================== #
# Rotation signal
# ====================================================================== #


def compute_rotation_signal(
    today: pd.DataFrame,
    past: pd.DataFrame,
    name_col: str = "name",
    flow_col: str = "net_amount_rate",
    rank_jump_threshold: int = 5,
) -> pd.DataFrame:
    """识别「轮动入场」信号：排名跃升 + 今日净流入.

    "板块今日按资金净流入率排名，与 N 日前对比" → 排名跃升 >= 阈值
    且今日净流入率 > 0 视为轮动入场信号.

    Args:
        today: 今日板块 DataFrame.
        past: N 日前的板块 DataFrame（用于对照排名）.
        name_col: 板块名列名.
        flow_col: 资金净流入列名.
        rank_jump_threshold: 触发阈值（排名上升至少多少位）.

    Returns:
        含 ``name`` / ``today_rank`` / ``past_rank`` / ``rank_jump`` /
        ``today_flow`` 的 DataFrame，按 rank_jump 降序. 无信号则返回空表.
    """
    if today is None or today.empty or past is None or past.empty:
        return pd.DataFrame(
            columns=[name_col, "today_rank", "past_rank", "rank_jump", "today_flow"]
        )
    if flow_col not in today.columns or flow_col not in past.columns:
        logger.warning("compute_rotation_signal 缺少 %s 列", flow_col)
        return pd.DataFrame(
            columns=[name_col, "today_rank", "past_rank", "rank_jump", "today_flow"]
        )

    today_ranked = today.copy()
    today_ranked["today_rank"] = today_ranked[flow_col].rank(ascending=False, method="min").astype(int)

    past_ranked = past.copy()
    past_ranked["past_rank"] = past_ranked[flow_col].rank(ascending=False, method="min").astype(int)

    merged = today_ranked[[name_col, flow_col, "today_rank"]].merge(
        past_ranked[[name_col, "past_rank"]],
        on=name_col, how="inner",
    )
    merged = merged.rename(columns={flow_col: "today_flow"})
    merged["rank_jump"] = merged["past_rank"] - merged["today_rank"]

    # 触发条件：排名上升 ≥ 阈值 + 今日仍净流入
    signal = merged[
        (merged["rank_jump"] >= rank_jump_threshold)
        & (merged["today_flow"] > 0)
    ].sort_values("rank_jump", ascending=False).reset_index(drop=True)
    return signal
