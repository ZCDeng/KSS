#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨截面Rank + Regime Detection V3
基于V1可靠代码，仅改进Regime用法：从"方向过滤"改为"仓位/杠杆调节"

策略：
- 纯多空对冲：1.0L - 1.0S（市场中性，基准）
- Regime离散调节：bull(1.3L-0.7S), neutral(1.0L-1.0S), bear(0.7L-1.3S)
- Regime平滑调节：用regime_score连续映射多空权重
- 波动率缩放：高波动时降低整体仓位
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


def load_cached_data():
    """加载V1缓存的数据和因子"""
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

    # 读取指数并检测Regime
    index_df = pd.read_csv(os.path.join(OUTPUT_DIR, 'cs_index_000688_SH.csv'))
    index_df['trade_date'] = pd.to_datetime(index_df['trade_date'])
    regime_df = detect_regime(index_df)

    return combined, regime_df, symbols


def generate_factors(df):
    """生成因子（与V1保持一致）"""
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


def cross_sectional_normalize(df, factor_cols):
    df = df.copy()
    for col in factor_cols:
        daily_mean = df.groupby('trade_date')[col].transform('mean')
        daily_std = df.groupby('trade_date')[col].transform('std')
        df[col] = (df[col] - daily_mean) / (daily_std + 1e-8)
    return df


def detect_regime(index_df):
    c = index_df['close']
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    trend_20 = (c - ma20) / ma20
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    macd_hist = macd - macd_signal

    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))

    vol_20 = c.pct_change().rolling(20).std()
    vol_60 = c.pct_change().rolling(60).std()
    vol_regime = vol_20 / (vol_60 + 1e-8)

    score = pd.Series(0.0, index=index_df.index)
    score += np.where(trend_20 > 0.05, 3, 0)
    score += np.where((trend_20 > 0) & (trend_20 <= 0.05), 1.5, 0)
    score += np.where(trend_20 < -0.05, -3, 0)
    score += np.where((trend_20 < 0) & (trend_20 >= -0.05), -1.5, 0)
    score += np.where((ma10 > ma20) & (ma20 > ma60), 2, np.where((ma10 < ma20) & (ma20 < ma60), -2, 0))
    score += np.where(macd > macd_signal, 1.5, -1.5)
    score += np.where(macd_hist > macd_hist.shift(1), 0.5, -0.5)
    score += np.where(rsi > 80, -2, np.where((rsi > 60) & (rsi <= 80), -0.5, 0))
    score += np.where(rsi < 20, 2, np.where((rsi < 40) & (rsi >= 20), 0.5, 0))
    score += np.where(vol_regime > 1.5, -1, 0)

    regime = pd.DataFrame()
    regime['trade_date'] = index_df['trade_date']
    regime['regime_score'] = score
    regime['regime'] = np.select([score > 3, score < -3], ['bull', 'bear'], default='neutral')
    return regime


def train_model(train_df, feature_cols, label_col, val_df=None):
    X_train = train_df[feature_cols].values
    y_train = train_df[label_col].values
    valid_sets = [lgb.Dataset(X_train, y_train)]
    valid_names = ['train']
    if val_df is not None and len(val_df) > 0:
        valid_sets.append(lgb.Dataset(val_df[feature_cols].values, val_df[label_col].values))
        valid_names.append('valid')

    params = {
        'objective': 'regression',
        'metric': 'rmse',
        'boosting_type': 'gbdt',
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'verbose': -1,
        'random_state': 42,
    }

    model = lgb.train(
        params, lgb.Dataset(X_train, y_train),
        num_boost_round=500,
        valid_sets=valid_sets, valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
    )
    return model


