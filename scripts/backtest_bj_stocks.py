#!/usr/bin/env python3
"""北证两只票（胜业电气 920128 / 同惠电子 920509）回测.

设计取舍:
    - 北证不支持 ``moneyflow`` 接口, 因此跳过资金流维度;
    - 样本期短(胜业 ~366 日 / 同惠 ~585 日), **拒绝 LightGBM 单股训练** (必然 overfit),
      改用 rule-based 信号 + 描述性统计 + 紫苏叶卡位定性;
    - 不进入 ``scan_combo_signals`` 横截面排序 (北证不在该 universe).

输出:
    - ``storage/reports/bj_backtest/<code>_report.md`` 单股报告
    - ``storage/reports/bj_backtest/<code>_panels.png`` 多面板图(价/MACD/RSI/动量/估值)
    - ``storage/reports/bj_backtest/<code>_data.csv`` 落盘清洗后数据
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kss.data.tushare_client import TushareClient
from kss.features.technical import TechnicalFactors
from kss.supply_chain.registry import ChainRegistry

# 输出目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "storage" / "reports" / "bj_backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 交易成本
BUY_COST = 0.001
SELL_COST = 0.002

# 中文字体
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


def fetch_data(
    pro: Any, ts_code: str, start: str = "20230101", end: str = "20260607"
) -> pd.DataFrame:
    """拉日线 + 估值快照, 合并为统一 DataFrame.

    Args:
        pro: Tushare pro_api 实例.
        ts_code: 北证代码, 如 ``920128.BJ``.
        start: 起始日期 YYYYMMDD.
        end: 结束日期 YYYYMMDD.

    Returns:
        含 OHLCV + 估值列, 按 trade_date 升序.
    """
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        raise RuntimeError(f"daily 接口返回空: {ts_code}")
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 估值
    db = pro.daily_basic(
        ts_code=ts_code,
        start_date=start,
        end_date=end,
        fields=(
            "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,"
            "pe,pe_ttm,pb,ps_ttm,total_mv,circ_mv"
        ),
    )
    if db is not None and not db.empty:
        db["trade_date"] = pd.to_datetime(db["trade_date"])
        df = df.merge(
            db.drop(columns=["ts_code"]),
            on="trade_date",
            how="left",
        )
    else:
        for col in ("turnover_rate", "volume_ratio", "pe", "pe_ttm", "pb", "ps_ttm", "total_mv", "circ_mv"):
            df[col] = np.nan
    return df


def fetch_fina(pro: Any, ts_code: str) -> pd.DataFrame:
    """拉财务指标(8 个季度)."""
    fi = pro.fina_indicator(
        ts_code=ts_code,
        start_date="20230101",
        end_date="20260601",
        fields=(
            "ts_code,ann_date,end_date,roe,roe_dt,grossprofit_margin,"
            "netprofit_margin,debt_to_assets,or_yoy,netprofit_yoy,"
            "q_sales_yoy,q_profit_yoy"
        ),
    )
    if fi is None or fi.empty:
        return pd.DataFrame()
    fi["end_date"] = pd.to_datetime(fi["end_date"])
    return fi.sort_values("end_date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 因子
# ---------------------------------------------------------------------------


def build_factors(df: pd.DataFrame) -> pd.DataFrame:
    """生成技术因子 (复用 ``kss.features.TechnicalFactors``).

    Returns:
        在原 df 上 join 进各因子列.
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["vol"]

    factors: dict[str, pd.Series] = {}
    factors.update(TechnicalFactors.momentum(c, periods=(1, 5, 10, 20, 60)))
    factors.update(TechnicalFactors.moving_averages(c, periods=(5, 10, 20, 60)))
    factors.update(TechnicalFactors.macd(c))
    factors.update(TechnicalFactors.rsi(c))
    factors.update(TechnicalFactors.kdj(h, l, c))
    factors.update(TechnicalFactors.bollinger(c))
    factors.update(TechnicalFactors.candlestick(o, h, l, c))
    factors.update(TechnicalFactors.price_position(c, h, l))

    out = df.copy()
    for k, s in factors.items():
        out[k] = s

    # BBI (KSS 习用): 3/6/12/24 均线均值
    bbi = (
        c.rolling(3).mean()
        + c.rolling(6).mean()
        + c.rolling(12).mean()
        + c.rolling(24).mean()
    ) / 4.0
    out["bbi"] = bbi
    out["bbi_bull"] = (c > bbi).astype(int)

    # 量比 / 量能放大
    out["vol_ma5_ratio"] = v / v.rolling(5).mean()
    out["amt_ma5_ratio"] = df["amount"] / df["amount"].rolling(5).mean()

    # 未来收益, 用于信号统计
    for p in (1, 5, 10, 20):
        out[f"fwd_ret_{p}d"] = c.shift(-p) / c - 1
    return out


