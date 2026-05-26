#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""688322 & 688017 每日复盘 + 次日预测 → Telegram

每个交易日 19:00 cron 调用, 输出 4 段:
  1. 当日表现 (价 / 量 / MACD / 大单 / 形态触发)
  2. 大盘背景 (5 个核心指数, 异常项 alerted)
  3. 次日预测 (大盘条件 + 资金条件 双口径均值, 历史 IC 标注)
  4. 操作建议 (基于触发形态 + 历史样本)

用法:
  python3 scripts/daily_review_322_017.py                 # 今日, console 通道
  python3 scripts/daily_review_322_017.py --channel telegram
  python3 scripts/daily_review_322_017.py --channel all
  python3 scripts/daily_review_322_017.py --dry-run       # 仅打印
  python3 scripts/daily_review_322_017.py --date 20260522 # 指定日期

数据获取顺序: tushare 增量 → 失败时退化到 cs_data_xxxx.csv 缓存 + 响亮告警 (不静默崩溃)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.notifications.manager import CHANNEL_CHOICES, send_to_channels  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 每只股票的标签:
#   alpha       - 板块龙头 alpha 主升, 大单流出常是主力换手 (反指)
#   speculation - 博弈股, 跟板块低相关, 大单流出 = 真出货 (顺指)
STOCKS = [
    ('688322', '奥比中光', 'alpha'),
    ('688017', '绿的谐波', 'alpha'),
    ('688268', '华特气体', 'speculation'),
]
CATEGORY_LABEL = {
    'alpha': '🚀 板块龙头 (alpha 主升)',
    'speculation': '🎲 博弈股 (跟板块低相关)',
}
CATEGORY_FUND_NOTE = {
    'alpha': '_注: alpha 股大单流出常为主力换手, 历史样本验证为反指_',
    'speculation': '_注: 博弈股大单流出多为真出货, 历史样本验证为顺指_',
}
INDICES = [
    ('000698.SH', '科创100'),
    ('931494.CSI', '机器人'),
    ('399006.SZ', '创业板'),
    ('000688.SH', '科创50'),
    ('930719.CSI', '中证机器'),
]
ROLL_WIN = 480  # 滚动 2 年分位


# ===== 数据加载 =====

def _pro():
    import tushare as ts
    ts.set_token(os.environ['TUSHARE_TOKEN'])
    return ts.pro_api()


def next_trade_date(today_yyyymmdd: str) -> str:
    """返回 today 之后的第一个交易日 (跳过周末和节假日). 失败时退化为 today+3 天."""
    from datetime import datetime, timedelta
    try:
        pro = _pro()
        end = (datetime.strptime(today_yyyymmdd, '%Y%m%d') + timedelta(days=15)).strftime('%Y%m%d')
        cal = pro.trade_cal(exchange='SSE', start_date=today_yyyymmdd, end_date=end)
        cal = cal[cal['is_open'] == 1].sort_values('cal_date')
        nxt = cal[cal['cal_date'] > today_yyyymmdd]
        if len(nxt):
            return nxt.iloc[0]['cal_date']
    except Exception as e:
        logger.warning(f"  trade_cal 失败, 退化到 +1 工作日: {e}")
    d = datetime.strptime(today_yyyymmdd, '%Y%m%d')
    while True:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 0=周一 ... 4=周五
            return d.strftime('%Y%m%d')


def _ensure_stock_data(sym: str, target_date: str) -> pd.DataFrame:
    """加载或增量更新 cs_data csv 到目标日期."""
    fp = PROJECT_ROOT / f'cs_data_{sym}.csv'
    if not fp.exists():
        raise FileNotFoundError(f"{fp} 不存在")
    df = pd.read_csv(fp)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    last = df['trade_date'].max().strftime('%Y%m%d')
    if last < target_date:
        try:
            pro = _pro()
            new = pro.daily(ts_code=f'{sym}.SH', start_date=last, end_date=target_date)
            basic = pro.daily_basic(ts_code=f'{sym}.SH', start_date=last, end_date=target_date)
            if len(new) and len(basic):
                new['trade_date'] = pd.to_datetime(new['trade_date'], format='%Y%m%d')
                basic['trade_date'] = pd.to_datetime(basic['trade_date'], format='%Y%m%d')
                merged = new.merge(
                    basic[['trade_date', 'turnover_rate', 'volume_ratio', 'pe', 'pb', 'total_mv']],
                    on='trade_date', how='left'
                )
                new_rows = merged[merged['trade_date'] > df['trade_date'].max()]
                if len(new_rows):
                    df = pd.concat([df, new_rows], ignore_index=True).sort_values('trade_date').reset_index(drop=True)
                    df.to_csv(fp, index=False)
                    logger.info(f"  {sym} 新增 {len(new_rows)} 行")
        except Exception as e:
            logger.warning(f"  {sym} 增量更新失败, 退化到缓存 (截止 {last}): {e}")
    return df


