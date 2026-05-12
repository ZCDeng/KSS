#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨截面Rank + Regime Detection 量化回测

核心逻辑：
1. 获取科创板Top20成分股数据
2. 生成单股因子
3. 截面标准化（每个因子在当日所有股票中z-score）
4. Regime Detection（基于科创50指数）
   - bull:   做多截面Top20%
   - bear:   空仓/做空截面Bottom20%
   - neutral: 多空对冲（多Top20% + 空Bottom20%）
5. LightGBM训练截面Rank模型
6. 分组回测

借鉴: RD-Agent(Q) arXiv:2505.15155 的 factor-model co-optimization
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
from scipy import stats
import matplotlib.pyplot as plt
import os, sys, warnings
from datetime import datetime
import tushare as ts

warnings.filterwarnings('ignore')
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# ============ Tushare初始化 ============
def init_tushare():
    token = os.environ.get('TUSHARE_TOKEN', '')
    if not token:
        for p in ['~/.tushare/token', '.tushare_token', 'tushare_token.txt']:
            fp = os.path.expanduser(p)
            if os.path.exists(fp):
                with open(fp) as f:
                    token = f.read().strip()
                break
    ts.set_token(token)
    return ts.pro_api()


# ============ 数据获取（带缓存） ============
def get_stock_data(pro, symbol, start_date='20230101', end_date='20250510'):
    """获取单股日线数据（含daily_basic）"""
    cache_file = os.path.join(OUTPUT_DIR, f'cs_data_{symbol}.csv')
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df

    df = pro.daily(ts_code=f'{symbol}.SH', start_date=start_date, end_date=end_date)
    if df is None or len(df) == 0:
        return None
    df['trade_date'] = pd.to_datetime(df['trade_date'])

    # 获取daily_basic
    try:
        basic = pro.daily_basic(ts_code=f'{symbol}.SH', start_date=start_date, end_date=end_date)
        if basic is not None and len(basic) > 0:
            basic['trade_date'] = pd.to_datetime(basic['trade_date'])
            cols = ['trade_date', 'turnover_rate', 'volume_ratio', 'pe', 'pb', 'total_mv', 'circ_mv']
            cols = [c for c in cols if c in basic.columns]
            df = df.merge(basic[cols], on='trade_date', how='left')
    except Exception as e:
        pass

    for col in ['turnover_rate', 'volume_ratio']:
        if col not in df.columns:
            df[col] = 0 if col == 'turnover_rate' else 1

    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cache_file, index=False)
    return df


def get_index_data(pro, index_code='000688.SH'):
    """获取指数数据（Regime Detection用）"""
    cache_file = os.path.join(OUTPUT_DIR, f'cs_index_{index_code.replace(".", "_")}.csv')
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df

    df = pro.index_daily(ts_code=index_code, start_date='20230101', end_date='20250510')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cache_file, index=False)
    return df


