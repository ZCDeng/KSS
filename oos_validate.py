#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组合策略的滚动 OOS 验证

把所有历史触发样本按半年 (H1/H2) 切分, 对回测得出的 ★ Top 入场组合 和
✗ 避雷组合, 计算每个半年窗口的 5d 均值, 看 edge 是跨时间稳定还是集中在
某个行情阶段 (过拟合风险)。

判定:
  ✓ 稳定         : 正向窗口比例 ≥ 75% 且 最差窗口 > -1%
  △ 部分稳定      : 正向窗口比例 ≥ 60%
  ✗ 不稳定 / 过拟合: 其余, 通常意味着 in-sample edge 来自特定行情阶段
"""

import argparse
import numpy as np
import pandas as pd

from backtest_pattern_match import build_rps_panel, ROOT
from pool_mtf_spread import (
    pick_top_n_by_mv, collect_pool_triggers, _b, _rps_band,
)

HALVES = [
    ('2023H1', '2023-01-01', '2023-06-30'),
    ('2023H2', '2023-07-01', '2023-12-31'),
    ('2024H1', '2024-01-01', '2024-06-30'),
    ('2024H2', '2024-07-01', '2024-12-31'),
    ('2025H1', '2025-01-01', '2025-06-30'),
    ('2025H2', '2025-07-01', '2025-12-31'),
    ('2026H1', '2026-01-01', '2026-12-31'),
]

ENTRY_COMBOS = [
    ('P3 base (基准, 无过滤)',              'P3', lambda d: pd.Series(True, index=d.index)),
    ('P3 + RPS50-80 + W趋势↓',              'P3', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up'))),
    ('P3 + RPS50-80 + W趋势↓ + OBV>EMA20',  'P3', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema')),
    ('P2 base (基准)',                       'P2', lambda d: pd.Series(True, index=d.index)),
    ('P2 + RPS50-80 + W趋势↓ + OBV>EMA20',  'P2', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema')),
    ('P3 + RPS50-80 + RSI<70',               'P3', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'rsi_overbought'))),
    ('P3 + OBV顶背离 + W趋势↓',              'P3', lambda d: _b(d, 'obv_top_div') & (~_b(d, 'w_trend_up'))),
]

AVOID_COMBOS = [
    ('P3 base (基准)',                       'P3', lambda d: pd.Series(True, index=d.index)),
    ('P3 + W趋势↑ + OBV顶背离',              'P3', lambda d: _b(d, 'w_trend_up') & _b(d, 'obv_top_div')),
    ('P3 + W趋势↑ + RPS≥80',                'P3', lambda d: _b(d, 'w_trend_up') & (d['rps_w_lag'].fillna(-1) >= 80)),
    ('P3 + 多指标顶背离 + W趋势↑',           'P3', lambda d: _b(d, 'multi_top_div') & _b(d, 'w_trend_up')),
    ('P2 + OBV量价确认 + W趋势↑',            'P2', lambda d: _b(d, 'obv_confirm_up') & _b(d, 'w_trend_up')),
]


def evaluate_per_window(df_pat: pd.DataFrame, cond_fn) -> pd.DataFrame:
    sub = df_pat[cond_fn(df_pat)].copy()
    if sub.empty:
        return pd.DataFrame()
    rows = []
    for label, t0, t1 in HALVES:
        win = sub[(sub['trade_date'] >= t0) & (sub['trade_date'] <= t1)]
        s = win['fwd_5d'].dropna()
        rows.append({
            'win_label': label,
            'n': len(s),
            'm5': s.mean() if len(s) > 0 else np.nan,
            'w5': (s > 0).mean() if len(s) > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def run_combos(combos, pool, title, base_m5_per_window=None):
    print('\n' + '=' * 102)
    print(f"  {title}")
    print('=' * 102)
    out_base = {}

    for cname, pcode, cfn in combos:
        df_pat = pool.get(pcode)
        if df_pat is None or df_pat.empty:
            continue
        win_df = evaluate_per_window(df_pat, cfn)
        full_sub = df_pat[cfn(df_pat)]
        full_s = full_sub['fwd_5d'].dropna()
        full_n = len(full_s)
        full_m = full_s.mean() if full_n > 0 else np.nan
        full_w = (full_s > 0).mean() if full_n > 0 else np.nan

        valid = win_df[win_df['n'] >= 5]
        pos_ratio = (valid['m5'] > 0).mean() * 100 if not valid.empty else np.nan
        worst = valid['m5'].min() if not valid.empty else np.nan
        std_m = valid['m5'].std() if len(valid) >= 2 else np.nan

        print(f"\n  ── [{pcode}] {cname}")
        print(f"     in-sample 全样本: n={full_n}  5d均 {full_m*100:+.2f}%  胜 {full_w*100:.1f}%")
        header = f"     {'窗口':<8}{'n':<5}{'5d均':<10}{'胜率':<8}"
        if base_m5_per_window is not None and 'base' not in cname.lower():
            header += f"{'vs基准':<10}"
        print(header)
        for _, r in win_df.iterrows():
            if r['n'] < 5:
                print(f"     {r['win_label']:<8}{r['n']:<5d}{'-':<10}{'-':<8}{'样本不足' if r['n']>0 else '空':<10}")
                continue
            base_str = ''
            if base_m5_per_window is not None and 'base' not in cname.lower():
                base_m = base_m5_per_window.get(pcode, {}).get(r['win_label'], np.nan)
                if pd.notna(base_m):
                    lift = (r['m5'] - base_m) * 100
                    base_str = f"{lift:+5.2f}%"
                else:
                    base_str = '—'
            print(f"     {r['win_label']:<8}{r['n']:<5d}"
                  f"{r['m5']*100:+6.2f}%   {r['w5']*100:5.1f}%   {base_str:<10}")

        if not np.isnan(pos_ratio):
            if pos_ratio >= 75 and worst > -0.01:
                verdict = '✓ 稳定'
            elif pos_ratio >= 60 and worst > -0.02:
                verdict = '△ 部分稳定'
            elif pos_ratio <= 30:
                verdict = '✓ 持续负向 (避雷组合应当如此)'
            else:
                verdict = '✗ 不稳定 / 过拟合'
            print(f"     一致性: 正向窗口 {pos_ratio:.0f}%  最差 {worst*100:+.2f}%  "
                  f"跨窗 std {std_m*100:.2f}%  → {verdict}")
        # 记录 base 用于后续 combo 的 vs-base 对比
        if 'base' in cname.lower():
            out_base[pcode] = {r['win_label']: r['m5'] for _, r in win_df.iterrows()}
    return out_base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=30)
    parser.add_argument('--board', choices=['kechuang', 'chuangye', 'all'],
                        default='kechuang')
    args = parser.parse_args()

    print('[*] 构建 RPS 面板 ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成 ({rps_panel.shape[1]} 票)')

    print(f'[*] 选 top-{args.top_n} ({args.board}) ...', end='', flush=True)
    symbols = pick_top_n_by_mv(ROOT, args.top_n, board=args.board)
    print(f' {len(symbols)} 只')

    print('[*] 聚合历史触发 ...', end='', flush=True)
    pool, _ = collect_pool_triggers(symbols, rps_panel)
    print(f' 完成 (P1={len(pool["P1"])}, P2={len(pool["P2"])}, P3={len(pool["P3"])})')

    # 先跑 base 拿到各 pattern 各窗口基准, 后续 combo 显示 vs-base lift
    print(f'\n  数据池: {args.board} 板块 top-{args.top_n}')
    print(f'  窗口: {len(HALVES)} 个半年 ({HALVES[0][1]} ~ {HALVES[-1][2]})')

    base_per_window = run_combos(
        ENTRY_COMBOS, pool,
        f'入场组合 OOS 半年滚动 (基准 = 该形态全样本无过滤)',
    )

    run_combos(
        AVOID_COMBOS, pool,
        f'避雷组合 OOS 半年滚动 (期望: 持续负向 / 持续低胜率)',
        base_m5_per_window=base_per_window,
    )
    print()


if __name__ == '__main__':
    main()
