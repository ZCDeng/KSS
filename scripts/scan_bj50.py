#!/usr/bin/env python3
"""北证 50 全量批量扫描 + 综合排序.

设计取舍:
    - 数据源: tushare ``daily`` + ``daily_basic`` + ``fina_indicator``,
      本地 CSV 缓存到 ``storage/bj_cache/`` 避免重复打接口.
    - 评分: 五维加权 (质量 30 / 成长 25 / 估值 20 / 强势 15 / 流动性 10).
      每维独立计算 0-1 归一化, 再加权求和 ∈ [0, 1].
    - 强负面剔除 (硬过滤): 上市 < 60 日 / Q1 净利 YoY < -50% / PE_TTM > 200
      (且分母正常) / 累计跌幅 < -50% 且趋势未止跌.
    - 紫苏叶卡位代理: 净利率 × 毛利率定性, 细分卡位 LLM 标注另外补.

输出:
    - ``storage/reports/bj50_scan/scan_<date>.csv`` 全量打分表
    - ``storage/reports/bj50_scan/scan_<date>.md`` Top 5 + Top 10 + 全榜 markdown 报告
    - 文件命名带日期, 多次跑覆盖当日同名文件.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 路径 (须在 import kss 之前: cron 用 Homebrew Python, kss 未装包, 靠 sys.path 定位)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient, _fetch_with_retry  # noqa: E402


CACHE_DIR = PROJECT_ROOT / "storage" / "bj_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = PROJECT_ROOT / "storage" / "reports" / "bj50_scan"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Tushare 历史截止日：禁止硬编码（曾写死 20260607 → App 北证日线全员停在 6 月）。
# 可用 env KSS_BJ_END_DATE=YYYYMMDD 覆盖（回测/定点复现）。
def _end_date_yyyymmdd() -> str:
    override = (os.environ.get("KSS_BJ_END_DATE") or "").strip()
    if len(override) == 8 and override.isdigit():
        return override
    return datetime.now().strftime("%Y%m%d")

# 评分维度权重 (V2: 北证特化, 加入相对动量 + 关注度代理)
WEIGHTS = {
    "quality": 0.30,      # 财务质量 (ROE/毛利/资负/净利率)
    "growth": 0.25,       # 成长 (4Q净利+营收+季度连续性)
    "value": 0.20,        # 估值 (PE_TTM 历史分位 + 绝对值)
    "momentum": 0.10,     # **相对动量** (个股收益 - 北证50指数收益)
    "attention": 0.10,    # 关注度代理 (股东户数趋势 + 换手率突破)
    "liquidity": 0.05,    # 流动性 (北证整体差, 降权)
}


def _safe_float(v: Any) -> float:
    """安全 float 转换: None / NaN / 字符串均→ nan, 不抛异常."""
    try:
        if v is None:
            return float("nan")
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


def fetch_bj50_constituents(pro: Any) -> pd.DataFrame:
    """拉北证 50 最新成分股 + 权重 + 基本信息."""
    # index_weight 是月度截面, 回看 60 天保证覆盖最近一期调样
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    iw = _fetch_with_retry(
        lambda: pro.index_weight(index_code="899050.BJ", start_date=start, end_date=end),
        f"index_weight 899050.BJ ({start}~{end})",
    )
    if iw is None:
        raise RuntimeError("北证50成分股拉取失败")
    # 取最新一期
    latest = iw["trade_date"].max()
    iw = iw[iw["trade_date"] == latest].copy()
    iw = iw.sort_values("weight", ascending=False).reset_index(drop=True)

    # 拉所有北证基本信息一次, 然后 merge.
    # 注: tushare ``exchange='BJ'`` 返回空 (920 段位识别 bug), 用 ``market='北交所'``.
    basic_all = _fetch_with_retry(
        lambda: pro.stock_basic(
            market="北交所",
            list_status="L",
            fields="ts_code,name,industry,list_date,fullname,area",
        ),
        "stock_basic (market=北交所)",
    )
    if basic_all is None:
        raise RuntimeError("北交所股票基本信息拉取失败")
    iw = iw.merge(basic_all, left_on="con_code", right_on="ts_code", how="left")
    return iw


def _cache_path(ts_code: str, kind: str) -> Path:
    return CACHE_DIR / f"{ts_code}_{kind}.csv"


def fetch_daily(pro: Any, ts_code: str, force: bool = False) -> pd.DataFrame:
    """拉日线 (含本地缓存)."""
    cache = _cache_path(ts_code, "daily")
    if cache.exists() and not force:
        df = pd.read_csv(cache, parse_dates=["trade_date"])
        return df
    df = pro.daily(ts_code=ts_code, start_date="20230101", end_date=_end_date_yyyymmdd())
    if df is None or df.empty:
        return pd.DataFrame()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def fetch_daily_basic(pro: Any, ts_code: str, force: bool = False) -> pd.DataFrame:
    """拉每日估值 (含缓存)."""
    cache = _cache_path(ts_code, "db")
    if cache.exists() and not force:
        df = pd.read_csv(cache, parse_dates=["trade_date"])
        return df
    df = pro.daily_basic(
        ts_code=ts_code,
        start_date="20230101",
        end_date=_end_date_yyyymmdd(),
        fields=(
            "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,"
            "pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv"
        ),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def fetch_fina(pro: Any, ts_code: str, force: bool = False) -> pd.DataFrame:
    """拉财务指标 (含缓存)."""
    cache = _cache_path(ts_code, "fina")
    if cache.exists() and not force:
        df = pd.read_csv(cache, parse_dates=["ann_date", "end_date"])
        return df
    df = pro.fina_indicator(
        ts_code=ts_code,
        start_date="20230101",
        end_date=_end_date_yyyymmdd(),
        fields=(
            "ts_code,ann_date,end_date,roe,roe_dt,grossprofit_margin,"
            "netprofit_margin,debt_to_assets,or_yoy,netprofit_yoy,"
            "q_sales_yoy,q_profit_yoy"
        ),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def fetch_holdernumber(pro: Any, ts_code: str, force: bool = False) -> pd.DataFrame:
    """拉股东户数 (季度披露, 含缓存).

    用于关注度代理: 户数减少 → 筹码集中 → 价值发现先行信号.
    """
    cache = _cache_path(ts_code, "holder")
    if cache.exists() and not force:
        df = pd.read_csv(cache, parse_dates=["ann_date", "end_date"])
        return df
    df = pro.stk_holdernumber(ts_code=ts_code, start_date="20230101", end_date=_end_date_yyyymmdd())
    if df is None or df.empty:
        return pd.DataFrame()
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def fetch_bj50_index(pro: Any, force: bool = False) -> pd.DataFrame:
    """拉北证 50 指数日线 (全局共享, 不按个股缓存).

    用于相对动量计算: ``stock_ret - bj50_ret``.
    """
    cache = CACHE_DIR / "_bj50_index.csv"
    if cache.exists() and not force:
        df = pd.read_csv(cache, parse_dates=["trade_date"])
        return df
    df = pro.index_daily(ts_code="899050.BJ", start_date="20230101", end_date=_end_date_yyyymmdd())
    if df is None or df.empty:
        raise RuntimeError("北证50指数日线拉取失败")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def fetch_all(pro: Any, ts_code: str, force: bool = False) -> dict[str, pd.DataFrame]:
    """并行拉 daily + db + fina + holdernumber."""
    return {
        "daily": fetch_daily(pro, ts_code, force=force),
        "db": fetch_daily_basic(pro, ts_code, force=force),
        "fina": fetch_fina(pro, ts_code, force=force),
        "holder": fetch_holdernumber(pro, ts_code, force=force),
    }


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def calc_indicators(
    ts_code: str, name: str, industry: str, list_date: str,
    daily: pd.DataFrame, db: pd.DataFrame, fina: pd.DataFrame,
    holder: pd.DataFrame, bj50_index: pd.DataFrame,
    today: pd.Timestamp,
) -> dict[str, Any] | None:
    """计算单只票全部指标 (未打分阶段).

    Args:
        bj50_index: 北证 50 指数日线 DataFrame (trade_date / close), 用于相对动量.
        holder: 股东户数 DataFrame (end_date / holder_num), 用于关注度代理.
    """
    if daily.empty or len(daily) < 20:
        return None

    # 截至今日
    daily = daily[daily["trade_date"] <= today].copy()
    if daily.empty:
        return None

    last = daily.iloc[-1]
    n_days = len(daily)
    list_dt = pd.to_datetime(list_date) if list_date else None
    days_listed = (today - list_dt).days if list_dt is not None else n_days

    # ---- 价格 / 动量 ----
    close = daily["close"]
    ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else np.nan
    ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 60 else np.nan
    ret_120d = float(close.iloc[-1] / close.iloc[-121] - 1) if len(close) > 120 else np.nan
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1] if len(close) > 60 else np.nan
    above_ma60 = bool(close.iloc[-1] > ma60) if pd.notna(ma60) else False

    # 累计回撤
    equity = (1 + close.pct_change().fillna(0)).cumprod()
    dd_now = float(equity.iloc[-1] / equity.cummax().iloc[-1] - 1)
    max_dd = float((equity / equity.cummax() - 1).min())

    # ---- 相对动量 (vs 北证 50) ----
    # 把指数对齐到个股交易日, 用同期收益差 stock_ret - idx_ret 表示相对强度.
    rel_ret_20d = rel_ret_60d = rel_ret_120d = np.nan
    idx_ret_60d = np.nan
    if not bj50_index.empty:
        idx = bj50_index[bj50_index["trade_date"] <= today].copy()
        if len(idx) > 20:
            # 用日线对齐: 找个股开始日对应的指数收盘
            idx_close = idx.set_index("trade_date")["close"]
            stock_dates = daily["trade_date"].values
            for h, key in [(20, "rel_ret_20d"), (60, "rel_ret_60d"), (120, "rel_ret_120d")]:
                if len(close) > h:
                    d_now = stock_dates[-1]
                    d_ref = stock_dates[-(h + 1)]
                    # 个股收益
                    s_ret = float(close.iloc[-1] / close.iloc[-(h + 1)] - 1)
                    # 指数收益: 用 asof 找最近的指数收盘
                    try:
                        idx_now = float(idx_close.asof(pd.Timestamp(d_now)))
                        idx_ref = float(idx_close.asof(pd.Timestamp(d_ref)))
                        i_ret = idx_now / idx_ref - 1 if idx_ref > 0 else np.nan
                        rel = s_ret - i_ret if pd.notna(i_ret) else np.nan
                        if h == 20: rel_ret_20d = rel
                        elif h == 60:
                            rel_ret_60d = rel
                            idx_ret_60d = i_ret
                        else: rel_ret_120d = rel
                    except Exception:
                        pass

    # ---- 估值 ----
    pe_ttm = float(last.get("close", np.nan))  # placeholder; 真值在 db
    pe_ttm = np.nan
    pb = np.nan
    pe_quantile = np.nan
    total_mv_yi = np.nan
    if not db.empty:
        db = db[db["trade_date"] <= today].copy()
        if not db.empty:
            db_last = db.iloc[-1]
            pe_ttm = _safe_float(db_last.get("pe_ttm"))
            pb = _safe_float(db_last.get("pb"))
            total_mv_yi = _safe_float(db_last.get("total_mv")) / 10000
            # PE_TTM 历史分位
            pe_series = db["pe_ttm"].dropna()
            pe_series = pe_series[pe_series > 0]  # 只看正 PE
            if len(pe_series) > 30 and pd.notna(pe_ttm) and pe_ttm > 0:
                pe_quantile = float((pe_series < pe_ttm).mean())

    # ---- 流动性 ----
    amount_mean_wan = float(daily["amount"].mean())  # 千元单位
    turnover_mean = float(db["turnover_rate"].mean()) if not db.empty and "turnover_rate" in db else np.nan

    # ---- 关注度代理 (V2 新增) ----
    # (1) 股东户数趋势: 最新 / 4 季度前 → 越小越好 (筹码集中)
    holder_latest = np.nan
    holder_change_4q = np.nan  # +20% = 散户涌入, -20% = 筹码集中
    holder_n_quarters = 0
    if not holder.empty:
        h = holder.sort_values("end_date").copy()
        # 取披露日 <= today
        h = h[h["ann_date"] <= today]
        if len(h) >= 1:
            holder_n_quarters = len(h)
            holder_latest = float(h["holder_num"].iloc[-1])
            # 用最近 4 季前作基准 (退化到现有最长样本)
            ref_idx = max(0, len(h) - 5)
            holder_ref = float(h["holder_num"].iloc[ref_idx])
            if holder_ref > 0:
                holder_change_4q = (holder_latest / holder_ref) - 1

    # (2) 关注度突破: 最近 20 日换手 / 过去 60 日换手 → > 1 = 升温
    attention_ratio = np.nan
    if not db.empty and "turnover_rate" in db.columns:
        tr = db.sort_values("trade_date")["turnover_rate"].dropna()
        if len(tr) >= 60:
            t20 = tr.iloc[-20:].mean()
            t60 = tr.iloc[-60:].mean()
            if t60 > 0.01:
                attention_ratio = float(t20 / t60)

    # ---- 财务 ----
    roe_ttm = np.nan
    netprofit_yoy_q = np.nan  # 最近一季度
    netprofit_yoy_mean_4q = np.nan  # 最近 4 季度同比平均
    or_yoy_mean_4q = np.nan
    gp_margin = np.nan
    np_margin = np.nan
    debt_ratio = np.nan
    n_positive_quarters = 0
    if not fina.empty:
        fi = fina.sort_values("end_date").reset_index(drop=True)
        # ROE TTM: 用最新季度年度 ROE (年报或最新季 cumulative)
        fi_last = fi.iloc[-1]
        netprofit_yoy_q = _safe_float(fi_last.get("netprofit_yoy"))
        gp_margin = _safe_float(fi_last.get("grossprofit_margin"))
        np_margin = _safe_float(fi_last.get("netprofit_margin"))
        debt_ratio = _safe_float(fi_last.get("debt_to_assets"))
        # 找最新年报 ROE
        end_dates = fi["end_date"]
        annual_mask = end_dates.dt.month == 12
        if annual_mask.any():
            roe_ttm = _safe_float(fi.loc[annual_mask, "roe"].iloc[-1])
        else:
            roe_ttm = _safe_float(fi_last.get("roe"))
        # 4 季度 YoY 平均
        last_4 = fi.tail(4)
        if "netprofit_yoy" in last_4.columns:
            yoys = last_4["netprofit_yoy"].dropna()
            if len(yoys) > 0:
                netprofit_yoy_mean_4q = float(yoys.mean())
                n_positive_quarters = int((yoys > 0).sum())
        if "or_yoy" in last_4.columns:
            or_yoys = last_4["or_yoy"].dropna()
            if len(or_yoys) > 0:
                or_yoy_mean_4q = float(or_yoys.mean())

    # ---- 描述性 ----
    rets = close.pct_change().dropna()
    vol_ann = float(rets.std() * np.sqrt(240))
    ann_ret = float((1 + rets).prod() ** (240 / max(len(rets), 1)) - 1) if len(rets) > 30 else np.nan

    return {
        "ts_code": ts_code,
        "name": name,
        "industry": industry,
        "list_date": list_date,
        "days_listed": days_listed,
        "n_days_data": n_days,
        # 价格
        "close": float(last["close"]),
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "ret_120d": ret_120d,
        "above_ma60": above_ma60,
        "dd_now": dd_now,
        "max_dd": max_dd,
        "ann_ret": ann_ret,
        "vol_ann": vol_ann,
        # 相对动量 (V2)
        "rel_ret_20d": rel_ret_20d,
        "rel_ret_60d": rel_ret_60d,
        "rel_ret_120d": rel_ret_120d,
        "idx_ret_60d": idx_ret_60d,
        # 估值
        "pe_ttm": pe_ttm,
        "pb": pb,
        "pe_quantile": pe_quantile,
        "total_mv_yi": total_mv_yi,
        # 流动性
        "amount_mean_wan": amount_mean_wan / 1000,  # 万元
        "turnover_mean": turnover_mean,
        # 关注度 (V2)
        "holder_latest": holder_latest,
        "holder_change_4q": holder_change_4q,
        "holder_n_quarters": holder_n_quarters,
        "attention_ratio": attention_ratio,
        # 财务
        "roe_ttm": roe_ttm,
        "netprofit_yoy_q": netprofit_yoy_q,
        "netprofit_yoy_mean_4q": netprofit_yoy_mean_4q,
        "or_yoy_mean_4q": or_yoy_mean_4q,
        "gp_margin": gp_margin,
        "np_margin": np_margin,
        "debt_ratio": debt_ratio,
        "n_positive_quarters": n_positive_quarters,
    }


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def score_quality(row: pd.Series) -> float:
    """质量分: ROE + 毛利 + 资负率 + 净利率."""
    s = 0.0
    roe = row.get("roe_ttm", np.nan)
    gp = row.get("gp_margin", np.nan)
    nm = row.get("np_margin", np.nan)
    db = row.get("debt_ratio", np.nan)

    # ROE (年报)
    if pd.notna(roe):
        if roe > 20: s += 0.35
        elif roe > 15: s += 0.28
        elif roe > 10: s += 0.18
        elif roe > 5: s += 0.08
        elif roe < 0: s -= 0.20

    # 毛利率
    if pd.notna(gp):
        if gp > 50: s += 0.25
        elif gp > 35: s += 0.18
        elif gp > 25: s += 0.10
        elif gp < 15: s -= 0.10

    # 净利率
    if pd.notna(nm):
        if nm > 20: s += 0.20
        elif nm > 10: s += 0.12
        elif nm > 5: s += 0.05
        elif nm < 0: s -= 0.15

    # 资产负债率
    if pd.notna(db):
        if db < 30: s += 0.20
        elif db < 50: s += 0.10
        elif db > 70: s -= 0.15

    return max(0.0, min(s, 1.0))


def score_growth(row: pd.Series) -> float:
    """成长分: 净利 4Q YoY 均值 + 营收 4Q YoY 均值 + 正季度数."""
    s = 0.0
    np_yoy_4 = row.get("netprofit_yoy_mean_4q", np.nan)
    or_yoy_4 = row.get("or_yoy_mean_4q", np.nan)
    n_pos = row.get("n_positive_quarters", 0)
    np_yoy_q = row.get("netprofit_yoy_q", np.nan)

    if pd.notna(np_yoy_4):
        if np_yoy_4 > 50: s += 0.40
        elif np_yoy_4 > 30: s += 0.30
        elif np_yoy_4 > 15: s += 0.20
        elif np_yoy_4 > 0: s += 0.10
        elif np_yoy_4 < -30: s -= 0.30
        elif np_yoy_4 < 0: s -= 0.10

    if pd.notna(or_yoy_4):
        if or_yoy_4 > 30: s += 0.20
        elif or_yoy_4 > 15: s += 0.12
        elif or_yoy_4 > 0: s += 0.05
        elif or_yoy_4 < -10: s -= 0.10

    # 季度同比连续性
    if n_pos == 4: s += 0.20
    elif n_pos == 3: s += 0.12
    elif n_pos <= 1: s -= 0.10

    # 最近季度 (反映近期景气拐点)
    if pd.notna(np_yoy_q):
        if np_yoy_q > 30: s += 0.10
        elif np_yoy_q < -30: s -= 0.20

    return max(0.0, min(s, 1.0))


def score_value(row: pd.Series) -> float:
    """估值分: PE_TTM 在自身历史分位中越低分越高 + 绝对 PE 合理性."""
    s = 0.5  # 起始中性
    pe = row.get("pe_ttm", np.nan)
    pe_q = row.get("pe_quantile", np.nan)

    # PE 历史分位
    if pd.notna(pe_q):
        if pe_q < 0.2: s += 0.35
        elif pe_q < 0.4: s += 0.20
        elif pe_q < 0.6: s += 0.05
        elif pe_q > 0.8: s -= 0.25
        else: s -= 0.10

    # PE 绝对值
    if pd.notna(pe) and pe > 0:
        if pe < 20: s += 0.15
        elif pe < 35: s += 0.05
        elif pe > 80: s -= 0.15
        elif pe > 50: s -= 0.05

    return max(0.0, min(s, 1.0))


def score_momentum(row: pd.Series) -> float:
    """相对动量分 (V2): 个股相对北证 50 指数的超额收益.

    在普跌环境下, 跌得少也是 alpha; 在普涨环境下, 跑赢指数才算强势.
    阈值在相对收益上设置, 比绝对动量更稳定.
    """
    s = 0.0
    rr20 = row.get("rel_ret_20d", np.nan)
    rr60 = row.get("rel_ret_60d", np.nan)
    rr120 = row.get("rel_ret_120d", np.nan)
    above_ma60 = row.get("above_ma60", False)
    # 个股绝对走势仅作均线补充, 不主导

    # 20 日相对动量 (短期)
    if pd.notna(rr20):
        if rr20 > 0.10: s += 0.20
        elif rr20 > 0.03: s += 0.12
        elif rr20 > 0: s += 0.05
        elif rr20 < -0.10: s -= 0.15
        elif rr20 < -0.03: s -= 0.05

    # 60 日相对动量 (中期, 权重最高)
    if pd.notna(rr60):
        if rr60 > 0.20: s += 0.30
        elif rr60 > 0.10: s += 0.22
        elif rr60 > 0.03: s += 0.15
        elif rr60 > 0: s += 0.08
        elif rr60 < -0.20: s -= 0.20
        elif rr60 < -0.10: s -= 0.10

    # 120 日相对动量 (长期趋势)
    if pd.notna(rr120):
        if rr120 > 0.30: s += 0.20
        elif rr120 > 0.10: s += 0.12
        elif rr120 > 0: s += 0.05
        elif rr120 < -0.30: s -= 0.10

    # 均线状态 (个股自身 MA60)
    if above_ma60: s += 0.15
    else: s -= 0.05

    return max(0.0, min(s, 1.0))


def score_attention(row: pd.Series) -> float:
    """关注度分 (V2 新增): 股东户数趋势 + 换手率突破.

    替代 Serenity 紫苏叶框架的 coverage_gap 维度
    (北证分析师覆盖近乎为 0, 该维度退化为常数).

    - 股东户数减少 → 筹码集中 → 价值发现先行信号
    - 近 20 日换手 vs 过去 60 日换手 → 关注度升温
    """
    s = 0.0
    hc = row.get("holder_change_4q", np.nan)
    ar = row.get("attention_ratio", np.nan)
    n_q = row.get("holder_n_quarters", 0)

    # (1) 股东户数 4 季度变化
    if pd.notna(hc) and n_q >= 2:
        if hc < -0.25: s += 0.45    # 户数减少 25%+ → 强筹码集中
        elif hc < -0.10: s += 0.30  # 减少 10%+ → 弱筹码集中
        elif hc < -0.05: s += 0.15
        elif hc < 0.10: s += 0.05   # 基本不变
        elif hc < 0.30: s -= 0.10   # 散户涌入
        else: s -= 0.25             # 散户暴增 (减持/解禁/炒作进场)

    # (2) 关注度突破: t20/t60 比值
    if pd.notna(ar):
        if ar > 2.0: s += 0.30      # 急剧升温 (突破 / 题材点燃)
        elif ar > 1.5: s += 0.22
        elif ar > 1.2: s += 0.15
        elif ar > 0.8: s += 0.08    # 平稳
        elif ar > 0.5: s -= 0.05
        else: s -= 0.15             # 关注度暴跌

    # 数据缺失 → 中性 (北证小票披露不规则)
    if pd.isna(hc) and pd.isna(ar):
        return 0.4

    return max(0.0, min(s + 0.3, 1.0))  # 起点 +0.3 防全员低分


def score_liquidity(row: pd.Series) -> float:
    """流动性分: 日均成交 + 换手率."""
    s = 0.0
    amt = row.get("amount_mean_wan", np.nan)  # 万元
    turnover = row.get("turnover_mean", np.nan)

    if pd.notna(amt):
        if amt > 5000: s += 0.50
        elif amt > 2000: s += 0.35
        elif amt > 1000: s += 0.20
        elif amt > 500: s += 0.10
        elif amt < 200: s -= 0.20

    if pd.notna(turnover):
        if turnover > 5: s += 0.30
        elif turnover > 2: s += 0.20
        elif turnover > 0.5: s += 0.10
        elif turnover < 0.2: s -= 0.10

    # 上限
    return max(0.0, min(s + 0.2, 1.0))  # 北证整体流动性差, 起点 +0.2 防全员低分


# ---------------------------------------------------------------------------
# 硬过滤 + 主流程
# ---------------------------------------------------------------------------


def hard_filter(row: pd.Series) -> tuple[bool, str]:
    """硬过滤: 返回 (是否通过, 失败原因)."""
    days = row.get("days_listed", 0)
    if days < 60:
        return False, "上市 < 60 日"

    np_yoy_q = row.get("netprofit_yoy_q", np.nan)
    if pd.notna(np_yoy_q) and np_yoy_q < -60:
        return False, f"最新季净利同比崩盘 ({np_yoy_q:.0f}%)"

    pe = row.get("pe_ttm", np.nan)
    if pd.notna(pe) and pe > 200 and pe > 0:
        return False, f"PE_TTM 极高 ({pe:.0f})"

    roe = row.get("roe_ttm", np.nan)
    if pd.notna(roe) and roe < -5:
        return False, f"ROE 大幅亏损 ({roe:.1f}%)"

    return True, ""


def perilla_tag(row: pd.Series) -> str:
    """紫苏叶代理标签 (基于行业 + 净利率 + 毛利率粗判)."""
    industry = str(row.get("industry", ""))
    gp = row.get("gp_margin", np.nan)
    nm = row.get("np_margin", np.nan)

    # 紫苏叶倾向行业 (Serenity 框架下的高 moat 子行业)
    high_moat_industries = {
        "半导体", "电器仪表", "专用机械", "通信设备", "医疗保健", "化学制药",
        "生物制药", "软件服务", "互联网", "其他电子",
    }
    low_moat_industries = {
        "钢铁", "煤炭", "化纤", "化工原料", "化肥", "建材", "包装材料",
    }

    if industry in low_moat_industries:
        return "C 价格战赛道"
    if industry in high_moat_industries and pd.notna(gp) and gp > 40:
        return "A 高 moat"
    if pd.notna(gp) and gp > 35 and pd.notna(nm) and nm > 15:
        return "B 中高 moat"
    if pd.notna(gp) and gp > 25:
        return "B 中 moat"
    return "C 低 moat"


def scan_one(
    pro: Any,
    con_row: pd.Series,
    bj50_index: pd.DataFrame,
    today: pd.Timestamp,
    force: bool,
) -> dict[str, Any] | None:
    """扫描单只票, 返回完整指标 dict (含打分)."""
    ts_code = con_row["ts_code"]
    name = con_row.get("name", "")
    industry = con_row.get("industry", "")
    list_date = con_row.get("list_date", "")
    weight = float(con_row.get("weight", 0))

    try:
        data = fetch_all(pro, ts_code, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] {ts_code} ({name}) 数据拉取失败: {exc}")
        return None

    ind = calc_indicators(
        ts_code, str(name), str(industry), str(list_date),
        data["daily"], data["db"], data["fina"],
        data["holder"], bj50_index,
        today,
    )
    if ind is None:
        return None

    ind["weight_idx"] = weight  # 指数权重
    return ind


def score_all(df: pd.DataFrame) -> pd.DataFrame:
    """对全表打分 + 排序 (在原 df 上加列)."""
    df = df.copy()

    # 硬过滤
    filters = df.apply(hard_filter, axis=1)
    df["pass"] = filters.map(lambda x: x[0])
    df["reject_reason"] = filters.map(lambda x: x[1])

    # 六维分 (V2: 新增 s_attention)
    df["s_quality"] = df.apply(score_quality, axis=1)
    df["s_growth"] = df.apply(score_growth, axis=1)
    df["s_value"] = df.apply(score_value, axis=1)
    df["s_momentum"] = df.apply(score_momentum, axis=1)
    df["s_attention"] = df.apply(score_attention, axis=1)
    df["s_liquidity"] = df.apply(score_liquidity, axis=1)

    df["total_score"] = (
        df["s_quality"] * WEIGHTS["quality"]
        + df["s_growth"] * WEIGHTS["growth"]
        + df["s_value"] * WEIGHTS["value"]
        + df["s_momentum"] * WEIGHTS["momentum"]
        + df["s_attention"] * WEIGHTS["attention"]
        + df["s_liquidity"] * WEIGHTS["liquidity"]
    )
    # 未通过硬过滤的票, total_score 仍计算但排序时下沉
    df["sort_key"] = np.where(df["pass"], df["total_score"], df["total_score"] - 10)

    # 紫苏叶代理
    df["perilla_tag"] = df.apply(perilla_tag, axis=1)

    df = df.sort_values("sort_key", ascending=False).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def fmt_pct(x: float, signed: bool = True) -> str:
    if pd.isna(x):
        return "-"
    if signed:
        return f"{x*100:+.1f}%" if abs(x) < 100 else f"{x:+.0f}"
    return f"{x*100:.1f}%"


def fmt_pct_raw(x: float) -> str:
    """已经是百分数形式的指标 (ROE/毛利等 tushare 返回直接 % 数值)."""
    return f"{x:+.1f}%" if pd.notna(x) else "-"


def render_top_row(rank: int, row: pd.Series, detailed: bool = True) -> str:
    """单只票详细 markdown."""
    lines = []
    badge = row["perilla_tag"]
    lines.append(
        f"### #{rank} {row['name']} ({row['ts_code']}) — {badge}, 总分 {row['total_score']:.2f}\n"
    )
    lines.append(
        f"- 行业: {row['industry']} · 总市值 {row['total_mv_yi']:.1f} 亿 · 指数权重 {row['weight_idx']:.2f}%"
    )
    lines.append(
        f"- 收盘 {row['close']:.2f} · PE_TTM {row['pe_ttm']:.0f} (历史 {row['pe_quantile']*100 if pd.notna(row['pe_quantile']) else 0:.0f}% 分位) · PB {row['pb']:.1f}"
    )
    lines.append(
        f"- 财务: ROE {fmt_pct_raw(row['roe_ttm'])}, 毛利率 {fmt_pct_raw(row['gp_margin'])}, "
        f"净利率 {fmt_pct_raw(row['np_margin'])}, 资负率 {fmt_pct_raw(row['debt_ratio'])}"
    )
    lines.append(
        f"- 成长: 4Q 净利 YoY 均值 {fmt_pct_raw(row['netprofit_yoy_mean_4q'])} ({row['n_positive_quarters']}/4 正), "
        f"营收 YoY 均值 {fmt_pct_raw(row['or_yoy_mean_4q'])}, 最新季净利同比 {fmt_pct_raw(row['netprofit_yoy_q'])}"
    )
    lines.append(
        f"- 动量(绝对): 20d {fmt_pct(row['ret_20d'])} / 60d {fmt_pct(row['ret_60d'])} / 120d {fmt_pct(row['ret_120d'])}, "
        f"MA60 {'上' if row['above_ma60'] else '下'}, 现回撤 {fmt_pct(row['dd_now'])}"
    )
    lines.append(
        f"- **动量(相对北证50)**: 20d {fmt_pct(row['rel_ret_20d'])} / 60d {fmt_pct(row['rel_ret_60d'])} / 120d {fmt_pct(row['rel_ret_120d'])} "
        f"(同期指数 60d {fmt_pct(row['idx_ret_60d'])})"
    )
    lines.append(
        f"- **关注度**: 股东户数 {row['holder_latest']:.0f} 户, 4季度变化 {fmt_pct(row['holder_change_4q'])}, "
        f"换手 t20/t60 = {row['attention_ratio']:.2f}x" if pd.notna(row.get('attention_ratio', np.nan)) else f"- **关注度**: 数据不全"
    )
    lines.append(
        f"- 流动性: 日均 {row['amount_mean_wan']:.0f} 万 · 换手 {row['turnover_mean']:.1f}%"
    )
    lines.append(
        f"- 六维分: 质量 {row['s_quality']:.2f} · 成长 {row['s_growth']:.2f} · 估值 {row['s_value']:.2f} · "
        f"动量 {row['s_momentum']:.2f} · 关注度 {row['s_attention']:.2f} · 流动性 {row['s_liquidity']:.2f}"
    )
    if not row["pass"]:
        lines.append(f"- ⚠️ **未通过硬过滤**: {row['reject_reason']}")
    return "\n".join(lines) + "\n"


def render_report(df: pd.DataFrame, today: pd.Timestamp) -> str:
    """完整 markdown 报告."""
    lines: list[str] = []
    date_str = today.strftime("%Y-%m-%d")
    lines.append(f"# 北证 50 全量扫描报告 — {date_str}\n")
    lines.append(
        f"- 样本: 北证 50 成分股 ({len(df)} 只), 数据截止 {date_str}"
    )
    lines.append(
        f"- 评分: 质量 {WEIGHTS['quality']*100:.0f}% · 成长 {WEIGHTS['growth']*100:.0f}% · "
        f"估值 {WEIGHTS['value']*100:.0f}% · **相对动量** {WEIGHTS['momentum']*100:.0f}% · "
        f"**关注度** {WEIGHTS['attention']*100:.0f}% · 流动性 {WEIGHTS['liquidity']*100:.0f}%"
    )
    lines.append(
        f"- V2 北证特化: 动量改用相对北证50指数; 关注度用股东户数趋势+换手率突破\n"
    )
    lines.append(
        f"- 硬过滤: 上市<60日 / Q1净利同比<-60% / PE_TTM>200 / ROE<-5%\n"
    )

    passed = df[df["pass"]].copy()
    rejected = df[~df["pass"]].copy()

    lines.append(f"## TL;DR — 值得跟踪的 5 只\n")
    if len(passed) >= 5:
        for i, (_, row) in enumerate(passed.head(5).iterrows(), 1):
            lines.append(render_top_row(i, row))
    lines.append("---\n")

    # Top 10
    lines.append("## Top 10 总览\n")
    lines.append(
        "| 排名 | 代码 | 名称 | 行业 | 卡位 | 总分 | 质 | 长 | 估 | **势** | **注** | 流 | PE | ROE | 4Q净利YoY | **rel60d** | 户数Δ |"
    )
    lines.append("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, (_, row) in enumerate(passed.head(10).iterrows(), 1):
        lines.append(
            f"| {i} | {row['ts_code']} | {row['name']} | {row['industry']} | "
            f"{row['perilla_tag']} | {row['total_score']:.2f} | "
            f"{row['s_quality']:.2f} | {row['s_growth']:.2f} | {row['s_value']:.2f} | "
            f"{row['s_momentum']:.2f} | {row['s_attention']:.2f} | {row['s_liquidity']:.2f} | "
            f"{row['pe_ttm']:.0f} | {fmt_pct_raw(row['roe_ttm'])} | "
            f"{fmt_pct_raw(row['netprofit_yoy_mean_4q'])} | {fmt_pct(row['rel_ret_60d'])} | "
            f"{fmt_pct(row['holder_change_4q'])} |"
        )
    lines.append("")

    # 硬过滤掉的票
    if len(rejected) > 0:
        lines.append(f"## 硬过滤拒绝 ({len(rejected)} 只)\n")
        lines.append("| 代码 | 名称 | 行业 | 原因 | 总分 |")
        lines.append("|---|---|---|---|---:|")
        for _, row in rejected.iterrows():
            lines.append(
                f"| {row['ts_code']} | {row['name']} | {row['industry']} | "
                f"{row['reject_reason']} | {row['total_score']:.2f} |"
            )
        lines.append("")

    # 全榜
    lines.append("## 全 50 只评分表\n")
    lines.append(
        "| # | 代码 | 名称 | 行业 | 总分 | 卡位 | PE | ROE | 4Q净利YoY | rel60d | 户数Δ | 现回撤 |"
    )
    lines.append("|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for i, (_, row) in enumerate(df.iterrows(), 1):
        flag = "" if row["pass"] else "❌"
        lines.append(
            f"| {i}{flag} | {row['ts_code']} | {row['name']} | {row['industry']} | "
            f"{row['total_score']:.2f} | {row['perilla_tag']} | "
            f"{row['pe_ttm']:.0f} | {fmt_pct_raw(row['roe_ttm'])} | "
            f"{fmt_pct_raw(row['netprofit_yoy_mean_4q'])} | {fmt_pct(row['rel_ret_60d'])} | "
            f"{fmt_pct(row['holder_change_4q'])} | {fmt_pct(row['dd_now'])} |"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram 推送 (V2)
# ---------------------------------------------------------------------------


def _load_prev_scan(today: pd.Timestamp, days_back: int = 7) -> pd.DataFrame:
    """找前一交易日的扫描 CSV (向前最多 7 天).

    Returns:
        前一日的 score 表; 找不到返回空 df.
    """
    for delta in range(1, days_back + 1):
        prev = today - pd.Timedelta(days=delta)
        candidate = OUT_DIR / f"scan_{prev.strftime('%Y%m%d')}.csv"
        if candidate.exists():
            try:
                df = pd.read_csv(candidate)
                df["_prev_date"] = prev.strftime("%Y-%m-%d")
                return df
            except Exception:
                continue
    return pd.DataFrame()


def _rank_arrow(today_rank: int, prev_rank: int | None) -> str:
    """生成 ↑↓ 排名变化标记."""
    if prev_rank is None:
        return "(NEW)"
    delta = prev_rank - today_rank  # 正数 = 上升
    if delta == 0: return "(-)"
    if delta > 0: return f"(↑{delta})"
    return f"(↓{-delta})"


def build_telegram_message(
    df_today: pd.DataFrame, df_prev: pd.DataFrame,
    bj50_index: pd.DataFrame, today: pd.Timestamp,
) -> str:
    """构造北证 50 扫描日报 Telegram 消息 (Markdown 格式).

    包含:
        - 当日指数表现
        - Top 5 (vs 昨日排名变化)
        - 户数 +30% 散户涌入 / -25% 筹码集中 警告
        - 新增硬过滤拒绝 (一季报炸雷)
    """
    lines: list[str] = []
    date_str = today.strftime("%Y-%m-%d")
    lines.append(f"🌾 *北证50扫描 · {date_str}*")

    # 当日指数
    if not bj50_index.empty:
        idx = bj50_index.sort_values("trade_date")
        idx_today = idx[idx["trade_date"] <= today].iloc[-1] if len(idx) > 0 else None
        if idx_today is not None:
            pct = idx_today.get("pct_chg", 0)
            lines.append(f"📊 指数: {idx_today['close']:.0f} ({pct:+.2f}%)")
    lines.append("")

    passed = df_today[df_today["pass"]].copy().reset_index(drop=True)
    rejected = df_today[~df_today["pass"]].copy()

    # 前一日排名映射
    prev_ranks: dict[str, int] = {}
    prev_passed_ts: set[str] = set()
    if not df_prev.empty and "pass" in df_prev.columns:
        prev_passed = df_prev[df_prev["pass"]].reset_index(drop=True)
        prev_ranks = {row["ts_code"]: i + 1 for i, row in prev_passed.iterrows()}
        prev_passed_ts = set(prev_passed["ts_code"])

    # Top 5
    lines.append("*🏆 Top 5* (vs 昨日)")
    for i, (_, row) in enumerate(passed.head(5).iterrows(), 1):
        prev_rank = prev_ranks.get(row["ts_code"])
        arrow = _rank_arrow(i, prev_rank)
        lines.append(
            f"`{i}` *{row['name']}* `{row['ts_code']}` {arrow} *{row['total_score']:.2f}*"
        )
        # 六维 + 关键数据
        lines.append(
            f"   质{row['s_quality']:.2f} 长{row['s_growth']:.2f} 估{row['s_value']:.2f} "
            f"势{row['s_momentum']:.2f} 注{row['s_attention']:.2f}"
        )
        hc = row.get("holder_change_4q")
        hc_str = f"{hc*100:+.0f}%" if pd.notna(hc) else "-"
        rel60 = row.get("rel_ret_60d")
        rel_str = f"{rel60*100:+.0f}%" if pd.notna(rel60) else "-"
        lines.append(
            f"   PE{row['pe_ttm']:.0f} ROE{row['roe_ttm']:.0f}% rel60d {rel_str} 户数Δ {hc_str}"
        )
    lines.append("")

    # 关注度警告
    alerts: list[str] = []
    if "holder_change_4q" in df_today.columns:
        # 散户暴增 (>+50%): 顶部信号
        flood = df_today[
            df_today["pass"] & (df_today["holder_change_4q"] > 0.50)
        ].sort_values("holder_change_4q", ascending=False)
        for _, r in flood.head(5).iterrows():
            alerts.append(f"⚠️ *{r['name']}* `{r['ts_code']}`: 户数+{r['holder_change_4q']*100:.0f}% 散户暴增")

        # 筹码极度集中 (<-25%)
        concentr = df_today[
            df_today["pass"] & (df_today["holder_change_4q"] < -0.25)
        ].sort_values("holder_change_4q")
        for _, r in concentr.head(5).iterrows():
            alerts.append(f"💎 *{r['name']}* `{r['ts_code']}`: 户数{r['holder_change_4q']*100:.0f}% 筹码集中")

    if alerts:
        lines.append("*🔔 关注度警告*")
        lines.extend(alerts)
        lines.append("")

    # 新增硬过滤 (今日新雷)
    if not df_prev.empty and "pass" in df_prev.columns and len(rejected) > 0:
        prev_rejected_ts = set(df_prev[~df_prev["pass"]]["ts_code"]) if "pass" in df_prev.columns else set()
        new_rejects = rejected[~rejected["ts_code"].isin(prev_rejected_ts)]
        if len(new_rejects) > 0:
            lines.append("*🔴 今日新雷*")
            for _, r in new_rejects.head(5).iterrows():
                lines.append(f"❌ *{r['name']}* `{r['ts_code']}`: {r['reject_reason']}")
            lines.append("")

    # 出/入榜变化
    if prev_passed_ts:
        today_passed_ts = set(passed["ts_code"])
        new_in_top10 = []
        dropped_from_top10 = []
        for i, (_, row) in enumerate(passed.head(10).iterrows(), 1):
            if row["ts_code"] not in {c for c, r in prev_ranks.items() if r <= 10}:
                new_in_top10.append(f"{row['name']} ({row['ts_code']})")
        for code, prev_r in prev_ranks.items():
            if prev_r <= 10:
                today_idx = passed.index[passed["ts_code"] == code].tolist()
                today_r = today_idx[0] + 1 if today_idx else 999
                if today_r > 10:
                    dropped_from_top10.append(code)
        if new_in_top10:
            lines.append("*📈 新进 Top 10*: " + ", ".join(new_in_top10))
        if dropped_from_top10:
            lines.append(f"*📉 跌出 Top 10*: {len(dropped_from_top10)} 只")

    lines.append("")
    lines.append(f"_扫描 {len(df_today)}/50, 通过硬过滤 {len(passed)}, 拒绝 {len(rejected)}_")
    lines.append(f"_对比基准: {df_prev['_prev_date'].iloc[0] if not df_prev.empty else '无昨日数据'}_")

    return "\n".join(lines)


def push_telegram(message: str) -> bool:
    """调 TelegramBot 发送 (失败仅 print, 不抛)."""
    try:
        from kss.notifications.telegram_bot import TelegramBot
        bot = TelegramBot()
        ok = bot.send(message, parse_mode="Markdown")
        print(f"  [telegram] sent={ok}")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"  [telegram] 推送失败: {exc}")
        return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="北证 50 全量扫描 (V2)")
    parser.add_argument("--force-refresh", action="store_true", help="强制刷新缓存")
    parser.add_argument("--threads", type=int, default=4, help="并发线程数")
    parser.add_argument(
        "--today", type=str, default="",
        help="报告时点 YYYY-MM-DD, 缺省取今天 (cron 用)",
    )
    parser.add_argument(
        "--push-telegram", action="store_true",
        help="推送 Top 5 + 关键变动到 Telegram (cron 用)",
    )
    args = parser.parse_args()

    pro = TushareClient()._pro
    if args.today:
        today = pd.Timestamp(args.today)
    else:
        today = pd.Timestamp.now().normalize()

    print("[1/3] 拉北证 50 成分股 ...")
    constituents = fetch_bj50_constituents(pro)
    print(f"  → {len(constituents)} 只成分股, 调样日 {constituents['trade_date'].iloc[0]}")
    print(f"  → 权重 top5: {constituents.head(5)[['con_code', 'name', 'weight']].values.tolist()}")

    print("  → 拉北证 50 指数日线 (相对动量基准) ...")
    bj50_index = fetch_bj50_index(pro, force=args.force_refresh)
    print(f"  → 指数样本 {len(bj50_index)} 日 ({bj50_index['trade_date'].iloc[0].strftime('%Y-%m-%d')} → {bj50_index['trade_date'].iloc[-1].strftime('%Y-%m-%d')})")

    print(f"[2/3] 批量扫描 (并发 {args.threads}) ...")
    rows: list[dict[str, Any]] = []
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = {
            pool.submit(scan_one, pro, row, bj50_index, today, args.force_refresh): row["ts_code"]
            for _, row in constituents.iterrows()
        }
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                res = fut.result()
                if res is not None:
                    rows.append(res)
                    print(f"  ✓ {code} ({res['name']})")
                else:
                    print(f"  ✗ {code}: 数据不足")
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {code}: {exc}")
    elapsed = time.time() - start
    print(f"  → 完成 {len(rows)}/{len(constituents)} 只, 用时 {elapsed:.1f}s")

    if not rows:
        raise RuntimeError("无数据可打分")

    df = pd.DataFrame(rows)

    print("[3/3] 打分 + 渲染报告 ...")
    df = score_all(df)

    # 落盘
    date_str = today.strftime("%Y%m%d")
    csv_path = OUT_DIR / f"scan_{date_str}.csv"
    md_path = OUT_DIR / f"scan_{date_str}.md"
    df.to_csv(csv_path, index=False)
    md_path.write_text(render_report(df, today), encoding="utf-8")

    # console summary
    print("\n" + "=" * 80)
    print(f"扫描完成. 输出:")
    print(f"  CSV: {csv_path}")
    print(f"  MD:  {md_path}\n")

    passed = df[df["pass"]]
    print(f"Top 5 (通过硬过滤共 {len(passed)} 只):")
    for i, (_, row) in enumerate(passed.head(5).iterrows(), 1):
        print(
            f"  #{i} {row['name']} ({row['ts_code']}) {row['industry']:8s} "
            f"总分 {row['total_score']:.2f} | "
            f"质 {row['s_quality']:.2f} 长 {row['s_growth']:.2f} 估 {row['s_value']:.2f} "
            f"势 {row['s_momentum']:.2f} 注 {row['s_attention']:.2f} 流 {row['s_liquidity']:.2f} | "
            f"PE {row['pe_ttm']:.0f} ROE {row['roe_ttm']:.1f}% rel60d {row['rel_ret_60d']*100:+.0f}% 户数Δ {row['holder_change_4q']*100:+.0f}%"
        )

    if len(df) - len(passed) > 0:
        print(f"\n硬过滤拒绝 ({len(df) - len(passed)} 只):")
        for _, row in df[~df["pass"]].iterrows():
            print(f"  ✗ {row['name']} ({row['ts_code']}): {row['reject_reason']}")

    # Telegram 推送 (V2 cron)
    if args.push_telegram:
        print("\n[push] 构造 Telegram 消息 ...")
        df_prev = _load_prev_scan(today)
        if df_prev.empty:
            print("  [push] 找不到前一日扫描 CSV (向前 7 天), 仍发送当日 Top 5")
        else:
            print(f"  [push] 对比基准: {df_prev['_prev_date'].iloc[0]}")
        msg = build_telegram_message(df, df_prev, bj50_index, today)
        print("  [push] 消息预览:")
        for line in msg.split("\n"):
            print(f"    | {line}")
        push_telegram(msg)


if __name__ == "__main__":
    main()
