#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
池级 MTF spread 分析

对一个股票池, 聚合所有历史 P1/P2/P3 触发样本, 按周线状态
(W 趋势 / W&D MACD 同向 / 横截面 RPS) 切片, 计算"池级 Δspread"。

目的: 验证单股看到的"周线趋势过滤有效/无效"是否在池级层面收敛到稳定方向,
排除单股小样本带来的方差污染。

复用 backtest_pattern_match 的 add_indicators / pattern_* / attach_mtf。
"""

import argparse
import glob
import os
import sys
import numpy as np
import pandas as pd

from backtest_pattern_match import (
    load, add_indicators,
    pattern_macd_extreme, pattern_volume_burst, pattern_consecutive_big_up,
    build_rps_panel, attach_mtf, stats, ROOT,
)

PATTERNS = {
    'P1': ('MACD柱≥P95',     pattern_macd_extreme,        {'q_th': 0.95}),
    'P2': ('量能≥P95',        pattern_volume_burst,        {'q_th': 0.95}),
    'P3': ('5d≥2根放量大阳',   pattern_consecutive_big_up,  {'min_cnt': 2}),
}

def _rps_band(d, lo, hi):
    r = d['rps_w_lag'].fillna(-1)
    return (r >= lo) & (r < hi)


def _b(d, col):
    return d[col].fillna(False).astype(bool)


FILTERS = [
    ('全部 (基准)',      lambda d: pd.Series(True, index=d.index)),
    ('W趋势↑',           lambda d: _b(d, 'w_trend_up')),
    ('W趋势↓',           lambda d: ~_b(d, 'w_trend_up')),
    ('W&D MACD同向',     lambda d: _b(d, 'w_d_macd_confirm')),
    ('W&D MACD不同向',   lambda d: ~_b(d, 'w_d_macd_confirm')),
    ('RPS≥80',           lambda d: d['rps_w_lag'].fillna(-1) >= 80),
    ('RPS 50-80',        lambda d: _rps_band(d, 50, 80)),
    ('RPS<50',           lambda d: (d['rps_w_lag'].fillna(101) >= 0) & (d['rps_w_lag'].fillna(101) < 50)),
    # OBV 单一过滤
    ('OBV>EMA20',        lambda d: _b(d, 'obv_above_ema')),
    ('OBV<EMA20',        lambda d: ~_b(d, 'obv_above_ema')),
    ('OBV顶背离',         lambda d: _b(d, 'obv_top_div')),
    ('OBV量价确认',       lambda d: _b(d, 'obv_confirm_up')),
    # RSI / CCI / WR 单一
    ('RSI>70 超买',       lambda d: _b(d, 'rsi_overbought')),
    ('RSI<30 超卖',       lambda d: _b(d, 'rsi_oversold')),
    ('RSI 顶背离',        lambda d: _b(d, 'rsi_top_div')),
    ('CCI>100',          lambda d: _b(d, 'cci_extreme_pos')),
    ('CCI 顶背离',        lambda d: _b(d, 'cci_top_div')),
    ('WR>-20 超买',       lambda d: _b(d, 'wr_overbought')),
    ('多指标顶背离',       lambda d: _b(d, 'multi_top_div')),
]

# 组合过滤器 (multi-condition AND) — 测三类假说
#   方向 2 反向使用: "极值 + 双重过热" 应该是反向 / 避免入场信号
#   方向 3 RPS 甜区: "极值 + RPS 50-80" 是延续兑现的甜区
#   OBV 强化:   OBV 量价确认 / OBV 顶背离 是否能在甜区/警示区上叠加增益
COMBOS = [
    # ── 甜区组合 (期望正向)
    ('RPS50-80',                         lambda d: _rps_band(d, 50, 80)),
    ('RPS50-80 ∧ W趋势↓',                lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up'))),
    ('RPS50-80 ∧ W趋势↑',                lambda d: _rps_band(d, 50, 80) & _b(d, 'w_trend_up')),
    ('RPS50-80 ∧ OBV>EMA20',             lambda d: _rps_band(d, 50, 80) & _b(d, 'obv_above_ema')),
    ('RPS50-80 ∧ OBV量价确认',           lambda d: _rps_band(d, 50, 80) & _b(d, 'obv_confirm_up')),
    ('RPS50-80 ∧ W趋势↓ ∧ OBV>EMA20',   lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema')),
    # ── 反向警示组合 (期望负向 / 避免入场)
    ('W趋势↑ ∧ RPS≥80 (双重过热)',         lambda d: _b(d, 'w_trend_up') & (d['rps_w_lag'].fillna(-1) >= 80)),
    ('W趋势↑ ∧ OBV顶背离 (顶部避雷)',       lambda d: _b(d, 'w_trend_up') & _b(d, 'obv_top_div')),
    ('RPS≥80 ∧ OBV顶背离',               lambda d: (d['rps_w_lag'].fillna(-1) >= 80) & _b(d, 'obv_top_div')),
    # ── 反转入场组合
    ('OBV顶背离 ∧ W趋势↓',                lambda d: _b(d, 'obv_top_div') & (~_b(d, 'w_trend_up'))),
    ('OBV量价确认 ∧ W趋势↑',              lambda d: _b(d, 'obv_confirm_up') & _b(d, 'w_trend_up')),
    # ── RSI/CCI/WR 叠加 RPS 甜区
    ('RPS50-80 ∧ RSI<70 (避超买)',        lambda d: _rps_band(d, 50, 80) & (~_b(d, 'rsi_overbought'))),
    ('RPS50-80 ∧ CCI>100 (势头确认)',     lambda d: _rps_band(d, 50, 80) & _b(d, 'cci_extreme_pos')),
    ('RPS50-80 ∧ WR<-20 (避超买)',        lambda d: _rps_band(d, 50, 80) & (~_b(d, 'wr_overbought'))),
    # ── 多指标顶背离 避雷
    ('多指标顶背离 ∧ W趋势↑ (顶部共振)',     lambda d: _b(d, 'multi_top_div') & _b(d, 'w_trend_up')),
    ('多指标顶背离 ∧ RPS≥80 (强势顶部)',     lambda d: _b(d, 'multi_top_div') & (d['rps_w_lag'].fillna(-1) >= 80)),
    # ── 三重最优叠加
    ('RPS50-80 ∧ OBV量价确认 ∧ CCI>100',  lambda d: _rps_band(d, 50, 80) & _b(d, 'obv_confirm_up') & _b(d, 'cci_extreme_pos')),
    ('RPS50-80 ∧ OBV量价确认 ∧ RSI<70',   lambda d: _rps_band(d, 50, 80) & _b(d, 'obv_confirm_up') & (~_b(d, 'rsi_overbought'))),
]

# spread 判定阈值 (基于研究报告建议)
VERDICT_THRESH = {
    'effective': (1.0, 5.0),     # Δ5d均 >= 1.0%  且 Δ5d胜 >= 5pct
    'weak':      (0.3, 2.0),     # 任一达标
    'reverse':   (-0.5, -3.0),   # 反向
}


def pick_top_n_by_mv(root: str, n: int, board: str = 'all',
                     min_rows: int = 300) -> list:
    """按最新总市值挑 top-n; board: 'kechuang'=688xxx, 'chuangye'=300/301/302xxx, 'all'=不限"""
    files = sorted(glob.glob(os.path.join(root, 'cs_data_*.csv')))
    rows = []
    for fp in files:
        sym = os.path.basename(fp).replace('cs_data_', '').replace('.csv', '')
        if board == 'kechuang' and not sym.startswith('688'):
            continue
        if board == 'chuangye' and not (sym.startswith('300') or sym.startswith('301')
                                         or sym.startswith('302')):
            continue
        try:
            df = pd.read_csv(fp, usecols=['trade_date', 'total_mv'])
        except Exception:
            continue
        if len(df) < min_rows:
            continue
        mv = df['total_mv'].dropna()
        if mv.empty:
            continue
        rows.append((sym, float(mv.iloc[-1])))
    rows.sort(key=lambda x: -x[1])
    return [s for s, _ in rows[:n]]


def collect_pool_triggers(symbols: list, rps_panel: pd.DataFrame) -> dict:
    """每只票算出 P1/P2/P3 历史触发样本 + MTF 状态 + fwd 收益, 池级合并"""
    pool = {pcode: [] for pcode in PATTERNS}
    used = []
    for sym in symbols:
        try:
            df = add_indicators(load(sym))
        except Exception as e:
            print(f'  skip {sym}: {e}', file=sys.stderr)
            continue
        df = attach_mtf(df, sym, rps_panel)
        last_date = df['trade_date'].iloc[-1]
        used.append(sym)
        for pcode, (_, pfn, kwargs) in PATTERNS.items():
            mask = pfn(df, **kwargs)
            sub = df[mask & (df['trade_date'] < last_date)].copy()
            if sub.empty:
                continue
            sub = sub.assign(sym=sym)
            pool[pcode].append(sub[['sym', 'trade_date', 'fwd_5d', 'fwd_10d',
                                    'w_trend_up', 'w_d_macd_confirm', 'rps_w_lag',
                                    'obv_above_ema', 'obv_top_div', 'obv_confirm_up',
                                    'rsi_overbought', 'rsi_oversold', 'rsi_top_div',
                                    'cci_extreme_pos', 'cci_extreme_neg', 'cci_top_div',
                                    'wr_overbought', 'wr_oversold', 'multi_top_div']])
    return ({pcode: (pd.concat(lst) if lst else pd.DataFrame())
             for pcode, lst in pool.items()}, used)


def per_filter_stats(sub: pd.DataFrame, fmask: pd.Series):
    return stats(sub.loc[fmask, 'fwd_5d']), stats(sub.loc[fmask, 'fwd_10d'])


def verdict_for(sm5: float, sw5: float) -> str:
    eff_m, eff_w = VERDICT_THRESH['effective']
    weak_m, weak_w = VERDICT_THRESH['weak']
    rev_m, rev_w = VERDICT_THRESH['reverse']
    if sm5 >= eff_m and sw5 >= eff_w:
        return '✓ 有效'
    if sm5 >= weak_m or sw5 >= weak_w:
        return '△ 弱信号'
    if sm5 < rev_m or sw5 < rev_w:
        return '✗ 反向'
    return '— 无差异'


def per_stock_spread(df_pool: pd.DataFrame, up_mask_fn, dn_mask_fn) -> pd.DataFrame:
    """各股票上的 W趋势↑ - W趋势↓ Δ5d均 分布, 用于看池内一致性"""
    rows = []
    for sym, g in df_pool.groupby('sym'):
        um = up_mask_fn(g); dm = dn_mask_fn(g)
        u = g.loc[um, 'fwd_5d'].dropna()
        d = g.loc[dm, 'fwd_5d'].dropna()
        if len(u) < 5 or len(d) < 5:
            continue
        rows.append({
            'sym': sym,
            'n_up': len(u), 'n_dn': len(d),
            'mean_up': u.mean(), 'mean_dn': d.mean(),
            'delta_m5': (u.mean() - d.mean()) * 100,
            'delta_w5': ((u > 0).mean() - (d > 0).mean()) * 100,
        })
    return pd.DataFrame(rows)


def report_pool(pcode: str, label: str, df: pd.DataFrame, pool_size: int,
                show_per_stock: bool = True):
    print(f"\n  ── {pcode} · {label}  "
          f"({len(df)} 个触发样本, 来自 {df['sym'].nunique()}/{pool_size} 只票)")
    print(f"    {'过滤':<18}{'n':<6}{'5d均':<10}{'5d胜':<8}{'10d均':<10}{'10d胜':<8}")
    print('    ' + '-' * 64)
    by_name = {}
    for fname, ffn in FILTERS:
        fmask = ffn(df)
        s5, s10 = per_filter_stats(df, fmask)
        by_name[fname] = (s5, s10)
        if s5['n'] == 0:
            print(f"    {fname:<18}{'-':<6}{'-':<10}{'-':<8}{'-':<10}{'-':<8}")
            continue
        print(f"    {fname:<18}{s5['n']:<6d}{s5['mean']*100:+6.2f}%   "
              f"{s5['win']*100:5.1f}%  {s10['mean']*100:+6.2f}%   {s10['win']*100:5.1f}%")

    spreads = []
    for up, dn, tag in [
        ('W趋势↑',         'W趋势↓',         '周线趋势'),
        ('W&D MACD同向',   'W&D MACD不同向', '日周 MACD 共振'),
        ('RPS≥80',         'RPS<50',         'RPS 强弱'),
    ]:
        if up not in by_name or dn not in by_name:
            continue
        u5, u10 = by_name[up]; d5, d10 = by_name[dn]
        if u5['n'] == 0 or d5['n'] == 0:
            continue
        sm5  = (u5['mean'] - d5['mean']) * 100
        sw5  = (u5['win']  - d5['win'])  * 100
        sm10 = (u10['mean']- d10['mean'])* 100
        sw10 = (u10['win'] - d10['win']) * 100
        spreads.append((tag, up, dn, sm5, sw5, sm10, sw10, verdict_for(sm5, sw5)))
    print(f"\n    池级 Δspread:")
    for tag, up, dn, sm5, sw5, sm10, sw10, v in spreads:
        print(f"      [{tag:<14}] {up} − {dn}:  "
              f"Δ5d均 {sm5:+5.2f}%  Δ5d胜 {sw5:+5.1f}pct  "
              f"Δ10d均 {sm10:+5.2f}%  Δ10d胜 {sw10:+5.1f}pct  → {v}")

    if show_per_stock:
        # 单股 W趋势↑↓ Δ5d 分布
        per = per_stock_spread(
            df,
            lambda g: g['w_trend_up'].fillna(False).astype(bool),
            lambda g: ~g['w_trend_up'].fillna(False).astype(bool),
        )
        if not per.empty:
            pos = (per['delta_m5'] > 0).sum()
            neg = (per['delta_m5'] <= 0).sum()
            print(f"    单股一致性 (W趋势↑−↓ Δ5d均 分布, n={len(per)} 只 ≥5 样本):")
            print(f"      正向 {pos} 只 | 反向 {neg} 只 | 中位 {per['delta_m5'].median():+.2f}% "
                  f"| 均值 {per['delta_m5'].mean():+.2f}% | "
                  f"P25 {per['delta_m5'].quantile(0.25):+.2f}% / "
                  f"P75 {per['delta_m5'].quantile(0.75):+.2f}%")


def report_combos(pcode: str, label: str, df: pd.DataFrame, min_n: int = 30):
    """组合过滤器分析: 列出每个 combo 的 5d/10d 表现 + Δvs 基准 + ★/✗ 标记"""
    base_5 = stats(df['fwd_5d'])
    base_10 = stats(df['fwd_10d'])
    b5m, b5w = base_5['mean'], base_5['win']
    b10m, b10w = base_10['mean'], base_10['win']

    print(f"\n  ── {pcode} · {label} · 组合过滤  (基准: 5d 均 {b5m*100:+.2f}% / 胜 {b5w*100:.1f}%, "
          f"n={base_5['n']})")
    print(f"    {'组合':<34}{'n':<5}{'5d均':<10}{'5d胜':<8}"
          f"{'10d均':<10}{'10d胜':<8}{'Δ5d均':<9}{'Δ5d胜':<9}标记")
    print('    ' + '-' * 100)

    records = []
    for cname, cfn in COMBOS:
        cmask = cfn(df)
        s5 = stats(df.loc[cmask, 'fwd_5d'])
        s10 = stats(df.loc[cmask, 'fwd_10d'])
        if s5['n'] == 0:
            continue
        dm5 = (s5['mean'] - b5m) * 100
        dw5 = (s5['win'] - b5w) * 100
        flag = ''
        if s5['n'] >= min_n:
            if dm5 >= 2.0 and dw5 >= 5.0:
                flag = '★ 显著正向'
            elif dm5 <= -2.0 and dw5 <= -5.0:
                flag = '✗ 显著反向'
            elif dm5 >= 1.0 or dw5 >= 3.0:
                flag = '△ 弱正'
            elif dm5 <= -1.0 or dw5 <= -3.0:
                flag = '△ 弱反'
        n_warn = ' (n<30)' if s5['n'] < min_n else ''
        records.append({'combo': cname, 'n': s5['n'], 'm5': s5['mean'], 'w5': s5['win'],
                        'm10': s10['mean'], 'w10': s10['win'], 'dm5': dm5, 'dw5': dw5,
                        'flag': flag,
                        '_samples5d': df.loc[cmask, 'fwd_5d'].dropna().values})
        print(f"    {(cname + n_warn):<34}{s5['n']:<5d}"
              f"{s5['mean']*100:+6.2f}%   {s5['win']*100:5.1f}%  "
              f"{s10['mean']*100:+6.2f}%   {s10['win']*100:5.1f}%  "
              f"{dm5:+6.2f}%  {dw5:+5.1f}pct  {flag}")
    return records


def bootstrap_ci(samples: np.ndarray, n_boot: int = 1000,
                 alpha: float = 0.05, seed: int = 42) -> tuple:
    """非参数 bootstrap 给均值的 (1-alpha) 置信区间"""
    if len(samples) < 5:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(samples), size=(n_boot, len(samples)))
    boot_means = samples[idx].mean(axis=1)
    lo = np.percentile(boot_means, 100 * alpha / 2)
    hi = np.percentile(boot_means, 100 * (1 - alpha / 2))
    return (samples.mean(), lo, hi)


def report_top_combos_across(all_records: dict, pool_data: dict = None,
                             n_boot: int = 1000):
    """跨形态汇总: 最值得入场的 / 最值得规避的组合"""
    flat = []
    for pcode, recs in all_records.items():
        for r in recs:
            flat.append({**r, 'pcode': pcode})
    if not flat:
        return
    dfr = pd.DataFrame(flat)
    dfr = dfr[dfr['n'] >= 30].copy()
    if dfr.empty:
        return
    dfr['score'] = dfr['m5'] * 100 + dfr['w5'] * 100 * 0.5  # 简单复合分

    print('\n' + '=' * 92)
    print('  跨形态最佳 / 最差组合汇总 (n≥30, 按 5d 均 + 0.5×5d 胜复合排序)')
    print('=' * 92)

    def _print_block(rows: pd.DataFrame, title: str):
        print(f"\n  {title}:")
        print(f"    {'pat':<5}{'combo':<34}{'n':<5}{'5d均':<9}"
              f"{'95%CI下':<10}{'95%CI上':<10}{'5d胜':<7}{'CI≥0?':<7}")
        for _, r in rows.iterrows():
            samples = r['_samples5d']
            m, lo, hi = bootstrap_ci(samples, n_boot=n_boot)
            ci_pos = '✓' if lo > 0 else ('—' if lo > -0.005 else '✗')
            print(f"    {r['pcode']:<5}{r['combo']:<34}{r['n']:<5d}"
                  f"{r['m5']*100:+6.2f}% "
                  f"{lo*100:+6.2f}%   {hi*100:+6.2f}%   "
                  f"{r['w5']*100:5.1f}%  {ci_pos}")

    top = dfr.sort_values('score', ascending=False).head(6)
    _print_block(top, '▲ Top 6 入场组合 (bootstrap 95% CI, n_boot={})'.format(n_boot))
    bot = dfr.sort_values('score', ascending=True).head(6)
    _print_block(bot, '▼ Bottom 6 避雷 / fade 候选 (CI≥0? ✗ = 下界显著负)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbols', nargs='*', default=None,
                        help='股票代码列表; 不传则按 --top-n 自动选 (按总市值)')
    parser.add_argument('--top-n', type=int, default=30,
                        help='symbols 未指定时按最新总市值取 top-n (默认 30)')
    parser.add_argument('--board', choices=['all', 'kechuang', 'chuangye'],
                        default='all', help='板块过滤 (kechuang=688, chuangye=300/301/302)')
    parser.add_argument('--patterns', nargs='+', default=['P1', 'P2', 'P3'],
                        choices=['P1', 'P2', 'P3'], help='要分析的形态')
    parser.add_argument('--no-per-stock', action='store_true',
                        help='不输出单股一致性分布')
    parser.add_argument('--n-boot', type=int, default=1000,
                        help='bootstrap 重抽样次数 (默认 1000)')
    args = parser.parse_args()

    print('[*] 构建周线 RPS 横截面面板 (科创板全样本) ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成 ({rps_panel.shape[1]} 票 × {len(rps_panel)} 周)')

    if not args.symbols:
        print(f'[*] 按总市值挑 Top-{args.top_n} (板块={args.board}) ...', end='', flush=True)
        symbols = pick_top_n_by_mv(ROOT, args.top_n, board=args.board)
        print(f' {len(symbols)} 只')
    else:
        symbols = args.symbols

    print(f'[*] 聚合 {len(symbols)} 只票的历史触发 ...', end='', flush=True)
    pool, used = collect_pool_triggers(symbols, rps_panel)
    print(f' 完成 ({len(used)} 只有效)')
    print(f'    池: {", ".join(used)}')

    print('\n' + '=' * 92)
    print(f"  池级多周期过滤 spread 分析  (池 = {len(used)} 只科创板)")
    print('  ※ 周线 / RPS 取上周末值, 防未来函数')
    print('  ※ 验证目标: W趋势↑ Δspread 在池级是否收敛到正值')
    print('=' * 92)

    all_combo_records = {}
    for pcode in args.patterns:
        label, _, _ = PATTERNS[pcode]
        df = pool[pcode]
        if df.empty:
            print(f"\n  {pcode} · {label}: 无触发样本")
            continue
        report_pool(pcode, label, df, len(used),
                    show_per_stock=not args.no_per_stock)
        all_combo_records[pcode] = report_combos(pcode, label, df)

    report_top_combos_across(all_combo_records)
    print()


if __name__ == '__main__':
    main()
