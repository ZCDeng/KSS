#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多指标共振实时监控系统.

用法：``python monitor.py [股票代码] [股票代码] ...``
示例：``python monitor.py 688322 688017 000001``

依赖：本脚本使用 ``kss.data.TushareClient`` 调 Tushare（带重试与异常吞底兜底）。
需先 ``pip install -e .`` 把 kss 包安装到当前 Python 环境。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kss.data.tushare_client import TushareClient

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent


# ============================================================ #
# 参数与数据加载
# ============================================================ #


def load_best_params(path: Path | None = None) -> dict:
    """加载历史最优参数；缺失或损坏返回空字典.

    旧实现把 ``json.load`` 放在模块顶层 ``with open(...)``，文件缺失或被截断会让
    ``import monitor`` 阶段就抛 FileNotFoundError / JSONDecodeError，cron 整行
    silent 挂掉（仅 cron.log 才看得到）。本函数兜底为空 dict，让监控主流程继续跑。
    """
    path = path or (OUTPUT_DIR / "best_params.json")
    if not path.exists():
        logger.warning("最优参数文件不存在: %s （策略触发建议将跳过）", path)
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("最优参数文件无法解析: %s (%s)", path, exc)
        return {}


def fetch_stock_data(
    client: TushareClient,
    ts_code: str,
    days: int = 120,
) -> pd.DataFrame | None:
    """获取股票近期数据.

    通过 ``TushareClient`` 拉取，自动重试与超时由数据层处理（见 kss.data.tushare_client）.
    ``daily_basic`` 部分失败时填充默认 ``volume_ratio=1.0`` / ``turnover_rate=0``，
    避免老实现裸列访问 ``df_basic[[...]]`` 在 None 上 TypeError。
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    df_daily = client.fetch_daily(ts_code, start_date, end_date)
    if df_daily is None or df_daily.empty:
        logger.warning("daily 数据获取失败或为空: %s", ts_code)
        return None

    df_basic = client.fetch_daily_basic(ts_code, start_date, end_date)
    if df_basic is None or df_basic.empty:
        # 部分失败：保留 daily，basic 列填默认值
        logger.warning(
            "daily_basic 获取失败: %s；使用默认 volume_ratio/turnover_rate", ts_code,
        )
        basic_default = pd.DataFrame({
            "trade_date": df_daily["trade_date"].copy(),
            "volume_ratio": 1.0,
            "turnover_rate": 0.0,
            "turnover_rate_f": 0.0,
        })
        df = df_daily.merge(basic_default, on="trade_date", how="left")
    else:
        keep_cols = [
            c
            for c in ("trade_date", "volume_ratio", "turnover_rate", "turnover_rate_f")
            if c in df_basic.columns
        ]
        df = df_daily.merge(df_basic[keep_cols], on="trade_date", how="left")

    df = df.sort_values("trade_date").reset_index(drop=True)

    # 中文列名 + 派生列
    rename_map = {
        "trade_date": "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "pre_close": "昨收",
        "vol": "成交量",
        "amount": "成交额",
        "pct_chg": "涨跌幅",
    }
    df = df.rename(columns=rename_map)
    df["日期"] = pd.to_datetime(df["日期"])
    df["振幅"] = (df["最高"] - df["最低"]) / df["昨收"] * 100

    return df


# ============================================================ #
# 指标计算
# ============================================================ #


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有技术指标.

    始终对入参做 ``.copy()``，不污染调用方 DataFrame（旧实现直接 ``df[col] = ...``
    把 20+ 列写回调用方，与 ``fetch_stock_data`` 内部 ``rename_map`` 的链式 mutation
    一起形成不可推理的双重副作用）。

    RSI 与 CCI 在退化场景（无价格变动 / 单常数序列）会出现 0/0；分母统一 ``+1e-8``
    保证输出为有限浮点（约 50/0），避免 ``signals['rsi_value']`` 等下游打印 NaN。
    """
    df = df.copy()
    close = df["收盘"]

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_BAR"] = 2 * (df["DIF"] - df["DEA"])

    # BBI
    df["BBI"] = (
        close.rolling(3).mean()
        + close.rolling(6).mean()
        + close.rolling(12).mean()
        + close.rolling(24).mean()
    ) / 4

    # RSI（防 0/0：分母 + 1e-8）
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    rs6 = gain.rolling(6).mean() / (loss.rolling(6).mean() + 1e-8)
    rs12 = gain.rolling(12).mean() / (loss.rolling(12).mean() + 1e-8)
    df["RSI6"] = 100 - 100 / (1 + rs6)
    df["RSI12"] = 100 - 100 / (1 + rs12)

    # CCI（防 0/0：md + 1e-8）
    tp = (df["最高"] + df["最低"] + df["收盘"]) / 3
    ma_tp = tp.rolling(14).mean()
    md = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["CCI14"] = (tp - ma_tp) / (0.015 * (md + 1e-8))

    # KDJ
    low_list = df["最低"].rolling(window=9, min_periods=9).min()
    high_list = df["最高"].rolling(window=9, min_periods=9).max()
    rsv = (close - low_list) / ((high_list - low_list) + 1e-8) * 100
    df["K"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["D"] = df["K"].ewm(alpha=1 / 3, adjust=False).mean()
    df["J"] = 3 * df["K"] - 2 * df["D"]

    # BOLL
    df["BOLL_MID"] = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["BOLL_UP"] = df["BOLL_MID"] + 2 * std
    df["BOLL_LOW"] = df["BOLL_MID"] - 2 * std

    # WR
    high10 = df["最高"].rolling(10).max()
    low10 = df["最低"].rolling(10).min()
    df["WR10"] = (high10 - close) / ((high10 - low10) + 1e-8) * -100

    # MA
    df["MA5"] = close.rolling(5).mean()
    df["MA10"] = close.rolling(10).mean()
    df["MA20"] = close.rolling(20).mean()
    df["MA60"] = close.rolling(60).mean()

    # 成交量
    vol = df["成交量"]
    df["VOL_RATIO_5"] = vol / (vol.rolling(5).mean() + 1e-8)

    # 动量
    df["MOM5"] = close.pct_change(5)

    return df


def calc_signals(df: pd.DataFrame) -> dict:
    """计算方向信号."""
    signals: dict = {}

    # MACD
    signals["macd_dir"] = 1 if df["DIF"].iloc[-1] > df["DEA"].iloc[-1] else -1
    signals["macd_golden"] = (
        df["DIF"].iloc[-1] > df["DEA"].iloc[-1]
        and df["DIF"].iloc[-2] <= df["DEA"].iloc[-2]
    )

    # BBI
    signals["bbi_dir"] = 1 if df["收盘"].iloc[-1] > df["BBI"].iloc[-1] else -1

    # RSI
    rsi6 = df["RSI6"].iloc[-1]
    signals["rsi_dir"] = 1 if rsi6 > 50 else -1
    if rsi6 < 30:
        signals["rsi_dir"] = 1
    if rsi6 > 70:
        signals["rsi_dir"] = -1
    signals["rsi_value"] = rsi6

    # CCI
    cci = df["CCI14"].iloc[-1]
    signals["cci_dir"] = 1 if cci > 0 else -1
    if cci < -100:
        signals["cci_dir"] = 1
    if cci > 100:
        signals["cci_dir"] = -1
    signals["cci_value"] = cci

    # KDJ
    j = df["J"].iloc[-1]
    signals["kdj_dir"] = 1 if j > 0 else -1
    if j < -20:
        signals["kdj_dir"] = 1
    if j > 100:
        signals["kdj_dir"] = -1
    signals["j_value"] = j

    # BOLL
    price = df["收盘"].iloc[-1]
    signals["boll_dir"] = 1 if price > df["BOLL_MID"].iloc[-1] else -1
    if price > df["BOLL_UP"].iloc[-1]:
        signals["boll_dir"] = -1
    if price < df["BOLL_LOW"].iloc[-1]:
        signals["boll_dir"] = 1

    # WR
    wr = df["WR10"].iloc[-1]
    signals["wr_dir"] = 1 if wr < -80 else (-1 if wr > -20 else 0)
    signals["wr_value"] = wr

    # MA 排列
    ma_score = 0
    if df["MA5"].iloc[-1] > df["MA10"].iloc[-1]:
        ma_score += 1
    if df["MA10"].iloc[-1] > df["MA20"].iloc[-1]:
        ma_score += 1
    if df["MA20"].iloc[-1] > df["MA60"].iloc[-1]:
        ma_score += 1
    if price > df["MA5"].iloc[-1]:
        ma_score += 1
    signals["ma_dir"] = 1 if ma_score >= 3 else (-1 if ma_score <= 1 else 0)
    signals["ma_score"] = ma_score

    # 量比
    vr = df["volume_ratio"].iloc[-1]
    signals["vr_dir"] = 1 if vr > 1.5 else (-1 if vr < 0.5 else 0)
    signals["vr_value"] = vr

    # 成交量异动
    vol_r = df["VOL_RATIO_5"].iloc[-1]
    signals["vol_spike"] = 1 if vol_r > 2 else (-1 if vol_r < 0.5 else 0)

    # 动量
    signals["mom_dir"] = 1 if df["MOM5"].iloc[-1] > 0 else -1

    return signals


def calc_resonance(signals: dict) -> float:
    """计算共振评分."""
    weights = {
        "macd_dir": 1.5, "bbi_dir": 1.0, "rsi_dir": 1.0, "cci_dir": 1.0,
        "kdj_dir": 1.0, "boll_dir": 0.8, "wr_dir": 0.8, "ma_dir": 1.2,
        "vr_dir": 0.5, "vol_spike": 0.5, "mom_dir": 0.8,
    }
    score = sum(signals.get(k, 0) * w for k, w in weights.items())
    max_possible = sum(abs(v) for v in weights.values())
    return score / max_possible * 10


# ============================================================ #
# 单股分析
# ============================================================ #


def _resolve_ts_code(symbol: str) -> str:
    """根据代码前缀推断 ts_code 后缀."""
    if symbol.startswith("6"):
        return f"{symbol}.SH"
    if symbol.startswith("0") or symbol.startswith("3"):
        return f"{symbol}.SZ"
    return symbol


def analyze_stock(client: TushareClient, symbol: str, best_params: dict) -> None:
    """分析单只股票（打印到 stdout）."""
    ts_code = _resolve_ts_code(symbol)

    print(f"\n{'=' * 60}")
    print(f"分析股票: {symbol}")
    print(f"{'=' * 60}")

    df = fetch_stock_data(client, ts_code)
    if df is None or len(df) < 30:
        print("获取数据失败或数据不足")
        return

    df = calc_indicators(df)
    signals = calc_signals(df)
    score = calc_resonance(signals)

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    print(f"\n📊 最新数据 ({latest['日期'].strftime('%Y-%m-%d')})")
    print(f"  收盘价: {latest['收盘']:.2f} (涨跌: {latest['涨跌幅']:.2f}%)")
    print(
        f"  成交量: {latest['成交量'] / 10000:.0f}万手  "
        f"成交额: {latest['成交额'] / 100000000:.2f}亿"
    )
    print(
        f"  量比: {latest['volume_ratio']:.2f}  "
        f"换手率: {latest['turnover_rate']:.2f}%"
    )
    print(f"  振幅: {latest['振幅']:.2f}%")

    print(f"\n📈 技术指标状态")
    macd_label = (
        "金叉✅"
        if signals["macd_golden"]
        else ("看多📈" if signals["macd_dir"] == 1 else "看空📉")
    )
    print(
        f"  MACD: DIF={latest['DIF']:.3f} DEA={latest['DEA']:.3f} {macd_label}"
    )
    rsi_label = (
        "超买⚠️"
        if signals["rsi_value"] > 70
        else ("超卖💡" if signals["rsi_value"] < 30 else "中性")
    )
    print(f"  RSI6: {signals['rsi_value']:.1f} ({rsi_label})")
    print(f"  CCI14: {signals['cci_value']:.1f}")
    print(f"  KDJ-J: {signals['j_value']:.1f}")
    print(f"  WR10: {signals['wr_value']:.1f}")
    print(
        f"  BOLL: 中轨={latest['BOLL_MID']:.2f} "
        f"上轨={latest['BOLL_UP']:.2f} 下轨={latest['BOLL_LOW']:.2f}"
    )
    ma_label = (
        "多头排列✅"
        if signals["ma_score"] >= 3
        else ("空头排列❌" if signals["ma_score"] <= 1 else "震荡")
    )
    print(f"  均线排列: {signals['ma_score']}/4 {ma_label}")

    print(f"\n🎯 多指标共振评分: {score:.2f} / 10")
    if score >= 5:
        print("  🔥 共振看多信号！多个指标同步指向多头")
    elif score <= -5:
        print("  ❄️ 共振看空信号！多个指标同步指向空头")
    elif score > 0:
        print("  📈 偏多头，但尚未形成强共振")
    else:
        print("  📉 偏空头，但尚未形成强共振")

    # 异动检测
    print(f"\n⚡ 异动检测")
    if (
        latest["volume_ratio"] > 1.5
        and latest["涨跌幅"] > 3
        and signals["macd_golden"]
    ):
        print(
            f"  🚀 量价齐升异动：放量({latest['volume_ratio']:.1f}倍)"
            f"+大涨({latest['涨跌幅']:.1f}%)+MACD金叉"
        )
    if (
        signals["rsi_value"] < 35
        and signals["cci_value"] < -80
        and signals["wr_value"] < -80
    ):
        print("  💡 超卖反弹信号：RSI/CCI/WR均处超卖区间")
    if (
        signals["rsi_value"] > 70
        and signals["cci_value"] > 100
        and signals["wr_value"] > -20
    ):
        print("  ⚠️ 超买回落风险：RSI/CCI/WR均处超买区间")
    if (
        latest["volume_ratio"] > 1.8
        and prev["volume_ratio"] < 1.0
        and latest["涨跌幅"] > 2
    ):
        print(
            f"  🚀 放量突破：量比从{prev['volume_ratio']:.1f}"
            f"跳升至{latest['volume_ratio']:.1f}"
        )

    # 最优参数建议
    params = best_params.get(symbol)
    if params and score >= params["threshold"]:
        print(f"\n💰 策略触发建议（基于最优参数）")
        print(
            f"  当前评分{score:.1f} >= 阈值{params['threshold']}，满足共振看多条件"
        )
        print(f"  建议持仓: {params['hold_days']}天")
        print(
            f"  止损位: -{params['stop_loss']:.0%}  "
            f"止盈位: +{params['stop_profit']:.0%}"
        )
        print(
            f"  历史回测胜率: {params['win_rate']:.1%}  "
            f"期望收益: {params['expected_return']:.0%}"
        )
    elif params:
        print(
            f"\n💤 策略未触发（评分{score:.1f} < 阈值{params['threshold']}）"
        )

    print(f"\n{'=' * 60}")


# ============================================================ #
# 入口
# ============================================================ #


def main(argv: list[str] | None = None) -> int:
    """主入口；返回退出码以便 cron 检测失败."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    argv = list(sys.argv[1:] if argv is None else argv)
    symbols = argv if argv else ["688322", "688017"]

    best_params = load_best_params()
    client = TushareClient()

    print(f"\n{'=' * 60}")
    print("多指标共振实时监控系统")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    failed: list[str] = []
    for symbol in symbols:
        try:
            analyze_stock(client, symbol, best_params)
        except Exception:
            logger.exception("分析 %s 失败", symbol)
            failed.append(symbol)

    if failed:
        print(f"\n⚠️ 以下股票分析失败: {failed}")
        return 1
    print("\n监控完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