# ---------------------------------------------------------------------------
# 信号 (rule-based)
# ---------------------------------------------------------------------------


def gen_signals(df: pd.DataFrame) -> dict[str, pd.Series]:
    """生成多个独立信号 (bool Series).

    Returns:
        信号名 → bool 序列 (与 df 同长).
    """
    signals: dict[str, pd.Series] = {}

    # MACD 金叉: 当日 macd_hist > 0 且前日 ≤ 0
    mh = df["macd_hist"]
    signals["MACD金叉"] = (mh > 0) & (mh.shift(1) <= 0)

    # MACD 死叉: 反之
    signals["MACD死叉"] = (mh < 0) & (mh.shift(1) >= 0)

    # BBI 转多: 收盘上穿 BBI
    bbi = df["bbi"]
    c = df["close"]
    signals["BBI转多"] = (c > bbi) & (c.shift(1) <= bbi.shift(1))
    signals["BBI转空"] = (c < bbi) & (c.shift(1) >= bbi.shift(1))

    # RSI 极值反转
    rsi = df["rsi_14"]
    signals["RSI<30反弹"] = (rsi.shift(1) < 30) & (rsi >= 30)
    signals["RSI>70回落"] = (rsi.shift(1) > 70) & (rsi <= 70)

    # 量价突破: 20 日新高 + 量比 > 1.5
    high20 = df["high"].rolling(20).max().shift(1)
    signals["量价突破20"] = (c > high20) & (df["vol_ma5_ratio"] > 1.5)

    # 布林带跌破 + 反弹
    boll_pos = df.get("boll_position")
    if boll_pos is not None:
        signals["布林下轨反弹"] = (boll_pos.shift(1) < -0.95) & (boll_pos >= -0.95)

    # 高位放量: 量比 > 2.5 + 收盘在 60 日 80% 分位以上
    rolling_pct = df["close"].rolling(60).apply(
        lambda x: (x.rank().iloc[-1] - 1) / max(len(x) - 1, 1), raw=False
    )
    signals["高位放量"] = (df["vol_ma5_ratio"] > 2.5) & (rolling_pct > 0.8)

    return signals


def evaluate_signal(
    df: pd.DataFrame, mask: pd.Series, horizons: tuple[int, ...] = (5, 10, 20)
) -> dict[str, Any]:
    """评估单信号: 触发次数 + 各 horizon 上的胜率/均值.

    Args:
        df: 含 ``fwd_ret_{h}d`` 列的因子表.
        mask: 信号触发 bool 序列.
        horizons: 持有天数列表.

    Returns:
        ``{"n": 触发数, "5d": {"win": ..., "mean": ..., "median": ...}, ...}``.
    """
    out: dict[str, Any] = {"n": int(mask.sum())}
    for h in horizons:
        col = f"fwd_ret_{h}d"
        if col not in df.columns:
            continue
        rets = df.loc[mask, col].dropna()
        if rets.empty:
            out[f"{h}d"] = {"win": np.nan, "mean": np.nan, "median": np.nan, "n_valid": 0}
            continue
        out[f"{h}d"] = {
            "win": float((rets > 0).mean()),
            "mean": float(rets.mean()),
            "median": float(rets.median()),
            "n_valid": int(len(rets)),
        }
    return out


# ---------------------------------------------------------------------------
# 描述性统计
# ---------------------------------------------------------------------------


