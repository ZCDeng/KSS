#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨截面Rank + 滚动训练(Walk-forward) + 波动率缩放

实现：
1. Walk-forward滚动训练（每5个交易日用过去120天数据重训）
2. 波动率缩放（动态仓位管理）
3. 移除无效的Regime方向判断
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import matplotlib.pyplot as plt
import os, warnings

warnings.filterwarnings('ignore')
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def load_data():
    symbols_df = pd.read_csv(os.path.join(OUTPUT_DIR, 'kcb50_top20_symbols.csv'))
    symbols = symbols_df['symbol'].tolist()
    all_data = []
    for symbol in symbols:
        cache_file = os.path.join(OUTPUT_DIR, f'cs_data_{symbol}.csv')
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df['symbol'] = symbol
            all_data.append(df)
    combined = pd.concat(all_data, ignore_index=True)
    return combined, symbols


def generate_factors(df):
    c = df['close']
    h = df['high']
    l = df['low']
    v = df['vol']
    o = df['open']
    amt = df.get('amount', v * c)
    factors = pd.DataFrame()
    factors['trade_date'] = df['trade_date']
    for p in [1, 5, 10, 20, 60]:
        factors[f'mom_{p}d'] = c.pct_change(p)
    factors['volatility_5d'] = c.pct_change().rolling(5).std()
    factors['volatility_20d'] = c.pct_change().rolling(20).std()
    factors['volatility_60d'] = c.pct_change().rolling(60).std()
    factors['atr_14'] = (h - l).rolling(14).mean() / c
    factors['vol_ma5_ratio'] = v / v.rolling(5).mean()
    factors['vol_ma20_ratio'] = v / v.rolling(20).mean()
    factors['amount_ma5_ratio'] = amt / amt.rolling(5).mean()
    factors['vr'] = df.get('volume_ratio', pd.Series(1.0, index=df.index))
    factors['turnover'] = df.get('turnover_rate', pd.Series(0.0, index=df.index))
    factors['close_to_high_20d'] = c / h.rolling(20).max()
    factors['close_to_low_20d'] = c / l.rolling(20).min()
    factors['close_to_high_60d'] = c / h.rolling(60).max()
    factors['close_to_low_60d'] = c / l.rolling(60).min()
    for p in [5, 10, 20, 60]:
        ma = c.rolling(p).mean()
        factors[f'ma_{p}_ratio'] = c / ma
        factors[f'ma_{p}_slope'] = ma.pct_change(5)
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    factors['macd'] = ema12 - ema26
    factors['macd_signal'] = factors['macd'].ewm(span=9).mean()
    factors['macd_hist'] = factors['macd'] - factors['macd_signal']
    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    factors['rsi_14'] = 100 - (100 / (1 + rs))
    lowest_9 = l.rolling(9).min()
    highest_9 = h.rolling(9).max()
    rsv = (c - lowest_9) / (highest_9 - lowest_9 + 1e-8) * 100
    factors['kdj_k'] = rsv.ewm(com=2).mean()
    factors['kdj_d'] = factors['kdj_k'].ewm(com=2).mean()
    factors['kdj_j'] = 3 * factors['kdj_k'] - 2 * factors['kdj_d']
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    factors['boll_upper'] = (ma20 + 2*std20) / c
    factors['boll_lower'] = (ma20 - 2*std20) / c
    factors['boll_width'] = 4 * std20 / c
    factors['amplitude'] = (h - l) / c
    factors['body_ratio'] = (c - o) / c
    factors['upper_shadow'] = (h - c.clip(lower=o)) / c
    factors['lower_shadow'] = (c.clip(upper=o) - l) / c
    factors['price_vol_corr_20d'] = c.pct_change().rolling(20).corr(v.pct_change())
    if 'pe' in df.columns:
        factors['pe'] = df['pe']
        factors['pb'] = df['pb']
    if 'total_mv' in df.columns:
        factors['log_mv'] = np.log(df['total_mv'] + 1)
    for p in [5, 10, 20]:
        factors[f'future_return_{p}d'] = c.shift(-p) / c - 1
    return factors