def _ensure_index_data(code: str, target_date: str) -> pd.DataFrame:
    fp = PROJECT_ROOT / f'idx_{code.replace(".", "_")}.csv'
    if fp.exists():
        df = pd.read_csv(fp, dtype={'trade_date': str})
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        last = df['trade_date'].max().strftime('%Y%m%d')
    else:
        df = pd.DataFrame()
        last = '20230101'
    if last < target_date:
        pro = _pro()
        new = pro.index_daily(ts_code=code, start_date=last, end_date=target_date)
        if len(new):
            new['trade_date'] = pd.to_datetime(new['trade_date'], format='%Y%m%d')
            if len(df):
                df = pd.concat([df, new[new['trade_date'] > df['trade_date'].max()]], ignore_index=True)
            else:
                df = new
            df = df.sort_values('trade_date').reset_index(drop=True)
            out = df.copy()
            out['trade_date'] = out['trade_date'].dt.strftime('%Y%m%d')
            out.to_csv(fp, index=False)
    return df


def _ensure_moneyflow(sym: str, target_date: str) -> pd.DataFrame:
    fp = PROJECT_ROOT / f'mf_{sym}_SH.csv'
    if fp.exists():
        df = pd.read_csv(fp, dtype={'trade_date': str})
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        last = df['trade_date'].max().strftime('%Y%m%d')
    else:
        df = pd.DataFrame()
        last = '20230101'
    if last < target_date:
        try:
            pro = _pro()
            new = pro.moneyflow(ts_code=f'{sym}.SH', start_date=last, end_date=target_date)
            if len(new):
                new['trade_date'] = pd.to_datetime(new['trade_date'], format='%Y%m%d')
                if len(df):
                    df = pd.concat([df, new[new['trade_date'] > df['trade_date'].max()]], ignore_index=True)
                else:
                    df = new
                df = df.sort_values('trade_date').reset_index(drop=True)
                out = df.copy()
                out['trade_date'] = out['trade_date'].dt.strftime('%Y%m%d')
                out.to_csv(fp, index=False)
        except Exception as e:
            logger.warning(f"  {sym} 资金流增量更新失败, 退化到缓存 (截止 {last}): {e}")
    if len(df):
        df['lg_total_net'] = (df['buy_lg_amount'] + df['buy_elg_amount']) - (df['sell_lg_amount'] + df['sell_elg_amount'])
        df['elg_net'] = df['buy_elg_amount'] - df['sell_elg_amount']
        df['lg_net'] = df['buy_lg_amount'] - df['sell_lg_amount']
        df['md_net'] = df['buy_md_amount'] - df['sell_md_amount']
        df['sm_net'] = df['buy_sm_amount'] - df['sell_sm_amount']
    return df


# ===== 指标计算 =====

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, v = df['close'], df['vol']
    pct = df['pct_chg'] / 100.0
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = (df['macd'] - df['macd_signal']) * 2
    df['vol_ma20'] = v.rolling(20).mean()
    df['vol_ma20_ratio'] = v / df['vol_ma20']
    df['macd_hist_q'] = df['macd_hist'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: s.rank(pct=True).iloc[-1], raw=False)
    df['turn_q'] = df['turnover_rate'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: s.rank(pct=True).iloc[-1], raw=False)
    df['volr_q'] = df['vol_ma20_ratio'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: s.rank(pct=True).iloc[-1], raw=False)
    df['is_big_up'] = ((pct >= 0.05) & (v > df['vol_ma20'])).astype(int)
    df['big_up_5d_cnt'] = df['is_big_up'].rolling(5).sum()
    df['close_20d_high'] = c == c.rolling(20).max()
    df['close_2y_high'] = c == c.rolling(ROLL_WIN).max()
    for n in [1, 3, 5, 10]:
        df[f'fwd_{n}d'] = c.shift(-n) / c - 1
    return df


def stats(s: pd.Series) -> tuple[int, float, float]:
    s = s.dropna()
    if len(s) == 0:
        return 0, np.nan, np.nan
    return len(s), s.mean(), (s > 0).mean()


