"""冻结截断日 D 的泄漏隔离守卫（U3，最高风险失败模式）.

Kronos 吃滑动窗口、无内建 split / 泄漏防护；"无污染前向证据"的价值主张全靠
这层断言守住。三道防线：

- :func:`assert_train_before_cutoff` —— 训练样本时间戳 ≤ D（防训练污染）。
- :func:`assert_window_precedes_target` —— 推理输入窗口末尾严格早于被预测 bar
  （防特征级 look-ahead，``Covers AE5``；现有 ``purge_gap`` 只挡 label 泄漏，
  挡不住这个，见 docs/solutions/known_bias_gaps.md）。
- :func:`assert_after_cutoff` —— 影子 / 压测只跑 D 之后（前向隔离）。

断言失败一律 ``LeakageError`` fail loud —— 泄漏不能静默放过。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from kss.kronos.config import FORWARD_WINDOW_TRADING_DAYS


class LeakageError(AssertionError):
    """时间隔离被违反（训练污染 / 特征级 look-ahead / 前向越界）."""


def _to_series(ts) -> pd.Series:
    return pd.Series(pd.to_datetime(pd.Series(ts).reset_index(drop=True)))


def assert_train_before_cutoff(timestamps, cutoff) -> None:
    """断言所有训练样本时间戳 ≤ 截断日 D（含端点合法）.

    Raises:
        LeakageError: 任一样本时间戳 > ``cutoff``.
    """
    ts = _to_series(timestamps)
    cutoff = pd.Timestamp(cutoff)
    bad = ts[ts > cutoff]
    if len(bad) > 0:
        raise LeakageError(
            f"训练样本越过截断日 D={cutoff.date()}：{len(bad)} 条 > D "
            f"（最晚 {bad.max().date()}）"
        )


def assert_window_precedes_target(x_timestamp, y_timestamp) -> None:
    """断言输入窗口末尾严格早于第一根被预测 bar（``Covers AE5``）.

    特征级 look-ahead 的核心防线：喂模型的最后一根历史 bar 必须严格早于被预测
    bar，否则模型在预测时已看到答案。

    Raises:
        LeakageError: ``x`` 末尾 ≥ ``y`` 首个 bar.
    """
    x = _to_series(x_timestamp)
    y = _to_series(y_timestamp)
    if len(x) == 0 or len(y) == 0:
        raise LeakageError("空时间戳序列，无法校验隔离")
    x_last = x.max()
    y_first = y.min()
    if x_last >= y_first:
        raise LeakageError(
            f"输入窗口末尾 {x_last.date()} ≥ 被预测 bar {y_first.date()}："
            "特征级 look-ahead 泄漏"
        )


def assert_after_cutoff(timestamps, cutoff) -> None:
    """断言所有时间戳严格晚于截断日 D（影子 / 压测只跑 D 之后）.

    Raises:
        LeakageError: 任一时间戳 ≤ ``cutoff``.
    """
    ts = _to_series(timestamps)
    cutoff = pd.Timestamp(cutoff)
    bad = ts[ts <= cutoff]
    if len(bad) > 0:
        raise LeakageError(
            f"前向序列含截断日之后以外的样本：{len(bad)} 条 ≤ D={cutoff.date()}"
        )


def resolve_cutoff_date(
    available_dates,
    *,
    explicit: str | None = None,
    forward_window: int = FORWARD_WINDOW_TRADING_DAYS,
) -> pd.Timestamp:
    """确定冻结截断日 D.

    显式优先；否则自动推导为"留出 ``forward_window`` 个未来交易日"的那一天，
    即 D 之后恰好有 ``forward_window`` 个交易日给影子 / 压测。

    Args:
        available_dates: 可用交易日（任意可转 datetime 的序列）.
        explicit: 显式 D（``YYYY-MM-DD``）；缺省自动推导.
        forward_window: D 之后保留的交易日数.

    Returns:
        截断日 ``pd.Timestamp``.

    Raises:
        ValueError: 自动推导但可用交易日不足 ``forward_window + 1``.
    """
    if explicit is not None:
        return pd.Timestamp(explicit)
    dates = pd.to_datetime(pd.Series(available_dates)).drop_duplicates().sort_values()
    dates = dates.reset_index(drop=True)
    if len(dates) < forward_window + 1:
        raise ValueError(
            f"可用交易日 {len(dates)} 不足以留出 forward_window={forward_window}"
            "（需 ≥ forward_window+1）"
        )
    # 取倒数第 (forward_window+1) 个 → 其后恰好 forward_window 个交易日。
    return dates.iloc[-(forward_window + 1)]


@dataclass(frozen=True)
class FreezeMetadata:
    """冻结模型 artifact 的元数据（带 D 标记，里程碑内不再训练）.

    Attributes:
        cutoff_date: 冻结截断日 D（``YYYY-MM-DD``）.
        base_model_repo / base_tokenizer_repo: 微调起点 checkpoint.
        n_train_samples: 训练滑窗样本数.
        n_symbols: 训练标的数.
        lookback_window / predict_window: 训练滑窗结构.
        finetuned_at: 微调完成时间戳（ISO）；由 finetune 流程写入.
        notes: 备注.
    """

    cutoff_date: str
    base_model_repo: str
    base_tokenizer_repo: str
    n_train_samples: int
    n_symbols: int
    lookback_window: int
    predict_window: int
    finetuned_at: str | None = None
    notes: str = ""

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "FreezeMetadata":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