# ============ 单股因子生成 ============
def generate_factors(df):
    """生成单股因子（62+个）"""
    c = df['close']
    h = df['high']
    l = df['low']
    v = df['vol']
    o = df['open']
    amt = df.get('amount', v * c)

    factors = pd.DataFrame()
    factors['trade_date'] = df['trade_date']

    # 1. 动量类
    for p in [1, 5, 10, 20, 60]:
        factors[f'mom_{p}d'] = c.pct_change(p)

    # 2. 波动率类
    factors['volatility_5d'] = c.pct_change().rolling(5).std()
    factors['volatility_20d'] = c.pct_change().rolling(20).std()
    factors['volatility_60d'] = c.pct_change().rolling(60).std()
    factors['atr_14'] = (h - l).rolling(14).mean() / c

    # 3. 成交量类
    factors['vol_ma5_ratio'] = v / v.rolling(5).mean()
    factors['vol_ma20_ratio'] = v / v.rolling(20).mean()
    factors['amount_ma5_ratio'] = amt / amt.rolling(5).mean()
    factors['vr'] = df.get('volume_ratio', pd.Series(1.0, index=df.index))
    factors['turnover'] = df.get('turnover_rate', pd.Series(0.0, index=df.index))

    # 4. 价格位置类
    factors['close_to_high_20d'] = c / h.rolling(20).max()
    factors['close_to_low_20d'] = c / l.rolling(20).min()
    factors['close_to_high_60d'] = c / h.rolling(60).max()
    factors['close_to_low_60d'] = c / l.rolling(60).min()

    # 5. 均线类
    for p in [5, 10, 20, 60]:
        ma = c.rolling(p).mean()
        factors[f'ma_{p}_ratio'] = c / ma
        factors[f'ma_{p}_slope'] = ma.pct_change(5)

    # 6. MACD
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    factors['macd'] = ema12 - ema26
    factors['macd_signal'] = factors['macd'].ewm(span=9).mean()
    factors['macd_hist'] = factors['macd'] - factors['macd_signal']

    # 7. RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    factors['rsi_14'] = 100 - (100 / (1 + rs))

    # 8. KDJ
    lowest_9 = l.rolling(9).min()
    highest_9 = h.rolling(9).max()
    rsv = (c - lowest_9) / (highest_9 - lowest_9 + 1e-8) * 100
    factors['kdj_k'] = rsv.ewm(com=2).mean()
    factors['kdj_d'] = factors['kdj_k'].ewm(com=2).mean()
    factors['kdj_j'] = 3 * factors['kdj_k'] - 2 * factors['kdj_d']

    # 9. BOLL
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    factors['boll_upper'] = (ma20 + 2*std20) / c
    factors['boll_lower'] = (ma20 - 2*std20) / c
    factors['boll_width'] = 4 * std20 / c

    # 10. 振幅/实体
    factors['amplitude'] = (h - l) / c
    factors['body_ratio'] = (c - o) / c
    factors['upper_shadow'] = (h - c.clip(lower=o)) / c
    factors['lower_shadow'] = (c.clip(upper=o) - l) / c

    # 11. 价量相关性
    factors['price_vol_corr_20d'] = c.pct_change().rolling(20).corr(v.pct_change())

    # 12. 估值因子（如有）
    if 'pe' in df.columns:
        factors['pe'] = df['pe']
        factors['pb'] = df['pb']

    # 13. 市值因子
    if 'total_mv' in df.columns:
        factors['log_mv'] = np.log(df['total_mv'] + 1)

    # 目标变量：未来收益率
    for p in [5, 10, 20]:
        factors[f'future_return_{p}d'] = c.shift(-p) / c - 1

    return factors


# ============ 截面标准化 ============
def cross_sectional_normalize(df, factor_cols):
    """对每个因子在截面上做z-score标准化（去除市值/行业影响）"""
    df = df.copy()
    for col in factor_cols:
        # 每日截面均值和标准差
        daily_mean = df.groupby('trade_date')[col].transform('mean')
        daily_std = df.groupby('trade_date')[col].transform('std')
        df[col] = (df[col] - daily_mean) / (daily_std + 1e-8)
    return df


def cross_sectional_rank(df, factor_cols):
    """对每个因子在截面上做排名标准化（0-1）"""
    df = df.copy()
    for col in factor_cols:
        df[col] = df.groupby('trade_date')[col].rank(pct=True)
    return df


