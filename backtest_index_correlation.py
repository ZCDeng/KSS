#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
688322 & 688017 与主流指数的相关性回测

指数池:
  000001.SH 上证指数      000300.SH 沪深300
  399001.SZ 深证成指      000905.SH 中证500
  399006.SZ 创业板指      000852.SH 中证1000
  000688.SH 科创50        000698.SH 科创100
  931494.CSI 机器人指数   930719.CSI 中证机器
  931007.CSI 智能机械

输出:
  - 全样本 Pearson 相关 (按日 pct_chg)
  - 滚动 60 日相关 (展示阶段性)
  - Beta (回归个股 = α + β * 指数)
  - 同涨/同跌一致性
  - 近 60 日子样本 vs 全样本对比
"""

import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))


def get_pro():
    import tushare as ts
    token = os.environ.get('TUSHARE_TOKEN', '')
    ts.set_token(token)
    return ts.pro_api()


INDEXES = [
    ('000001.SH', '上证指数'),
    ('000300.SH', '沪深300'),
    ('000905.SH', '中证500'),
    ('000852.SH', '中证1000'),
    ('399001.SZ', '深证成指'),
    ('399006.SZ', '创业板指'),
    ('000688.SH', '科创50'),
    ('000698.SH', '科创100'),
    ('931494.CSI', '机器人指数'),
    ('930719.CSI', '中证机器'),
    ('931007.CSI', '智能机械'),
]

STOCKS = [('688322.SH', '奥比中光'), ('688017.SH', '绿的谐波')]

START = '20230101'
END = '20260522'


def load_stock(sym_code: str) -> pd.DataFrame:
    sym = sym_code.split('.')[0]
    fp = os.path.join(ROOT, f'cs_data_{sym}.csv')
    df = pd.read_csv(fp)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[['trade_date', 'pct_chg']].rename(columns={'pct_chg': sym_code})
    df[sym_code] = df[sym_code] / 100.0
    return df


def load_index(pro, code: str) -> pd.DataFrame | None:
    cache = os.path.join(ROOT, f'idx_{code.replace(".", "_")}.csv')
    if os.path.exists(cache):
        df = pd.read_csv(cache, dtype={'trade_date': str})
    else:
        try:
            df = pro.index_daily(ts_code=code, start_date=START, end_date=END)
        except Exception as e:
            print(f"  下载 {code} 失败: {e}")
            return None
        if df is None or len(df) == 0:
            return None
        df.to_csv(cache, index=False)
        df['trade_date'] = df['trade_date'].astype(str)
    # Tushare 约定 YYYYMMDD 字符串；read_csv 推断为 int 后 pd.to_datetime
    # 会按纳秒解析（1970 起点），导致 index 与股票日期不对齐 → 相关性表全 NaN.
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df[['trade_date', 'pct_chg']].rename(columns={'pct_chg': code})
    df[code] = df[code] / 100.0
    return df


def beta(stock: pd.Series, index: pd.Series) -> tuple[float, float, float]:
    """OLS: stock = alpha + beta * index, 返回 (alpha 年化, beta, R^2)"""
    df = pd.concat([stock, index], axis=1).dropna()
    if len(df) < 30:
        return np.nan, np.nan, np.nan
    x = df.iloc[:, 1].values
    y = df.iloc[:, 0].values
    n = len(x)
    mx, my = x.mean(), y.mean()
    cov = ((x - mx) * (y - my)).sum() / (n - 1)
    var = ((x - mx) ** 2).sum() / (n - 1)
    b = cov / var if var > 0 else np.nan
    a = my - b * mx
    yhat = a + b * x
    ss_res = ((y - yhat) ** 2).sum()
    ss_tot = ((y - my) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return a * 252, b, r2


def co_move(stock: pd.Series, index: pd.Series) -> tuple[float, float, float]:
    """同涨/同跌/同向 一致性"""
    df = pd.concat([stock, index], axis=1).dropna()
    if len(df) == 0:
        return np.nan, np.nan, np.nan
    s = df.iloc[:, 0]
    i = df.iloc[:, 1]
    same_up = ((s > 0) & (i > 0)).sum() / max((i > 0).sum(), 1)
    same_dn = ((s < 0) & (i < 0)).sum() / max((i < 0).sum(), 1)
    same_dir = ((s * i) > 0).mean()
    return same_up, same_dn, same_dir


def main():
    pro = get_pro()
    print("加载指数数据...")
    idx_dfs = []
    idx_meta = []
    for code, name in INDEXES:
        df = load_index(pro, code)
        if df is not None and len(df) > 100:
            idx_dfs.append(df)
            idx_meta.append((code, name))
            print(f"  ✓ {code} {name}: {len(df)} 行")
        else:
            print(f"  ✗ {code} {name}: 跳过")

    # merge all
    base = None
    for df in idx_dfs:
        base = df if base is None else base.merge(df, on='trade_date', how='outer')

    for stk_code, _ in STOCKS:
        sdf = load_stock(stk_code)
        base = base.merge(sdf, on='trade_date', how='outer')

    base = base.sort_values('trade_date').reset_index(drop=True)
    print(f"\n合并后 {len(base)} 行, {base['trade_date'].min().date()} ~ {base['trade_date'].max().date()}")

    # 切两段
    full = base.dropna(subset=[s for s, _ in STOCKS], how='any')
    cut60 = full.tail(60)
    cut20 = full.tail(20)

    print("\n" + "=" * 88)
    print("  全样本相关矩阵 (Pearson, daily pct_chg)")
    print("=" * 88)
    print(f"  {'指数':<14}{'代码':<14}{'与688322':<15}{'与688017':<15}{'近60日 322':<15}{'近60日 017':<15}")
    print('  ' + '-' * 84)

    for code, name in idx_meta:
        c322 = full[code].corr(full['688322.SH'])
        c017 = full[code].corr(full['688017.SH'])
        c322_60 = cut60[code].corr(cut60['688322.SH'])
        c017_60 = cut60[code].corr(cut60['688017.SH'])
        print(f"  {name:<14}{code:<14}{c322:+.3f}          {c017:+.3f}          "
              f"{c322_60:+.3f}          {c017_60:+.3f}")

    print("\n" + "=" * 88)
    print("  Beta / R² (个股 = α + β * 指数, 全样本)")
    print("=" * 88)
    print(f"  {'指数':<14}{'β 322':<10}{'R² 322':<10}{'β 017':<10}{'R² 017':<10}")
    print('  ' + '-' * 56)
    for code, name in idx_meta:
        _, b322, r322 = beta(full['688322.SH'], full[code])
        _, b017, r017 = beta(full['688017.SH'], full[code])
        print(f"  {name:<14}{b322:+.2f}     {r322:.3f}     {b017:+.2f}     {r017:.3f}")

    print("\n" + "=" * 88)
    print("  同向一致性 (全样本; 个股 vs 指数 当日同涨/同跌比例)")
    print("=" * 88)
    print(f"  {'指数':<14}{'322同涨':<10}{'322同跌':<10}{'322同向':<10}{'017同涨':<10}{'017同跌':<10}{'017同向':<10}")
    print('  ' + '-' * 76)
    for code, name in idx_meta:
        su322, sd322, sa322 = co_move(full['688322.SH'], full[code])
        su017, sd017, sa017 = co_move(full['688017.SH'], full[code])
        print(f"  {name:<14}{su322*100:5.1f}%    {sd322*100:5.1f}%    {sa322*100:5.1f}%    "
              f"{su017*100:5.1f}%    {sd017*100:5.1f}%    {sa017*100:5.1f}%")

    # 综合打分: corr * 0.4 + R² * 0.4 + 同向 * 0.2
    print("\n" + "=" * 88)
    print("  综合打分 (corr×0.4 + R²×0.4 + 同向×0.2; 越高越像)")
    print("=" * 88)
    print(f"  {'指数':<14}{'322 分':<10}{'017 分':<10}")
    print('  ' + '-' * 36)
    scores322 = []
    scores017 = []
    for code, name in idx_meta:
        c322 = full[code].corr(full['688322.SH'])
        c017 = full[code].corr(full['688017.SH'])
        _, _, r322 = beta(full['688322.SH'], full[code])
        _, _, r017 = beta(full['688017.SH'], full[code])
        _, _, sa322 = co_move(full['688322.SH'], full[code])
        _, _, sa017 = co_move(full['688017.SH'], full[code])
        s322 = (c322 if pd.notna(c322) else 0) * 0.4 + (r322 if pd.notna(r322) else 0) * 0.4 + (sa322 if pd.notna(sa322) else 0) * 0.2
        s017 = (c017 if pd.notna(c017) else 0) * 0.4 + (r017 if pd.notna(r017) else 0) * 0.4 + (sa017 if pd.notna(sa017) else 0) * 0.2
        scores322.append((name, code, s322))
        scores017.append((name, code, s017))
        print(f"  {name:<14}{s322:.3f}     {s017:.3f}")

    print("\n  【688322 关系最密切的 Top3】")
    for name, code, s in sorted(scores322, key=lambda x: -x[2])[:3]:
        print(f"    {name} ({code}): {s:.3f}")
    print("\n  【688017 关系最密切的 Top3】")
    for name, code, s in sorted(scores017, key=lambda x: -x[2])[:3]:
        print(f"    {name} ({code}): {s:.3f}")


if __name__ == '__main__':
    main()
