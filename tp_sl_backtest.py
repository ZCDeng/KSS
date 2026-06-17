#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TP/SL 持仓策略回测

对历史触发样本逐日模拟止盈/止损:
  开仓: 触发日 close
  退出顺序判定 (每日):
    1. 若 low ≤ -SL_pct → 止损出场 (悲观假设: 同日触及双边时止损先)
    2. 若 high ≥ +TP_pct → 止盈出场
    3. 否则继续持有
  时间止损: N 日仍未出 → 收盘价出场

输出:
  trade-level Sharpe (mean / std), 累计净值, 最大回撤
  对比: 基础触发 vs 入场组合过滤后 vs 避雷过滤后
"""

import argparse
import numpy as np
import pandas as pd

from backtest_pattern_match import (
    load, add_indicators,
    pattern_macd_extreme, pattern_volume_burst, pattern_consecutive_big_up,
    build_rps_panel, attach_mtf, ROOT,
)
from pool_mtf_spread import pick_top_n_by_mv, _b, _rps_band

PATTERNS = {
    'P1': pattern_macd_extreme,
    'P2': pattern_volume_burst,
    'P3': pattern_consecutive_big_up,
}
PATTERN_KW = {'P1': {'q_th': 0.95}, 'P2': {'q_th': 0.95}, 'P3': {'min_cnt': 2}}

STRATEGIES = [
    ('P3 全样本 (基准)',                 'P3', lambda d: pd.Series(True, index=d.index)),
    ('P3 + RPS50-80 + W趋势↓',           'P3', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up'))),
    ('P3 + RPS50-80 + W趋势↓ + OBV>EMA20', 'P3', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema')),
    ('P3 + 排除 (W趋势↑+OBV顶背离)',      'P3', lambda d: ~(_b(d, 'w_trend_up') & _b(d, 'obv_top_div'))),
    ('P2 全样本 (基准)',                 'P2', lambda d: pd.Series(True, index=d.index)),
    ('P2 + RPS50-80 + W趋势↓ + OBV>EMA20', 'P2', lambda d: _rps_band(d, 50, 80) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema')),
    ('P2 + 排除 (OBV量价确认+W趋势↑)',    'P2', lambda d: ~(_b(d, 'obv_confirm_up') & _b(d, 'w_trend_up'))),
]


def simulate_trades(df: pd.DataFrame, trigger_mask: pd.Series,
                    tp_pct: float, sl_pct: float, n_days: int) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    trigger_mask = trigger_mask.reset_index(drop=True)
    indices = df.index[trigger_mask].tolist()
    trades = []
    for idx in indices:
        if idx + n_days >= len(df):
            continue
        entry = df.iloc[idx]['close']
        if entry <= 0:
            continue
        result = None; exit_day = None; exit_type = None
        for j in range(1, n_days + 1):
            row = df.iloc[idx + j]
            ret_low = row['low'] / entry - 1
            ret_high = row['high'] / entry - 1
            if ret_low <= -sl_pct:
                result = -sl_pct; exit_day = j; exit_type = 'SL'; break
            if ret_high >= tp_pct:
                result = tp_pct; exit_day = j; exit_type = 'TP'; break
        if result is None:
            result = df.iloc[idx + n_days]['close'] / entry - 1
            exit_day = n_days; exit_type = 'TIME'
        trades.append({
            'entry_date': df.iloc[idx]['trade_date'],
            'ret': result,
            'exit_day': exit_day,
            'exit_type': exit_type,
        })
    return pd.DataFrame(trades)


def aggregate(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {'n': 0}
    r = trades['ret'].values
    n = len(r)
    mean_r = r.mean()
    std_r = r.std()
    sharpe = mean_r / std_r if std_r > 0 else np.nan
    win = (r > 0).mean()
    tp_rate = (trades['exit_type'] == 'TP').mean()
    sl_rate = (trades['exit_type'] == 'SL').mean()
    time_rate = (trades['exit_type'] == 'TIME').mean()
    # 等权重组合净值 (按 entry_date 排序, 每笔独立 1 元资金)
    trades_sorted = trades.sort_values('entry_date').reset_index(drop=True)
    equity = (1 + trades_sorted['ret']).cumprod()
    peak = equity.cummax()
    dd = (equity / peak - 1).min()
    # 平均持仓天数
    mean_hold = trades['exit_day'].mean()
    return {
        'n': n, 'mean': mean_r, 'std': std_r, 'sharpe': sharpe,
        'win': win, 'tp_rate': tp_rate, 'sl_rate': sl_rate, 'time_rate': time_rate,
        'cum_ret': equity.iloc[-1] - 1 if len(equity) > 0 else 0,
        'max_dd': dd, 'mean_hold': mean_hold,
    }


def run_strategy(symbols: list, rps_panel: pd.DataFrame,
                 strategy_name: str, pcode: str, cond_fn,
                 tp_pct: float, sl_pct: float, n_days: int) -> dict:
    pat_fn = PATTERNS[pcode]
    pat_kw = PATTERN_KW[pcode]
    all_trades = []
    for sym in symbols:
        try:
            df = add_indicators(load(sym))
            df = attach_mtf(df, sym, rps_panel)
        except Exception:
            continue
        trigger_mask = pat_fn(df, **pat_kw) & cond_fn(df)
        trades = simulate_trades(df, trigger_mask, tp_pct, sl_pct, n_days)
        if not trades.empty:
            trades['sym'] = sym
            all_trades.append(trades)
    if not all_trades:
        return {'name': strategy_name, 'pcode': pcode, **aggregate(pd.DataFrame())}
    full = pd.concat(all_trades, ignore_index=True)
    agg = aggregate(full)
    return {'name': strategy_name, 'pcode': pcode, **agg}


def print_table(rows: list, tp: float, sl: float, n: int, board: str):
    print('\n' + '=' * 110)
    print(f"  TP/SL 持仓回测: TP +{tp*100:.1f}% / SL -{sl*100:.1f}% / 时间止损 {n} 日 ‖ "
          f"池={board} top-30")
    print('=' * 110)
    print(f"  {'策略':<40}{'n':<6}{'均收':<9}{'std':<8}{'Sharpe':<8}"
          f"{'胜率':<7}{'TP%':<7}{'SL%':<7}{'TIME%':<7}{'累净':<9}{'最大回撤':<9}{'持仓日'}")
    print('  ' + '-' * 106)
    for r in rows:
        if r['n'] == 0:
            print(f"  {r['name']:<40}{'无样本'}")
            continue
        print(f"  {r['name']:<40}{r['n']:<6d}"
              f"{r['mean']*100:+6.2f}%  {r['std']*100:5.2f}%  {r['sharpe']:+5.2f}  "
              f"{r['win']*100:5.1f}%  {r['tp_rate']*100:5.1f}%  "
              f"{r['sl_rate']*100:5.1f}%  {r['time_rate']*100:5.1f}%  "
              f"{r['cum_ret']*100:+7.1f}%  {r['max_dd']*100:+6.1f}%  {r['mean_hold']:.1f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tp', type=float, default=0.05, help='止盈百分比 (默认 5%)')
    parser.add_argument('--sl', type=float, default=0.03, help='止损百分比 (默认 3%)')
    parser.add_argument('--n-days', type=int, default=10, help='时间止损天数 (默认 10)')
    parser.add_argument('--board', choices=['kechuang', 'chuangye'], default='kechuang')
    parser.add_argument('--top-n', type=int, default=30)
    args = parser.parse_args()

    print('[*] 构建 RPS 面板 ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成 ({rps_panel.shape[1]} 票)')

    print(f'[*] 选 top-{args.top_n} ({args.board}) ...', end='', flush=True)
    symbols = pick_top_n_by_mv(ROOT, args.top_n, board=args.board)
    print(f' {len(symbols)} 只')

    print('[*] 逐股逐 trigger 模拟 TP/SL ...')
    rows = []
    for i, (name, pcode, cfn) in enumerate(STRATEGIES, 1):
        print(f'  [{i}/{len(STRATEGIES)}] {name}', flush=True)
        r = run_strategy(symbols, rps_panel, name, pcode, cfn,
                         args.tp, args.sl, args.n_days)
        rows.append(r)

    print_table(rows, args.tp, args.sl, args.n_days, args.board)
    print()


if __name__ == '__main__':
    main()
