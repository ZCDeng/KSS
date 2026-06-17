#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPS 阈值 walk-forward 动态选择

测试假设: "RPS 50-80 是最佳带"是否可以通过历史数据动态学到。

做法:
  对每个半年测试窗口 T, 用前 1 个半年作训练窗口, 在训练窗口里评估 5 个 RPS 分位带
  (0-20, 20-40, 40-60, 60-80, 80-100) 的 5d 平均收益, 选最佳带应用到测试窗口。

对比 3 条曲线:
  WF (walk-forward): 动态选择
  STATIC: 固定 RPS 50-80
  ORACLE: 事后选每个测试窗口实际最佳带 (上限)

如果 WF 接近 ORACLE 且优于 STATIC → 学习有意义
如果 WF 接近 STATIC → 50-80 已是稳健默认, 动态选择没增量
如果 WF < STATIC → 最佳带在窗口间漂移大, 学不到
"""

import argparse
import numpy as np
import pandas as pd

from backtest_pattern_match import build_rps_panel, ROOT
from pool_mtf_spread import pick_top_n_by_mv, collect_pool_triggers, _b

HALVES = [
    ('2023H1', '2023-01-01', '2023-06-30'),
    ('2023H2', '2023-07-01', '2023-12-31'),
    ('2024H1', '2024-01-01', '2024-06-30'),
    ('2024H2', '2024-07-01', '2024-12-31'),
    ('2025H1', '2025-01-01', '2025-06-30'),
    ('2025H2', '2025-07-01', '2025-12-31'),
    ('2026H1', '2026-01-01', '2026-12-31'),
]

RPS_BANDS = [
    ('RPS 0-20',   lambda d: (d['rps_w_lag'].fillna(-1) >= 0)  & (d['rps_w_lag'].fillna(-1) < 20)),
    ('RPS 20-40',  lambda d: (d['rps_w_lag'].fillna(-1) >= 20) & (d['rps_w_lag'].fillna(-1) < 40)),
    ('RPS 40-60',  lambda d: (d['rps_w_lag'].fillna(-1) >= 40) & (d['rps_w_lag'].fillna(-1) < 60)),
    ('RPS 60-80',  lambda d: (d['rps_w_lag'].fillna(-1) >= 60) & (d['rps_w_lag'].fillna(-1) < 80)),
    ('RPS 80-100', lambda d: (d['rps_w_lag'].fillna(-1) >= 80) & (d['rps_w_lag'].fillna(-1) <= 100)),
]

# 在 W趋势↓ 子集内做带选择 (这个轴上的 in-sample 信号最强)
W_FILTER = lambda d: ~_b(d, 'w_trend_up')


def slice_window(df: pd.DataFrame, t0: str, t1: str) -> pd.DataFrame:
    return df[(df['trade_date'] >= t0) & (df['trade_date'] <= t1)]


def band_mean_5d(df_win: pd.DataFrame, band_fn) -> tuple:
    """返回 (mean_5d, n)"""
    sub = df_win[band_fn(df_win) & W_FILTER(df_win)]
    s = sub['fwd_5d'].dropna()
    return (s.mean() if len(s) >= 5 else None, len(s))


def best_band_in_window(df_win: pd.DataFrame, min_n: int = 5) -> tuple:
    """返回 (best_band_idx, best_band_name, mean)"""
    best_idx, best_name, best_m = None, None, -np.inf
    for i, (name, fn) in enumerate(RPS_BANDS):
        m, n = band_mean_5d(df_win, fn)
        if m is None or n < min_n:
            continue
        if m > best_m:
            best_idx, best_name, best_m = i, name, m
    return (best_idx, best_name, best_m)


def static_band_test(df_win: pd.DataFrame, start: int, end: int) -> tuple:
    """RPS [start, end) 静态带在测试窗口的表现"""
    band_fn = lambda d: (d['rps_w_lag'].fillna(-1) >= start) & (d['rps_w_lag'].fillna(-1) < end)
    return band_mean_5d(df_win, band_fn)


def report(pcode: str, pool: dict, board: str):
    df_pat = pool.get(pcode)
    if df_pat is None or df_pat.empty:
        print(f"\n  {pcode}: 无样本")
        return

    print(f"\n  {pcode} 形态 (W趋势↓ 子集内做 RPS 带选择, {board})")
    print(f"  {'测试窗口':<10}{'训练窗口':<10}{'WF 选带':<14}{'WF train':<11}"
          f"{'WF test':<10}{'STATIC 50-80':<14}{'ORACLE 最佳':<14}{'WF-STATIC':<10}")
    print('  ' + '-' * 92)

    wf_returns, static_returns, oracle_returns = [], [], []
    for i in range(1, len(HALVES)):
        train_label, t0_train, t1_train = HALVES[i-1]
        test_label, t0_test, t1_test = HALVES[i]
        df_train = slice_window(df_pat, t0_train, t1_train)
        df_test = slice_window(df_pat, t0_test, t1_test)

        # WF: 从训练窗口学带, 应用到测试窗口
        train_idx, train_name, train_m = best_band_in_window(df_train)
        if train_idx is None:
            print(f"  {test_label:<10}{train_label:<10}{'训练样本不足':<14}")
            continue
        # 测试窗口表现 (WF 选的带)
        wf_m, wf_n = band_mean_5d(df_test, RPS_BANDS[train_idx][1])
        # STATIC 50-80 表现 (W趋势↓ 子集)
        static_m, static_n = static_band_test(df_test, 50, 80)
        static_sub_m = None
        if static_n >= 5:
            # 在 W趋势↓ 内的 RPS 50-80
            sub = df_test[_b(df_test, 'w_trend_up').apply(lambda x: not x)
                          & (df_test['rps_w_lag'].fillna(-1) >= 50)
                          & (df_test['rps_w_lag'].fillna(-1) < 80)]
            s = sub['fwd_5d'].dropna()
            static_sub_m = s.mean() if len(s) >= 5 else None
        # ORACLE: 测试窗口事后最佳带
        oracle_idx, oracle_name, oracle_m = best_band_in_window(df_test)

        wf_s = f"{wf_m*100:+5.2f}%/n{wf_n}" if wf_m is not None else f"n{wf_n}小"
        static_s = (f"{static_sub_m*100:+5.2f}%" if static_sub_m is not None
                    else '样本不足')
        oracle_s = (f"{oracle_name.replace('RPS ', '')}:{oracle_m*100:+5.2f}%"
                    if oracle_m > -np.inf else '—')
        diff_s = (f"{(wf_m-static_sub_m)*100:+5.2f}%"
                  if wf_m is not None and static_sub_m is not None else '—')

        print(f"  {test_label:<10}{train_label:<10}"
              f"{train_name.replace('RPS ', ''):<14}{train_m*100:+5.2f}%      "
              f"{wf_s:<10}{static_s:<14}{oracle_s:<14}{diff_s:<10}")

        if wf_m is not None: wf_returns.append(wf_m)
        if static_sub_m is not None: static_returns.append(static_sub_m)
        if oracle_m > -np.inf: oracle_returns.append(oracle_m)

    print('  ' + '-' * 92)
    if wf_returns and static_returns and oracle_returns:
        wf_avg = np.mean(wf_returns) * 100
        st_avg = np.mean(static_returns) * 100
        or_avg = np.mean(oracle_returns) * 100
        # capture ratio: WF 能捕获多少 oracle alpha
        if or_avg != 0:
            capture = (wf_avg - st_avg) / (or_avg - st_avg) * 100 if or_avg != st_avg else 0
        else:
            capture = 0
        print(f"  汇总 ({len(wf_returns)} 个测试窗口):  "
              f"WF {wf_avg:+5.2f}%  STATIC {st_avg:+5.2f}%  ORACLE {or_avg:+5.2f}%")
        verdict = (
            '✓ WF 显著优于 STATIC, 学到了' if (wf_avg - st_avg) >= 1.0 else
            '△ WF 略优于 STATIC' if (wf_avg - st_avg) >= 0.2 else
            '✗ WF 不如 STATIC, 阈值漂移过快学不到' if (wf_avg - st_avg) <= -0.5 else
            '— WF ≈ STATIC, 动态选择无增量'
        )
        oracle_lift = or_avg - st_avg
        print(f"  WF - STATIC = {wf_avg - st_avg:+.2f}% / ORACLE - STATIC = {oracle_lift:+.2f}% "
              f"({'capture ' + str(int(capture)) + '%' if oracle_lift != 0 else 'no oracle alpha'}) "
              f"→ {verdict}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=30)
    parser.add_argument('--board', choices=['kechuang', 'chuangye', 'all'],
                        default='kechuang')
    parser.add_argument('--patterns', nargs='+', default=['P2', 'P3'],
                        choices=['P1', 'P2', 'P3'])
    args = parser.parse_args()

    print('[*] 构建 RPS 面板 ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成')

    print(f'[*] 选 top-{args.top_n} ({args.board}) ...', end='', flush=True)
    symbols = pick_top_n_by_mv(ROOT, args.top_n, board=args.board)
    print(f' {len(symbols)} 只')

    print('[*] 聚合历史触发 ...', end='', flush=True)
    pool, _ = collect_pool_triggers(symbols, rps_panel)
    print(f' 完成 (P2={len(pool["P2"])}, P3={len(pool["P3"])})')

    print('\n' + '=' * 100)
    print(f'  RPS 带 walk-forward 选择 — 池={args.board} top-{args.top_n}')
    print(f'  训练: 用前一个半年评估 5 个 RPS 分位带, 选最优')
    print(f'  测试: 应用到下一个半年, 对比静态 50-80 和事后 ORACLE')
    print('=' * 100)

    for pcode in args.patterns:
        report(pcode, pool, args.board)
    print()


if __name__ == '__main__':
    main()