def scenario_distribution(hist: pd.DataFrame, mask: pd.Series) -> dict:
    """把历史样本的次日全日收益按 5 个情形分桶, 输出概率/预期区间/极值.

    情形定义 (基于次日 close vs 当日 close 的 pct_chg):
      A. 强势突破上行: > +5%
      B. 温和上涨:    +1% ~ +5%
      C. 横盘震荡:    -1% ~ +1%
      D. 温和回落:    -5% ~ -1%
      E. 大跌破位:    < -5%

    预期区间用 P25/P75 of fwd_1d (收盘) / fwd_max_1d (高位) / fwd_min_1d (低位).
    fwd_max/min 用 next high/low 估算 (这里近似用 fwd_1d 本身的极值).
    """
    sub = hist[mask].copy()
    if len(sub) == 0:
        return {'n': 0}
    r = sub['fwd_1d'].dropna()
    if len(r) == 0:
        return {'n': 0}
    n = len(r)
    buckets = {
        'A_break':  (r > 0.05).sum() / n,
        'B_up':     ((r > 0.01) & (r <= 0.05)).sum() / n,
        'C_flat':   ((r > -0.01) & (r <= 0.01)).sum() / n,
        'D_down':   ((r > -0.05) & (r <= -0.01)).sum() / n,
        'E_break':  (r <= -0.05).sum() / n,
    }
    return {
        'n': n,
        **buckets,
        'p25': r.quantile(0.25),
        'p50': r.quantile(0.50),
        'p75': r.quantile(0.75),
        'p10': r.quantile(0.10),
        'p90': r.quantile(0.90),
        'mean': r.mean(),
        'win': (r > 0).mean(),
    }


def key_levels(df: pd.DataFrame, last: pd.Series) -> dict:
    """关键位: MA / 前高 / 涨停 等."""
    c = df['close']
    h = df['high']
    return {
        'close': last['close'],
        'open': last['open'],
        'high_today': last['high'],
        'low_today': last['low'],
        'ma5': c.rolling(5).mean().iloc[-1],
        'ma10': c.rolling(10).mean().iloc[-1],
        'ma20': c.rolling(20).mean().iloc[-1],
        'high_2y': h.tail(ROLL_WIN).max(),
        'high_60d': h.tail(60).max(),
        'limit_up': round(last['close'] * 1.20, 2),  # 科创板 ±20%
        'limit_down': round(last['close'] * 0.80, 2),
    }


def adjusted_scenarios(base_dist: dict, stock: dict) -> tuple[dict, dict]:
    """基于 当前形态特征 对基础情形分布做修正, 返回 (adjusted_probs, reason_map).

    修正规则 (累乘小幅 boost / dampen):
      - MACD 缩柱(顶背离前奏): D 上调 + E 上调, A 下调
      - 三类形态全触发(P1+P2+P3): A 上调, D/E 下调
      - 资金条件 fund_10d > 3% (历史利多): A/B 上调
      - 资金条件 fund_1d < 0 (短期偏负, 如 017): D/C 上调, A 下调
    最后归一化到 sum=1.
    """
    if base_dist.get('n', 0) == 0:
        return base_dist, {}
    probs = {k: base_dist[k] for k in ['A_break', 'B_up', 'C_flat', 'D_down', 'E_break']}
    reasons = {k: [] for k in probs}

    # MACD 缩柱
    if (pd.notna(stock['macd_yest']) and stock['macd_hist'] < stock['macd_yest']
            and stock['macd_hist_q'] > 0.9):
        probs['A_break'] *= 0.7; reasons['A_break'].append('MACD缩柱-')
        probs['B_up']    *= 0.9
        probs['D_down']  *= 1.3; reasons['D_down'].append('MACD缩柱+')
        probs['E_break'] *= 1.2; reasons['E_break'].append('MACD缩柱+')

    # 三类全触发
    if stock['p1'] and stock['p2'] and stock['p3']:
        probs['A_break'] *= 1.3; reasons['A_break'].append('三极值+')
        probs['B_up']    *= 1.1
        probs['D_down']  *= 0.85
        probs['E_break'] *= 0.7; reasons['E_break'].append('三极值-')

    # 资金 10日利多
    if pd.notna(stock['fund_10d']) and stock['fund_10d'] > 0.03:
        probs['A_break'] *= 1.15; reasons['A_break'].append('资金10d+')
        probs['B_up']    *= 1.1

    # 资金 1 日偏负 (e.g. 017 的 -0.46%)
    if pd.notna(stock['fund_1d']) and stock['fund_1d'] < -0.003:
        probs['A_break'] *= 0.85
        probs['D_down']  *= 1.15; reasons['D_down'].append('资金1d-')
        probs['C_flat']  *= 1.1

    # 归一化
    total = sum(probs.values())
    probs = {k: v / total for k, v in probs.items()}
    return probs, reasons


