"""IC 双源仲裁闭环接线：固化指标的日终 pack 写入预测账本 + sign_proxy 健康度跟踪.

按现有生产口径 demote-only（见 docs/solutions/known_bias_gaps.md「诚实状态」）：不注入回测
先验、不新建 promote 驱动。状态迁移（PENDING_REVIEW/RETIRED 等）仍走
``kss.backtest.factor_health`` 既有的 ``arbitrate()`` 消费方，本模块只负责按同一口径
（``IC_METHOD_SIGN_PROXY``）生产账本记录与 IC 快照，不在此重复仲裁判定逻辑。

MI 不在本模块范围内——它今天没有写账本，本次也不追溯接入（KTD3：MI 专属数学与既有
生产行为原地不动）。
"""

from __future__ import annotations

from typing import Any

from kss.indicators.registry import RegistryEntry


def prediction_id_for(entry: RegistryEntry, prediction_date: str, symbol: str) -> str:
    """``{date}_{symbol}_{entry.id}``——与既有 ``{date}_{symbol}`` 主键不冲突的命名空间。"""
    return f"{prediction_date}_{symbol}_{entry.id}"


def record_from_pack(entry: RegistryEntry, pack: dict[str, Any]) -> Any | None:
    """pack → PredictionRecord；status 非 ok 或缺关键字段时返回 None（不入半成品账）。"""
    from kss.prediction.ledger import PredictionRecord

    if pack.get("status") != "ok":
        return None
    asof, symbol, pred_score = pack.get("asof"), pack.get("symbol"), pack.get("pred_score")
    if not asof or not symbol or pred_score is None:
        return None
    action = pack.get("action")
    return PredictionRecord(
        prediction_id=prediction_id_for(entry, str(asof), str(symbol)),
        prediction_date=str(asof),
        symbol=str(symbol),
        strategy=entry.id,
        factor_value=float(pred_score),
        planned_weight=1.0 if action in ("BUY", "HOLD_LONG") else 0.0,
    )


def record_pack(entry: RegistryEntry, pack: dict[str, Any], *, ledger: Any = None) -> bool:
    """把一个 pack 的当日预测写入账本（去重跳过返回 False，见 record_prediction 语义）。"""
    from kss.prediction.ledger import PredictionLedger

    record = record_from_pack(entry, pack)
    if record is None:
        return False
    ledger = ledger or PredictionLedger()
    return ledger.record_prediction(record)


def sign_proxy_series(entry: RegistryEntry, *, ledger: Any = None) -> Any:
    """从账本读 entry 名下已结算记录，构造逐日 sign_proxy 序列（预测方向 vs 实现方向一致性）.

    值域 [-1, 1]：同号=1、异号=-1、任一方为 0=0；同日多标的取均值（对齐
    ``factor_health._dedup_daily_ic`` 的去重日惯例）。
    """
    import pandas as pd

    from kss.prediction.ledger import STATUS_SETTLED, PredictionLedger

    ledger = ledger or PredictionLedger()
    rows = [
        r
        for r in ledger.query(status=STATUS_SETTLED)
        if r.get("strategy") == entry.id
        and r.get("realized_ret") is not None
        and r.get("factor_value") is not None
    ]
    if not rows:
        return pd.Series(dtype=float)
    dates: list[str] = []
    values: list[float] = []
    for r in rows:
        fv = float(r["factor_value"])
        rr = float(r["realized_ret"])
        proxy = 0.0 if (fv == 0.0 or rr == 0.0) else (1.0 if (fv > 0) == (rr > 0) else -1.0)
        dates.append(str(r["prediction_date"]))
        values.append(proxy)
    return pd.Series(values, index=pd.Index(dates, name="prediction_date"))


def refresh_factor_health(
    entry: RegistryEntry, window_end: str, *, tracker: Any = None, ledger: Any = None
) -> Any | None:
    """跑一次 entry 的 sign_proxy 健康度快照落库；无已结算数据时返回 None（不强行落空快照）。"""
    from kss.backtest.factor_health import IC_METHOD_SIGN_PROXY, FactorHealthTracker

    tracker = tracker or FactorHealthTracker()
    ic_series = sign_proxy_series(entry, ledger=ledger)
    if ic_series.empty:
        return None
    return tracker.record_ic_snapshot(
        entry.id, window_end, ic_series, source="realized", method=IC_METHOD_SIGN_PROXY
    )
