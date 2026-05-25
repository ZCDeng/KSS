#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
688322 "价格创新高 + 大单流出" 的历史样本回测

问题: 322 5/22 当天 价格创历史新高 121.21, 但大单 -9227 万 (流出扩大),
      这种"机构对倒"信号 在历史上是不是顶部信号?

口径:
  - 大单流出: (buy_lg+buy_elg) - (sell_lg+sell_elg) < 0
  - 价格新高: close 是过去 N 日的新高 (N=20/60)
  - 量能放大: vol > vol_ma20

统计 与 322 当前条件类似的历史样本, 后 1/3/5/10 日表现
对照组: "价格新高 + 大单流入" 看做空白对照
"""

import os
import warnings
import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))

# 延迟初始化：只在 cache miss 真正要拉数据时才需 TUSHARE_TOKEN。
# 离线 / 缓存命中场景不应因缺 token 直接 KeyError 退出。
_pro = None


def _get_pro():
    global _pro
    if _pro is None:
        token = os.environ.get('TUSHARE_TOKEN')
        if not token:
            raise RuntimeError(
                "TUSHARE_TOKEN 未设置；本脚本仅在 cache miss 时需要它。"
                "如果不打算回填，先确认所有 mf_*.csv 缓存到位。"
            )
        ts.set_token(token)
        _pro = ts.pro_api()
    return _pro

import argparse
DEFAULT_SYMBOLS = [('688322.SH', '奥比中光'), ('688017.SH', '绿的谐波')]
NAMES_MAP = {
    '688322': '奥比中光', '688017': '绿的谐波', '688268': '华特气体',
    '688106': '金宏气体', '688353': '华盛锂电', '688585': '上纬新材',
    '688331': '荣昌生物', '688196': '卓越新能',
}


def load_mf(code: str) -> pd.DataFrame:
    cache = os.path.join(ROOT, f'mf_{code.replace(".", "_")}.csv')
    if os.path.exists(cache):
        df = pd.read_csv(cache, dtype={'trade_date': str})
    else:
        df = _get_pro().moneyflow(ts_code=code, start_date='20230101', end_date='20260522')
        df.to_csv(cache, index=False)
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df.sort_values('trade_date').reset_index(drop=True)
    # 大单合计 (大单+特大单)
    df['lg_total_net'] = (df['buy_lg_amount'] + df['buy_elg_amount']) - (df['sell_lg_amount'] + df['sell_elg_amount'])
    df['lg_total_buy'] = df['buy_lg_amount'] + df['buy_elg_amount']
    df['lg_total_sell'] = df['sell_lg_amount'] + df['sell_elg_amount']
    return df[['trade_date', 'lg_total_net', 'lg_total_buy', 'lg_total_sell',
               'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount']]


def load_stock(sym: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(ROOT, f'cs_data_{sym}.csv'))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['vol_ma20'] = df['vol'].rolling(20).mean()
    df['close_20d_high'] = df['close'] == df['close'].rolling(20).max()
    df['close_60d_high'] = df['close'] == df['close'].rolling(60).max()
    df['close_2y_high'] = df['close'] == df['close'].rolling(480).max()
    df['vol_above_ma20'] = df['vol'] > df['vol_ma20']
    df['day_ret'] = df['close'] / df['pre_close'] - 1
    # 前向
    for n in [1, 3, 5, 10]:
        df[f'fwd_{n}d'] = df['close'].shift(-n) / df['close'] - 1
    return df


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {'n': 0, 'mean': np.nan, 'med': np.nan, 'win': np.nan,
                'max': np.nan, 'min': np.nan, 'std': np.nan}
    return {
        'n': len(s),
        'mean': s.mean(),
        'med': s.median(),
        'win': (s > 0).mean(),
        'max': s.max(),
        'min': s.min(),
        'std': s.std(),
    }


def fmt(label, st):
    if st['n'] == 0:
        return f"  {label:<14}  无样本"
    return (f"  {label:<14} n={st['n']:<3d} 均值 {st['mean']*100:+6.2f}% "
            f"中位 {st['med']*100:+6.2f}% 胜率 {st['win']*100:5.1f}% "
            f"最大 {st['max']*100:+6.1f}% 最小 {st['min']*100:+6.1f}% σ {st['std']*100:5.2f}%")


def run_stock(sym_code: str, name: str):
    sym = sym_code.split('.')[0]
    print('\n' + '=' * 92)
    print(f"  {sym} {name} · 大单净流向 × 价格位置 条件回测")
    print('=' * 92)
    mf = load_mf(sym_code)
    px = load_stock(sym)
    df = px.merge(mf, on='trade_date', how='inner')

    # 当前状态 (取最后一天)
    last = df.iloc[-1]
    print(f"\n  ◆ 当前 ({last['trade_date'].date()}): close {last['close']:.2f}  日涨 {last['day_ret']*100:+.2f}%")
    print(f"     大单+特大 净流 {last['lg_total_net']:+.0f} 万 (买 {last['lg_total_buy']:.0f} / 卖 {last['lg_total_sell']:.0f})")
    flags = []
    if last['close_20d_high']:
        flags.append('20日新高')
    if last['close_60d_high']:
        flags.append('60日新高')
    if last['close_2y_high']:
        flags.append('2年新高')
    if last['vol_above_ma20']:
        flags.append('量放大于MA20')
    print(f"     标签: {' / '.join(flags) if flags else '无'}")

    # 历史排除当前
    hist = df.iloc[:-1].copy()

    print("\n  ── 形态 A · 价格 20 日新高 + 大单净流出 (与当前 322 类似)")
    A = hist[hist['close_20d_high'] & (hist['lg_total_net'] < 0)]
    for n in [1, 3, 5, 10]:
        print(fmt(f'后{n}日收益', stats(A[f'fwd_{n}d'])))
    print(f"     样本日期范围: {A['trade_date'].min().date() if len(A) else '-'} ~ {A['trade_date'].max().date() if len(A) else '-'}")

    print("\n  ── 形态 B · 价格 20 日新高 + 大单净流入 (对照组)")
    B = hist[hist['close_20d_high'] & (hist['lg_total_net'] > 0)]
    for n in [1, 3, 5, 10]:
        print(fmt(f'后{n}日收益', stats(B[f'fwd_{n}d'])))

    print("\n  ── 形态 C · 价格 60 日新高 + 大单净流出 (更强信号)")
    C = hist[hist['close_60d_high'] & (hist['lg_total_net'] < 0)]
    for n in [1, 3, 5, 10]:
        print(fmt(f'后{n}日收益', stats(C[f'fwd_{n}d'])))

    print("\n  ── 形态 D · 价格 60 日新高 + 大单净流入 (对照组)")
    D = hist[hist['close_60d_high'] & (hist['lg_total_net'] > 0)]
    for n in [1, 3, 5, 10]:
        print(fmt(f'后{n}日收益', stats(D[f'fwd_{n}d'])))

    # 极端组: 大单流出额最深的 Top 10% (相对 vol 缩放)
    print("\n  ── 形态 E · 大单流出额 Top 10% (无论价格)")
    q = hist['lg_total_net'].quantile(0.1)
    E = hist[hist['lg_total_net'] <= q]
    for n in [1, 3, 5, 10]:
        print(fmt(f'后{n}日收益', stats(E[f'fwd_{n}d'])))

    # 配对差: A vs B 显著性 (t-test 简易版)
    print("\n  ── A vs B 差异 (新高+流出 vs 新高+流入)")
    print(f"  {'周期':<8}{'A 均值':<12}{'B 均值':<12}{'差异':<12}{'A n':<6}{'B n':<6}")
    for n in [1, 3, 5, 10]:
        a = stats(A[f'fwd_{n}d']); b = stats(B[f'fwd_{n}d'])
        if a['n'] and b['n']:
            print(f"  后{n:<5d} {a['mean']*100:+6.2f}%    {b['mean']*100:+6.2f}%    "
                  f"{(a['mean']-b['mean'])*100:+6.2f}%   {a['n']:<6d}{b['n']:<6d}")

    # 最近 3 次 A 形态触发
    if len(A) > 0:
        print(f"\n  ── 最近 5 次 A 形态触发 (新高+大单流出):")
        for _, r in A.tail(5).iterrows():
            f5 = r.get('fwd_5d'); f10 = r.get('fwd_10d')
            f5s = f'{f5*100:+.1f}%' if pd.notna(f5) else 'NA'
            f10s = f'{f10*100:+.1f}%' if pd.notna(f10) else 'NA'
            print(f"     {r['trade_date'].date()}  close {r['close']:.2f}  大单净流 {r['lg_total_net']:+8.0f}万  后5日 {f5s}  后10日 {f10s}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbols', nargs='*',
                        help='股票代码列表 (无 .SH 后缀), 默认 688322 688017')
    args = parser.parse_args()
    if args.symbols:
        pairs = [(f'{s}.SH', NAMES_MAP.get(s, s)) for s in args.symbols]
    else:
        pairs = DEFAULT_SYMBOLS
    for code, name in pairs:
        run_stock(code, name)


if __name__ == '__main__':
    main()