# ============ Regime Detection ============
def detect_regime(index_df):
    """
    基于科创50指数的市场状态识别
    返回: regime_df ['trade_date', 'regime', 'regime_score']
    - regime: 'bull'(上涨), 'neutral'(震荡), 'bear'(下跌)
    - regime_score: 综合评分（越高越 bullish）
    """
    c = index_df['close']

    # 均线系统
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()

    # 趋势强度
    trend_10 = (c - ma10) / ma10
    trend_20 = (c - ma20) / ma20
    trend_60 = (c - ma60) / ma60
    golden_cross = (ma20 > ma60).astype(int)

    # MACD
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    macd_hist = macd - macd_signal

    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))

    # 波动率
    vol_20 = c.pct_change().rolling(20).std()
    vol_60 = c.pct_change().rolling(60).std()
    vol_regime = vol_20 / (vol_60 + 1e-8)

    # 综合评分系统
    score = pd.Series(0.0, index=index_df.index)

    # 趋势分 (+4 ~ -4)
    score += np.where(trend_20 > 0.05, 3, 0)
    score += np.where((trend_20 > 0) & (trend_20 <= 0.05), 1.5, 0)
    score += np.where(trend_20 < -0.05, -3, 0)
    score += np.where((trend_20 < 0) & (trend_20 >= -0.05), -1.5, 0)

    # 均线排列 (+2 ~ -2)
    score += np.where((ma10 > ma20) & (ma20 > ma60), 2, 0)
    score += np.where((ma10 < ma20) & (ma20 < ma60), -2, 0)

    # MACD (+2 ~ -2)
    score += np.where(macd > macd_signal, 1.5, -1.5)
    score += np.where(macd_hist > macd_hist.shift(1), 0.5, -0.5)

    # RSI (+2 ~ -2)
    score += np.where(rsi > 80, -2, 0)      # 严重超买
    score += np.where((rsi > 60) & (rsi <= 80), -0.5, 0)
    score += np.where(rsi < 20, 2, 0)       # 严重超卖
    score += np.where((rsi < 40) & (rsi >= 20), 0.5, 0)

    # 波动率 (震荡市特征)
    score += np.where(vol_regime > 1.5, -1, 0)  # 高波动通常不利于趋势策略

    regime = pd.DataFrame()
    regime['trade_date'] = index_df['trade_date']
    regime['regime_score'] = score

    # 分类: bear < -3, neutral [-3, 3], bull > 3
    conditions = [
        score > 3,
        score < -3
    ]
    choices = ['bull', 'bear']
    regime['regime'] = np.select(conditions, choices, default='neutral')

    return regime


# ============ 模型训练 ============
def train_rank_model(train_df, feature_cols, label_col, val_df=None):
    """训练LightGBM Rank模型"""
    X_train = train_df[feature_cols].values
    y_train = train_df[label_col].values

    if val_df is not None and len(val_df) > 0:
        X_val = val_df[feature_cols].values
        y_val = val_df[label_col].values
        valid_sets = [lgb.Dataset(X_train, y_train), lgb.Dataset(X_val, y_val)]
        valid_names = ['train', 'valid']
    else:
        valid_sets = [lgb.Dataset(X_train, y_train)]
        valid_names = ['train']

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
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
    )
    return model


