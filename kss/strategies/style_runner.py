"""风格对照日更 runner —— 建面板、跑四风格、写 style_contrast 快照.

供 ``scripts/style_contrast_daily.py`` 与 bridge 任务调用。单风格失败只写
failed 槽，不阻断其余风格（R7 / KTD4）。
"""

from __future__ import annotations

import glob
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kss.features.pipeline import FactorPipeline
from kss.storage.style_contrast import (
    STATUS_FAILED,
    STATUS_OK,
    write_style_slot,
)
from kss.strategies.styles import STYLE_ORDER, build_style_strategy

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 5
MIN_DAYS = 60


def load_style_panel(
    data_glob: str,
    *,
    min_days: int = MIN_DAYS,
) -> pd.DataFrame:
    """加载含低波/价值/反转因子的 long panel.

    列：trade_date, symbol, volatility_20d, pb, ret_5d, close, open,
    next_open_ret（可选，用于门禁回测）。
    """
    files = sorted(glob.glob(data_glob))
    if not files:
        raise FileNotFoundError(f"未找到 {data_glob}")

    panels: list[pd.DataFrame] = []
    for f in files:
        df = pd.read_csv(f)
        if "ts_code" not in df.columns and "symbol" not in df.columns:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) < min_days:
            continue
        try:
            factors = FactorPipeline(df).generate()
        except Exception as exc:  # noqa: BLE001
            logger.debug("FactorPipeline skip %s: %s", f, exc)
            continue
        out = pd.DataFrame(
            {
                "trade_date": df["trade_date"].values,
                "symbol": df["ts_code"].values if "ts_code" in df.columns else df["symbol"].values,
                "close": df["close"].values,
                "open": df["open"].values,
            }
        )
        if "volatility_20d" in factors.columns:
            out["volatility_20d"] = factors["volatility_20d"].values
        else:
            ret = df["close"].pct_change()
            out["volatility_20d"] = ret.rolling(20).std().values
        if "pb" in factors.columns:
            out["pb"] = factors["pb"].values
        elif "pb" in df.columns:
            out["pb"] = df["pb"].values
        else:
            out["pb"] = np.nan
        out["ret_5d"] = df["close"].pct_change(5).values
        # T 日信号 → T+1 open 到 T+2 open 收益（与 formal 纸交易口径对齐）
        open_s = df["open"].astype(float)
        out["next_open_ret"] = (open_s.shift(-2) / open_s.shift(-1) - 1.0).values
        panels.append(out)

    if not panels:
        return pd.DataFrame()
    return pd.concat(panels, ignore_index=True)


