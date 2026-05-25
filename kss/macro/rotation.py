"""部门轮换映射 —— 阶段 → 申万一级行业偏好（P2 of Bolton 周期框架）.

把 ``kss/config/sector_rotation.yaml`` 的静态映射表加载成查询函数：

- :func:`get_preferred_industries(stage)` —— 返回该阶段优先池
- :func:`get_avoid_industries(stage)`     —— 返回该阶段回避池
- :func:`score_industry_fit(name, stage)` —— [-1, 1] 偏好分（preferred=+1，avoid=-1）

调用方：

- :mod:`scan_combo_signals` Top-N 评分阶段加 ``rotation_bonus``
- :mod:`kss.sector.commentary` LLM prompt 注入"本阶段优先/回避板块"

设计:

- **热加载**：每次调用都重读 YAML（成本可忽略，便于实盘调参不重启）
- **前缀匹配**：申万子项"医药生物-中药" 匹配父项"医药生物"；反之亦然
- **未知阶段 / 未知行业** → 中性 0.0，不抛错
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from kss.config.paths import INDUSTRY_MAP_PARQUET, STORAGE_ROOT

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sector_rotation.yaml"
_STOCK_NAMES_CSV = STORAGE_ROOT / "stock_names.csv"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """读 ``sector_rotation.yaml``；缺失返回空骨架.

    Args:
        path: 自定义路径（测试用）；``None`` 走默认.

    Returns:
        ``{"version": int, "country": str, "stages": {...}, "weights": {...}}``.
    """
    p = Path(path) if path else _CONFIG_PATH
    if not p.exists():
        logger.warning("sector_rotation.yaml 不存在: %s", p)
        return {"version": 0, "country": "CN", "stages": {}, "weights": _DEFAULT_WEIGHTS}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.setdefault("stages", {})
    data.setdefault("weights", _DEFAULT_WEIGHTS)
    return data


def get_preferred_industries(
    stage: str,
    path: Path | str | None = None,
) -> list[str]:
    """返回 ``stage`` 阶段的优先行业列表（申万一级 + 可能的子项）.

    Args:
        stage: 阶段标签 ``"I"`` / ``"II"`` / ``"III"`` / ``"IV"``.
        path: 自定义配置路径.

    Returns:
        行业名 list；未知阶段或缺配置返回 ``[]``.
    """
    cfg = load_config(path)
    return list(cfg["stages"].get(stage, {}).get("preferred", []))


def get_avoid_industries(
    stage: str,
    path: Path | str | None = None,
) -> list[str]:
    """返回 ``stage`` 阶段的回避行业列表."""
    cfg = load_config(path)
    return list(cfg["stages"].get(stage, {}).get("avoid", []))


def get_rationale(stage: str, path: Path | str | None = None) -> str:
    """返回该阶段的轮换逻辑文字（commentary prompt 用）."""
    cfg = load_config(path)
    return str(cfg["stages"].get(stage, {}).get("rationale", ""))


def score_industry_fit(
    industry_name: str | None,
    stage: str | None,
    path: Path | str | None = None,
) -> float:
    """行业 ↔ 阶段适配分（``[-1, 1]``）.

    匹配规则（按顺序）：

    1. 精确字符串匹配 → preferred (+1.0) / avoid (-1.0)
    2. 前缀匹配：``industry_name`` 以池中某条为前缀（"医药生物" 命中"医药生物-中药"）
    3. 后缀匹配反向：池中某条以 ``industry_name`` 为前缀（"医药生物-中药" 命中"医药生物"）
    4. 否则中性 0.0

    Args:
        industry_name: 申万一级行业名（带可选子项后缀）.
        stage: 阶段标签；为空或未知 → 0.0.
        path: 自定义配置路径.

    Returns:
        ``[-1, 1]`` 区间的偏好分；``industry_name`` 或 ``stage`` 缺时为 0.
    """
    if not industry_name or not stage:
        return 0.0
    cfg = load_config(path)
    weights = cfg.get("weights", _DEFAULT_WEIGHTS)
    bucket = cfg["stages"].get(stage)
    if bucket is None:
        return 0.0
    if _match_any(industry_name, bucket.get("preferred", [])):
        return float(weights.get("preferred", 1.0))
    if _match_any(industry_name, bucket.get("avoid", [])):
        return float(weights.get("avoid", -1.0))
    return float(weights.get("neutral", 0.0))


# ---------------------------------------------------------------------------- #
# Industry map (ts_code → SW L1 中文行业名)
# ---------------------------------------------------------------------------- #


def load_industry_map(path: Path | str | None = None) -> dict[str, str]:
    """加载 ``ts_code → 申万一级行业中文名`` 映射，供个股 rotation_score 用.

    优先级（从高到低，命中即返回，缺则降级）：

    1. ``storage/macro/industry_map_swl1.parquet`` —— 由
       :mod:`scripts.backfill_industry_map` 拉全市场 SW L1 写入；
       期望列 ``ts_code`` + ``sw_l1_name``.
    2. ``storage/stock_names.csv`` —— 兜底，列 ``ts_code`` + ``industry``；
       覆盖 KCB 约 600 只 + 颗粒度可能为 SW L2/L3.
    3. 都缺则返回空 dict（caller 应能容忍 → :func:`score_industry_fit`
       传 ``None``/``''`` 仍返回 0.0）.

    Args:
        path: 自定义 parquet 路径（测试用）；``None`` 走默认.

    Returns:
        ``{ts_code (带 .SH/.SZ): industry_name}``；缺数据时 ``{}``.
    """
    p = Path(path) if path else INDUSTRY_MAP_PARQUET
    if p.exists():
        try:
            df = pd.read_parquet(p)
            if "ts_code" in df.columns and "sw_l1_name" in df.columns:
                df = df[df["sw_l1_name"].notna() & (df["sw_l1_name"].astype(str) != "")]
                return dict(zip(df["ts_code"].astype(str), df["sw_l1_name"].astype(str)))
        except Exception as exc:    # noqa: BLE001
            logger.warning("load_industry_map: 读 parquet 失败 %s (%s)，降级 csv", p, exc)

    if _STOCK_NAMES_CSV.exists():
        try:
            df = pd.read_csv(_STOCK_NAMES_CSV, usecols=["ts_code", "industry"], encoding="utf-8")
            df = df[df["industry"].notna() & (df["industry"].astype(str) != "")]
            return dict(zip(df["ts_code"].astype(str), df["industry"].astype(str)))
        except Exception as exc:    # noqa: BLE001
            logger.warning("load_industry_map: 读 csv 失败 %s (%s)", _STOCK_NAMES_CSV, exc)

    return {}


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


_DEFAULT_WEIGHTS: dict[str, float] = {
    "preferred": 1.0,
    "avoid": -1.0,
    "neutral": 0.0,
}


def _match_any(name: str, candidates: list[str]) -> bool:
    """精确 + 双向前缀匹配（处理申万一级 ↔ 子项命名差异）."""
    n = name.strip()
    if not n:
        return False
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        if n == c:
            return True
        if n.startswith(c) or c.startswith(n):
            return True
    return False
