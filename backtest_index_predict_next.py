#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
科创100 / 机器人指数 当日表现 → 688322 & 688017 次日盘口

回答的问题:
  - 指数当日 +X% (或 -X%), 个股次日"高开/低开"概率, 幅度均值?
  - 个股次日"全日 close-to-close"收益分布?
  - 指数当日方向对个股次日的预测力 (条件均值差 / 信息系数 IC)?

输出 3 张表:
  T1. 按指数当日涨跌幅分箱, 看个股次日 跳空(open vs pre_close) 分布
  T2. 同分箱下, 看个股次日 全日 (close vs pre_close) 分布
  T3. 滞后 1 日 IC (rank corr of 指数 day T vs 个股 day T+1)
"""

import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))

INDEXES = [
    ('000698.SH', '科创100'),
    ('931494.CSI', '机器人指数'),
    ('000688.SH', '科创50'),
    ('000852.SH', '中证1000'),
]
STOCKS = [('688322', '奥比中光'), ('688017', '绿的谐波')]


def load_stock(sym: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(ROOT, f'cs_data_{sym}.csv'))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[['trade_date', 'open', 'close', 'pre_close']].sort_values('trade_date').reset_index(drop=True)
    df[f'{sym}_gap'] = df['open'] / df['pre_close'] - 1     # 隔夜跳空
    df[f'{sym}_day'] = df['close'] / df['pre_close'] - 1    # 全日
    df[f'{sym}_intra'] = df['close'] / df['open'] - 1       # 日内
    return df[['trade_date', f'{sym}_gap', f'{sym}_day', f'{sym}_intra']]


def load_index(code: str) -> pd.DataFrame:
    fp = os.path.join(ROOT, f'idx_{code.replace(".", "_")}.csv')
    df = pd.read_csv(fp, dtype={'trade_date': str})
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df[['trade_date', 'pct_chg']].rename(columns={'pct_chg': code})
    df[code] = df[code] / 100.0
    return df.sort_values('trade_date').reset_index(drop=True)


# 分箱
BINS = [-np.inf, -0.02, -0.01, -0.003, 0.003, 0.01, 0.02, np.inf]
BIN_LABELS = ['<-2%', '-2~-1%', '-1~-0.3%', '-0.3~0.3%', '0.3~1%', '1~2%', '>2%']


def conditional_table(df: pd.DataFrame, idx_col: str, target_col: str, label: str):
    """分箱条件分布: idx day T -> stock day T+1"""
    d = df.copy()
    d['idx_bin'] = pd.cut(d[idx_col].shift(1), bins=BINS, labels=BIN_LABELS)  # 用昨日指数
    rows = []
    for bin_label in BIN_LABELS:
        sub = d[d['idx_bin'] == bin_label][target_col].dropna()
        if len(sub) == 0:
            rows.append({'区间': bin_label, 'n': 0, '均值': np.nan, '中位': np.nan,
                         '上涨概率': np.nan, '高开概率': np.nan, '最大': np.nan, '最小': np.nan})
            continue
        rows.append({
            '区间': bin_label, 'n': len(sub),
            '均值': sub.mean(), '中位': sub.median(),
            '上涨概率': (sub > 0).mean(), '高开概率': (sub > 0).mean(),
            '最大': sub.max(), '最小': sub.min(),
        })
    return pd.DataFrame(rows)


def print_table(df: pd.DataFrame, title: str):
    print(f"\n  {title}")
    print('  ' + '-' * 80)
    print(f"  {'区间':<12}{'n':<5}{'均值':<10}{'中位':<10}{'>0概率':<10}{'最大':<10}{'最小':<10}")
    for _, r in df.iterrows():
        if r['n'] == 0:
            print(f"  {r['区间']:<12}{'-':<5}{'-':<10}{'-':<10}{'-':<10}{'-':<10}{'-':<10}")
            continue
        print(f"  {r['区间']:<12}{int(r['n']):<5d}"
              f"{r['均值']*100:+6.2f}%   "
              f"{r['中位']*100:+6.2f}%   "
              f"{r['上涨概率']*100:5.1f}%    "
              f"{r['最大']*100:+6.1f}%   "
              f"{r['最小']*100:+6.1f}%")


def spearman_lag_ic(idx: pd.Series, stk: pd.Series) -> tuple[float, int]:
    """指数 day T 的 rank corr with 股票 day T+1"""
    d = pd.concat([idx.shift(1), stk], axis=1).dropna()
    if len(d) < 30:
        return np.nan, 0
    return d.iloc[:, 0].corr(d.iloc[:, 1], method='spearman'), len(d)


def main():
    print("加载数据...")
    base = load_stock('688322').merge(load_stock('688017'), on='trade_date', how='outer')
    for code, _ in INDEXES:
        base = base.merge(load_index(code), on='trade_date', how='outer')
    base = base.sort_values('trade_date').reset_index(drop=True)

    print(f"  样本: {len(base)} 行, {base['trade_date'].min().date()} ~ {base['trade_date'].max().date()}")

    # ============== T1 / T2: 条件分箱表 ==============
    for idx_code, idx_name in INDEXES:
        print('\n' + '=' * 90)
        print(f"  指数 {idx_name} ({idx_code}) 当日 → 个股次日")
        print('=' * 90)
        for sym, name in STOCKS:
            print(f"\n  ◆ {sym} {name}")
            t1 = conditional_table(base, idx_code, f'{sym}_gap', '次日跳空')
            print_table(t1, f"T1  指数昨日涨跌 → {sym} 次日跳空(open/pre_close)")
            t2 = conditional_table(base, idx_code, f'{sym}_day', '次日全日')
            print_table(t2, f"T2  指数昨日涨跌 → {sym} 次日全日(close/pre_close)")

    # ============== T3: 信息系数 IC ==============
    print('\n' + '=' * 90)
    print("  T3 · 滞后 1 日 Spearman IC (指数 day T 排名 vs 个股 day T+1 排名)")
    print('=' * 90)
    print(f"  {'指数':<14}{'股票':<12}{'对次日跳空 IC':<18}{'对次日全日 IC':<18}{'对次日日内 IC':<18}{'n':<6}")
    print('  ' + '-' * 86)
    for idx_code, idx_name in INDEXES:
        for sym, name in STOCKS:
            ic_gap, n1 = spearman_lag_ic(base[idx_code], base[f'{sym}_gap'])
            ic_day, _ = spearman_lag_ic(base[idx_code], base[f'{sym}_day'])
            ic_intra, _ = spearman_lag_ic(base[idx_code], base[f'{sym}_intra'])
            print(f"  {idx_name:<14}{sym:<12}{ic_gap:+.4f}             "
                  f"{ic_day:+.4f}             {ic_intra:+.4f}             {n1}")

    # ============== T4: 用 5/21 最新指数, 给出 5/25 的条件预测 ==============
    print('\n' + '=' * 90)
    print("  T4 · 用 5/21 指数实际涨跌, 给出 5/25 的条件分布预测")
    print('=' * 90)
    last_date = base['trade_date'].max()
    last = base[base['trade_date'] == last_date].iloc[0]
    print(f"  基准日: {last_date.date()}")
    for idx_code, idx_name in INDEXES:
        v = last[idx_code]
        if pd.isna(v):
            continue
        # 找 bin
        bin_idx = next((i for i, b in enumerate(BINS[:-1]) if BINS[i] <= v < BINS[i+1]), None)
        bin_label = BIN_LABELS[bin_idx] if bin_idx is not None else 'NA'
        print(f"\n  {idx_name} 当日 {v*100:+.2f}% → 落入 [{bin_label}] 桶")
        for sym, name in STOCKS:
            # 重用 conditional_table
            t = conditional_table(base, idx_code, f'{sym}_day', '')
            row = t[t['区间'] == bin_label]
            if len(row) == 0 or row.iloc[0]['n'] == 0:
                continue
            r = row.iloc[0]
            gap_t = conditional_table(base, idx_code, f'{sym}_gap', '')
            gap_row = gap_t[gap_t['区间'] == bin_label].iloc[0]
            print(f"    {sym} {name}: 次日跳空 均值 {gap_row['均值']*100:+.2f}% ({gap_row['上涨概率']*100:.0f}% 高开), "
                  f"次日全日 均值 {r['均值']*100:+.2f}% ({r['上涨概率']*100:.0f}% 收涨), n={int(r['n'])}")


if __name__ == '__main__':
    main()