def attach_sector_momentum(
    panel: pd.DataFrame,
    *,
    prediction_date: str,
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    """从 sector_rotation 快照给面板股打 sector_momentum_score.

    打分逻辑（2026-08-06 重构）：
    不再依赖"板块 leader 股 → 匹配面板 symbol"（个股 leader 与科创板面板不交集），
    改为"板块名 → 匹配面板股的 industry → 板块 heatScore 直接作为成分股分数"。
    即：每只股票所属行业若在热点快照里，该股获得该行业的 heatScore 作为动量分。

    无快照时列保持全 NaN，sector 风格将失败占位。
    """
    out = panel.copy()
    if "sector_momentum_score" not in out.columns:
        out["sector_momentum_score"] = np.nan
    try:
        from kss.storage.sector_rotation import read_by_date, read_latest

        snap = read_by_date(prediction_date, db_path) or read_latest(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sector_rotation 读取失败: %s", exc)
        return out
    if not snap:
        return out

    # ---- 阶段 1：板块 heatScore 映射 ----
    # 从快照 industries/concepts 提取 name→heatScore，作为行业动量分源。
    industry_heat: dict[str, float] = {}
    for key in ("industries", "concepts", "boards", "themes", "hotBoards", "items"):
        val = snap.get(key)
        if not isinstance(val, list):
            continue
        for item in val:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            heat = item.get("heatScore")
            if name and heat is not None:
                industry_heat[name] = max(
                    industry_heat.get(name, float("-inf")), float(heat)
                )
    if not industry_heat:
        logger.warning("sector_rotation 快照无有效 heatScore，跳过")
        return out

    # ---- 阶段 2：加载行业映射，给每只面板股打行业动量分 ----
    # 股票所属行业若在热点快照里，该股获得该行业的 heatScore。
    # 这替代了原来的"个股 leader 匹配"（面板全是科创板，与主板 leader 不交集）。
    from kss.macro.rotation import load_industry_map

    ind_map = load_industry_map()
    if not ind_map:
        logger.warning("industry_map 为空，sector_momentum_score 全 NaN")
        return out

    # 面板股 → 行业 → heatScore
    sym_to_heat: dict[str, float] = {}
    for sym, industry in ind_map.items():
        heat = industry_heat.get(industry)
        if heat is not None:
            sym_to_heat[sym] = heat

    if not sym_to_heat:
        logger.warning(
            "industry_map 与 sector_rotation 无交集（%d 个行业，%d 只股），全 NaN",
            len(industry_heat), len(ind_map),
        )
        return out

    mapped = out["symbol"].map(sym_to_heat)
    out["sector_momentum_score"] = mapped.where(
        mapped.notna(), out["sector_momentum_score"]
    )
    hit = int(mapped.notna().sum())
    logger.info(
        "sector_momentum 行业映射: %d/%d 只股打分, 覆盖 %d 个热点行业",
        hit, len(out), len(sym_to_heat),
    )
    return out


def _picks_from_signals(sig: pd.DataFrame) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for _, row in sig.iterrows():
        picks.append(
            {
                "symbol": str(row["symbol"]),
                "factor_value": float(row["factor_value"])
                if pd.notna(row.get("factor_value"))
                else None,
                "rank_pct": float(row["rank_pct"]) if pd.notna(row.get("rank_pct")) else None,
                "rank_position": int(row["rank_position"]),
                "planned_weight": float(row["planned_weight"]),
                "selection_reason": str(row.get("selection_reason") or ""),
            }
        )
    return picks


def _gate_label_for_style(
    strategy: Any,
    panel: pd.DataFrame,
) -> str:
    """轻量门禁：用历史 next_open_ret 回测；失败则 research_blocked."""
    try:
        bt = strategy.backtest(panel)
        if bt is None or bt.empty or "portfolio_return" not in bt.columns:
            return "research_blocked"
        gate = strategy.evaluate_gate(bt["portfolio_return"])
        return gate.label
    except Exception as exc:  # noqa: BLE001
        logger.debug("gate eval failed for %s: %s", strategy.meta.style_id, exc)
        return "research_blocked"


def run_style_contrast_day(
    panel: pd.DataFrame,
    *,
    prediction_date: str | None = None,
    top_n: int = DEFAULT_TOP_N,
    db_path: str | Path | None = None,
    evaluate_gate: bool = True,
) -> dict[str, Any]:
    """对指定日跑四风格并写快照.

    Returns:
        汇总 dict：prediction_date, slots[], ok_count, failed_count.
    """
    if panel.empty:
        raise ValueError("风格对照面板为空")

    df = panel.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if prediction_date is None:
        target = df["trade_date"].max()
        prediction_date = str(pd.Timestamp(target).date())
    else:
        target = pd.Timestamp(prediction_date)

    day = df[df["trade_date"] == target]
    if day.empty:
        # 允许 date 字符串匹配
        day = df[df["trade_date"].dt.strftime("%Y-%m-%d") == prediction_date]
    if day.empty:
        raise ValueError(f"面板无 prediction_date={prediction_date} 的行")

    df = attach_sector_momentum(df, prediction_date=prediction_date, db_path=db_path)
    generated_at = datetime.now().isoformat()
    slots: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0

    for style_id in STYLE_ORDER:
        strategy = build_style_strategy(style_id, top_n=top_n)
        meta = strategy.meta
        try:
            sig = strategy.generate_signals(df, date=target)
            picks = _picks_from_signals(sig)
            if not picks:
                raise ValueError("Top-N 结果为空")
            gate_label = (
                _gate_label_for_style(strategy, df) if evaluate_gate else "research_blocked"
            )
            write_style_slot(
                prediction_date,
                style_id,
                status=STATUS_OK,
                payload={"picks": picks, "top_n": top_n},
                gate_label=gate_label,
                source_tags=list(meta.source_tags),
                name=meta.name,
                generated_at=generated_at,
                db_path=db_path,
            )
            slots.append(
                {
                    "style_id": style_id,
                    "status": STATUS_OK,
                    "gate_label": gate_label,
                    "n_picks": len(picks),
                }
            )
            ok_count += 1
        except Exception as exc:  # noqa: BLE001 — 单风格失败占位
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("风格 %s 失败: %s", style_id, err)
            write_style_slot(
                prediction_date,
                style_id,
                status=STATUS_FAILED,
                error=err,
                payload={},
                gate_label="research_blocked",
                source_tags=list(meta.source_tags),
                name=meta.name,
                generated_at=generated_at,
                db_path=db_path,
            )
            slots.append(
                {
                    "style_id": style_id,
                    "status": STATUS_FAILED,
                    "error": err,
                    "gate_label": "research_blocked",
                }
            )
            failed_count += 1

    return {
        "prediction_date": prediction_date,
        "slots": slots,
        "ok_count": ok_count,
        "failed_count": failed_count,
        "generated_at": generated_at,
    }