def cs_normalize(df, factor_cols):
    df = df.copy()
    for col in factor_cols:
        mean = df.groupby('trade_date')[col].transform('mean')
        std = df.groupby('trade_date')[col].transform('std')
        df[col] = (df[col] - mean) / (std + 1e-8)
    return df


def train_model(train_df, feature_cols, label_col):
    X = train_df[feature_cols].values
    y = train_df[label_col].values
    params = {
        'objective': 'regression', 'metric': 'rmse', 'boosting_type': 'gbdt',
        'num_leaves': 31, 'learning_rate': 0.05, 'feature_fraction': 0.8,
        'bagging_fraction': 0.8, 'bagging_freq': 5, 'verbose': -1, 'random_state': 42,
    }
    # 从训练集分出20%作为验证集用于早停
    n = len(X)
    split = int(n * 0.8)
    train_data = lgb.Dataset(X[:split], y[:split])
    valid_data = lgb.Dataset(X[split:], y[split:])
    model = lgb.train(params, train_data, num_boost_round=500,
                      valid_sets=[valid_data], valid_names=['valid'],
                      callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)])
    return model


def walk_forward(df, feature_cols, label_col, train_window=120, retrain_freq=5):
    dates = sorted(df['trade_date'].unique())
    results = []
    print(f"Walk-forward: 总交易日{len(dates)}, 训练窗口{train_window}, 重训频率{retrain_freq}")
    for i in range(train_window, len(dates), retrain_freq):
        train_dates = dates[i-train_window:i]
        test_dates = dates[i:min(i+retrain_freq, len(dates))]
        train_df = df[df['trade_date'].isin(train_dates)].dropna()
        test_df = df[df['trade_date'].isin(test_dates)].dropna()
        if len(train_df) < 200 or len(test_df) < 20:
            continue
        model = train_model(train_df, feature_cols, label_col)
        test_df = test_df.copy()
        test_df['pred_score'] = model.predict(test_df[feature_cols].values)
        test_df['daily_rank'] = test_df.groupby('trade_date')['pred_score'].rank(pct=True)
        daily = test_df.groupby('trade_date').apply(lambda g: pd.Series({
            'long_return': g.loc[g['daily_rank'] >= 0.8, label_col].mean(),
            'short_return': g.loc[g['daily_rank'] <= 0.2, label_col].mean(),
        })).reset_index()
        daily['ls_return'] = daily['long_return'] - daily['short_return']
        results.append(daily)
        if len(results) % 10 == 0:
            print(f"  完成 {len(results)} 轮, 当前: {test_dates[0]}")
    return pd.concat(results, ignore_index=True) if results else None


def vol_scale(df, target=0.15, lookback=20):
    df = df.copy()
    df['roll_vol'] = df['ls_return'].rolling(lookback).std()
    df['scale'] = np.clip(target / (df['roll_vol'] + 0.05), 0.3, 1.5)
    df['scaled_return'] = df['ls_return'] * df['scale'].fillna(1.0)
    return df


