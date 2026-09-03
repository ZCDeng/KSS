"""紫苏叶候选的证据与排除审计.

评分只回答“结构像不像瓶颈”；审计层回答“证据是否足以把它当成候选”。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Collection, Literal
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from kss.supply_chain.registry import StockChainInfo

_DateState = Literal["missing", "future", "stale", "fresh"]
_PIT_VERDICTS = {"support", "conflict", "unknown", "failed"}
_PIT_REQUIRED_FIELDS = {
    "as_of",
    "published_at",
    "retrieved_at",
    "source_kind",
    "source_url",
    "verdict",
}
_AUDIT_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PerillaAssessment:
    """紫苏叶候选审计结果."""

    status: str
    exclusion_reasons: tuple[str, ...]
    review_flags: tuple[str, ...]
    positive_signals: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """转成 bridge / 前端友好的普通字典."""
        return {
            "status": self.status,
            "exclusionReasons": list(self.exclusion_reasons),
            "reviewFlags": list(self.review_flags),
            "positiveSignals": list(self.positive_signals),
        }


def _parse_iso_date(value: Any) -> date | None:
    """解析 YYYY-MM-DD 日期；非法或空值返回 None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _date_state(value: Any, *, as_of: date, stale_after_days: int) -> _DateState:
    """返回日期相对审计基准日的状态."""
    parsed = _parse_iso_date(value)
    if parsed is None:
        return "missing"
    age_days = (as_of - parsed).days
    if age_days < 0:
        return "future"
    if age_days > stale_after_days:
        return "stale"
    return "fresh"


def _parse_iso_datetime(value: Any) -> datetime | None:
    """解析带时区 ISO 日期时间；无时区值视为不可审计."""
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _audit_evidence_history(
    history: Any,
    *,
    as_of: date,
) -> tuple[list[str], list[str]]:
    """审计 PIT 证据条目的必备时间轴与结论口径."""
    if not isinstance(history, (tuple, list)) or not history:
        return ["待补PIT证据历史"], []

    malformed = False
    lookahead = False
    chronology_error = False
    verdicts: set[str] = set()
    for item in history:
        if not isinstance(item, dict):
            malformed = True
            continue
        if any(item.get(field) in (None, "") for field in _PIT_REQUIRED_FIELDS):
            malformed = True
            continue

        business_date = _parse_iso_date(item.get("as_of"))
        published_date = _parse_iso_date(item.get("published_at"))
        retrieved_at = _parse_iso_datetime(item.get("retrieved_at"))
        if business_date is None or published_date is None or retrieved_at is None:
            malformed = True
        else:
            # PIT 的审计基准日按 A 股交易日口径（Asia/Shanghai）解释。来源时间
            # 可能是 UTC；必须先统一时区再取自然日，否则 UTC 深夜会漏过上海次日
            # 的前视检查。
            retrieved_date = retrieved_at.astimezone(_AUDIT_TIMEZONE).date()
            if business_date > published_date or published_date > retrieved_date:
                chronology_error = True
            if published_date > as_of or retrieved_date > as_of:
                lookahead = True

        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in _PIT_VERDICTS:
            malformed = True
        else:
            verdicts.add(verdict)

    reviews: list[str] = []
    if malformed:
        reviews.append("PIT证据历史字段不完整")
    if chronology_error:
        reviews.append("PIT证据时间顺序无效")
    if lookahead:
        reviews.append("PIT证据包含前视信息")
    if "conflict" in verdicts:
        reviews.append("PIT历史存在冲突证据")
    if "unknown" in verdicts:
        reviews.append("PIT历史存在待确认结论")
    if "failed" in verdicts:
        reviews.append("PIT历史存在取证失败")

    positives = [] if malformed or chronology_error or lookahead else ["PIT证据历史已记录"]
    return reviews, positives


def _tri_state_gate(
    value: Any,
    *,
    false_reason: str,
    unknown_flag: str,
    true_signal: str,
) -> tuple[str | None, str | None, str | None]:
    """处理显式 True/False/未知的证据门."""
    if value is False:
        return false_reason, None, None
    if value is True:
        return None, None, true_signal
    return None, unknown_flag, None