# ============ 回测核心 ============
def cs_backtest(df, model, feature_cols, label_col, period=5, regime_filter=True):
    """
    跨截面回测
    df需包含: trade_date, symbol, pred_score, regime, future_return_*
    """
    df = df.sort_values(['trade_date', 'symbol']).copy()

    # 每日预测
    df['pred_score'] = model.predict(df[feature_cols])

    # 每日分组（截面排名）
    df['daily_rank'] = df.groupby('trade_date')['pred_score'].rank(pct=True)

    # 定义组合
    long_mask = df['daily_rank'] >= 0.8   # Top 20%
    short_mask = df['daily_rank'] <= 0.2  # Bottom 20%

    returns = df.groupby('trade_date').apply(lambda g: pd.Series({
        'long_return': g.loc[g['daily_rank'] >= 0.8, label_col].mean(),
        'short_return': g.loc[g['daily_rank'] <= 0.2, label_col].mean(),
        'n_long': (g['daily_rank'] >= 0.8).sum(),
        'n_short': (g['daily_rank'] <= 0.2).sum(),
    })).reset_index()

    # 合并regime
    regime_map = df.groupby('trade_date')['regime'].first().reset_index()
    returns = returns.merge(regime_map, on='trade_date', how='left')

    # 根据regime调整策略
    if regime_filter:
        # bull: 做多Top20%
        # bear: 做空Bottom20%（或空仓）
        # neutral: 多空对冲
        returns['strategy_return'] = np.where(
            returns['regime'] == 'bull',
            returns['long_return'],
            np.where(
                returns['regime'] == 'bear',
                -returns['short_return'],  # 做空bottom20%
                returns['long_return'] - returns['short_return']  # 中性: 多空对冲
            )
        )
    else:
        # 纯多空对冲（不管regime）
        returns['strategy_return'] = returns['long_return'] - returns['short_return']

    # 计算累计收益
    returns['cum_return'] = (1 + returns['strategy_return']).cumprod() - 1
    returns['cum_long'] = (1 + returns['long_return']).cumprod() - 1
    returns['cum_short'] = (1 + returns['short_return']).cumprod() - 1

    # 统计指标
    r = returns['strategy_return'].dropna()
    total_ret = returns['cum_return'].iloc[-1] if len(returns) > 0 else 0
    annual_ret = (1 + total_ret) ** (252 / len(r)) - 1 if len(r) > 0 else 0
    volatility = r.std() * np.sqrt(252)
    sharpe = annual_ret / volatility if volatility > 0 else 0

    # 最大回撤
    cum = (1 + r).cumprod()
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max
    max_dd = drawdown.min()

    calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0

    # 胜率
    win_rate = (r > 0).mean()

    metrics = {
        'total_return': total_ret,
        'annual_return': annual_ret,
        'volatility': volatility,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'calmar': calmar,
        'win_rate': win_rate,
        'avg_daily_return': r.mean(),
        'n_days': len(r),
    }

    return metrics, returns