# ===== 报告生成 =====

def stock_section(sym: str, name: str, df: pd.DataFrame, mf_today: dict | None) -> dict:
    """单只股票指标快照 + 形态触发 + 后续条件均值."""
    last = df.iloc[-1]
    hist = df.iloc[:-1]

    # 三类形态触发
    p1_macd = (last['macd_hist_q'] or 0) >= 0.95
    p2_vol = max(last['turn_q'] or 0, last['volr_q'] or 0) >= 0.95
    p3_bigup = last['big_up_5d_cnt'] >= 2

    # P1 历史条件分布
    mask1 = (hist['macd_hist_q'] >= 0.95)
    n1, m1_5d, w1_5d = stats(hist.loc[mask1, 'fwd_5d'])
    _, m1_10d, w1_10d = stats(hist.loc[mask1, 'fwd_10d'])
    _, m1_1d, w1_1d = stats(hist.loc[mask1, 'fwd_1d'])

    # 资金条件分布: 20日新高 + 大单流出 (历史样本, 不依赖今日 mf)
    if 'lg_total_net' in df.columns:
        mask_fund = hist['close_20d_high'] & (hist['lg_total_net'] < 0)
        nf, mf_5d, wf_5d = stats(hist.loc[mask_fund, 'fwd_5d'])
        _, mf_10d, wf_10d = stats(hist.loc[mask_fund, 'fwd_10d'])
        _, mf_1d, wf_1d = stats(hist.loc[mask_fund, 'fwd_1d'])
    else:
        nf = 0; mf_1d = mf_5d = mf_10d = np.nan; wf_1d = wf_5d = wf_10d = np.nan

    out = {
        'sym': sym, 'name': name, 'date': last['trade_date'].date(),
        'close': last['close'], 'pct': last['pct_chg'],
        'open': last['open'], 'high': last['high'], 'low': last['low'],
        'turnover': last['turnover_rate'], 'volume_ratio': last.get('volume_ratio', np.nan),
        'macd_hist': last['macd_hist'], 'macd_hist_q': last['macd_hist_q'] or 0,
        'is_macd_2y_high': last['macd_hist'] >= df['macd_hist'].tail(ROLL_WIN).max() - 1e-9,
        'turn_q': last['turn_q'] or 0, 'volr_q': last['volr_q'] or 0,
        'big_up_5d_cnt': int(last['big_up_5d_cnt']),
        'p1': p1_macd, 'p2': p2_vol, 'p3': p3_bigup,
        'macd_yest': df.iloc[-2]['macd_hist'] if len(df) >= 2 else np.nan,
        'mf_today': mf_today,
        # 历史样本
        'p1_n': n1, 'p1_1d': m1_1d, 'p1_5d': m1_5d, 'p1_10d': m1_10d,
        'p1_w5': w1_5d, 'p1_w10': w1_10d,
        # 资金样本
        'fund_n': nf, 'fund_1d': mf_1d, 'fund_5d': mf_5d, 'fund_10d': mf_10d,
        'fund_w1': wf_1d, 'fund_w5': wf_5d, 'fund_w10': wf_10d,
    }
    # 情形分布: 优先用"三类形态匹配"的样本; 不足时退化到 P1
    mask_full = (
        (hist['macd_hist_q'] >= 0.95)
        & ((hist['turn_q'] >= 0.95) | (hist['volr_q'] >= 0.95))
        & (hist['big_up_5d_cnt'] >= 2)
    )
    base_dist = scenario_distribution(hist, mask_full)
    if base_dist.get('n', 0) < 10:
        base_dist = scenario_distribution(hist, mask1)
        out['scenario_basis'] = f'MACD极值 (P1)'
    else:
        out['scenario_basis'] = f'三类形态共触'
    out['scenario'] = base_dist
    out['scenario_adj'], out['scenario_reasons'] = adjusted_scenarios(base_dist, out)
    # 关键位
    out['levels'] = key_levels(df, last)
    return out


