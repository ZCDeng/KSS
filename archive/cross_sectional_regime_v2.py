#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨截面Rank + Regime Detection V2
改进：Regime从"方向过滤"改为"仓位/杠杆调节"

策略逻辑：
- 纯多空对冲提供截面Alpha（long top20% - short bottom20%）
- Regime提供方向Beta调节：
  * bull: 130% long / 70% short → 净多头敞口 +60%
  * neutral: 100% long / 100% short → 净敞口 0（市场中性）
  * bear: 70% long / 130% short → 净空头敞口 -60%
- 额外引入"波动率缩放"：高波动时降低整体仓位
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


def cs_backtest_v2(df, model, feature_cols, label_col, period=5):
    """
    改进版跨截面回测
    - 纯多空对冲（市场中性）
    - Regime调节版（动态多空比例）
    """
    df = df.sort_values(['trade_date', 'symbol']).copy()
    df['pred_score'] = model.predict(df[feature_cols])
    df['daily_rank'] = df.groupby('trade_date')['pred_score'].rank(pct=True)

    # 每日分组收益率
    daily = df.groupby('trade_date').apply(lambda g: pd.Series({
        'long_return': g.loc[g['daily_rank'] >= 0.8, label_col].mean(),
        'short_return': g.loc[g['daily_rank'] <= 0.2, label_col].mean(),
        'mid_return': g.loc[(g['daily_rank'] > 0.2) & (g['daily_rank'] < 0.8), label_col].mean(),
        'regime': g['regime'].iloc[0],
        'regime_score': g['regime_score'].iloc[0],
        'n_long': (g['daily_rank'] >= 0.8).sum(),
        'n_short': (g['daily_rank'] <= 0.2).sum(),
    })).reset_index()

    # 1. 纯多空对冲（市场中性）
    daily['pure_ls_return'] = daily['long_return'] - daily['short_return']

    # 2. Regime调节版（动态多空比例）
    # bull: 1.3L - 0.7S = 0.6(net long) + 0.6(long-short)
    # neutral: 1.0L - 1.0S = 0 + 1.0(long-short)
    # bear: 0.7L - 1.3S = -0.6(net short) + 0.6(long-short)
    def regime_weights(r):
        if r == 'bull':
            return 1.3, 0.7
        elif r == 'bear':
            return 0.7, 1.3
        else:
            return 1.0, 1.0

    w_long, w_short = zip(*daily['regime'].apply(regime_weights))
    daily['w_long'] = w_long
    daily['w_short'] = w_short
    daily['regime_ls_return'] = daily['w_long'] * daily['long_return'] - daily['w_short'] * daily['short_return']

    # 3. 平滑Regime版（用regime_score做连续权重）
    # sigmoid映射到[-0.5, 0.5]，再加1作为long权重，减1作为short权重
    score_norm = np.clip(daily['regime_score'] / 6, -1, 1)  # 归一化到[-1,1]
    daily['smooth_w_long'] = 1.0 + 0.3 * score_norm
    daily['smooth_w_short'] = 1.0 - 0.3 * score_norm
    daily['smooth_regime_return'] = (daily['smooth_w_long'] * daily['long_return']
                                      - daily['smooth_w_short'] * daily['short_return'])

    # 4. 波动率缩放（高波动时降低整体仓位）
    vol = daily['pure_ls_return'].rolling(20).std()
    vol_scale = np.clip(0.15 / (vol + 0.05), 0.3, 1.5)
    daily['vol_scaled_return'] = daily['pure_ls_return'] * vol_scale.fillna(1.0)

    # 计算累计收益和指标
    strategies = {
        '纯多空对冲': 'pure_ls_return',
        'Regime离散调节': 'regime_ls_return',
        'Regime平滑调节': 'smooth_regime_return',
        '波动率缩放': 'vol_scaled_return',
    }

    results = {}
    for name, col in strategies.items():
        r = daily[col].dropna()
        if len(r) == 0:
            continue
        cum = (1 + r).cumprod()
        total_ret = cum.iloc[-1] - 1
        annual_ret = (1 + total_ret) ** (252 / len(r)) - 1
        volatility = r.std() * np.sqrt(252)
        sharpe = annual_ret / volatility if volatility > 0 else 0

        running_max = cum.cummax()
        drawdown = (cum - running_max) / running_max
        max_dd = drawdown.min()
        calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0
        win_rate = (r > 0).mean()

        results[name] = {
            'total_return': total_ret,
            'annual_return': annual_ret,
            'volatility': volatility,
            'sharpe': sharpe,
            'max_drawdown': max_dd,
            'calmar': calmar,
            'win_rate': win_rate,
            'avg_daily': r.mean(),
            'cum_series': cum.values,
            'daily_series': r.values,
        }

    return results, daily