def backtest_v3(df, model, feature_cols, label_col, period=5):
    """V3回测：纯多空 + Regime仓位调节"""
    df = df.sort_values(['trade_date', 'symbol']).copy()
    df['pred_score'] = model.predict(df[feature_cols])
    df['daily_rank'] = df.groupby('trade_date')['pred_score'].rank(pct=True)

    daily = df.groupby('trade_date').apply(lambda g: pd.Series({
        'long_return': g.loc[g['daily_rank'] >= 0.8, label_col].mean(),
        'short_return': g.loc[g['daily_rank'] <= 0.2, label_col].mean(),
        'regime': g['regime'].iloc[0],
        'regime_score': g['regime_score'].iloc[0],
    })).reset_index()

    # 1. 纯多空对冲
    daily['pure_ls'] = daily['long_return'] - daily['short_return']

    # 2. Regime离散调节
    def get_weights(r):
        if r == 'bull': return 1.3, 0.7
        elif r == 'bear': return 0.7, 1.3
        else: return 1.0, 1.0
    w = daily['regime'].apply(get_weights)
    daily['w_long'] = [x[0] for x in w]
    daily['w_short'] = [x[1] for x in w]
    daily['regime_disc'] = daily['w_long'] * daily['long_return'] - daily['w_short'] * daily['short_return']

    # 3. Regime平滑调节
    score_norm = np.clip(daily['regime_score'] / 6, -1, 1)
    daily['sw_l'] = 1.0 + 0.3 * score_norm
    daily['sw_s'] = 1.0 - 0.3 * score_norm
    daily['regime_smooth'] = daily['sw_l'] * daily['long_return'] - daily['sw_s'] * daily['short_return']

    # 4. 波动率缩放
    vol = daily['pure_ls'].rolling(20).std()
    scale = np.clip(0.15 / (vol + 0.05), 0.3, 1.5)
    daily['vol_scale'] = daily['pure_ls'] * scale.fillna(1.0)

    # 计算指标
    results = {}
    for name, col in [('纯多空对冲', 'pure_ls'), ('Regime离散调节', 'regime_disc'),
                      ('Regime平滑调节', 'regime_smooth'), ('波动率缩放', 'vol_scale')]:
        r = daily[col].dropna()
        if len(r) == 0: continue
        cum = (1 + r).cumprod()
        total = cum.iloc[-1] - 1
        annual = (1 + total) ** (252 / len(r)) - 1 if len(r) > 0 else 0
        vola = r.std() * np.sqrt(252)
        sharpe = annual / vola if vola > 0 else 0
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max
        max_dd = dd.min()
        calmar = annual / abs(max_dd) if max_dd != 0 else 0
        win = (r > 0).mean()
        results[name] = {
            'total_return': total, 'annual_return': annual,
            'sharpe': sharpe, 'max_drawdown': max_dd,
            'calmar': calmar, 'win_rate': win,
            'daily': r.values, 'cum': cum.values - 1,
        }
    return results, daily


def plot_v3(results, daily, period, path):
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    ax = axes[0, 0]
    dates = daily['trade_date']
    for name, m in results.items():
        ax.plot(dates[:len(m['cum'])], m['cum'], label=name, linewidth=2)
    ax.set_title(f'累计收益 ({period}日预测)')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[0, 1]
    names = list(results.keys())
    x = np.arange(len(names))
    for i, metric in enumerate(['sharpe', 'calmar', 'win_rate']):
        vals = [results[n][metric] for n in names]
        ax.bar(x + i*0.2, vals, 0.2, label=metric)
    ax.set_xticks(x + 0.2)
    ax.set_xticklabels(names, rotation=15, fontsize=9)
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax = axes[1, 0]
    for name, m in results.items():
        ax.hist(m['daily'], bins=25, alpha=0.5, label=name)
    ax.set_title('日收益分布')
    ax.legend(fontsize=9)

    ax = axes[1, 1]
    for regime in ['bull', 'neutral', 'bear']:
        sub = daily[daily['regime'] == regime]
        if len(sub) > 0:
            ax.bar(regime, sub['pure_ls'].mean(), alpha=0.6, label='纯多空')
            ax.bar(regime, sub['regime_disc'].mean(), alpha=0.6, label='离散调节')
    ax.set_title('按Regime的日均收益')
    ax.axhline(0, color='black', linewidth=0.5)

    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"图已保存: {path}")


