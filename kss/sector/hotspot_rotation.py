"""板块热点轮动 —— 单日快照生成.

Phase 1 只负责：
- 从 ``Tushare moneyflow_ind_dc`` / ``moneyflow_cnt_ths`` 生成
  行业/概念当日排名与 heat_score.
- 归档到 ``storage/sector_rotation/YYYYMMDD.json``.
- 不计算历史聚合、分类、龙头 persistence；这些交给 Phase 2/3.

设计纪律：

- 数据层失败 → 返回 ``None`` + warning，不外抛.
- 输出 schema 与 ``docs/plans/2026-06-19-001-feat-sector-hotspot-rotation-plan.md``
  保持一致，字段宁可 null 也不编造.
- 所有比率/金额保留原始单位，JSON 中显式标注.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from kss.data.tushare_client import TushareClient
from kss.sector.scorer import compute_heat_score, load_config

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("storage") / "sector_rotation"


@dataclass
class HotspotBoard:
    """单个板块（行业/概念）的当日快照.

    Attributes:
        name: 板块名称.
        source: 来源类型，``industry`` 或 ``concept``.
        boardCode: 板块代码（若有）.
        pctChange: 当日涨幅（原始单位，百分比，如 3.17）.
        heatScore: 热度分（按配置权重加权）.
        todayRank: 当日按 pctChange 降序排名.
        previousRank: 上一交易日排名；Phase 1 无历史，恒为 ``None``.
        rankJump: 排名变化；Phase 1 恒为 ``None``.
        top3Appearances: 近 N 日进入 Top3 次数；Phase 1 恒为 0.
        streakDays: 连续霸榜天数；Phase 1 恒为 0.
        strengthDelta: 龙头涨幅环比；Phase 1 恒为 ``None``.
        kaipanStrengthScore: KAIPAN 强度分；Phase 1 恒为 ``None``.
        kaipanRank: KAIPAN 排名；Phase 1 恒为 ``None``.
        flowPersistenceScore: 资金流持续性分；Phase 1 恒为 ``None``.
        classification: 四象限分类；Phase 1 恒为 ``satellite``.
        classificationConfidence: 分类置信度；Phase 1 恒为 ``low``.
        evidenceSources: 参与判定的数据源列表.
        leaderStocks: 龙头股列表；Phase 1 恒为 ``None``.
        missing: 该板块缺失的数据源说明.
    """

    name: str
    source: str
    boardCode: str | None = None
    pctChange: float | None = None
    heatScore: float | None = None
    todayRank: int = 0
    previousRank: int | None = None
    rankJump: int | None = None
    top3Appearances: int = 0
    streakDays: int = 0
    strengthDelta: float | None = None
    kaipanStrengthScore: int | None = None
    kaipanRank: int | None = None
    flowPersistenceScore: float | None = None
    classification: str = "satellite"
    classificationConfidence: str = "low"
    evidenceSources: list[str] = field(default_factory=list)
    leaderStocks: list[dict[str, Any]] | None = None
    missing: list[str] = field(default_factory=list)


@dataclass
class HotspotRotationSnapshot:
    """单日板块热点轮动快照.

    Attributes:
        tradeDate: 交易日，``YYYYMMDD``.
        lookbackDays: 回看天数；Phase 1 固定为 1.
        tradingDaysUsed: 实际使用的交易日列表.
        historyCoverage: 历史覆盖度；Phase 1 固定为 1.0.
        missing: 全局缺失的数据源.
        industries: 行业榜单.
        concepts: 概念榜单.
        kaipanBoards: KAIPAN 榜单；Phase 1 恒为空列表.
        leaderBoards: 龙头板块；Phase 1 恒为空列表.
        crossSourceSignals: 跨源信号；Phase 1 各类别均为空列表.
    """

    tradeDate: str
    lookbackDays: int = 1
    tradingDaysUsed: list[str] = field(default_factory=list)
    historyCoverage: float = 1.0
    missing: list[str] = field(default_factory=list)
    industries: list[HotspotBoard] = field(default_factory=list)
    concepts: list[HotspotBoard] = field(default_factory=list)
    kaipanBoards: list[HotspotBoard] = field(default_factory=list)
    leaderBoards: list[HotspotBoard] = field(default_factory=list)
    crossSourceSignals: dict[str, list[str]] = field(
        default_factory=lambda: {
            "mainline": [],
            "demonBoard": [],
            "oldHotspotFading": [],
            "satellite": [],
        }
    )


def _rank_min(series: pd.Series) -> pd.Series:
    """按降序给出 min 排名（并列取最小名次的标准竞赛排名）."""
    return series.rank(method="min", ascending=False).astype(int)


def _build_boards(
    df: pd.DataFrame | None,
    source: str,
    name_col: str,
    code_col: str | None,
    weights: dict[str, float],
    top_n: int | None = None,
) -> tuple[list[HotspotBoard], list[str]]:
    """把原始 DataFrame 转成 HotspotBoard 列表.

    Returns:
        (boards, missing)。``pct_change`` 缺失时整个 source 标记缺失。
    """
    if df is None or df.empty:
        return [], [source]

    required = [name_col, "pct_change"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("[%s] 缺少必需列 %s，跳过该 source", source, missing)
        return [], [f"{source}:{','.join(missing)}"]

    work = df.copy()
    work["today_rank"] = _rank_min(work["pct_change"])

    scored = compute_heat_score(work, weights=weights, name_col=name_col)
    if scored.empty:
        logger.warning("[%s] heat_score 计算结果为空", source)
        return [], [f"{source}:heat_score_empty"]

    # 确保 name 列存在，compute_heat_score 会保留原始列
    boards: list[HotspotBoard] = []
    for _, row in scored.iterrows():
        if top_n is not None and row["today_rank"] > top_n:
            continue
        boards.append(
            HotspotBoard(
                name=str(row[name_col]),
                source=source,
                boardCode=str(row[code_col]) if code_col and code_col in row else None,
                pctChange=float(row["pct_change"]) if pd.notna(row["pct_change"]) else None,
                heatScore=float(row["heat_score"]) if pd.notna(row["heat_score"]) else None,
                todayRank=int(row["today_rank"]),
                evidenceSources=[source],
            )
        )
    return boards, []


def build_hotspot_rotation_snapshot(
    trade_date: str,
    client: TushareClient | None = None,
    config_path: str | Path = "storage/sector_review_config.json",
    top_n_industry: int | None = None,
    top_n_concept: int | None = None,
) -> HotspotRotationSnapshot | None:
    """生成单日板块热点轮动快照.

    Args:
        trade_date: 交易日，``YYYYMMDD``.
        client: ``TushareClient`` 实例；``None`` 时用默认单例.
        config_path: 评分配置文件路径.
        top_n_industry: 行业榜保留前 N；``None`` 保留全部.
        top_n_concept: 概念榜保留前 N；``None`` 保留全部.

    Returns:
        :class:`HotspotRotationSnapshot`；核心数据源失败时返回 ``None``.
    """
    if client is None:
        client = TushareClient()

    config = load_config(config_path)

    raw_ind = client.fetch_moneyflow_ind_dc(trade_date)
    raw_cnt = client.fetch_moneyflow_cnt_ths(trade_date)

    if raw_ind is None and raw_cnt is None:
        logger.warning("[hotspot_rotation] %s 行业与概念数据均缺失，无法生成快照", trade_date)
        return None

    snap = HotspotRotationSnapshot(tradeDate=trade_date)
    snap.tradingDaysUsed = [trade_date]

    # 行业：过滤 content_type == '行业'，与 sector_review 保持一致
    if raw_ind is not None and "content_type" in raw_ind.columns:
        raw_ind = raw_ind[raw_ind["content_type"] == "行业"].reset_index(drop=True)

    snap.industries, ind_missing = _build_boards(
        raw_ind,
        source="industry",
        name_col="name",
        code_col="ts_code",
        weights=config.get("industry_heat_weights", {"pct_change": 1.0}),
        top_n=top_n_industry,
    )
    snap.missing.extend(ind_missing)

    snap.concepts, cnt_missing = _build_boards(
        raw_cnt,
        source="concept",
        name_col="name",
        code_col=None,
        weights=config.get("concept_heat_weights", {"pct_change": 1.0}),
        top_n=top_n_concept,
    )
    snap.missing.extend(cnt_missing)

    if snap.missing:
        logger.warning(
            "[hotspot_rotation] %s 部分数据缺失: %s",
            trade_date, snap.missing,
        )

    return snap


def snapshot_to_dict(snap: HotspotRotationSnapshot) -> dict[str, Any]:
    """把快照转成可 JSON 序列化的字典."""
    return asdict(snap)


def save_snapshot(
    snap: HotspotRotationSnapshot,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """保存快照到 ``output_dir/YYYYMMDD.json``.

    Returns:
        保存的文件路径.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{snap.tradeDate}.json"
    out_file.write_text(
        json.dumps(snapshot_to_dict(snap), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[hotspot_rotation] 快照已保存: %s", out_file)
    return out_file