def plot_v2(results_dict, daily_df, period, output_path):
    """绘制V2对比图"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # 1. 累计收益曲线
    ax = axes[0, 0]
    dates = daily_df['trade_date']
    for name, metrics in results_dict.items():
        ax.plot(dates[:len(metrics['cum_series'])], metrics['cum_series'] - 1,
                label=name, linewidth=2)
    ax.set_title(f'累计收益曲线 ({period}日预测)')
    ax.set_xlabel('日期')
    ax.set_ylabel('累计收益')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 2. 夏普/Calmar对比
    ax = axes[0, 1]
    names = list(results_dict.keys())
    x = np.arange(len(names))
    width = 0.25
    metrics_list = ['sharpe', 'calmar', 'win_rate']
    colors = ['#2E86AB', '#A23B72', '#F18F01']
    for i, m in enumerate(metrics_list):
        vals = [results_dict[n].get(m, 0) for n in names]
        ax.bar(x + i*width, vals, width, label=m, color=colors[i])
    ax.set_title('关键指标对比')
    ax.set_xticks(x + width)
    ax.set_xticklabels(names, rotation=15)
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 3. 日收益分布
    ax = axes[1, 0]
    for name, metrics in results_dict.items():
        ax.hist(metrics['daily_series'], bins=25, alpha=0.5, label=name)
    ax.set_title('日收益分布')
    ax.set_xlabel('日收益')
    ax.set_ylabel('频次')
    ax.legend()

    # 4. 按Regime的策略表现
    ax = axes[1, 1]
    regime_perf = {}
    for regime in ['bull', 'neutral', 'bear']:
        sub = daily_df[daily_df['regime'] == regime]
        if len(sub) > 0:
            regime_perf[regime] = {
                '纯多空': sub['pure_ls_return'].mean(),
                '离散调节': sub['regime_ls_return'].mean(),
                '平滑调节': sub['smooth_regime_return'].mean(),
            }

    if regime_perf:
        regimes = list(regime_perf.keys())
        strategies = list(regime_perf[regimes[0]].keys())
        x = np.arange(len(regimes))
        width = 0.25
        for i, s in enumerate(strategies):
            vals = [regime_perf[r][s] for r in regimes]
            ax.bar(x + i*width, vals, width, label=s)
        ax.set_title('按Regime的日均收益')
        ax.set_xticks(x + width)
        ax.set_xticklabels(regimes)
        ax.legend()
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"V2结果图已保存: {output_path}")


def main():
    print("=" * 70)
    print("跨截面Rank + Regime Detection V2（仓位调节版）")
    print("=" * 70)

    # 读取已有数据
    print("\n加载已缓存数据...")
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
    print(f"加载数据: {len(combined)} 行, {combined['symbol'].nunique()} 只股票")

    # 读取Regime
    regime_df = pd.read_csv(os.path.join(OUTPUT_DIR, 'cs_index_000688_SH.csv'))
    regime_df['trade_date'] = pd.to_datetime(regime_df['trade_date'])

    # 重新检测Regime（使用更敏感参数）
    c = regime_df['close']
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    trend_20 = (c - ma20) / ma20
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()

    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))

    score = pd.Series(0.0, index=regime_df.index)
    score += np.where(trend_20 > 0.03, 2, np.where(trend_20 < -0.03, -2, 0))
    score += np.where((ma10 > ma20) & (ma20 > ma60), 1.5, np.where((ma10 < ma20) & (ma20 < ma60), -1.5, 0))
    score += np.where(macd > macd_signal, 1, -1)
    score += np.where(rsi > 75, -1.5, np.where(rsi < 25, 1.5, 0))

    regime_df['regime_score'] = score
    regime_df['regime'] = np.select(
        [score > 2.5, score < -2.5],
        ['bull', 'bear'],
        default='neutral'
    )

    print(f"Regime分布: {regime_df['regime'].value_counts().to_dict()}")

    # 生成因子（简化版，复用rd_agent_quant中的generate_factors逻辑）
    print("\n生成因子...")

    def gen_factors(df):
        c = df['close']
        h = df['high']
        l = df['low']
        v = df['vol']
        o = df['open']
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
        for p in [5, 10, 20]:
            factors[f'future_return_{p}d'] = c.shift(-p) / c - 1
        return factors

    factor_list = []
    for symbol in combined['symbol'].unique():
        sub = combined[combined['symbol'] == symbol].sort_values('trade_date').reset_index(drop=True)
        f = gen_factors(sub)
        f['symbol'] = symbol
        factor_list.append(f)

    factor_df = pd.concat(factor_list, ignore_index=True)

    # 截面标准化
    feature_cols = [c for c in factor_df.columns
                    if c not in ['trade_date', 'symbol',
                                 'future_return_5d', 'future_return_10d', 'future_return_20d']]

    def cs_rank(df, cols):
        df = df.copy()
        for col in cols:
            df[col] = df.groupby('trade_date')[col].rank(pct=True)
        return df

    def cs_norm(df, cols):
        df = df.copy()
        for col in cols:
            mean = df.groupby('trade_date')[col].transform('mean')
            std = df.groupby('trade_date')[col].transform('std')
            df[col] = (df[col] - mean) / (std + 1e-8)
        return df

    factor_df = cs_rank(factor_df, feature_cols)
    factor_df = cs_norm(factor_df, feature_cols)

    # 合并regime
    factor_df = factor_df.merge(regime_df[['trade_date', 'regime', 'regime_score']],
                                on='trade_date', how='left')

    # 划分训练/测试
    dates = sorted(factor_df['trade_date'].unique())
    train_end = dates[int(len(dates) * 0.75)]
    train_df = factor_df[factor_df['trade_date'] <= train_end].dropna()
    test_df = factor_df[factor_df['trade_date'] > train_end].dropna()

    print(f"\n训练集: {len(train_df)} 条")
    print(f"测试集: {len(test_df)} 条")

    # 训练 + 回测
    all_results = {}
    for period in [5, 10, 20]:
        label_col = f'future_return_{period}d'
        print(f"\n{'='*50}")
        print(f"预测周期: {period}日")
        print(f"{'='*50}")

        # 训练
        X_train = train_df[feature_cols].values
        y_train = train_df[label_col].values
        X_val = test_df[feature_cols].values
        y_val = test_df[label_col].values

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
            params,
            lgb.Dataset(X_train, y_train),
            num_boost_round=500,
            valid_sets=[lgb.Dataset(X_train, y_train), lgb.Dataset(X_val, y_val)],
            valid_names=['train', 'valid'],
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
        )

        # V2回测
        results, daily = cs_backtest_v2(test_df, model, feature_cols, label_col, period)
        all_results[f'{period}d'] = (results, daily)

        for name, metrics in results.items():
            print(f"\n[{name}]")
            print(f"  总收益: {metrics['total_return']*100:.2f}%")
            print(f"  年化收益: {metrics['annual_return']*100:.2f}%")
            print(f"  夏普: {metrics['sharpe']:.2f}")
            print(f"  最大回撤: {metrics['max_drawdown']*100:.2f}%")
            print(f"  Calmar: {metrics['calmar']:.2f}")
            print(f"  胜率: {metrics['win_rate']*100:.1f}%")

        # 画图
        plot_v2(results, daily, period,
                os.path.join(OUTPUT_DIR, f'cs_regime_v2_{period}d.png'))

    # 汇总图
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    periods = [5, 10, 20]
    strategy_names = list(all_results['5d'][0].keys())

    for idx, period in enumerate(periods):
        results, daily = all_results[f'{period}d']

        # 累计收益
        ax = axes[0, idx]
        dates = daily['trade_date']
        for name, metrics in results.items():
            ax.plot(dates[:len(metrics['cum_series'])], metrics['cum_series'] - 1,
                    label=name, linewidth=2)
        ax.set_title(f'{period}日预测 - 累计收益')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        # 夏普对比
        ax = axes[1, idx]
        names = list(results.keys())
        sharpes = [results[n]['sharpe'] for n in names]
        colors = ['#2E86AB', '#A23B72', '#F18F01', '#6A994E']
        bars = ax.bar(range(len(names)), sharpes, color=colors[:len(names)])
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=15, fontsize=8)
        ax.set_title(f'{period}日预测 - 夏普比率')
        ax.grid(True, alpha=0.3)
        # 标注数值
        for bar, val in zip(bars, sharpes):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                    f'{val:.2f}', ha='center', va='bottom', fontsize=8)

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'cs_regime_v2_summary.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n汇总图已保存: cs_regime_v2_summary.png")

    print("\n" + "=" * 70)
    print("Regime V2（仓位调节版）回测完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()
