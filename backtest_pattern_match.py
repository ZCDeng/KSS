#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
688322 & 688017 当前形态 vs 历史相似情形 回测

聚焦三类"和以往不同"的形态:
  P1. MACD 柱极值 (当日 macd_hist >= 滚动 2 年 P95)
  P2. 量能异常   (turnover_rate 或 vol/MA20 >= 滚动 2 年 P95)
  P3. 连续放量大阳 (5 日窗口内 >=2 根 +5% 且 vol>MA20 的阳线)

对每类:
  - 标注当前样本所处分位
  - 找历史相似日, 统计后 1/3/5/10 日条件收益分布 (胜率/均值/中位/最大回撤)
  - 列出近期最近 3 次触发的具体日期与后续走法

最终给出短期判断综合表。
"""

import argparse
import glob
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))

WEEK_RULE = 'W-FRI'
RPS_LOOKBACK_W = 13  # 13 周累计收益用于横截面 RPS 排名

# 默认两支; CLI 可覆盖。NAMES 仅做 label, 命中即用, 否则用代码本身
DEFAULT_SYMBOLS = ['688322', '688017']
NAMES = {
    '688322': '奥比中光',
    '688017': '绿的谐波',
    '688268': '华特气体',
    '688106': '金宏气体',
    '688353': '华盛锂电',
    '688585': '上纬新材',
    '688331': '荣昌生物',
    '688196': '卓越新能',
}
FWD = [1, 3, 5, 10]
ROLL_WIN = 480  # 约 2 年, 用于滚动分位


def load(sym: str) -> pd.DataFrame:
    fp = os.path.join(ROOT, f'cs_data_{sym}.csv')
    df = pd.read_csv(fp)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close']
    v = df['vol']
    pct = df['pct_chg'] / 100.0

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = (df['macd'] - df['macd_signal']) * 2  # 通行 macd_hist = (DIF-DEA)*2

    df['vol_ma20'] = v.rolling(20).mean()
    df['vol_ma20_ratio'] = v / df['vol_ma20']

    # 滚动 2 年分位
    df['macd_hist_q'] = df['macd_hist'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: (s.rank(pct=True).iloc[-1]), raw=False)
    df['turn_q'] = df['turnover_rate'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: (s.rank(pct=True).iloc[-1]), raw=False)
    df['volr_q'] = df['vol_ma20_ratio'].rolling(ROLL_WIN, min_periods=120).apply(
        lambda s: (s.rank(pct=True).iloc[-1]), raw=False)

    df['is_big_up'] = ((pct >= 0.05) & (v > df['vol_ma20'])).astype(int)
    df['big_up_5d_cnt'] = df['is_big_up'].rolling(5).sum()

    # OBV (On-Balance Volume) + 派生信号
    diff_close = c.diff()
    obv_sign = np.where(diff_close > 0, 1, np.where(diff_close < 0, -1, 0))
    df['obv'] = (obv_sign * v).cumsum()
    df['obv_ema20'] = df['obv'].ewm(span=20, adjust=False).mean()
    df['obv_above_ema'] = df['obv'] > df['obv_ema20']
    # 顶背离: 今日价格突破 20 日 (不含今日) 新高, 但 OBV 未突破 20 日新高 → 量价不确认
    close_prev_max20 = c.shift(1).rolling(20).max()
    obv_prev_max20 = df['obv'].shift(1).rolling(20).max()
    df['is_close_break20'] = c > close_prev_max20
    df['is_obv_break20'] = df['obv'] > obv_prev_max20
    df['obv_top_div'] = df['is_close_break20'] & (~df['is_obv_break20'])
    df['obv_confirm_up'] = df['is_close_break20'] & df['is_obv_break20']

    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi14'] = 100 - 100 / (1 + rs)
    df['rsi_overbought'] = df['rsi14'] > 70
    df['rsi_oversold'] = df['rsi14'] < 30
    # RSI 顶背离: 价格突破 20 日新高 + RSI 没突破 14 日新高
    rsi_prev_max14 = df['rsi14'].shift(1).rolling(14).max()
    df['rsi_top_div'] = df['is_close_break20'] & (df['rsi14'] <= rsi_prev_max14)

    # CCI(20)
    tp = (df['high'] + df['low'] + c) / 3
    sma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    df['cci20'] = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
    df['cci_extreme_pos'] = df['cci20'] > 100
    df['cci_extreme_neg'] = df['cci20'] < -100
    cci_prev_max20 = df['cci20'].shift(1).rolling(20).max()
    df['cci_top_div'] = df['is_close_break20'] & (df['cci20'] <= cci_prev_max20)

    # Williams %R (14)
    hh14 = df['high'].rolling(14).max()
    ll14 = df['low'].rolling(14).min()
    df['wr14'] = -100 * (hh14 - c) / (hh14 - ll14).replace(0, np.nan)
    df['wr_overbought'] = df['wr14'] > -20  # 接近 0 = 超买
    df['wr_oversold'] = df['wr14'] < -80    # 接近 -100 = 超卖

    # 多指标共振顶背离 (RSI + CCI + OBV 至少 2 个顶背离)
    div_count = (df['obv_top_div'].astype(int) + df['rsi_top_div'].astype(int)
                 + df['cci_top_div'].astype(int))
    df['multi_top_div'] = div_count >= 2

    # 未来收益 (按收盘价)
    for n in FWD:
        df[f'fwd_{n}d'] = c.shift(-n) / c - 1
        df[f'fwd_max_{n}d'] = (
            df['high'].shift(-1).rolling(n).max().shift(-(n - 1)) / c - 1
            if n > 1 else df['high'].shift(-1) / c - 1
        )
        df[f'fwd_min_{n}d'] = (
            df['low'].shift(-1).rolling(n).min().shift(-(n - 1)) / c - 1
            if n > 1 else df['low'].shift(-1) / c - 1
        )
    return df


def stats(series: pd.Series) -> dict:
    s = series.dropna()
    if len(s) == 0:
        return {'n': 0, 'mean': np.nan, 'med': np.nan, 'win': np.nan, 'max': np.nan, 'min': np.nan}
    return {
        'n': len(s),
        'mean': s.mean(),
        'med': s.median(),
        'win': (s > 0).mean(),
        'max': s.max(),
        'min': s.min(),
    }


def fmt_row(label: str, st: dict) -> str:
    if st['n'] == 0:
        return f'  {label:<14} 无样本'
    return (f"  {label:<14} n={st['n']:<3d}  均值 {st['mean']*100:+6.2f}%  "
            f"中位 {st['med']*100:+6.2f}%  胜率 {st['win']*100:5.1f}%  "
            f"最大 {st['max']*100:+6.1f}%  最小 {st['min']*100:+6.1f}%")


def pattern_macd_extreme(df: pd.DataFrame, q_th: float = 0.95):
    return df['macd_hist_q'] >= q_th


def pattern_volume_burst(df: pd.DataFrame, q_th: float = 0.95):
    return (df['turn_q'] >= q_th) | (df['volr_q'] >= q_th)


def pattern_consecutive_big_up(df: pd.DataFrame, min_cnt: int = 2):
    return df['big_up_5d_cnt'] >= min_cnt


def analyze_pattern(df: pd.DataFrame, sym: str, name: str,
                    mask: pd.Series, title: str, current_state: str):
    print(f"\n  ── {title}")
    print(f"     当前状态: {current_state}")
    sub = df[mask & (df['trade_date'] < df['trade_date'].iloc[-1])]  # 排除最新一天本身
    if len(sub) == 0:
        print('     历史无相似样本')
        return
    print(f"     历史相似日数: {len(sub)} ({sub['trade_date'].min().date()} ~ {sub['trade_date'].max().date()})")
    for n in FWD:
        print(fmt_row(f'后{n}日收益', stats(sub[f'fwd_{n}d'])))
    # 极值
    print(fmt_row('后5日最高', stats(sub['fwd_max_5d'])))
    print(fmt_row('后5日最低', stats(sub['fwd_min_5d'])))
    print(fmt_row('后10日最高', stats(sub['fwd_max_10d'])))
    print(fmt_row('后10日最低', stats(sub['fwd_min_10d'])))

    # 最近 3 次触发详情
    recent = sub.tail(3)
    if len(recent) > 0:
        print(f"     最近 3 次触发:")
        for _, r in recent.iterrows():
            f5 = r.get('fwd_5d')
            f10 = r.get('fwd_10d')
            mn10 = r.get('fwd_min_10d')
            f5_s = f'{f5*100:+.1f}%' if pd.notna(f5) else 'NA'
            f10_s = f'{f10*100:+.1f}%' if pd.notna(f10) else 'NA'
            mn_s = f'{mn10*100:+.1f}%' if pd.notna(mn10) else 'NA'
            print(f"       {r['trade_date'].date()}  收{r['close']:.2f}  后5日{f5_s}  后10日{f10_s}  后10日最深回撤{mn_s}")


def current_position(df: pd.DataFrame, sym: str) -> dict:
    last = df.iloc[-1]
    info = {
        'date': last['trade_date'].date(),
        'close': last['close'],
        'pct_chg': last['pct_chg'],
        'macd_hist': last['macd_hist'],
        'macd_hist_q': last['macd_hist_q'],
        'turnover': last['turnover_rate'],
        'turn_q': last['turn_q'],
        'vol_ma20_ratio': last['vol_ma20_ratio'],
        'volr_q': last['volr_q'],
        'big_up_5d_cnt': last['big_up_5d_cnt'],
    }
    # MACD 柱 2 年最大值
    win = df.tail(ROLL_WIN)
    info['macd_hist_max_2y'] = win['macd_hist'].max()
    info['is_macd_2y_high'] = bool(last['macd_hist'] >= win['macd_hist'].max() - 1e-9)
    return info


def header_for(sym: str, info: dict) -> str:
    pct = info['pct_chg']
    return (f"  日期 {info['date']}  收 {info['close']:.2f}  当日 {pct:+.2f}%\n"
            f"  MACD 柱 {info['macd_hist']:+.3f}  (滚动 2y 分位 P{info['macd_hist_q']*100:.0f}, "
            f"2y 最高 {info['macd_hist_max_2y']:+.3f}{'  ← 2y 新高' if info['is_macd_2y_high'] else ''})\n"
            f"  换手 {info['turnover']:.2f}%  (P{info['turn_q']*100:.0f})    "
            f"vol/MA20 {info['vol_ma20_ratio']:.2f} (P{info['volr_q']*100:.0f})\n"
            f"  近 5 日放量大阳数: {int(info['big_up_5d_cnt'])} 根")


# ──────────────────────────────────────────────────────────────────────────────
# 多周期 (周线 + 横截面 RPS) 过滤层
# 实现"改造 1+2+4": 周线趋势 / 周线 MACD 同向确认 / 13 周横截面 RPS
# 关键: 所有周线值都 shift(1), 仅使用上周末已收盘的数据避免未来函数
# ──────────────────────────────────────────────────────────────────────────────

def to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    d = df_daily.set_index('trade_date').sort_index()
    w = d.resample(WEEK_RULE).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'vol': 'sum', 'amount': 'sum',
    })
    return w.dropna(subset=['close'])


def add_weekly_indicators(w: pd.DataFrame) -> pd.DataFrame:
    c = w['close']
    w['ema20_w'] = c.ewm(span=20, adjust=False).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_w = ema12 - ema26
    sig_w = macd_w.ewm(span=9, adjust=False).mean()
    w['macd_hist_w'] = (macd_w - sig_w) * 2
    # 上周末值, 本周可用
    w['close_w_lag'] = c.shift(1)
    w['ema20_w_lag'] = w['ema20_w'].shift(1)
    w['macd_hist_w_lag'] = w['macd_hist_w'].shift(1)
    macd_hist_w_lag2 = w['macd_hist_w'].shift(2)
    # 周线趋势上行: 上周收盘 > 上周 EMA20, 且周线 MACD 柱较前周抬升
    w['w_trend_up'] = (
        (w['close_w_lag'] > w['ema20_w_lag']) &
        (w['macd_hist_w_lag'] > macd_hist_w_lag2)
    )
    # 周线 MACD 柱为正 (用于"日周共振"判定)
    w['w_macd_pos'] = w['macd_hist_w_lag'] > 0
    return w


def build_rps_panel(root: str) -> pd.DataFrame:
    """全样本横截面 RPS: 每周末按 13 周累计收益跨股票百分位排名 (0-100)"""
    files = sorted(glob.glob(os.path.join(root, 'cs_data_*.csv')))
    closes = {}
    for fp in files:
        sym = os.path.basename(fp).replace('cs_data_', '').replace('.csv', '')
        try:
            dfx = pd.read_csv(fp, usecols=['trade_date', 'close'])
        except Exception:
            continue
        dfx['trade_date'] = pd.to_datetime(dfx['trade_date'])
        dfx = dfx.sort_values('trade_date').set_index('trade_date')
        w_close = dfx['close'].resample(WEEK_RULE).last().dropna()
        if len(w_close) > RPS_LOOKBACK_W:
            closes[sym] = w_close
    if not closes:
        return pd.DataFrame()
    panel = pd.concat(closes, axis=1).sort_index()
    ret = panel.pct_change(RPS_LOOKBACK_W)
    rps = ret.rank(axis=1, pct=True) * 100  # 0-100, 越大越强
    return rps


def attach_mtf(df: pd.DataFrame, sym: str, rps_panel: pd.DataFrame) -> pd.DataFrame:
    """在日线 df 上挂接 w_trend_up / w_macd_pos / rps_w_lag / w_d_macd_confirm"""
    w = add_weekly_indicators(to_weekly(df))
    w_feat = w[['w_trend_up', 'w_macd_pos', 'close_w_lag',
                'ema20_w_lag', 'macd_hist_w_lag']].copy()
    if rps_panel is not None and not rps_panel.empty and sym in rps_panel.columns:
        rps_lag = rps_panel[sym].shift(1).rename('rps_w_lag')
        w_feat = w_feat.join(rps_lag, how='outer')
    else:
        w_feat['rps_w_lag'] = np.nan
    w_feat = w_feat.reset_index().rename(columns={'trade_date': 'week_end'})
    w_feat['week_id'] = w_feat['week_end'].dt.to_period(WEEK_RULE)

    out = df.copy()
    out['week_id'] = out['trade_date'].dt.to_period(WEEK_RULE)
    out = out.merge(
        w_feat[['week_id', 'w_trend_up', 'w_macd_pos', 'close_w_lag',
                'ema20_w_lag', 'macd_hist_w_lag', 'rps_w_lag']],
        on='week_id', how='left'
    ).drop(columns=['week_id'])
    out['d_macd_pos'] = out['macd_hist'] > 0
    out['w_d_macd_confirm'] = out['d_macd_pos'] & out['w_macd_pos'].fillna(False)
    return out


def print_mtf_context(sym: str, df_mtf: pd.DataFrame):
    last = df_mtf.iloc[-1]
    cw, ew = last['close_w_lag'], last['ema20_w_lag']
    mh = last['macd_hist_w_lag']
    rps = last['rps_w_lag']
    print('\n【多周期上下文 (上周末值, 本周可用)】')
    if pd.notna(cw) and pd.notna(ew):
        above = '上方' if cw > ew else '下方'
        macd_state = '正' if (pd.notna(mh) and mh > 0) else ('负' if pd.notna(mh) else 'NA')
        trend = '上行↑' if bool(last['w_trend_up']) else '横盘/下行↓'
        print(f"  周线: close {cw:.2f}  EMA20_W {ew:.2f} ({above})  "
              f"MACD柱 {mh:+.3f} ({macd_state})  → W趋势 {trend}")
    if pd.notna(rps):
        tag = '强势' if rps >= 80 else ('中等' if rps >= 50 else '弱势')
        print(f"  周线 RPS (13周累计 vs 科创板全样本横截面): P{rps:.0f} ({tag})")
    else:
        print('  周线 RPS: 暂无 (样本不足或未在面板)')
    today_macd = '正' if bool(last['d_macd_pos']) else '负'
    w_macd = '正' if bool(last['w_macd_pos']) else '负/NA'
    confirm = '✓ 日周同向' if bool(last['w_d_macd_confirm']) else '✗ 日周不同向'
    print(f"  日线 MACD 柱 {today_macd}  ×  周线 MACD 柱 {w_macd}  → {confirm}")


def collect_mtf_rows(sym: str, df_mtf: pd.DataFrame, info: dict) -> list:
    """对当前已触发形态按周线状态切片, 统计 5/10 日条件分布"""
    last_date = df_mtf['trade_date'].iloc[-1]
    patterns = [
        ('MACD柱≥P95', pattern_macd_extreme(df_mtf, 0.95),
         (info['macd_hist_q'] or 0) >= 0.95),
        ('量能≥P95', pattern_volume_burst(df_mtf, 0.95),
         max(info['turn_q'] or 0, info['volr_q'] or 0) >= 0.95),
        ('5d≥2根放量大阳', pattern_consecutive_big_up(df_mtf, 2),
         info['big_up_5d_cnt'] >= 2),
    ]
    out = []
    for label, mask, triggered in patterns:
        if not triggered:
            continue
        sub = df_mtf[mask & (df_mtf['trade_date'] < last_date)].copy()
        if len(sub) == 0:
            continue
        rps = sub['rps_w_lag']
        wtu = sub['w_trend_up'].fillna(False).astype(bool)
        wdc = sub['w_d_macd_confirm'].fillna(False).astype(bool)
        filters = [
            ('全部 (基准)', pd.Series(True, index=sub.index)),
            ('W趋势↑',       wtu),
            ('W趋势↓',       ~wtu),
            ('W&D MACD同向', wdc),
            ('W&D MACD不同向', ~wdc),
            ('RPS≥80 (强势)', rps.fillna(-1) >= 80),
            ('RPS 50-80',    (rps.fillna(-1) >= 50) & (rps.fillna(-1) < 80)),
            ('RPS<50 (弱势)', (rps.fillna(101) >= 0) & (rps.fillna(101) < 50)),
        ]
        for fname, fmask in filters:
            s5 = stats(sub.loc[fmask, 'fwd_5d'])
            s10 = stats(sub.loc[fmask, 'fwd_10d'])
            out.append({
                'sym': sym, 'pat': label, 'filter': fname,
                'n': s5['n'],
                'm5': s5['mean'], 'w5': s5['win'],
                'm10': s10['mean'], 'w10': s10['win'],
            })
    return out


def summary_table_mtf(rows: list):
    if not rows:
        return
    print('\n' + '=' * 92)
    print('  多周期过滤效果 (仅当前已触发形态, 历史样本按周线状态切片)')
    print('  ※ 周线 / RPS 值取上周末 (lag 1 周) 避免未来函数 ‖ RPS = 13 周累计收益横截面 P 值')
    print('=' * 92)
    print(f"  {'代码':<8}{'形态':<16}{'过滤':<18}{'n':<5}{'5d均':<10}{'5d胜':<8}"
          f"{'10d均':<10}{'10d胜':<8}")
    print('  ' + '-' * 88)
    cur_key = None
    for r in rows:
        key = (r['sym'], r['pat'])
        if key != cur_key:
            if cur_key is not None:
                print()
            cur_key = key
        n = r['n']
        if n == 0:
            print(f"  {r['sym']:<8}{r['pat']:<16}{r['filter']:<18}{'-':<5}"
                  f"{'-':<10}{'-':<8}{'-':<10}{'-':<8}")
            continue
        print(f"  {r['sym']:<8}{r['pat']:<16}{r['filter']:<18}{n:<5d}"
              f"{r['m5']*100:+6.2f}%   {r['w5']*100:5.1f}%  "
              f"{r['m10']*100:+6.2f}%   {r['w10']*100:5.1f}%")
    # 在底部输出关键 spread (W趋势↑ - W趋势↓), 一眼看出过滤是否有效
    print('\n  ── 关键 spread (W趋势↑ 减 W趋势↓; 正 = 周线趋势过滤有效)')
    df_r = pd.DataFrame(rows)
    for (sym, pat), g in df_r.groupby(['sym', 'pat'], sort=False):
        up = g[g['filter'] == 'W趋势↑']
        dn = g[g['filter'] == 'W趋势↓']
        if up.empty or dn.empty or up['n'].iloc[0] == 0 or dn['n'].iloc[0] == 0:
            continue
        sm5 = (up['m5'].iloc[0] - dn['m5'].iloc[0]) * 100
        sw5 = (up['w5'].iloc[0] - dn['w5'].iloc[0]) * 100
        sm10 = (up['m10'].iloc[0] - dn['m10'].iloc[0]) * 100
        sw10 = (up['w10'].iloc[0] - dn['w10'].iloc[0]) * 100
        verdict = '✓有效' if (sm5 >= 2.0 and sw5 >= 8.0) else (
            '△弱信号' if (sm5 >= 0.5 or sw5 >= 3.0) else '✗无效')
        print(f"    {sym}  {pat:<16} Δ5d均 {sm5:+5.2f}%  Δ5d胜 {sw5:+5.1f}pct  "
              f"Δ10d均 {sm10:+5.2f}%  Δ10d胜 {sw10:+5.1f}pct  → {verdict}")


def run_one(sym: str):
    name = NAMES.get(sym, sym)
    print('\n' + '=' * 78)
    print(f"  {sym} {name}")
    print('=' * 78)
    df = add_indicators(load(sym))
    info = current_position(df, sym)
    print('\n【当前形态】')
    print(header_for(sym, info))

    print('\n【三类形态历史相似情形】')

    # P1: MACD 柱极值
    cur1 = (f"macd_hist 处于 2y P{info['macd_hist_q']*100:.0f}, "
            f"{'已为 2y 新高' if info['is_macd_2y_high'] else '尚未破 2y 高'}")
    analyze_pattern(df, sym, name, pattern_macd_extreme(df, 0.95),
                    'P1 · MACD 柱 ≥ 2y P95', cur1)

    # P2: 量能异常
    cur2 = (f"换手 P{info['turn_q']*100:.0f}, vol/MA20 P{info['volr_q']*100:.0f}, "
            f"{'已触发' if (info['turn_q'] >= 0.95 or info['volr_q'] >= 0.95) else '未触发'}")
    analyze_pattern(df, sym, name, pattern_volume_burst(df, 0.95),
                    'P2 · 量能 ≥ 2y P95 (换手或 vol/MA20)', cur2)

    # P3: 连续放量大阳
    cur3 = f"近 5 日放量 +5% 大阳 {int(info['big_up_5d_cnt'])} 根, " + (
        '已触发' if info['big_up_5d_cnt'] >= 2 else '未触发')
    analyze_pattern(df, sym, name, pattern_consecutive_big_up(df, 2),
                    'P3 · 5 日内 ≥2 根放量 +5% 大阳', cur3)

    return df, info


def summary_table(rows: list):
    print('\n' + '=' * 78)
    print('  综合短期判断 (后 1/3/5/10 日均值收益 & 胜率，仅看当前已触发的形态)')
    print('=' * 78)
    print(f"  {'代码':<8}{'形态':<22}{'n':<5}{'1d均':<9}{'3d均':<9}{'5d均':<9}{'10d均':<9}{'5d胜':<7}{'10d胜':<7}")
    print('  ' + '-' * 76)
    for r in rows:
        n = r['n']
        if n == 0:
            print(f"  {r['sym']:<8}{r['pat']:<22}{'-':<5}{'-':<9}{'-':<9}{'-':<9}{'-':<9}{'-':<7}{'-':<7}")
            continue
        print(f"  {r['sym']:<8}{r['pat']:<22}{n:<5d}"
              f"{r['m1']*100:+6.2f}%  {r['m3']*100:+6.2f}%  {r['m5']*100:+6.2f}%  "
              f"{r['m10']*100:+6.2f}%  {r['w5']*100:5.1f}%  {r['w10']*100:5.1f}%")


def collect_summary_rows(sym: str, df: pd.DataFrame, info: dict) -> list:
    patterns = [
        ('MACD 柱 ≥P95', pattern_macd_extreme(df, 0.95),
         (info['macd_hist_q'] or 0) >= 0.95),
        ('量能 ≥P95', pattern_volume_burst(df, 0.95),
         max(info['turn_q'] or 0, info['volr_q'] or 0) >= 0.95),
        ('5d≥2根放量大阳', pattern_consecutive_big_up(df, 2),
         info['big_up_5d_cnt'] >= 2),
    ]
    out = []
    for label, mask, triggered in patterns:
        if not triggered:
            out.append({'sym': sym, 'pat': f'{label} [未触发]',
                        'n': 0, 'm1': 0, 'm3': 0, 'm5': 0, 'm10': 0, 'w5': 0, 'w10': 0})
            continue
        sub = df[mask & (df['trade_date'] < df['trade_date'].iloc[-1])]
        s1 = stats(sub['fwd_1d']); s3 = stats(sub['fwd_3d'])
        s5 = stats(sub['fwd_5d']); s10 = stats(sub['fwd_10d'])
        out.append({
            'sym': sym, 'pat': f'{label} [已触发]', 'n': s5['n'],
            'm1': s1['mean'], 'm3': s3['mean'], 'm5': s5['mean'], 'm10': s10['mean'],
            'w5': s5['win'], 'w10': s10['win'],
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbols', nargs='*', default=DEFAULT_SYMBOLS,
                        help=f'股票代码列表 (默认 {DEFAULT_SYMBOLS})')
    parser.add_argument('--no-mtf', action='store_true',
                        help='跳过多周期 (周线 + 横截面 RPS) 过滤层')
    args = parser.parse_args()

    rps_panel = None
    if not args.no_mtf:
        print('[*] 构建周线 RPS 横截面面板 (科创板全样本) ...', end='', flush=True)
        rps_panel = build_rps_panel(ROOT)
        if rps_panel.empty:
            print(' 无数据, 跳过 MTF')
            args.no_mtf = True
        else:
            print(f' 完成 ({rps_panel.shape[1]} 票 × {len(rps_panel)} 周)')

    all_rows, mtf_rows = [], []
    for sym in args.symbols:
        df, info = run_one(sym)
        if not args.no_mtf:
            df_mtf = attach_mtf(df, sym, rps_panel)
            print_mtf_context(sym, df_mtf)
            mtf_rows.extend(collect_mtf_rows(sym, df_mtf, info))
        all_rows.extend(collect_summary_rows(sym, df, info))
    summary_table(all_rows)
    if not args.no_mtf:
        summary_table_mtf(mtf_rows)
    print()


if __name__ == '__main__':
    main()