def descriptive_stats(df: pd.DataFrame) -> dict[str, Any]:
    """收益分布 / 波动 / 流动性核心统计."""
    ret = df["close"].pct_change().dropna()
    log_ret = np.log1p(ret)
    # 累计收益 + 最大回撤
    equity = (1 + ret).cumprod()
    rolling_max = equity.cummax()
    dd = equity / rolling_max - 1
    # 年化口径 (240 个交易日)
    ann_ret = float((equity.iloc[-1]) ** (240 / len(equity)) - 1)
    ann_vol = float(log_ret.std() * np.sqrt(240))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
    return {
        "n_days": int(len(df)),
        "date_range": (
            df["trade_date"].iloc[0].strftime("%Y-%m-%d"),
            df["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
        ),
        "ret_mean": float(ret.mean()),
        "ret_std": float(ret.std()),
        "skew": float(ret.skew()),
        "kurt": float(ret.kurt()),
        "ann_ret": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": float(sharpe) if not np.isnan(sharpe) else None,
        "max_drawdown": float(dd.min()),
        "amplitude_mean": float(((df["high"] - df["low"]) / df["close"]).mean()),
        "turnover_mean": float(df["turnover_rate"].mean()) if "turnover_rate" in df else np.nan,
        "turnover_max": float(df["turnover_rate"].max()) if "turnover_rate" in df else np.nan,
        "amount_mean_wan": float(df["amount"].mean()),  # 单位 千元 → 万元
        "amount_p10_wan": float(df["amount"].quantile(0.1)),
        "limit_up_days": int(((df["close"] / df["pre_close"] - 1) > 0.295).sum()),
        "limit_dn_days": int(((df["close"] / df["pre_close"] - 1) < -0.295).sum()),
    }


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------


def plot_panels(df: pd.DataFrame, ts_code: str, name: str) -> Path:
    """4 面板: 价格+均线/MACD/RSI/估值."""
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    d = df["trade_date"]

    # 1) 价 + BBI + MA20 + MA60
    ax = axes[0]
    ax.plot(d, df["close"], lw=1.2, color="#333", label="收盘")
    if "bbi" in df:
        ax.plot(d, df["bbi"], lw=0.9, color="#d62728", label="BBI", alpha=0.8)
    if df["close"].rolling(20).mean().notna().any():
        ax.plot(d, df["close"].rolling(20).mean(), lw=0.8, color="#1f77b4", label="MA20", alpha=0.7)
    if df["close"].rolling(60).mean().notna().any():
        ax.plot(d, df["close"].rolling(60).mean(), lw=0.8, color="#2ca02c", label="MA60", alpha=0.7)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"{name} ({ts_code}) - 价格 / BBI / 均线", fontsize=12)
    ax.grid(alpha=0.3)

    # 2) MACD
    ax = axes[1]
    ax.bar(d, df["macd_hist"], color=np.where(df["macd_hist"] > 0, "#d62728", "#2ca02c"), width=1.0, alpha=0.6)
    ax.plot(d, df["macd"], lw=0.9, color="#1f77b4", label="DIF")
    ax.plot(d, df["macd_signal"], lw=0.9, color="#ff7f0e", label="DEA")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_title("MACD", fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)

    # 3) RSI
    ax = axes[2]
    ax.plot(d, df["rsi_14"], lw=1.0, color="#9467bd")
    ax.axhline(70, color="#d62728", lw=0.6, ls="--")
    ax.axhline(30, color="#2ca02c", lw=0.6, ls="--")
    ax.set_ylim(0, 100)
    ax.set_title("RSI(14)", fontsize=11)
    ax.grid(alpha=0.3)

    # 4) PE_TTM + 换手率(右轴)
    ax = axes[3]
    if "pe_ttm" in df:
        ax.plot(d, df["pe_ttm"], lw=1.0, color="#8c564b", label="PE_TTM")
        ax.set_ylabel("PE_TTM")
        ax.legend(loc="upper left", fontsize=9)
    ax2 = ax.twinx()
    if "turnover_rate" in df:
        ax2.plot(d, df["turnover_rate"], lw=0.6, color="#7f7f7f", alpha=0.5, label="换手率")
        ax2.set_ylabel("换手率 %")
        ax2.legend(loc="upper right", fontsize=9)
    ax.set_title("PE_TTM & 换手率", fontsize=11)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / f"{ts_code}_panels.png"
    plt.savefig(out, dpi=110, bbox_inches="tight")
    plt.close()
    return out


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def fmt_pct(x: float) -> str:
    return f"{x*100:+.2f}%" if pd.notna(x) else "-"


