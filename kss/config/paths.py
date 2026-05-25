"""共享存储路径常量 (plan 010 #44).

所有 macro / risk_filter parquet 路径单点定义，避免 backfill_regime_history /
update_macro_daily / risk_filters 三处独立硬编码导致漂移.

这是零依赖叶子模块 —— 不 import 任何 kss/ 内东西，可被任何子模块安全 import.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
STORAGE_ROOT: Path = PROJECT_ROOT / "storage"
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


__all__ = [
    "CREDIT_DIR",
    "DAILY_PARQUET",
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