def main():
    print("=" * 70)
    print("跨截面Rank + Regime Detection V3（基于V1可靠代码）")
    print("=" * 70)

    combined, regime_df, symbols = load_cached_data()
    print(f"数据: {len(combined)} 行, {len(symbols)} 只股票")

    # 生成因子
    print("生成因子...")
    flist = []
    for sym in combined['symbol'].unique():
        sub = combined[combined['symbol'] == sym].sort_values('trade_date').reset_index(drop=True)
        f = generate_factors(sub)
        f['symbol'] = sym
        flist.append(f)
    factor_df = pd.concat(flist, ignore_index=True)

    # 截面标准化
    feature_cols = [c for c in factor_df.columns
                    if c not in ['trade_date', 'symbol',
                                 'future_return_5d', 'future_return_10d', 'future_return_20d']]
    factor_df = cross_sectional_normalize(factor_df, feature_cols)

    # 合并regime
    factor_df = factor_df.merge(regime_df[['trade_date', 'regime', 'regime_score']],
                                on='trade_date', how='left')

    # 划分训练/测试（与V1一致：80%训练）
    dates = sorted(factor_df['trade_date'].unique())
    train_end = dates[int(len(dates) * 0.8)]
    train_df = factor_df[factor_df['trade_date'] <= train_end].dropna()
    test_df = factor_df[factor_df['trade_date'] > train_end].dropna()
    print(f"训练集: {len(train_df)} 条")
    print(f"测试集: {len(test_df)} 条")

    # 训练 + 回测
    all_results = {}
    for period in [5, 10, 20]:
        label_col = f'future_return_{period}d'
        print(f"\n{'='*50}")
        print(f"{period}日预测")
        print(f"{'='*50}")

        model = train_model(train_df, feature_cols, label_col, test_df)
        results, daily = backtest_v3(test_df, model, feature_cols, label_col, period)
        all_results[f'{period}d'] = (results, daily)

        for name, m in results.items():
            print(f"\n[{name}]")
            print(f"  总收益: {m['total_return']*100:.2f}%")
            print(f"  年化: {m['annual_return']*100:.2f}%")
            print(f"  夏普: {m['sharpe']:.2f}")
            print(f"  最大回撤: {m['max_drawdown']*100:.2f}%")
            print(f"  Calmar: {m['calmar']:.2f}")
            print(f"  胜率: {m['win_rate']*100:.1f}%")

        plot_v3(results, daily, period,
                os.path.join(OUTPUT_DIR, f'cs_v3_{period}d.png'))

    # 汇总图
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    for idx, period in enumerate([5, 10, 20]):
        results, daily = all_results[f'{period}d']

        ax = axes[0, idx]
        dates = daily['trade_date']
        for name, m in results.items():
            ax.plot(dates[:len(m['cum'])], m['cum'], label=name, linewidth=2)
        ax.set_title(f'{period}日 - 累计收益')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        ax = axes[1, idx]
        names = list(results.keys())
        x = np.arange(len(names))
        sharpes = [results[n]['sharpe'] for n in names]
        colors = ['#2E86AB', '#A23B72', '#F18F01', '#6A994E']
        bars = ax.bar(x, sharpes, color=colors[:len(names)])
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=15, fontsize=8)
        ax.set_title(f'{period}日 - 夏普')
        ax.grid(True, alpha=0.3)
        for bar, val in zip(bars, sharpes):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                    f'{val:.2f}', ha='center', va='bottom', fontsize=8)

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'cs_v3_summary.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n汇总图: cs_v3_summary.png")
    print("=" * 70)


if __name__ == '__main__':
    main()