def index_condition_means(idx_dfs: dict, stock_df: pd.DataFrame) -> dict:
    """指数当日涨跌 → 个股次日均值 (>+2% 桶 / 1~2% 桶 / 等)."""
    out = {}
    BINS = [-np.inf, -0.02, -0.01, -0.003, 0.003, 0.01, 0.02, np.inf]
    LABELS = ['<-2%', '-2~-1%', '-1~-0.3%', '-0.3~0.3%', '0.3~1%', '1~2%', '>2%']
    for code, name in INDICES:
        idx = idx_dfs.get(code)
        if idx is None or not len(idx):
            continue
        idx_last = idx.iloc[-1]
        v = idx_last['pct_chg'] / 100.0
        bin_idx = next((i for i in range(len(BINS) - 1) if BINS[i] <= v < BINS[i+1]), None)
        if bin_idx is None:
            continue
        bin_label = LABELS[bin_idx]
        # 合并指数到 stock; idx_bin 用当日指数 (不 shift), next_day 是 stock T+1 close-to-close
        # 历史样本: row t 用 idx[t] 的 bin 配对 stock[t→t+1], 这是 lag-1 预测
        merged = stock_df.merge(
            idx[['trade_date', 'pct_chg']].rename(columns={'pct_chg': f'idx_{code}'}),
            on='trade_date', how='left')
        merged[f'idx_{code}'] = merged[f'idx_{code}'] / 100.0
        merged['idx_bin'] = pd.cut(merged[f'idx_{code}'], bins=BINS, labels=LABELS)
        merged['next_day'] = merged['close'].shift(-1) / merged['close'] - 1
        sub = merged[merged['idx_bin'] == bin_label]['next_day'].dropna()
        if len(sub):
            out[code] = {
                'name': name, 'value': v, 'bin': bin_label,
                'n': len(sub), 'mean': sub.mean(), 'win': (sub > 0).mean()
            }
    return out


def fmt_money(v: float) -> str:
    if abs(v) >= 10000:
        return f"{v/10000:+.2f}亿"
    return f"{v:+.0f}万"


_SCEN_LABEL = {
    'A_break': '强势突破上行 (>+5%)',
    'B_up':    '温和上涨 (+1~+5%)',
    'C_flat':  '横盘震荡 (-1~+1%)',
    'D_down':  '温和回落 (-1~-5%)',
    'E_break': '大跌破位 (<-5%)',
}


def _scenario_table(s: dict) -> list[str]:
    """渲染单股的情形分布表 (Markdown 代码块, 中文对齐)."""
    sc = s.get('scenario')
    adj = s.get('scenario_adj', {})
    reasons = s.get('scenario_reasons', {})
    if not sc or sc.get('n', 0) == 0:
        return ['  _情形分布: 样本不足_']
    lines = []
    lines.append(f"  *次日情形分布* (n={sc['n']}, 基于 {s['scenario_basis']})")
    lines.append("  ```")
    lines.append("  情形                       原始    修正后  备注")
    for key, label in _SCEN_LABEL.items():
        raw = sc.get(key, 0) * 100
        adj_p = adj.get(key, raw / 100) * 100
        arrow = ''
        if adj_p - raw > 3:
            arrow = ' ↑'
        elif raw - adj_p > 3:
            arrow = ' ↓'
        rs = '+'.join(reasons.get(key, [])[:2])
        rs_short = rs[:12] if rs else ''
        lines.append(f"  {label:<24}  {raw:5.1f}%  {adj_p:5.1f}%{arrow:<2} {rs_short}")
    lines.append("  ```")
    # 区间
    p25, p50, p75 = sc.get('p25'), sc.get('p50'), sc.get('p75')
    p10, p90 = sc.get('p10'), sc.get('p90')
    cl = s['close']
    if all(pd.notna(x) for x in [p25, p75, p10, p90]):
        lines.append(f"  *预期区间* (历史 P10/P90):")
        lines.append(f"     收盘 50% 概率落 *{cl*(1+p25):.2f} ~ {cl*(1+p75):.2f}* (中位 {cl*(1+p50):.2f})")
        lines.append(f"     极端 80% 区间 {cl*(1+p10):.2f} ~ {cl*(1+p90):.2f}")
    return lines


def _key_levels_block(s: dict) -> list[str]:
    lv = s['levels']
    cl = s['close']
    # 距前高
    dist_high = (lv['high_2y'] / cl - 1) * 100
    dist_ma5 = (lv['ma5'] / cl - 1) * 100
    dist_ma20 = (lv['ma20'] / cl - 1) * 100
    lines = ['  *关键位*:']
    if dist_high > 0:
        lines.append(f"     突破位 {lv['high_2y']:.2f} (距 {dist_high:+.1f}%) · 涨停 {lv['limit_up']:.2f}")
    else:
        lines.append(f"     当日已破 2y 高 ({lv['high_today']:.2f}) · 涨停 {lv['limit_up']:.2f}")
    lines.append(f"     支撑 MA5 {lv['ma5']:.2f} ({dist_ma5:+.1f}%) / MA20 {lv['ma20']:.2f} ({dist_ma20:+.1f}%)")
    lines.append(f"     今日开 {lv['open']:.2f} / 低 {lv['low_today']:.2f}")
    return lines