def assess_perilla(
    info: StockChainInfo,
    *,
    structural_updated: str | None = None,
    known_demand_chains: Collection[str] | None = None,
    stale_after_days: int = 90,
    as_of: date | None = None,
) -> PerillaAssessment:
    """审计紫苏叶候选是否满足结构硬条件与证据完备性.

    Args:
        info: 个股产业链元数据.
        structural_updated: 人工结构标注日期, ``YYYY-MM-DD``.
        known_demand_chains: 顶层已定义需求链名称；未传入时不做交叉校验.
        stale_after_days: 证据与结构标注过期阈值.
        as_of: 审计基准日; 默认取今天.

    Returns:
        ``PerillaAssessment``. status 为 ``excluded`` / ``needs_review`` /
        ``qualified``.
    """
    if stale_after_days < 0:
        raise ValueError("stale_after_days 不能为负数")

    today = as_of or date.today()
    exclusions: list[str] = []
    reviews: list[str] = []
    positives: list[str] = []

    if not tuple(getattr(info, "demand_chains", ()) or ()):
        exclusions.append("缺少需求链标注")
    else:
        positives.append("已绑定需求链")
        if known_demand_chains is not None:
            undefined = sorted(set(info.demand_chains) - set(known_demand_chains))
            if undefined:
                reviews.append(f"需求链未在顶层定义: {', '.join(undefined)}")

    if int(getattr(info, "chain_layer", 1) or 1) < 4:
        exclusions.append("产业链层级不足 L4")
    else:
        positives.append("深链层级达标")

    if int(getattr(info, "n_competitors_global", 99) or 99) > 3:
        exclusions.append("全球供应商超过 3 家")
    else:
        positives.append("全球竞争格局集中")

    if getattr(info, "demand_locked", False) is not True:
        exclusions.append("需求未锁定")
    else:
        positives.append("需求锁定")

    if float(getattr(info, "expansion_cycle_years", 0) or 0) < 2:
        exclusions.append("扩产周期短于 2 年")
    else:
        positives.append("扩产周期较长")

    sub = str(getattr(info, "substitutability", "") or "").strip().lower()
    if sub == "high":
        exclusions.append("可替代性高")
    elif sub not in {"low", "medium"}:
        exclusions.append("可替代性口径无效")
    else:
        positives.append("可替代性口径达标")

    tri_state_fields = (
        (
            "main_business_confirmed",
            "未确认属于主营业务",
            "待补主营业务收入证据",
            "主营业务已确认",
        ),
        (
            "import_substitution_valid",
            "进口替代逻辑无效",
            "待补进口替代有效性证据",
            "进口替代逻辑已确认",
        ),
        (
            "liquidity_eligible",
            "流动性不达标",
            "待补流动性资格证据",
            "流动性资格已确认",
        ),
        (
            "valuation_unpriced",
            "估值已充分定价",
            "待补估值未充分定价证据",
            "估值未充分定价",
        ),
    )
    for attr, false_reason, unknown_flag, true_signal in tri_state_fields:
        reason, flag, signal = _tri_state_gate(
            getattr(info, attr, None),
            false_reason=false_reason,
            unknown_flag=unknown_flag,
            true_signal=true_signal,
        )
        if reason:
            exclusions.append(reason)
        if flag:
            reviews.append(flag)
        if signal:
            positives.append(signal)

    evidence_sources = tuple(getattr(info, "evidence_sources", ()) or ())
    if not evidence_sources:
        reviews.append("待补证据来源")
    else:
        positives.append("证据来源已记录")

    pit_reviews, pit_positives = _audit_evidence_history(
        getattr(info, "evidence_history", ()),
        as_of=today,
    )
    reviews.extend(pit_reviews)
    positives.extend(pit_positives)

    analyst_count = getattr(info, "analyst_count", None)
    if analyst_count is None:
        reviews.append("待补分析师覆盖数据")
    else:
        positives.append("分析师覆盖数据已记录")

    evidence_as_of = getattr(info, "evidence_as_of", "")
    evidence_state = _date_state(
        evidence_as_of,
        as_of=today,
        stale_after_days=stale_after_days,
    )
    if evidence_state == "missing":
        reviews.append("待补证据日期")
    elif evidence_state == "future":
        reviews.append("证据日期晚于审计基准日")
    elif evidence_state == "stale":
        reviews.append("证据日期已过期")
    else:
        positives.append("证据日期有效")

    structural_state = _date_state(
        structural_updated,
        as_of=today,
        stale_after_days=stale_after_days,
    )
    if structural_state == "missing":
        reviews.append("待补结构标注日期")
    elif structural_state == "future":
        reviews.append("结构标注日期晚于审计基准日")
    elif structural_state == "stale":
        reviews.append("结构标注已过期")
    else:
        positives.append("结构标注有效")

    if exclusions:
        status = "excluded"
    elif reviews:
        status = "needs_review"
    else:
        status = "qualified"

    return PerillaAssessment(
        status=status,
        exclusion_reasons=tuple(dict.fromkeys(exclusions)),
        review_flags=tuple(dict.fromkeys(reviews)),
        positive_signals=tuple(dict.fromkeys(positives)),
    )
