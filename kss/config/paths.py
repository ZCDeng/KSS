"""共享存储路径常量 (plan 010 #44).

所有 macro / risk_filter parquet 路径单点定义，避免 backfill_regime_history /
update_macro_daily / risk_filters 三处独立硬编码导致漂移.

这是零依赖叶子模块 —— 不 import 任何 kss/ 内东西，可被任何子模块安全 import.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(var: str) -> Path | None:
    """读环境变量为绝对路径；未设/空返回 None。"""
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else None


# 不可变代码根：dev/源码树位置。bundle-mode 由 KSS_PROJECT_ROOT 指定。
PROJECT_ROOT: Path = _env_path("KSS_PROJECT_ROOT") or Path(__file__).resolve().parents[2]
# 可变状态根：sqlite/parquet/.md/logs。默认回落到 PROJECT_ROOT —— 未设 env 时
# STORAGE_ROOT 与历史行为逐字一致（in-repo），设了 KSS_STATE_ROOT 才重定向到 bundle 外。
STATE_ROOT: Path = _env_path("KSS_STATE_ROOT") or PROJECT_ROOT
STORAGE_ROOT: Path = STATE_ROOT / "storage"
MACRO_ROOT: Path = STORAGE_ROOT / "macro"

# ----- 日频 -----
DAILY_PARQUET: Path = MACRO_ROOT / "macro_daily.parquet"
MARGIN_PARQUET: Path = MACRO_ROOT / "margin_daily.parquet"
HSGT_PARQUET: Path = MACRO_ROOT / "hsgt_daily.parquet"
REGIME_PARQUET: Path = MACRO_ROOT / "regime_daily.parquet"
VALUATION_PARQUET: Path = MACRO_ROOT / "valuation_n_daily.parquet"
HS300_PE_PARQUET: Path = MACRO_ROOT / "hs300_dailybasic.parquet"

# ----- 月频 -----
MONTHLY_PARQUET: Path = MACRO_ROOT / "macro_monthly.parquet"
PMI_PARQUET: Path = MACRO_ROOT / "pmi_monthly.parquet"
VAI_PARQUET: Path = MACRO_ROOT / "vai_monthly.parquet"

# ----- 季频 / 一次性快照 -----
FINA_QUARTERLY_PARQUET: Path = MACRO_ROOT / "fina_quarterly.parquet"
STOCK_BASIC_PARQUET: Path = MACRO_ROOT / "stock_basic.parquet"
INDUSTRY_MAP_PARQUET: Path = MACRO_ROOT / "industry_map_swl1.parquet"

# ----- 信用利差 (CSV 按日存档) -----
CREDIT_DIR: Path = MACRO_ROOT / "credit_curve"

# ----- 阶段切换告警去重 sentinel (plan 011) -----
REGIME_ALERT_SENTINEL: Path = MACRO_ROOT / "regime_alert_sentinel.txt"

# ----- 报告输出 -----
REPORT_DIR: Path = STORAGE_ROOT / "reports"

# ----- 分时数据层 (plan 005；隔离 SQLite 库，解析于 STATE_ROOT 防 bundle 双根坑) -----
INTRADAY_DB: Path = STORAGE_ROOT / "intraday_quotes.db"


__all__ = [
    "CREDIT_DIR",
    "DAILY_PARQUET",
    "INTRADAY_DB",
    "FINA_QUARTERLY_PARQUET",
    "HS300_PE_PARQUET",
    "HSGT_PARQUET",
    "INDUSTRY_MAP_PARQUET",
    "MACRO_ROOT",
    "MARGIN_PARQUET",
    "MONTHLY_PARQUET",
    "PMI_PARQUET",
    "PROJECT_ROOT",
    "REGIME_ALERT_SENTINEL",
    "REGIME_PARQUET",
    "REPORT_DIR",
    "STOCK_BASIC_PARQUET",
    "STORAGE_ROOT",
    "VAI_PARQUET",
    "VALUATION_PARQUET",
]