def _advice_block(s: dict) -> list[str]:
    """基于 形态 + 资金 + MACD 缩柱 生成详细建议. category 决定资金信号语义."""
    risk_signals = []
    bull_signals = []
    cat = s.get('category', 'alpha')

    if s['p1'] and s['p2'] and s['p3']:
        bull_signals.append('三类极值共振')
    elif s['p1'] and s['p2']:
        risk_signals.append('双极值 (超买深处)')
    if pd.notna(s['macd_yest']) and s['macd_hist'] < s['macd_yest'] and s['macd_hist_q'] > 0.9:
        risk_signals.append('MACD缩柱 (顶背离前奏)')

    # 资金信号: alpha 股看历史利多/利空都按统计走; speculation 股语义本身是顺指
    fund_10d_pos = pd.notna(s['fund_5d']) and s['fund_5d'] > 0.01 and pd.notna(s['fund_w10']) and s['fund_w10'] > 0.6
    fund_1d_neg = pd.notna(s['fund_1d']) and s['fund_1d'] < -0.003

    if fund_10d_pos:
        bull_signals.append(f"资金条件历史利多 (10d {s['fund_10d']*100:+.1f}%, 胜率 {s['fund_w10']*100:.0f}%)")
    if fund_1d_neg:
        risk_signals.append(f"资金条件短期 1d 偏负 ({s['fund_1d']*100:+.1f}%, 胜率 {s['fund_w1']*100:.0f}%)")

    # 当日大单流出 → 按 category 区分语义
    if s.get('mf_today'):
        lg_net = s['mf_today'].get('lg_total_net', 0)
        if lg_net < -3000:  # 大单净流出 > 3000 万
            if cat == 'alpha':
                bull_signals.append(f"今日机构净流出 {fmt_money(lg_net)} — alpha 股历史样本验证为反指 (利多)")
            else:
                risk_signals.append(f"今日机构净流出 {fmt_money(lg_net)} — 博弈股历史样本验证为顺指 (利空)")
        elif lg_net > 3000:
            if cat == 'alpha':
                risk_signals.append(f"今日机构净流入 {fmt_money(lg_net)} — alpha 股流入对照组反而偏负, 不必当利好")
            else:
                bull_signals.append(f"今日机构净流入 {fmt_money(lg_net)} — 博弈股流入是有效买入信号")

    if s['p3'] and not (pd.notna(s['macd_yest']) and s['macd_hist'] < s['macd_yest']):
        bull_signals.append('连续大阳延续中')

    # 操作动作
    lv = s['levels']
    actions = []
    has_topping = any('MACD缩柱' in r for r in risk_signals)
    has_triple = '三类极值共振' in bull_signals
    has_short_neg = any('短期 1d 偏负' in r for r in risk_signals)

    # 回调期判定: 价格低于 MA20 且 MACD 柱在低分位
    in_pullback = (s['close'] < lv['ma20'] and s.get('macd_hist_q', 1) < 0.3)

    if has_topping:
        actions.append('持仓继续保留, *不加仓* (顶背离前奏)')
    elif has_triple:
        actions.append('持仓继续保留, 可激进持有但 *不建议追高*')
    elif in_pullback:
        actions.append('当前回调期, *观望或轻仓试探*, 等待 MACD 翻红 / 放量突破再介入')
    else:
        actions.append('持仓保留, 视开盘量能决定')

    if has_short_neg:
        actions.append('周一短期 1 日偏弱大概率, *不要追高*, 让子弹飞一天')

    if bull_signals and pd.notna(s['fund_10d']) and s['fund_10d'] > 0.03:
        actions.append(f"5-10 日仍看涨 (历史 10d {s['fund_10d']*100:+.1f}% · 胜率 {s['fund_w10']*100:.0f}%)")

    # 止损位: 今日最低 -1% (不取 MA20, 避免回调期 MA20 反成天花板)
    stop = lv['low_today'] * 0.99
    actions.append(f"止损位 *{stop:.2f}* (今日最低 -1%)")

    # 突破观察位: 20 日高优先 (短线), 否则 2y 高
    if '_df' in s:
        close_high_20d = s['_df']['high'].tail(20).max()
    else:
        close_high_20d = lv['high_60d']
    if close_high_20d > s['close'] * 1.01:
        actions.append(f"突破观察位 *{close_high_20d:.2f}* (20日高); 带量突破则持有, 否则止盈减仓")
    elif lv['high_2y'] > s['close'] * 1.01:
        actions.append(f"突破观察位 *{lv['high_2y']:.2f}* (2y 高); 带量突破则持有")
    else:
        actions.append("当前已接近/破 2y 高, 无上方目标位, *降低仓位预期*")

    lines = []
    if bull_signals:
        lines.append('  📈 ' + ' · '.join(bull_signals))
    if risk_signals:
        lines.append('  ⚠️ ' + ' · '.join(risk_signals))
    lines.append('  *建议*:')
    for a in actions:
        lines.append(f"     • {a}")
    return lines


