"""Kronos 接入的配置常量（Deferred-to-Implementation 默认值单点定义）.

计划把若干参数留到实现期定（见
``docs/plans/2026-06-15-001-feat-kronos-shadow-synthetic-stress-plan.md``
的 Open Questions）。这里集中成常量，方便后续运营调参，不散落到各模块。

零依赖叶子模块 —— 不 import 任何 kss/ 内东西，可被任何子模块安全 import.
"""

from __future__ import annotations

from pathlib import Path

from kss.config.paths import STORAGE_ROOT

# --------------------------------------------------------------------------- #
# 影子前向窗口
# --------------------------------------------------------------------------- #
# 毕业需满的固定前向窗口长度（交易日）。~1 季度。
FORWARD_WINDOW_TRADING_DAYS: int = 60

# 连续多少个周校验不达标 → 拉黑停用（沿用 daily_review 预测验证规则）。
BLACKLIST_AFTER_FAILED_WEEKS: int = 2

# --------------------------------------------------------------------------- #
# 微调 / 冻结截断日 D
# --------------------------------------------------------------------------- #
# D 缺省策略：从可用数据最新交易日往前推 FORWARD_WINDOW_TRADING_DAYS 个交易日，
# 留出 D 之后的前向窗口给影子/压测（见 isolation.resolve_cutoff_date）。
# 也可显式指定 YYYY-MM-DD 覆盖。
DEFAULT_CUTOFF_DATE: str | None = None  # None = 自动推导

# 微调输入的回看跨度（交易日上限）；None = 用 D 之前的全部可用历史。
FINETUNE_LOOKBACK_TRADING_DAYS: int | None = None

# 推理输入窗口（喂给模型的历史 bar 数）。base checkpoint 上下文 512，日线够用。
INFER_LOOKBACK_BARS: int = 400

# 预测步长（一次前向预测多少个未来 bar）。
PREDICT_LEN: int = 5

# --------------------------------------------------------------------------- #
# 不确定带 —— 采样
# --------------------------------------------------------------------------- #
# 批处理推理用多少条蒙特卡洛样本算点估计 + 分位带。
SAMPLE_COUNT: int = 30
# 不确定带分位（下/上）。
BAND_LOWER_Q: float = 0.1
BAND_UPPER_Q: float = 0.9

# --------------------------------------------------------------------------- #
# 存储 —— 预测表
# --------------------------------------------------------------------------- #
# 预测落 SQLite（与现有 storage 层一致），schema 见 batch_infer.PREDICTION_SCHEMA。
KRONOS_STORE_ROOT: Path = STORAGE_ROOT / "kronos"
PREDICTIONS_DB: Path = KRONOS_STORE_ROOT / "predictions.sqlite"
PREDICTIONS_TABLE: str = "kronos_predictions"

# 冻结模型 artifact 目录。
ARTIFACT_ROOT: Path = KRONOS_STORE_ROOT / "artifacts"

# --------------------------------------------------------------------------- #
# base checkpoint（HuggingFace）
# --------------------------------------------------------------------------- #
BASE_MODEL_REPO: str = "NeoQuasar/Kronos-base"
BASE_TOKENIZER_REPO: str = "NeoQuasar/Kronos-Tokenizer-base"
MAX_CONTEXT: int = 512

# --------------------------------------------------------------------------- #
# A 股微结构（合成校验闸门）
# --------------------------------------------------------------------------- #
# 主板/中小板 ±10%，科创板(688)/创业板(300) ±20%。
LIMIT_PCT_MAIN: float = 0.10
LIMIT_PCT_STAR_CHINEXT: float = 0.20
# 判定涨跌停的容差（浮点 + 四舍五入误差）。
LIMIT_TOLERANCE: float = 0.005
