#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
当日基准触发清单 (无过滤)

OOS 验证显示组合过滤大多过拟合, 但基础形态触发本身就是稳定 alpha:
  科创 P3 全样本: 86% 半年窗口正向, 跨窗 std 1.77%
  科创 P2 全样本: 100% 半年窗口正向, 跨窗 std 0.91%
  创业 P3 全样本: 86% 半年窗口正向, 跨窗 std 1.54%

本脚本输出当日纯触发清单, 不加任何 RPS/W趋势/OBV 过滤, 按用户指定字段排序。
人工筛选时建议:
  - 优先大 mv (流动性) + 中等振幅 (避免一字板)
  - 看 W 趋势 / RPS 仅作辅助, 不作硬过滤
"""

import argparse
import glob
import os
import pandas as pd

from backtest_pattern_match import (
    load, add_indicators,
    pattern_macd_extreme, pattern_volume_burst, pattern_consecutive_big_up,
    build_rps_panel, attach_mtf, ROOT, NAMES,
)

PATTERNS = {
    'P1': ('MACD柱≥P95',     pattern_macd_extreme,        {'q_th': 0.95}),
    'P2': ('量能≥P95',        pattern_volume_burst,        {'q_th': 0.95}),
    'P3': ('5d≥2根放量大阳',   pattern_consecutive_big_up,  {'min_cnt': 2}),
}


def detect_board(sym: str) -> str:
    if sym.startswith('688'):
        return '科创'
    if sym.startswith(('300', '301', '302')):
        return '创业'
    return '其他'


def scan_universe(board: str = 'all') -> list:
    files = sorted(glob.glob(os.path.join(ROOT, 'cs_data_*.csv')))
    out = []
    for fp in files:
        sym = os.path.basename(fp).replace('cs_data_', '').replace('.csv', '')
        if board == 'kechuang' and not sym.startswith('688'):
            continue
        if board == 'chuangye' and not sym.startswith(('300', '301', '302')):
            continue
        out.append(sym)
    return out


def evaluate_today(symbols: list, rps_panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        try:
            df = add_indicators(load(sym))
            df = attach_mtf(df, sym, rps_panel)
        except Exception:
            continue
        if len(df) < 60:
            continue
        last = df.iloc[-1:].copy().assign(sym=sym)
        for pcode, (_, pfn, kwargs) in PATTERNS.items():
            last[f'trig_{pcode}'] = pfn(df, **kwargs).iloc[-1]
        rows.append(last)
    if not rows:
        return pd.DataFrame()
    today_df = pd.concat(rows, ignore_index=True)
    max_date = today_df['trade_date'].max()
    return today_df[today_df['trade_date'] == max_date].reset_index(drop=True)


def print_pattern_list(today_df: pd.DataFrame, pcode: str, label: str,
                       sort_by: str, ascending: bool, top: int):
    trig = today_df[today_df[f'trig_{pcode}'].astype(bool)].copy()
    if trig.empty:
        print(f"\n  {pcode} · {label}: 今日无触发")
        return
    # 派生排序字段
    trig['_amp'] = trig['high'] / trig['low'] - 1
    trig['_mv_yi'] = trig.get('total_mv', pd.Series(0)) / 10000  # 万 → 亿
    sort_col = {'mv': '_mv_yi', 'amp': '_amp', 'pct': 'pct_chg',
                'amount': 'amount', 'rps': 'rps_w_lag'}[sort_by]
    trig = trig.sort_values(sort_col, ascending=ascending).head(top)

    print(f"\n  {pcode} · {label}  (今日触发 {today_df[f'trig_{pcode}'].sum()} 只, "
          f"展示前 {min(top, len(trig))})")
    print(f"     {'代码':<10}{'板':<5}{'名称':<10}{'mv亿':<8}"
          f"{'RPS':<5}{'W':<3}{'收盘':<9}{'当日%':<8}{'振幅':<7}"
          f"{'额万':<10}{'触发':<10}")
    print('     ' + '-' * 96)
    for _, r in trig.iterrows():
        sym = r['sym']
        name = NAMES.get(sym, '')
        rps = r['rps_w_lag']
        rps_s = f'{rps:.0f}' if pd.notna(rps) else '--'
        wtu = '↑' if bool(r['w_trend_up']) else '↓'
        mv = r['_mv_yi']
        mv_s = f'{mv:.0f}' if pd.notna(mv) else '--'
        amt_s = f'{r["amount"]/10:.0f}'  # 千元→万
        trigs = '+'.join([p for p in ['P1', 'P2', 'P3'] if r[f'trig_{p}']])
        print(f"     {sym:<10}{detect_board(sym):<5}{name:<10}{mv_s:<8}"
              f"{rps_s:<5}{wtu:<3}{r['close']:<9.2f}{r['pct_chg']:+6.2f}%  "
              f"{r['_amp']:>5.1%}  {amt_s:<10}{trigs:<10}")


def print_overlap(today_df: pd.DataFrame, top: int):
    """多形态共振 (P1+P2+P3 至少 2 个) 通常质量更高"""
    trig_count = (today_df['trig_P1'].astype(int) + today_df['trig_P2'].astype(int)
                  + today_df['trig_P3'].astype(int))
    sub = today_df[trig_count >= 2].copy()
    if sub.empty:
        print('\n  多形态共振: 今日无 2 个以上同发')
        return
    sub['_amp'] = sub['high'] / sub['low'] - 1
    sub['_mv_yi'] = sub.get('total_mv', pd.Series(0)) / 10000
    sub['_trig_cnt'] = trig_count[sub.index]
    sub = sub.sort_values(['_trig_cnt', '_mv_yi'], ascending=[False, False]).head(top)
    print(f"\n  多形态共振 (P1/P2/P3 至少 2 个同发, 今日 {len(sub)} 只)")
    print(f"     {'代码':<10}{'板':<5}{'名称':<10}{'mv亿':<8}"
          f"{'RPS':<5}{'W':<3}{'收盘':<9}{'当日%':<8}{'振幅':<7}{'触发':<14}")
    print('     ' + '-' * 96)
    for _, r in sub.iterrows():
        sym = r['sym']
        name = NAMES.get(sym, '')
        rps = r['rps_w_lag']
        rps_s = f'{rps:.0f}' if pd.notna(rps) else '--'
        wtu = '↑' if bool(r['w_trend_up']) else '↓'
        mv = r['_mv_yi']
        mv_s = f'{mv:.0f}' if pd.notna(mv) else '--'
        trigs = '+'.join([p for p in ['P1', 'P2', 'P3'] if r[f'trig_{p}']])
        print(f"     {sym:<10}{detect_board(sym):<5}{name:<10}{mv_s:<8}"
              f"{rps_s:<5}{wtu:<3}{r['close']:<9.2f}{r['pct_chg']:+6.2f}%  "
              f"{r['_amp']:>5.1%}  {trigs:<14}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', choices=['all', 'kechuang', 'chuangye'], default='all')
    parser.add_argument('--top', type=int, default=20, help='每个形态展示前 N 只 (默认 20)')
    parser.add_argument('--sort-by', choices=['mv', 'amp', 'pct', 'amount', 'rps'],
                        default='mv', help='排序字段 (默认按市值降序)')
    parser.add_argument('--ascending', action='store_true', help='升序排序 (默认降序)')
    parser.add_argument('--patterns', nargs='+', default=['P3', 'P2', 'P1'],
                        choices=['P1', 'P2', 'P3'])
    args = parser.parse_args()

    print('[*] 构建 RPS 面板 ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成')

    symbols = scan_universe(args.board)
    print(f'[*] 扫描 {len(symbols)} 只 ({args.board}) ...', end='', flush=True)
    today_df = evaluate_today(symbols, rps_panel)
    print(f' 完成 ({len(today_df)} 只有效)')
    if today_df.empty:
        return

    last_date = today_df['trade_date'].iloc[0]
    print(f'\n  数据截至: {last_date.date()}')
    print(f'  当日 P1/P2/P3 触发数: '
          f"P1={today_df['trig_P1'].sum()}  "
          f"P2={today_df['trig_P2'].sum()}  "
          f"P3={today_df['trig_P3'].sum()}")
    print(f'  排序: {args.sort_by} {"升" if args.ascending else "降"}序; 每形态前 {args.top}')

    print('\n' + '=' * 100)
    print(f'  当日基准触发清单 (无过滤, OOS 验证基础 alpha)')
    print('=' * 100)

    for pcode in args.patterns:
        label, _, _ = PATTERNS[pcode]
        print_pattern_list(today_df, pcode, label, args.sort_by,
                           args.ascending, args.top)

    print('\n' + '=' * 100)
    print(f'  多形态共振 (信号叠加, 优先关注)')
    print('=' * 100)
    print_overlap(today_df, args.top)
    print()


if __name__ == '__main__':
    main()