def _short_date(yyyy_mm_dd: str) -> str:
    """2026-05-22 → 05-22, 紧凑标题用."""
    parts = yyyy_mm_dd.split('-')
    return f"{parts[1]}-{parts[2]}" if len(parts) == 3 else yyyy_mm_dd


def _title_for(today_str: str, t1_str: str, sym: str = '', name: str = '') -> str:
    """[name]([sym]) R[MM-DD]/F[MM-DD]; sym 缺省时只显示日期 (大盘段用)."""
    rf = f"R{_short_date(today_str)}/F{_short_date(t1_str)}"
    if sym and name:
        return f"{name}({sym}) {rf}"
    if sym:
        return f"{sym} {rf}"
    return f"KSS {rf}"


def render(stocks: list[dict], idx_dfs: dict, today_str: str, t1_str: str) -> list[str]:
    """生成 Markdown 推送正文, 返回多段消息列表 (Telegram 单条上限 4096)."""
    chunks: list[str] = []

    # ===== 第一段: 标题 + 大盘背景 =====
    lines = [f"📊 *{_title_for(today_str, t1_str)}*", ""]
    lines.append("🌐 *大盘背景*")
    for code, name in INDICES:
        idx = idx_dfs.get(code)
        if idx is None or not len(idx):
            continue
        r = idx.iloc[-1]
        v = r['pct_chg']
        emoji = "🔴" if v < 0 else "🟢"
        lines.append(f"  {emoji} {name}: {r['close']:.2f} ({v:+.2f}%)")
    bot_idx = idx_dfs.get('931494.CSI')
    cz_idx = idx_dfs.get('930719.CSI')
    if bot_idx is not None and cz_idx is not None and len(bot_idx) and len(cz_idx):
        bot = bot_idx.iloc[-1]['pct_chg']
        cz = cz_idx.iloc[-1]['pct_chg']
        if (bot > 0) != (cz > 0):
            lines.append("  ⚠️ 机器人 vs 中证机器 反向, 子板块分化")
    chunks.append("\n".join(lines))

    # ===== 每只股票单独一段 =====
    for s in stocks:
        lines = []
        cat_label = CATEGORY_LABEL.get(s.get('category', ''), '')
        lines.append(f"📊 *{_title_for(today_str, t1_str, s['sym'], s['name'])}*")
        lines.append(f"  {cat_label}")
        lines.append(f"  收 {s['close']:.2f} ({s['pct']:+.2f}%)")
        lines.append(f"  开 {s['open']:.2f} / 高 {s['high']:.2f} / 低 {s['low']:.2f}")
        lines.append(f"  换手 {s['turnover']:.2f}% (P{s['turn_q']*100:.0f}) · 量比 {s['volume_ratio']:.2f}")
        macd_trend = "↑" if pd.notna(s['macd_yest']) and s['macd_hist'] > s['macd_yest'] else "↓"
        new_high = " 🆕2y高" if s['is_macd_2y_high'] else ""
        lines.append(f"  MACD柱 {s['macd_hist']:+.2f} {macd_trend} (P{s['macd_hist_q']*100:.0f}){new_high}")

        if s['mf_today']:
            mt = s['mf_today']
            lg_net = mt['lg_total_net']
            lines.append(f"  大+特大单 净流 {fmt_money(lg_net)} _(tushare 标准口径)_")
            lines.append(f"     特大 {fmt_money(mt['elg_net'])} / 大单 {fmt_money(mt['lg_net'])}")
            lines.append(f"     中单 {fmt_money(mt['md_net'])} / 小单 {fmt_money(mt['sm_net'])}")
        else:
            lines.append("  大+特大单 净流: _T+1 数据待更新_")

        flags = []
        flags.append(("✅" if s['p1'] else "❌") + " MACD极值")
        flags.append(("✅" if s['p2'] else "❌") + " 量能极值")
        flags.append(("✅" if s['p3'] else "❌") + f" 连续大阳({s['big_up_5d_cnt']}根)")
        lines.append(f"  形态: {' / '.join(flags)}")
        lines.append("")

        # 关键位
        lines.extend(_key_levels_block(s))
        lines.append("")

        # 历史均值 (3 口径)
        lines.append("  *3 口径次日均值* (历史 IC≈0, 仅做先验)")
        if s['p1'] and s['p1_n'] > 10:
            lines.append(f"     · MACD极值 (n={s['p1_n']}): "
                         f"1d {s['p1_1d']*100:+.2f}%, 5d {s['p1_5d']*100:+.2f}%, 10d {s['p1_10d']*100:+.2f}% (10d胜率 {s['p1_w10']*100:.0f}%)")
        if s['fund_n'] > 10:
            lines.append(f"     · 新高+大单流出 (n={s['fund_n']}): "
                         f"1d {s['fund_1d']*100:+.2f}% (胜率 {s['fund_w1']*100:.0f}%), "
                         f"5d {s['fund_5d']*100:+.2f}%, 10d {s['fund_10d']*100:+.2f}% (胜率 {s['fund_w10']*100:.0f}%)")
            note = CATEGORY_FUND_NOTE.get(s.get('category', ''), '')
            if note:
                lines.append(f"     {note}")
        idx_means = index_condition_means(idx_dfs, s['_df'])
        kc100 = idx_means.get('000698.SH')
        if kc100:
            lines.append(f"     · 大盘 (科创100 {kc100['bin']}, n={kc100['n']}): "
                         f"次日 {kc100['mean']*100:+.2f}% (胜率 {kc100['win']*100:.0f}%)")
        lines.append("")

        # 情形分布表
        lines.extend(_scenario_table(s))
        lines.append("")

        # 操作建议
        lines.extend(_advice_block(s))
        chunks.append("\n".join(lines))

    chunks.append("_自动生成 · 历史样本 IC≈0, 仅供参考, 非投资建议_")
    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', help='目标日期 YYYYMMDD, 默认今日')
    parser.add_argument('--channel', choices=CHANNEL_CHOICES, default='console')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    target = args.date or datetime.now().strftime('%Y%m%d')
    today_str = datetime.strptime(target, '%Y%m%d').strftime('%Y-%m-%d')
    t1 = next_trade_date(target)
    t1_str = datetime.strptime(t1, '%Y%m%d').strftime('%Y-%m-%d')

    logger.info(f"== {target} ({today_str}) 复盘 → 预测 {t1} ({t1_str}) ==")

    # 加载数据
    idx_dfs = {}
    for code, _ in INDICES:
        try:
            idx_dfs[code] = _ensure_index_data(code, target)
        except Exception as e:
            logger.warning(f"  指数 {code} 加载失败: {e}")
            idx_dfs[code] = None

    stocks_data = []
    for sym, name, category in STOCKS:
        df = _ensure_stock_data(sym, target)
        df = add_indicators(df)
        mf = _ensure_moneyflow(sym, target)
        mf_today = None
        if len(mf):
            mf_last = mf.iloc[-1]
            if mf_last['trade_date'].strftime('%Y%m%d') == target:
                mf_today = mf_last.to_dict()
        # 合并大单净流到 df 用于条件回测
        if len(mf):
            df = df.merge(mf[['trade_date', 'lg_total_net']], on='trade_date', how='left')
        s = stock_section(sym, name, df, mf_today)
        s['_df'] = df
        s['category'] = category
        stocks_data.append(s)

    chunks = render(stocks_data, idx_dfs, today_str, t1_str)

    # 存档: 不论 dry-run / 实际推送, 都落盘到 storage/daily_review/YYYY-MM-DD.md
    archive_dir = PROJECT_ROOT / 'storage' / 'daily_review'
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f'{today_str}.md'
    header = f"# KSS {today_str} 复盘 / {t1_str} 预测\n\n"
    body = "\n\n---\n\n".join(chunks)
    archive_path.write_text(header + body, encoding='utf-8')
    logger.info(f"  存档: {archive_path}")

    if args.dry_run:
        for i, c in enumerate(chunks):
            print(f"───── chunk {i+1}/{len(chunks)} ({len(c)} chars) ─────")
            print(c)
            print()
        return

    base_title = _title_for(today_str, t1_str)
    all_ok = True
    for i, msg in enumerate(chunks):
        t = base_title if i == 0 else None
        results = send_to_channels(msg, args.channel, title=t, parse_mode='Markdown')
        ok = results.get('telegram', True) if args.channel in ('telegram', 'all') else True
        logger.info(f"  chunk {i+1}/{len(chunks)} ({len(msg)} 字符): {results}")
        if not ok:
            all_ok = False
    if not all_ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
