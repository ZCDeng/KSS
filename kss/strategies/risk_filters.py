"""个股风险前置硬过滤 —— P4 of Bolton 周期框架.

在 combo_scan 进入 bootstrap 模式匹配之前先做硬过滤，剔除以下风险敞口：

1. **高杠杆**：资产负债率超出行业 L1 历史分位（默认 80 分位）
2. **低流动性**：过去 N 日均成交额低于阈值（默认 5000 万 / 20 日）
3. **ST / 退市**：名称带 ST / 已设 delist_date / 连续 N 年亏损 / 净资产为负

调用入口::

    from kss.strategies.risk_filters import apply_all_filters

    kept, removed = apply_all_filters(symbols, trade_date="20260524")
    print(f"过滤前 {len(symbols)}, 过滤后 {len(kept)}")
    for reason, n in Counter(r['reason'] for r in removed).items():
        print(f"  - {reason}: {n}")

设计：

- **失败安全 (fail-safe-include)**：缺数据的股票 **保留**，不剔除
  （避免数据问题误杀正常股；plan §Risks 第 4 行）
- **配置化**：阈值/开关在 ``kss/config/risk_filters.yaml``，热加载
- **缓存**：财务数据按季度落地 ``storage/macro/fina_quarterly.parquet``
- **零外部 IO 默认**：过滤函数接受预加载 DataFrame 注入；只有 cache miss
  时才调 Tushare，便于单测
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

logger = logging.getLogger(__name__)


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "risk_filters.yaml"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FINA_CACHE = _PROJECT_ROOT / "storage" / "macro" / "fina_quarterly.parquet"
_STOCK_BASIC_CACHE = _PROJECT_ROOT / "storage" / "macro" / "stock_basic.parquet"


# ---------------------------------------------------------------------------- #
# Public dataclasses
# ---------------------------------------------------------------------------- #


@dataclass
class FilterResult:
    """过滤结果：保留池 + 剔除明细（带理由）.

    Attributes:
        kept: 通过所有过滤的 ts_code list.
        removed: ``[{"ts_code": str, "reason": str, "detail": str}, ...]``.
            ``reason`` ∈ ``{"leverage", "liquidity", "st_risk"}``.
    """

    kept: list[str]
    removed: list[dict[str, str]]


# ---------------------------------------------------------------------------- #
# Config
# ---------------------------------------------------------------------------- #


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """读取 risk_filters.yaml；缺失返回内置 defaults."""
    p = Path(path) if path else _CONFIG_PATH
    if not p.exists():
        logger.warning("risk_filters.yaml 不存在，使用默认配置: %s", p)
        return _DEFAULT_CONFIG.copy()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # 浅合并：缺失子键回退 default
    out = {**_DEFAULT_CONFIG, **data}
    for key in ("leverage", "liquidity", "st_risk"):
        out[key] = {**_DEFAULT_CONFIG[key], **data.get(key, {})}
    return out


_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "leverage": {
        "enabled": True,
        "industry_quantile": 0.80,
        "fallback_absolute_max": 0.85,
        "industry_level": "L1",
        "min_industry_peers": 5,
    },
    "liquidity": {
        "enabled": True,
        "window_days": 20,
        "min_daily_amount_yuan": 50_000_000,
        "min_valid_days": 10,
    },
    "st_risk": {
        "enabled": True,
        "exclude_st_in_name": True,
        "exclude_with_delist_date": True,
        "exclude_consecutive_loss_years": 2,
        "exclude_negative_equity": True,
    },
}


# ---------------------------------------------------------------------------- #
# 1. 高杠杆过滤
# ---------------------------------------------------------------------------- #


def filter_high_leverage(
    symbols: Iterable[str],
    fina_panel: pd.DataFrame,
    industry_map: dict[str, str],
    config: dict[str, Any] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """按行业分位剔除高杠杆股.

    Args:
        symbols: 候选 ts_code.
        fina_panel: 含 ``ts_code`` + ``debt_to_assets`` + ``end_date`` 的最新季报.
            每股取最新一行；缺数据的股票按 fail-safe-include 保留.
        industry_map: ``{ts_code: industry_name}``；缺股按 fail-safe-include 保留.
        config: 配置 dict.

    Returns:
        ``(kept, removed)`` —— removed 元素 ``{"ts_code","reason":"leverage","detail":...}``.
    """
    cfg = (config or load_config())["leverage"]
    if not cfg.get("enabled", True):
        return list(symbols), []

    quantile = float(cfg["industry_quantile"])
    fallback_max = float(cfg["fallback_absolute_max"])
    min_peers = int(cfg["min_industry_peers"])

    # 每股取最新一行
    fina_latest = _latest_per_stock(fina_panel, key="ts_code", date_col="end_date")
    dta_map = dict(zip(fina_latest.get("ts_code", []), fina_latest.get("debt_to_assets", [])))

    # 行业 → debt_to_assets 列表
    industry_buckets: dict[str, list[float]] = defaultdict(list)
    for code, dta in dta_map.items():
        ind = industry_map.get(code)
        if ind and dta is not None and dta == dta:    # not NaN
            industry_buckets[ind].append(float(dta))

    kept: list[str] = []
    removed: list[dict[str, str]] = []
    for code in symbols:
        dta = dta_map.get(code)
        if dta is None or dta != dta:
            kept.append(code)    # fail-safe-include
            continue
        dta = float(dta)
        ind = industry_map.get(code)
        peers = industry_buckets.get(ind, [])
        threshold: float | None = None
        if len(peers) >= min_peers:
            threshold = float(pd.Series(peers).quantile(quantile))
        else:
            threshold = fallback_max
        if dta > threshold:
            removed.append({
                "ts_code": code,
                "reason": "leverage",
                "detail": f"debt_to_assets={dta:.3f} > {threshold:.3f} "
                          f"(industry={ind or 'N/A'}, n_peers={len(peers)})",
            })
        else:
            kept.append(code)
    return kept, removed


# ---------------------------------------------------------------------------- #
# 2. 低流动性过滤
# ---------------------------------------------------------------------------- #


def filter_low_liquidity(
    symbols: Iterable[str],
    daily_amounts: dict[str, pd.Series],
    config: dict[str, Any] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """按过去 N 日均成交额剔除低流动性股.

    Args:
        symbols: 候选 ts_code.
        daily_amounts: ``{ts_code: Series of daily amount in yuan}``；
            索引为 trade_date（升序），值为成交额（元）.
            Series 单位假设是 **元**；如调用方有"千元"原始单位需先换算.
            缺股按 fail-safe-include 保留.
        config: 配置 dict.

    Returns:
        ``(kept, removed)``.
    """
    cfg = (config or load_config())["liquidity"]
    if not cfg.get("enabled", True):
        return list(symbols), []

    window = int(cfg["window_days"])
    min_amt = float(cfg["min_daily_amount_yuan"])
    min_valid = int(cfg["min_valid_days"])

    kept: list[str] = []
    removed: list[dict[str, str]] = []
    for code in symbols:
        s = daily_amounts.get(code)
        if s is None or s.empty:
            kept.append(code)    # 缺数据保留
            continue
        tail = s.tail(window).dropna()
        if len(tail) < min_valid:
            kept.append(code)
            continue
        avg = float(tail.mean())
        if avg < min_amt:
            removed.append({
                "ts_code": code,
                "reason": "liquidity",
                "detail": f"avg_amount_{window}d={avg:,.0f}yuan < {min_amt:,.0f}yuan",
            })
        else:
            kept.append(code)
    return kept, removed


# ---------------------------------------------------------------------------- #
# 3. ST / 退市风险
# ---------------------------------------------------------------------------- #


def filter_st_risk(
    symbols: Iterable[str],
    stock_basic: pd.DataFrame,
    fina_panel: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """ST 名称 / delist_date / 连续亏损 / 净资产为负 多重检查.

    Args:
        symbols: 候选 ts_code.
        stock_basic: 含 ``ts_code`` / ``name`` / ``delist_date`` 的 DataFrame.
        fina_panel: 多季财务面板（含 ``ts_code`` / ``end_date`` /
            ``n_income_attr_p`` / ``bps``）；用于连续亏损 + 净资产为负.
            为 ``None`` 时跳过财报兜底.
        config: 配置 dict.

    Returns:
        ``(kept, removed)``.
    """
    cfg = (config or load_config())["st_risk"]
    if not cfg.get("enabled", True):
        return list(symbols), []

    name_map: dict[str, str] = {}
    delist_map: dict[str, Any] = {}
    if stock_basic is not None and not stock_basic.empty:
        for _, r in stock_basic.iterrows():
            code = str(r.get("ts_code", ""))
            if not code:
                continue
            name_map[code] = str(r.get("name", ""))
            d = r.get("delist_date")
            if d is not None and pd.notna(d) and str(d).strip():
                delist_map[code] = str(d)

    loss_years = int(cfg.get("exclude_consecutive_loss_years", 0))
    check_neg_equity = bool(cfg.get("exclude_negative_equity", True))
    loss_map, neg_eq_set = _build_fina_risk_maps(
        fina_panel, consecutive_loss_years=loss_years,
        check_negative_equity=check_neg_equity,
    )

    kept: list[str] = []
    removed: list[dict[str, str]] = []
    for code in symbols:
        nm = name_map.get(code, "")
        if cfg.get("exclude_st_in_name", True) and ("ST" in nm.upper()):
            removed.append({
                "ts_code": code, "reason": "st_risk",
                "detail": f"name='{nm}' contains ST",
            })
            continue
        if cfg.get("exclude_with_delist_date", True) and code in delist_map:
            removed.append({
                "ts_code": code, "reason": "st_risk",
                "detail": f"delist_date={delist_map[code]}",
            })
            continue
        if loss_years and loss_map.get(code, 0) >= loss_years:
            removed.append({
                "ts_code": code, "reason": "st_risk",
                "detail": f"consecutive_loss_years={loss_map[code]} >= {loss_years}",
            })
            continue
        if check_neg_equity and code in neg_eq_set:
            removed.append({
                "ts_code": code, "reason": "st_risk",
                "detail": "bps <= 0 (negative equity)",
            })
            continue
        kept.append(code)
    return kept, removed


# ---------------------------------------------------------------------------- #
# 顶层 orchestrator
# ---------------------------------------------------------------------------- #


def apply_all_filters(
    symbols: Iterable[str],
    fina_panel: pd.DataFrame | None = None,
    industry_map: dict[str, str] | None = None,
    daily_amounts: dict[str, pd.Series] | None = None,
    stock_basic: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
) -> FilterResult:
    """三道过滤串行 + 理由聚合.

    顺序：ST/退市 → 高杠杆 → 低流动性（ST 命中的股不再算后两步，省 IO）.

    Args:
        symbols: 候选 ts_code iterable.
        fina_panel / industry_map / daily_amounts / stock_basic: 同各子函数；
            为 ``None`` 时对应过滤器走 fail-safe-include 路径.
        config: 配置 dict；``None`` 自动加载.

    Returns:
        :class:`FilterResult`.
    """
    cfg = config or load_config()
    syms = list(symbols)
    if not cfg.get("enabled", True):
        return FilterResult(kept=syms, removed=[])

    all_removed: list[dict[str, str]] = []
    current = syms

    if stock_basic is not None or fina_panel is not None:
        sb = stock_basic if stock_basic is not None else pd.DataFrame()
        current, removed = filter_st_risk(
            current, sb, fina_panel=fina_panel, config=cfg,
        )
        all_removed.extend(removed)

    if fina_panel is not None and industry_map is not None:
        current, removed = filter_high_leverage(
            current, fina_panel, industry_map, config=cfg,
        )
        all_removed.extend(removed)

    if daily_amounts is not None:
        current, removed = filter_low_liquidity(
            current, daily_amounts, config=cfg,
        )
        all_removed.extend(removed)

    return FilterResult(kept=current, removed=all_removed)


def summarize_removed(removed: list[dict[str, str]]) -> dict[str, int]:
    """按 reason 聚合数量；给 Telegram / 控制台 banner 用."""
    counts: dict[str, int] = defaultdict(int)
    for r in removed:
        counts[r["reason"]] += 1
    return dict(counts)


# ---------------------------------------------------------------------------- #
# Data helpers —— cache + cs_data 桥接
# ---------------------------------------------------------------------------- #


def load_fina_cache(path: Path | None = None) -> pd.DataFrame | None:
    """读财务季频缓存；不存在返回 ``None``."""
    p = path or _FINA_CACHE
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save_fina_cache(df: pd.DataFrame, path: Path | None = None) -> None:
    """落地财务季频缓存（按 ts_code+end_date 去重 + 升级）."""
    p = path or _FINA_CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        old = pd.read_parquet(p)
        df = pd.concat([old, df]).drop_duplicates(
            subset=["ts_code", "end_date"], keep="last",
        )
    df.sort_values(["ts_code", "end_date"]).reset_index(drop=True).to_parquet(p, index=False)


def load_stock_basic_cache(path: Path | None = None) -> pd.DataFrame | None:
    """读 stock_basic 缓存；不存在返回 ``None``."""
    p = path or _STOCK_BASIC_CACHE
    if not p.exists():
        return None
    return pd.read_parquet(p)


def daily_amounts_from_cs_data(
    symbols: Iterable[str],
    project_root: Path | None = None,
    window: int = 20,
) -> dict[str, pd.Series]:
    """从 ``cs_data_<sym>.csv`` 抽取每股最近 N 日成交额（元）.

    cs_data 的 ``amount`` 列单位是**千元**（Tushare 约定），返回 Series 已 × 1000.

    Args:
        symbols: 6 位裸码 list（如 ``"688322"``）.
        project_root: 项目根；``None`` 自动定位.
        window: 取最后 N 行（含一定 buffer 避免周末 / 停牌缺日）.

    Returns:
        ``{ts_code (6 位裸码): Series}``；缺文件 / 缺列的股不进 dict.
    """
    root = project_root or _PROJECT_ROOT
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        # 接受裸码或带后缀
        base = str(sym).split(".")[0]
        fp = root / f"cs_data_{base}.csv"
        if not fp.exists():
            continue
        try:
            df = pd.read_csv(fp, usecols=["trade_date", "amount"])
        except Exception:
            continue
        if "amount" not in df.columns:
            continue
        df = df.sort_values("trade_date").tail(window * 2)
        s = pd.Series(df["amount"].values * 1000.0, index=df["trade_date"].astype(str))
        out[base] = s
    return out


def _latest_per_stock(
    df: pd.DataFrame | None,
    key: str = "ts_code",
    date_col: str = "end_date",
) -> pd.DataFrame:
    if df is None or df.empty or key not in df.columns:
        return pd.DataFrame(columns=[key, "debt_to_assets"])
    sorted_ = df.sort_values([key, date_col])
    return sorted_.groupby(key, as_index=False).tail(1)


def _build_fina_risk_maps(
    fina_panel: pd.DataFrame | None,
    consecutive_loss_years: int,
    check_negative_equity: bool,
) -> tuple[dict[str, int], set[str]]:
    """从多季财务面板算 ``{code: consecutive_loss_years}`` 与净资产为负 set.

    年度统计：按 ``end_date`` 后 4 位 == ``'1231'`` 取年报；
    依次倒序统计连续亏损年数.
    """
    loss_map: dict[str, int] = {}
    neg_eq: set[str] = set()
    if fina_panel is None or fina_panel.empty or "ts_code" not in fina_panel.columns:
        return loss_map, neg_eq

    df = fina_panel.copy()
    df["end_date"] = df["end_date"].astype(str)
    if "n_income_attr_p" in df.columns and consecutive_loss_years:
        annual = df[df["end_date"].str.endswith("1231")].copy()
        annual = annual.sort_values(["ts_code", "end_date"], ascending=[True, False])
        for code, sub in annual.groupby("ts_code", sort=False):
            count = 0
            for v in sub["n_income_attr_p"]:
                if v is None or v != v:
                    break
                if float(v) < 0:
                    count += 1
                else:
                    break
            if count > 0:
                loss_map[str(code)] = count

    if check_negative_equity and "bps" in df.columns:
        latest = _latest_per_stock(df, key="ts_code", date_col="end_date")
        for _, r in latest.iterrows():
            bps = r.get("bps")
            if bps is not None and bps == bps and float(bps) <= 0:
                neg_eq.add(str(r["ts_code"]))
    return loss_map, neg_eq