# ============ 可视化 ============
def plot_results(metrics_dict, returns_dict, period, output_path):
    """绘制回测结果对比图"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # 1. 累计收益曲线
    ax = axes[0, 0]
    for name, ret_df in returns_dict.items():
        ax.plot(ret_df['trade_date'], ret_df['cum_return'], label=name, linewidth=2)
    ax.set_title(f'策略累计收益曲线 ({period}日预测)')
    ax.set_xlabel('日期')
    ax.set_ylabel('累计收益')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 2. 关键指标对比
    ax = axes[0, 1]
    strategies = list(metrics_dict.keys())
    x = np.arange(len(strategies))
    width = 0.2

    metrics_to_plot = ['sharpe', 'calmar', 'win_rate']
    colors = ['#2E86AB', '#A23B72', '#F18F01']
    for i, m in enumerate(metrics_to_plot):
        vals = [metrics_dict[s].get(m, 0) for s in strategies]
        ax.bar(x + i*width, vals, width, label=m, color=colors[i])
    ax.set_title('关键指标对比')
    ax.set_xticks(x + width)
    ax.set_xticklabels(strategies)
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 3. Regime分布
    ax = axes[1, 0]
    for name, ret_df in returns_dict.items():
        regime_counts = ret_df['regime'].value_counts()
        ax.bar(regime_counts.index, regime_counts.values, alpha=0.6, label=name)
    ax.set_title('市场状态分布')
    ax.set_xlabel('Regime')
    ax.set_ylabel('天数')
    ax.legend()

    # 4. 收益分布（按regime）
    ax = axes[1, 1]
    for name, ret_df in returns_dict.items():
        for regime in ['bull', 'neutral', 'bear']:
            sub = ret_df[ret_df['regime'] == regime]['strategy_return'].dropna()
            if len(sub) > 0:
                ax.hist(sub, bins=20, alpha=0.5, label=f'{name}-{regime}')
    ax.set_title('日收益分布（按Regime）')
    ax.set_xlabel('日收益')
    ax.set_ylabel('频次')
    ax.legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"结果图已保存: {output_path}")


# ============ 主流程 ============
def main():
    print("=" * 70)
    print("跨截面Rank + Regime Detection 量化回测")
    print("=" * 70)

    pro = init_tushare()

    # 1. 获取成分股
    symbols_df = pd.read_csv(os.path.join(OUTPUT_DIR, 'kcb50_top20_symbols.csv'))
    symbols = symbols_df['symbol'].tolist()
    print(f"\n科创板Top20成分股: {len(symbols)}只")
    print(symbols)

    # 2. 获取指数数据（Regime Detection）
    print("\n[1/6] 获取科创50指数数据...")
    index_df = get_index_data(pro, '000688.SH')
    regime_df = detect_regime(index_df)
    print(f"Regime分布: {regime_df['regime'].value_counts().to_dict()}")

    # 3. 批量获取个股数据
    print("\n[2/6] 批量获取个股数据...")
    all_data = []
    for i, symbol in enumerate(symbols):
        df = get_stock_data(pro, symbol)
        if df is not None and len(df) > 100:
            df['symbol'] = symbol
            all_data.append(df)
            print(f"  {symbol}: {len(df)} 条记录 ✓")
        else:
            print(f"  {symbol}: 数据不足 ✗")

    if len(all_data) < 10:
        print(f"数据不足（仅{len(all_data)}只），退出")
        return

    combined = pd.concat(all_data, ignore_index=True)
    print(f"\n合并数据: {len(combined)} 行, {combined['symbol'].nunique()} 只股票")

    # 4. 生成因子
    print("\n[3/6] 生成单股因子...")
    factor_list = []
    for symbol in combined['symbol'].unique():
        sub = combined[combined['symbol'] == symbol].sort_values('trade_date').reset_index(drop=True)
        f = generate_factors(sub)
        f['symbol'] = symbol
        factor_list.append(f)

    factor_df = pd.concat(factor_list, ignore_index=True)
    print(f"因子数据: {len(factor_df)} 行, 因子数: {len(factor_df.columns)-4}")

    # 5. 截面标准化
    print("\n[4/6] 截面标准化...")
    feature_cols = [c for c in factor_df.columns
                    if c not in ['trade_date', 'symbol',
                                 'future_return_5d', 'future_return_10d', 'future_return_20d']]
    # 先做rank标准化（对异常值更鲁棒）
    factor_df = cross_sectional_rank(factor_df, feature_cols)
    # 再做z-score
    factor_df = cross_sectional_normalize(factor_df, feature_cols)
    print(f"截面标准化完成，特征数: {len(feature_cols)}")

    # 6. 合并regime
    factor_df = factor_df.merge(regime_df[['trade_date', 'regime', 'regime_score']],
                                on='trade_date', how='left')

    # 7. 划分训练/测试
    print("\n[5/6] 划分训练/测试集...")
    dates = factor_df['trade_date'].unique()
    dates = sorted(dates)
    train_end_idx = int(len(dates) * 0.75)
    train_end = dates[train_end_idx]

    train_df = factor_df[factor_df['trade_date'] <= train_end].dropna()
    test_df = factor_df[factor_df['trade_date'] > train_end].dropna()
    print(f"训练集: {len(train_df)} 条 ({train_df['trade_date'].min()} ~ {train_df['trade_date'].max()})")
    print(f"测试集: {len(test_df)} 条 ({test_df['trade_date'].min()} ~ {test_df['trade_date'].max()})")

    # 8. 训练模型 + 回测
    print("\n[6/6] 训练模型并回测...")
    results = {}
    all_returns = {}

    for period in [5, 10, 20]:
        label_col = f'future_return_{period}d'
        print(f"\n{'='*50}")
        print(f"预测周期: {period}日")
        print(f"{'='*50}")

        # 训练
        model = train_rank_model(train_df, feature_cols, label_col, test_df)

        # 测试集回测（带Regime过滤）
        metrics_regime, ret_regime = cs_backtest(
            test_df, model, feature_cols, label_col, period, regime_filter=True
        )

        # 测试集回测（不带Regime过滤，纯多空对冲）
        metrics_pure, ret_pure = cs_backtest(
            test_df, model, feature_cols, label_col, period, regime_filter=False
        )

        print(f"\n[带Regime过滤]")
        print(f"  总收益: {metrics_regime['total_return']*100:.2f}%")
        print(f"  年化收益: {metrics_regime['annual_return']*100:.2f}%")
        print(f"  夏普: {metrics_regime['sharpe']:.2f}")
        print(f"  最大回撤: {metrics_regime['max_drawdown']*100:.2f}%")
        print(f"  Calmar: {metrics_regime['calmar']:.2f}")
        print(f"  胜率: {metrics_regime['win_rate']*100:.1f}%")

        print(f"\n[纯多空对冲]")
        print(f"  总收益: {metrics_pure['total_return']*100:.2f}%")
        print(f"  年化收益: {metrics_pure['annual_return']*100:.2f}%")
        print(f"  夏普: {metrics_pure['sharpe']:.2f}")
        print(f"  最大回撤: {metrics_pure['max_drawdown']*100:.2f}%")
        print(f"  Calmar: {metrics_pure['calmar']:.2f}")
        print(f"  胜率: {metrics_pure['win_rate']*100:.1f}%")

        results[f'{period}d_regime'] = metrics_regime
        results[f'{period}d_pure'] = metrics_pure
        all_returns[f'{period}d_regime'] = ret_regime
        all_returns[f'{period}d_pure'] = ret_pure

        # 保存图
        plot_results(
            {f'{period}d_regime': metrics_regime, f'{period}d_pure': metrics_pure},
            {f'{period}d_regime': ret_regime, f'{period}d_pure': ret_pure},
            period,
            os.path.join(OUTPUT_DIR, f'cs_regime_results_{period}d.png')
        )

    # 汇总对比图
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # 夏普对比
    ax = axes[0, 0]
    strategies = ['5d_regime', '5d_pure', '10d_regime', '10d_pure', '20d_regime', '20d_pure']
    sharpes = [results[s]['sharpe'] for s in strategies]
    colors = ['#2E86AB' if 'regime' in s else '#F24236' for s in strategies]
    ax.bar(range(len(strategies)), sharpes, color=colors)
    ax.set_xticks(range(len(strategies)))
    ax.set_xticklabels(strategies, rotation=45)
    ax.set_title('夏普比率对比')
    ax.grid(True, alpha=0.3)

    # Calmar对比
    ax = axes[0, 1]
    calmars = [results[s]['calmar'] for s in strategies]
    ax.bar(range(len(strategies)), calmars, color=colors)
    ax.set_xticks(range(len(strategies)))
    ax.set_xticklabels(strategies, rotation=45)
    ax.set_title('Calmar比率对比')
    ax.grid(True, alpha=0.3)

    # 年化收益对比
    ax = axes[1, 0]
    rets = [results[s]['annual_return'] * 100 for s in strategies]
    ax.bar(range(len(strategies)), rets, color=colors)
    ax.set_xticks(range(len(strategies)))
    ax.set_xticklabels(strategies, rotation=45)
    ax.set_title('年化收益对比 (%)')
    ax.grid(True, alpha=0.3)

    # 最大回撤对比
    ax = axes[1, 1]
    dds = [results[s]['max_drawdown'] * 100 for s in strategies]
    ax.bar(range(len(strategies)), dds, color=colors)
    ax.set_xticks(range(len(strategies)))
    ax.set_xticklabels(strategies, rotation=45)
    ax.set_title('最大回撤对比 (%)')
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'cs_regime_summary.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n汇总图已保存: cs_regime_summary.png")

    print("\n" + "=" * 70)
    print("跨截面Rank + Regime Detection 回测完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()