def render_signal_table(signal_results: dict[str, dict[str, Any]]) -> str:
    """渲染信号表 (markdown)."""
    lines = [
        "| 信号 | 触发 | 5d 胜率 | 5d 均值 | 10d 胜率 | 10d 均值 | 20d 胜率 | 20d 均值 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, res in signal_results.items():
        row = [name, str(res.get("n", 0))]
        for h in (5, 10, 20):
            r = res.get(f"{h}d", {})
            win = r.get("win")
            mean = r.get("mean")
            row.append(f"{win*100:.0f}%" if win is not None and pd.notna(win) else "-")
            row.append(fmt_pct(mean) if mean is not None else "-")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_report(
    ts_code: str,
    name: str,
    industry: str,
    list_date: str,
    df: pd.DataFrame,
    stats: dict[str, Any],
    signal_results: dict[str, dict[str, Any]],
    fina: pd.DataFrame,
    chain_info: Any | None,
    chain_score: float,
    chain_tag: str,
    panels_path: Path,
    verdict: str,
) -> str:
    """生成 markdown 报告."""
    lines: list[str] = []
    lines.append(f"# {name} ({ts_code}) 北证回测报告\n")
    lines.append(f"- 行业: {industry}")
    lines.append(f"- 上市日期: {list_date}")
    lines.append(f"- 样本: {stats['date_range'][0]} → {stats['date_range'][1]} ({stats['n_days']} 个交易日)")
    lines.append(f"- 报告时点: {df['trade_date'].iloc[-1].strftime('%Y-%m-%d')}, 收盘 {df['close'].iloc[-1]:.2f}\n")

    # 综合判定
    lines.append("## TL;DR\n")
    lines.append(verdict + "\n")

    # 描述性
    lines.append("## 一、描述性统计 (样本期内)\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 年化收益 | {fmt_pct(stats['ann_ret'])} |")
    lines.append(f"| 年化波动 | {fmt_pct(stats['ann_vol'])} |")
    lines.append(f"| 夏普 | {stats['sharpe']:.2f} |" if stats["sharpe"] is not None else "| 夏普 | - |")
    lines.append(f"| 最大回撤 | {fmt_pct(stats['max_drawdown'])} |")
    lines.append(f"| 日均振幅 | {fmt_pct(stats['amplitude_mean'])} |")
    lines.append(f"| 日均换手 | {stats['turnover_mean']:.1f}% (峰值 {stats['turnover_max']:.1f}%) |")
    lines.append(f"| 日均成交 | {stats['amount_mean_wan']/1000:.0f} 万元 (10%分位 {stats['amount_p10_wan']/1000:.0f} 万) |")
    lines.append(f"| 涨停日数 (>29.5%) | {stats['limit_up_days']} 日 |")
    lines.append(f"| 跌停日数 (<-29.5%) | {stats['limit_dn_days']} 日 |")
    lines.append(f"| 收益偏度 / 峰度 | {stats['skew']:.2f} / {stats['kurt']:.2f} |\n")

    # 估值快照
    last = df.iloc[-1]
    lines.append("## 二、估值快照 (最新)\n")
    lines.append("| 指标 | 数值 | 样本分位 |")
    lines.append("|---|---|---|")
    for col, label in [("pe_ttm", "PE_TTM"), ("pb", "PB"), ("ps_ttm", "PS_TTM"), ("total_mv", "总市值(万元)")]:
        if col in df.columns and pd.notna(last[col]):
            series = df[col].dropna()
            quantile = float((series < last[col]).mean())
            val_str = f"{last[col]:.2f}" if col != "total_mv" else f"{last[col]/10000:.1f} 亿"
            lines.append(f"| {label} | {val_str} | {quantile*100:.0f}% |")
    lines.append("")

    # 财务
    lines.append("## 三、财务面 (近 6 个报告期)\n")
    if not fina.empty:
        fi = fina.sort_values("end_date", ascending=False).head(6).copy()
        fi["end_date"] = fi["end_date"].dt.strftime("%Y-%m-%d")
        lines.append("| 报告期 | ROE | 毛利率 | 净利率 | 资产负债率 | 营收YoY | 净利YoY |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in fi.iterrows():
            lines.append(
                "| {end_date} | {roe:.2f}% | {gp:.1f}% | {np:.1f}% | {db:.1f}% | {oy:.1f}% | {ny:.1f}% |".format(
                    end_date=r["end_date"],
                    roe=r.get("roe", np.nan) if pd.notna(r.get("roe")) else 0,
                    gp=r.get("grossprofit_margin", np.nan) if pd.notna(r.get("grossprofit_margin")) else 0,
                    np=r.get("netprofit_margin", np.nan) if pd.notna(r.get("netprofit_margin")) else 0,
                    db=r.get("debt_to_assets", np.nan) if pd.notna(r.get("debt_to_assets")) else 0,
                    oy=r.get("or_yoy", np.nan) if pd.notna(r.get("or_yoy")) else 0,
                    ny=r.get("netprofit_yoy", np.nan) if pd.notna(r.get("netprofit_yoy")) else 0,
                )
            )
        lines.append("")
    else:
        lines.append("无财务数据.\n")

    # 信号回测
    lines.append("## 四、Rule-based 信号回测 (信号触发后 5/10/20 日表现)\n")
    lines.append(render_signal_table(signal_results))
    lines.append("\n注: 胜率 = 信号触发后该 horizon 内收益 > 0 的比例; 均值 = 平均收益率(未扣交易成本).")
    lines.append("由于样本短(< 600 日), 多数信号触发数 < 10 次, 解读时需保留怀疑.\n")

    # 紫苏叶
    lines.append("## 五、紫苏叶产业链卡位定性\n")
    if chain_info is not None:
        lines.append(f"- 评分: {chain_score:.2f} ({chain_tag})")
        lines.append(f"- 需求链: {', '.join(chain_info.demand_chains)}")
        lines.append(f"- 链路深度: layer {chain_info.chain_layer} ({chain_info.chain_role})")
        lines.append(f"- 全球竞争者: {chain_info.n_competitors_global} 家")
        lines.append(f"- 国产替代: {chain_info.n_competitors_domestic} 家")
        lines.append(f"- 可替代性: {chain_info.substitutability}")
        lines.append(f"- 需求锁定: {chain_info.demand_locked}")
        lines.append(f"- 备注: {chain_info.analyst_notes}")
    else:
        lines.append("- 该票未进入 `kss/config/supply_chain.yaml` 标注库 (北证不在原 universe).")
        lines.append("- 评分: N/A — 紫苏叶维度需手动判定 (本报告 TL;DR 已给出).")
    lines.append("")

    # 图
    lines.append("## 六、多面板技术图\n")
    lines.append(f"![panels](./{panels_path.name})\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


# 手动卡位评估 (北证两只均未在 supply_chain.yaml 标注库)
# 这里给出基于公开资料的人工判断
MANUAL_CHAIN: dict[str, dict[str, Any]] = {
    "920128.BJ": {
        "name": "胜业电气",
        "demand_chains": ["新能源车", "光伏储能", "工业自动化"],
        "chain_layer": 5,  # 元器件层
        "chain_role": "薄膜电容/电感 (被动器件)",
        "n_competitors_global": 15,  # 村田/TDK/三星电机/Vishay/EPCOS/Kemet/AVX/华润/江海/法拉电子/...
        "n_competitors_domestic": 8,
        "substitutability": "high",  # 被动器件可替代性强
        "expansion_cycle_years": 1.0,
        "demand_locked": False,
        "verdict": "**非紫苏叶**. 处于元器件层 (供应链深度足够), 但全球玩家 15+、国产替代 8+、产品高度同质化, "
                   "在 Serenity「数玩家」框架下属于价格战赛道. 估值溢价 (PE_TTM 237) 缺乏护城河支撑.",
    },
    "920509.BJ": {
        "name": "同惠电子",
        "demand_chains": ["半导体测试", "电子制造检测", "国产仪器自主可控"],
        "chain_layer": 4,  # 测试仪器
        "chain_role": "LCR表/阻抗分析仪/直流电源 (电子测量仪器)",
        "n_competitors_global": 8,   # 是德/罗德施瓦茨/吉时利/HIOKI/同惠/固纬/艾德克斯/致远电子
        "n_competitors_domestic": 4,
        "substitutability": "medium",  # 高端仪器有技术门槛
        "expansion_cycle_years": 2.0,
        "demand_locked": True,  # 自主可控 + 半导体测试需求绑定
        "verdict": "**半紫苏叶**. 在国产电子测量仪器细分赛道处中端位置, 国内主要玩家 4 家, 受益于半导体测试 + "
                   "自主可控双轮驱动 (毛利率 56-59%、ROE 19.4%、资产负债率仅 11% 印证护城河). "
                   "估值高 (PE_TTM 80) 但有基本面支撑, 需观察是否能进入高端 (是德/罗德 替代).",
    },
}


def _resolve_chain(ts_code: str, registry: ChainRegistry) -> tuple[Any | None, float, str]:
    """优先用 YAML, 缺失则用手动 MANUAL_CHAIN."""
    info = registry.get(ts_code)
    if info is not None:
        return info, registry.score(ts_code), registry.format_tag(ts_code)

    manual = MANUAL_CHAIN.get(ts_code)
    if manual is None:
        return None, 0.0, ""

    # 构造一个 lightweight 占位 (复用 StockChainInfo 字段)
    from kss.supply_chain.registry import StockChainInfo

    info = StockChainInfo(
        ts_code=ts_code,
        name=manual["name"],
        demand_chains=tuple(manual["demand_chains"]),
        chain_layer=manual["chain_layer"],
        chain_role=manual["chain_role"],
        n_competitors_global=manual["n_competitors_global"],
        n_competitors_domestic=manual["n_competitors_domestic"],
        substitutability=manual["substitutability"],
        expansion_cycle_years=manual["expansion_cycle_years"],
        demand_locked=manual["demand_locked"],
        analyst_count=0,
        analyst_notes=manual["verdict"],
    )

    # 用 scoring 模块算分 (registry.score 不接受手动注入, 这里直接调 scoring)
    from kss.supply_chain.scoring import compute_perilla_score

    score = compute_perilla_score(info, registry._config)
    tag_emoji = "🌿" if score >= 0.6 else ("·" if score >= 0.3 else "")
    return info, score, f"{tag_emoji}{score:.2f}"


def run_one(pro: Any, ts_code: str, registry: ChainRegistry) -> None:
    """跑单只票完整回测."""
    # 基本信息
    bs = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry,list_date")
    if bs is None or bs.empty:
        raise RuntimeError(f"无 stock_basic: {ts_code}")
    row = bs.iloc[0]
    name = row["name"].strip()
    industry = row["industry"]
    list_date = row["list_date"]

    print(f"\n===== {name} ({ts_code}) =====")
    print(f"行业: {industry}, 上市: {list_date}")

    # 数据
    df = fetch_data(pro, ts_code)
    fina = fetch_fina(pro, ts_code)
    df = build_factors(df)

    # 落盘
    csv_path = OUT_DIR / f"{ts_code}_data.csv"
    df.to_csv(csv_path, index=False)

    # 描述性
    stats = descriptive_stats(df)
    print(f"  样本 {stats['n_days']} 日, 年化 {stats['ann_ret']*100:+.1f}%, 波动 {stats['ann_vol']*100:.1f}%, 最大回撤 {stats['max_drawdown']*100:.1f}%")
    print(f"  日均换手 {stats['turnover_mean']:.1f}%, 涨停 {stats['limit_up_days']} 日 / 跌停 {stats['limit_dn_days']} 日")

    # 信号
    sigs = gen_signals(df)
    sig_res: dict[str, dict[str, Any]] = {}
    for sname, mask in sigs.items():
        sig_res[sname] = evaluate_signal(df, mask)
        n = sig_res[sname]["n"]
        d5 = sig_res[sname].get("5d", {})
        print(
            f"  [信号] {sname}: n={n}, 5d胜率 "
            f"{d5.get('win', 0)*100 if d5.get('win') is not None else 0:.0f}%, "
            f"均值 {d5.get('mean', 0)*100 if d5.get('mean') is not None else 0:+.2f}%"
        )

    # 紫苏叶
    chain_info, chain_score, chain_tag = _resolve_chain(ts_code, registry)
    if chain_info:
        print(f"  紫苏叶: score={chain_score:.2f}, {chain_info.chain_role}, 全球竞争 {chain_info.n_competitors_global} 家")

    # 综合判定 (手写, 基于基本面 + 信号 + 卡位)
    verdict = compose_verdict(ts_code, df, stats, sig_res, fina, chain_info, chain_score)

    # 绘图
    panels_path = plot_panels(df, ts_code, name)

    # 报告
    md = render_report(
        ts_code=ts_code,
        name=name,
        industry=industry,
        list_date=list_date,
        df=df,
        stats=stats,
        signal_results=sig_res,
        fina=fina,
        chain_info=chain_info,
        chain_score=chain_score,
        chain_tag=chain_tag,
        panels_path=panels_path,
        verdict=verdict,
    )
    report_path = OUT_DIR / f"{ts_code}_report.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"  报告: {report_path}")
    print(f"  图: {panels_path}")
    print(f"  CSV: {csv_path}")


def compose_verdict(
    ts_code: str,
    df: pd.DataFrame,
    stats: dict[str, Any],
    sig_res: dict[str, dict[str, Any]],
    fina: pd.DataFrame,
    chain_info: Any | None,
    chain_score: float,
) -> str:
    """根据基本面 + 信号 + 卡位写一段中文综合判定."""
    last = df.iloc[-1]
    pe = last.get("pe_ttm", np.nan)
    pb = last.get("pb", np.nan)
    bbi_bull = bool(last.get("bbi_bull", 0))
    rsi = float(last.get("rsi_14", np.nan))

    fina_last = fina.iloc[-1] if not fina.empty else None
    roe = fina_last.get("roe") if fina_last is not None else np.nan
    np_yoy = fina_last.get("netprofit_yoy") if fina_last is not None else np.nan
    gp = fina_last.get("grossprofit_margin") if fina_last is not None else np.nan

    bullets = []
    bullets.append(f"- **当前**: 收盘 {last['close']:.2f}, BBI {'多' if bbi_bull else '空'}头, RSI {rsi:.0f}, PE_TTM {pe:.0f}, PB {pb:.1f}.")

    # 基本面
    if fina_last is not None:
        verdict_fina = []
        if pd.notna(roe):
            verdict_fina.append(f"最新季度 ROE {roe:.1f}%")
        if pd.notna(np_yoy):
            verdict_fina.append(f"净利同比 {np_yoy:+.1f}%")
        if pd.notna(gp):
            verdict_fina.append(f"毛利率 {gp:.1f}%")
        bullets.append(f"- **基本面**: " + ", ".join(verdict_fina) + ".")

    # 紫苏叶
    if chain_info is not None:
        manual = MANUAL_CHAIN.get(ts_code)
        if manual:
            bullets.append(f"- **产业链卡位**: {manual['verdict']}")
        else:
            bullets.append(f"- **产业链卡位**: 紫苏叶评分 {chain_score:.2f}, {chain_info.chain_role}.")
    else:
        bullets.append("- **产业链卡位**: 无标注数据.")

    # 信号近期触发
    recent_signals = []
    for sname, res in sig_res.items():
        # 取最近 20 日内有无触发
        if res["n"] == 0:
            continue
        col_signals = sname  # 仅描述
        # 由于我们没有原始 mask 列, 这里简化为整段样本胜率 > 60% 的为可参考
        d5 = res.get("5d", {})
        if d5.get("win") is not None and d5["win"] > 0.6 and res["n"] >= 5:
            recent_signals.append(f"{sname}(5d胜率{d5['win']*100:.0f}%)")
    if recent_signals:
        bullets.append(f"- **可参考信号** (历史 5d 胜率 >60%, n≥5): {', '.join(recent_signals)}.")
    else:
        bullets.append("- **信号**: 无统计显著(n≥5 & 5d胜率>60%)的 rule-based 信号. 样本太短, 不足以稳定建仓.")

    # 流动性 + 北证风险
    bullets.append(
        f"- **流动性**: 日均成交 {stats['amount_mean_wan']/1000:.0f} 万元 (10%分位 {stats['amount_p10_wan']/1000:.0f} 万), "
        f"日均换手 {stats['turnover_mean']:.1f}%. 北证 ±30% 涨跌幅放大波动, 进出需控制资金占比."
    )

    return "\n".join(bullets)


def main() -> None:
    parser = argparse.ArgumentParser(description="北证两只票回测")
    parser.add_argument(
        "--codes", nargs="+", default=["920128.BJ", "920509.BJ"],
        help="ts_code 列表 (默认胜业 920128.BJ + 同惠 920509.BJ)",
    )
    args = parser.parse_args()

    pro = TushareClient()._pro
    registry = ChainRegistry.from_yaml()

    for code in args.codes:
        try:
            run_one(pro, code, registry)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {code}: {exc}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