def metrics(r):
    r = r.dropna()
    if len(r) == 0: return {}
    cum = (1 + r).cumprod()
    total = cum.iloc[-1] - 1
    annual = (1 + total) ** (252 / len(r)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = annual / vol if vol > 0 else 0
    rm = cum.cummax()
    dd = (cum - rm) / rm
    max_dd = dd.min()
    calmar = annual / abs(max_dd) if max_dd != 0 else 0
    return {'total': total, 'annual': annual, 'sharpe': sharpe, 
            'max_dd': max_dd, 'calmar': calmar, 'win': (r > 0).mean(), 'n': len(r)}


def plot_results(df, period, path):
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    dates = df['trade_date']
    cum_ls = (1 + df['ls_return']).cumprod() - 1
    cum_sc = (1 + df['scaled_return']).cumprod() - 1
    axes[0,0].plot(dates, cum_ls, label='纯多空', lw=2)
    axes[0,0].plot(dates, cum_sc, label='波动率缩放', lw=2)
    axes[0,0].set_title(f'累计收益 ({period}日)'); axes[0,0].legend(); axes[0,0].grid(True, alpha=0.3)
    axes[0,1].plot(dates, df['roll_vol'], label='滚动波动率', color='orange')
    axes[0,1].axhline(y=0.15, color='r', ls='--', label='目标')
    axes[0,1].set_title('波动率缩放'); axes[0,1].legend(); axes[0,1].grid(True, alpha=0.3)
    axes[1,0].hist(df['ls_return'].dropna(), bins=30, alpha=0.6, label='纯多空')
    axes[1,0].hist(df['scaled_return'].dropna(), bins=30, alpha=0.6, label='波动率缩放')
    axes[1,0].set_title('日收益分布'); axes[1,0].legend()
    df['ym'] = df['trade_date'].dt.to_period('M')
    monthly = df.groupby('ym').agg({'ls_return': lambda x: (1+x).prod()-1, 'scaled_return': lambda x: (1+x).prod()-1})
    x = np.arange(len(monthly))
    axes[1,1].bar(x-0.175, monthly['ls_return'], 0.35, label='纯多空')
    axes[1,1].bar(x+0.175, monthly['scaled_return'], 0.35, label='波动率缩放')
    axes[1,1].set_xticks(x); axes[1,1].set_xticklabels([str(m) for m in monthly.index], rotation=45, fontsize=8)
    axes[1,1].set_title('月度收益'); axes[1,1].legend(); axes[1,1].axhline(0, color='k', lw=0.5)
    plt.tight_layout(); plt.savefig(path, dpi=150, bbox_inches='tight'); plt.close()
    print(f"图: {path}")


def main():
    print("=" * 70)
    print("跨截面Rank + 滚动训练 + 波动率缩放")
    print("=" * 70)
    combined, symbols = load_data()
    print(f"数据: {len(combined)} 行, {len(symbols)} 只")
    flist = []
    for sym in combined['symbol'].unique():
        sub = combined[combined['symbol'] == sym].sort_values('trade_date').reset_index(drop=True)
        f = generate_factors(sub)
        f['symbol'] = sym
        flist.append(f)
    factor_df = pd.concat(flist, ignore_index=True)
    feature_cols = [c for c in factor_df.columns if c not in ['trade_date', 'symbol', 'future_return_5d', 'future_return_10d', 'future_return_20d']]
    factor_df = cs_normalize(factor_df, feature_cols)
    print(f"因子: {len(feature_cols)}, 样本: {len(factor_df)}")
    for period in [5, 10, 20]:
        label = f'future_return_{period}d'
        print(f"\n{'='*50}\n{period}日 - Walk-forward\n{'='*50}")
        res = walk_forward(factor_df, feature_cols, label, train_window=120, retrain_freq=5)
        if res is None or len(res) == 0:
            print("空结果"); continue
        res = vol_scale(res, target=0.15, lookback=20)
        m_ls = metrics(res['ls_return'])
        m_sc = metrics(res['scaled_return'])
        for name, m in [('纯多空', m_ls), ('波动率缩放', m_sc)]:
            print(f"\n[{name}] 总收益:{m['total']*100:.1f}% 年化:{m['annual']*100:.1f}% 夏普:{m['sharpe']:.2f} 回撤:{m['max_dd']*100:.1f}% Calmar:{m['calmar']:.2f} 胜率:{m['win']*100:.1f}% 天数:{m['n']}")
        plot_results(res, period, os.path.join(OUTPUT_DIR, f'cs_walkforward_{period}d.png'))
    print("\n" + "=" * 70 + "\n完成!\n" + "=" * 70)

if __name__ == '__main__':
    main()
