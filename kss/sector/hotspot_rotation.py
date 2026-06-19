"""板块热点轮动 —— 单日快照与历史聚合.

Phase 2 扩展：
- 加载最近 N 个交易日归档，计算历史聚合指标.
- 接入 KAIPAN adapter 获取持续强度分.
- 四象限分类：真主线 / 妖板 / 老热点退潮 / 卫星.

设计纪律：

- 数据层失败 → 返回 ``None`` + warning，不外抛.
- 输出 schema 与 ``docs/plans/2026-06-19-001-feat-sector-hotspot-rotation-plan.md``
  保持一致.
- 所有比率/金额保留原始单位，JSON 中显式标注.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from kss.data.plate_rotation_adapter import fetch_plate_rotat_data
from kss.data.tushare_client import TushareClient
from kss.sector.scorer import compute_heat_score, load_config

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("storage") / "sector_rotation"
_SOURCE_KEY_MAP: dict[str, str] = {
    "industry": "industries",
    "concept": "concepts",
    "kaipan": "kaipanBoards",
}
DEFAULT_LOOKBACK_DAYS = 5
DEFAULT_CLASSIFICATION_TOP_N = 10


@dataclass
class HotspotBoard:
    """单个板块（行业/概念/KAIPAN）的当日快照."""

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
    """单日板块热点轮动快照."""

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


def _load_trade_calendar(
    trade_date: str,
    lookback_days: int,
    client: TushareClient | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> list[str]:
    """获取以 ``trade_date`` 结尾的最近 ``lookback_days`` 个交易日.

    优先用 Tushare ``trade_cal``；失败时退而扫描 ``output_dir/*.json`` 归档文件名.
    返回 newest first 的日期列表.
    """
    if client is None:
        client = TushareClient()

    # 估算起始日：lookback_days * 1.5 个自然日足够覆盖节假日
    end_dt = pd.to_datetime(trade_date, format="%Y%m%d")
    start_dt = end_dt - pd.Timedelta(days=int(lookback_days * 1.8))
    start_str = start_dt.strftime("%Y%m%d")
    end_str = trade_date

    try:
        df = client.get_pro().trade_cal(
            exchange="",
            start_date=start_str,
            end_date=end_str,
        )
        if df is not None and not df.empty and "cal_date" in df.columns and "is_open" in df.columns:
            open_days = df[df["is_open"].astype(int) == 1]["cal_date"].tolist()
            open_days = sorted(open_days, reverse=True)
            if trade_date in open_days:
                idx = open_days.index(trade_date)
                return open_days[idx : idx + lookback_days]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hotspot_rotation] trade_cal 获取失败: %s", exc)

    # fallback: 扫描归档文件
    try:
        files = sorted(output_dir.glob("*.json"), reverse=True)
        dates = [p.stem for p in files if p.stem.isdigit() and len(p.stem) == 8]
        if trade_date in dates:
            idx = dates.index(trade_date)
            return dates[idx : idx + lookback_days]
    except OSError as exc:
        logger.warning("[hotspot_rotation] 扫描归档目录失败: %s", exc)

    # 最后 fallback: 按自然日倒退（只跳过周末）
    days: list[str] = []
    d = end_dt
    while len(days) < lookback_days:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= pd.Timedelta(days=1)
    return days


def _load_historical_boards(
    trade_date: str,
    source: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> list[HotspotBoard] | None:
    """加载指定日期的历史归档中某 source 的板块列表."""
    path = output_dir / f"{trade_date}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        key = _SOURCE_KEY_MAP.get(source)
        if key is None or key not in data:
            return []
        return [HotspotBoard(**b) for b in data[key]]
    except (json.JSONDecodeError, TypeError, OSError) as exc:
        logger.warning("[hotspot_rotation] 加载 %s 归档失败: %s", path, exc)
        return None


def _aggregate_historical_metrics(
    boards: list[HotspotBoard],
    history: list[tuple[str, list[HotspotBoard]]],
) -> None:
    """就地填充板块的历史聚合指标.

    Args:
        boards: 当日板块列表.
        history: ``[(date, boards_that_day), ...]``，newest first，不含当日.
    """
    # 构建历史索引：name -> list of (date, board)
    hist_by_name: dict[str, list[tuple[str, HotspotBoard]]] = {}
    for date, hist_boards in history:
        for b in hist_boards:
            hist_by_name.setdefault(b.name, []).append((date, b))

    for board in boards:
        series = hist_by_name.get(board.name, [])
        if not series:
            continue

        # previousRank: 最近一个交易日的排名
        board.previousRank = series[0][1].todayRank
        board.rankJump = (board.previousRank or 0) - board.todayRank

        # top3Appearances: 近 N 日进入 Top3 的次数
        board.top3Appearances = sum(1 for _, b in series if b.todayRank <= 3)

        # streakDays: 从当前往前连续 todayRank == 1 的交易日数
        streak = 0
        for _, b in series:
            if b.todayRank == 1:
                streak += 1
            else:
                break
        board.streakDays = streak

        # strengthDelta: 只有当当前 leader 与昨日 leader 同名时才算
        # Phase 2 无 leader 数据，留 None；用 pct_change 近似不适合，保持 null
        board.strengthDelta = None


def _build_boards(
    df: pd.DataFrame | None,
    source: str,
    name_col: str,
    code_col: str | None,
    weights: dict[str, float],
    top_n: int | None = None,
) -> tuple[list[HotspotBoard], list[str]]:
    """把原始 DataFrame 转成 HotspotBoard 列表."""
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


def _build_kaipan_boards(
    kaipan_data: dict | None,
    top_n: int | None = None,
) -> tuple[list[HotspotBoard], list[str]]:
    """把 KAIPAN adapter 返回的原始数据转成 HotspotBoard 列表."""
    if kaipan_data is None:
        return [], ["kaipan"]
    rows = kaipan_data.get("today_top") or []
    if not rows:
        return [], ["kaipan:empty"]

    boards: list[HotspotBoard] = []
    for row in rows:
        rank = int(row.get("rank", 0))
        if top_n is not None and rank > top_n:
            continue
        value = row.get("value", "")
        try:
            score = int(value) if value else None
        except ValueError:
            score = None
        boards.append(
            HotspotBoard(
                name=str(row.get("name", "")),
                source="kaipan",
                boardCode=str(row.get("code", "")),
                pctChange=None,
                heatScore=None,
                todayRank=rank,
                kaipanStrengthScore=score,
                kaipanRank=rank,
                evidenceSources=["kaipan"],
            )
        )
    return boards, []


def _classify_board(
    board: HotspotBoard,
    top_n: int,
    kaipan_available: bool,
) -> tuple[str, str]:
    """对单个板块做四象限分类.

    Returns:
        (classification, confidence)。
    """
    burst = board.todayRank <= top_n

    sustained_sources = 0
    if board.top3Appearances >= 2:
        sustained_sources += 1
    if kaipan_available and board.kaipanRank is not None and board.kaipanRank <= top_n:
        sustained_sources += 1

    sustained = sustained_sources > 0

    if burst and sustained:
        return "mainline", "high" if sustained_sources >= 2 else "medium"
    if burst and not sustained:
        rank_jump = board.rankJump or 0
        if board.top3Appearances <= 1 and rank_jump >= 5:
            return "demonBoard", "medium"
        return "demonBoard", "low"
    if not burst and sustained:
        return "oldHotspotFading", "medium"
    return "satellite", "high"


def _apply_classification(
    boards: list[HotspotBoard],
    top_n: int,
    kaipan_available: bool,
    cross_signals: dict[str, list[str]],
) -> None:
    """就地分类并写入 crossSourceSignals."""
    for board in boards:
        cls, conf = _classify_board(board, top_n, kaipan_available)
        board.classification = cls
        board.classificationConfidence = conf
        cross_signals.setdefault(cls, []).append(board.name)


def build_hotspot_rotation_snapshot(
    trade_date: str,
    client: TushareClient | None = None,
    config_path: str | Path = "storage/sector_review_config.json",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    top_n_industry: int | None = None,
    top_n_concept: int | None = None,
    top_n_kaipan: int | None = None,
    classification_top_n: int = DEFAULT_CLASSIFICATION_TOP_N,
    enable_kaipan: bool = False,
) -> HotspotRotationSnapshot | None:
    """生成板块热点轮动快照.

    Args:
        trade_date: 交易日，``YYYYMMDD``.
        client: ``TushareClient`` 实例；``None`` 时用默认单例.
        config_path: 评分配置文件路径.
        lookback_days: 历史回看交易日数（含当日）.
        output_dir: 历史归档目录，用于加载历史快照.
        top_n_industry: 行业榜保留前 N；``None`` 保留全部.
        top_n_concept: 概念榜保留前 N；``None`` 保留全部.
        top_n_kaipan: KAIPAN 榜保留前 N；``None`` 保留全部.
        classification_top_n: 分类阈值（今日爆发/持续强度判断用）.
        enable_kaipan: 是否调用 KAIPAN 外部接口.

    Returns:
        :class:`HotspotRotationSnapshot`；核心数据源失败时返回 ``None``.
    """
    if client is None:
        client = TushareClient()

    out_dir = Path(output_dir)
    config = load_config(config_path)

    raw_ind = client.fetch_moneyflow_ind_dc(trade_date)
    raw_cnt = client.fetch_moneyflow_cnt_ths(trade_date)

    if raw_ind is None and raw_cnt is None:
        logger.warning("[hotspot_rotation] %s 行业与概念数据均缺失，无法生成快照", trade_date)
        return None

    trading_days = _load_trade_calendar(trade_date, lookback_days, client=client, output_dir=out_dir)
    if not trading_days or trading_days[0] != trade_date:
        logger.warning("[hotspot_rotation] %s 无法获取交易日历或当日不在日历中", trade_date)
        return None

    history_days = trading_days[1:]
    history_coverage = len(trading_days) / lookback_days

    snap = HotspotRotationSnapshot(
        tradeDate=trade_date,
        lookbackDays=lookback_days,
        tradingDaysUsed=trading_days,
        historyCoverage=history_coverage,
    )

    # 加载历史归档
    history: list[tuple[str, list[HotspotBoard]]] = []
    for d in history_days:
        # 尝试加载 industry 和 concept，任一成功即可用于聚合
        ind_hist = _load_historical_boards(d, "industry", output_dir=out_dir)
        cnt_hist = _load_historical_boards(d, "concept", output_dir=out_dir)
        combined = (ind_hist or []) + (cnt_hist or [])
        if combined:
            history.append((d, combined))

    # 行业
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
    _aggregate_historical_metrics(snap.industries, history)
    snap.missing.extend(ind_missing)

    # 概念
    snap.concepts, cnt_missing = _build_boards(
        raw_cnt,
        source="concept",
        name_col="name",
        code_col=None,
        weights=config.get("concept_heat_weights", {"pct_change": 1.0}),
        top_n=top_n_concept,
    )
    _aggregate_historical_metrics(snap.concepts, history)
    snap.missing.extend(cnt_missing)

    # KAIPAN
    kaipan_missing: list[str] = []
    if enable_kaipan:
        kaipan_data = fetch_plate_rotat_data("kaipan", days=lookback_days)
        snap.kaipanBoards, kaipan_missing = _build_kaipan_boards(
            kaipan_data, top_n=top_n_kaipan
        )
        # 尝试按名称把 KAIPAN 强度分嫁接到 industry/concept 板上
        kaipan_by_name = {b.name: b for b in snap.kaipanBoards}
        for b in snap.industries + snap.concepts:
            k = kaipan_by_name.get(b.name)
            if k is not None:
                b.kaipanStrengthScore = k.kaipanStrengthScore
                b.kaipanRank = k.kaipanRank
                if "kaipan" not in b.evidenceSources:
                    b.evidenceSources.append("kaipan")
    else:
        kaipan_missing = ["kaipan:disabled"]

    snap.missing.extend(kaipan_missing)

    # 分类
    kaipan_available = enable_kaipan and not any("disabled" in m for m in kaipan_missing)
    _apply_classification(snap.industries, classification_top_n, kaipan_available, snap.crossSourceSignals)
    _apply_classification(snap.concepts, classification_top_n, kaipan_available, snap.crossSourceSignals)

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
    """保存快照到 ``output_dir/YYYYMMDD.json``."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{snap.tradeDate}.json"
    out_file.write_text(
        json.dumps(snapshot_to_dict(snap), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[hotspot_rotation] 快照已保存: %s", out_file)
    return out_file
