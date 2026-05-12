#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
688017 单股深度分析：因子重要性 + 最优决策指标组合

目标：
1. 用LightGBM训练单股择时模型，提取特征重要性
2. 回测单股择时策略（基于预测分数阈值）
3. 遍历关键指标组合，找出最优决策规则
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import matplotlib.pyplot as plt
import os, warnings
from itertools import product

warnings.filterwarnings('ignore')
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 交易成本
BUY_COST = 0.001
SELL_COST = 0.002


def init_tushare():
    token = os.environ.get('TUSHARE_TOKEN', '')
    if not token:
        for p in ['~/.tushare/token', '.tushare_token', 'tushare_token.txt']:
            fp = os.path.expanduser(p)
            if os.path.exists(fp):
                with open(fp) as f:
                    token = f.read().strip()
                break
    import tushare as ts
    ts.set_token(token)
    return ts.pro_api()


def download_stock(pro, symbol, start='20230101', end='20250510'):
    cache = os.path.join(OUTPUT_DIR, f'cs_data_{symbol}.csv')
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
    df = pro.daily(ts_code=f'{symbol}.SH', start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return None
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    try:
        basic = pro.daily_basic(ts_code=f'{symbol}.SH', start_date=start, end_date=end)
        if basic is not None and len(basic) > 0:
            basic['trade_date'] = pd.to_datetime(basic['trade_date'])
            cols = ['trade_date', 'turnover_rate', 'volume_ratio', 'pe', 'pb', 'total_mv']
            cols = [c for c in cols if c in basic.columns]
            df = df.merge(basic[cols], on='trade_date', how='left')
    except:
        pass
    for col in ['turnover_rate', 'volume_ratio']:
        if col not in df.columns:
            df[col] = 0 if col == 'turnover_rate' else 1
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(cache, index=False)
    return df


def generate_factors(df):
    c = df['close']
    h = df['high']
    l = df['low']
    v = df['vol']
    o = df['open']
    amt = df.get('amount', v * c)
    factors = pd.DataFrame()
    factors['trade_date'] = df['trade_date']
    factors['close'] = c
    factors['open'] = o
    factors['high'] = h
    factors['low'] = l
    
    # 动量
    for p in [1, 5, 10, 20, 60]:
        factors[f'mom_{p}d'] = c.pct_change(p)
    
    # 波动率
    factors['volatility_5d'] = c.pct_change().rolling(5).std()
    factors['volatility_20d'] = c.pct_change().rolling(20).std()
    factors['volatility_60d'] = c.pct_change().rolling(60).std()
    factors['atr_14'] = (h - l).rolling(14).mean() / c
    
    # 成交量
    factors['vol_ma5_ratio'] = v / v.rolling(5).mean()
    factors['vol_ma20_ratio'] = v / v.rolling(20).mean()
    factors['amount_ma5_ratio'] = amt / amt.rolling(5).mean()
    factors['vr'] = df.get('volume_ratio', pd.Series(1.0, index=df.index))
    factors['turnover'] = df.get('turnover_rate', pd.Series(0.0, index=df.index))
    
    # 价格位置
    factors['close_to_high_20d'] = c / h.rolling(20).max()
    factors['close_to_low_20d'] = c / l.rolling(20).min()
    factors['close_to_high_60d'] = c / h.rolling(60).max()
    factors['close_to_low_60d'] = c / l.rolling(60).min()
    
    # 均线
    for p in [5, 10, 20, 60]:
        ma = c.rolling(p).mean()
        factors[f'ma_{p}_ratio'] = c / ma
        factors[f'ma_{p}_slope'] = ma.pct_change(5)
    
    # MACD
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    factors['macd'] = ema12 - ema26
    factors['macd_signal'] = factors['macd'].ewm(span=9).mean()
    factors['macd_hist'] = factors['macd'] - factors['macd_signal']
    factors['macd_bull'] = (factors['macd'] > factors['macd_signal']).astype(int)
    
    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    factors['rsi_14'] = 100 - (100 / (1 + rs))
    factors['rsi_oversold'] = (factors['rsi_14'] < 30).astype(int)
    factors['rsi_overbought'] = (factors['rsi_14'] > 70).astype(int)
    
    # KDJ
    lowest_9 = l.rolling(9).min()
    highest_9 = h.rolling(9).max()
    rsv = (c - lowest_9) / (highest_9 - lowest_9 + 1e-8) * 100
    factors['kdj_k'] = rsv.ewm(com=2).mean()
    factors['kdj_d'] = factors['kdj_k'].ewm(com=2).mean()
    factors['kdj_j'] = 3 * factors['kdj_k'] - 2 * factors['kdj_d']
    factors['kdj_golden'] = ((factors['kdj_k'] > factors['kdj_d']) & 
                             (factors['kdj_k'].shift(1) <= factors['kdj_d'].shift(1))).astype(int)
    
    # Bollinger
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    factors['boll_upper'] = (ma20 + 2*std20) / c
    factors['boll_lower'] = (ma20 - 2*std20) / c
    factors['boll_width'] = 4 * std20 / c
    factors['boll_position'] = (c - ma20) / (2 * std20 + 1e-8)
    
    # K线形态
    factors['amplitude'] = (h - l) / c
    factors['body_ratio'] = (c - o) / c
    factors['upper_shadow'] = (h - c.clip(lower=o)) / c
    factors['lower_shadow'] = (c.clip(upper=o) - l) / c
    
    # 价量相关
    factors['price_vol_corr_20d'] = c.pct_change().rolling(20).corr(v.pct_change())
    
    # 估值
    if 'pe' in df.columns:
        factors['pe'] = df['pe']
        factors['pb'] = df['pb']
    if 'total_mv' in df.columns:
        factors['log_mv'] = np.log(df['total_mv'] + 1)
    
    # 训练目标
    for p in [5, 10, 20]:
        factors[f'future_return_{p}d'] = c.shift(-p) / c - 1
    factors['next_day_return'] = c.shift(-1) / c - 1
    
    return factors


def train_and_importance(df, feature_cols, label_col):
    """训练模型并返回特征重要性"""
    train_df = df.dropna()
    X = train_df[feature_cols].values
    y = train_df[label_col].values
    
    params = {
        'objective': 'regression', 'metric': 'rmse', 'boosting_type': 'gbdt',
        'num_leaves': 31, 'learning_rate': 0.05, 'feature_fraction': 0.8,
        'bagging_fraction': 0.8, 'bagging_freq': 5, 'verbose': -1, 'random_state': 42,
    }
    
    n = len(X)
    split = int(n * 0.8)
    train_data = lgb.Dataset(X[:split], y[:split])
    valid_data = lgb.Dataset(X[split:], y[split:])
    
    model = lgb.train(params, train_data, num_boost_round=500,
                      valid_sets=[valid_data], valid_names=['valid'],
                      callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)])
    
    importance = model.feature_importance(importance_type='gain')
    imp_df = pd.DataFrame({
        'feature': feature_cols,
        'importance': importance,
    }).sort_values('importance', ascending=False)
    
    return model, imp_df


def backtest_single_stock(df, signal_col, threshold_long=0.5, threshold_short=None,
                          hold_days=5, stop_loss=None):
    """
    单股择时回测
    signal_col: 信号列名
    threshold_long: 买入阈值（信号大于此值买入）
    threshold_short: 做空阈值（信号小于此值做空，None表示不做空）
    hold_days: 持仓天数
    stop_loss: 止损比例（如0.05表示5%止损）
    """
    df = df.copy().reset_index(drop=True)
    df['signal'] = df[signal_col]
    
    capital = 1.0
    position = 0  # 0=空仓, 1=多头, -1=空头
    entry_price = 0
    entry_idx = -1
    trades = []
    equity = [capital]
    
    for i in range(len(df) - 1):
        signal = df['signal'].iloc[i]
        price = df['close'].iloc[i]
        next_return = df['next_day_return'].iloc[i]
        
        # 检查止损
        if position != 0 and stop_loss is not None and entry_price > 0:
            current_return = (price - entry_price) / entry_price if position == 1 else (entry_price - price) / entry_price
            if current_return < -stop_loss:
                # 止损平仓
                pnl = current_return if position == 1 else -current_return
                cost = SELL_COST if position == 1 else BUY_COST
                capital *= (1 + pnl * position - cost)
                trades.append({'type': 'stop', 'pnl': pnl * position - cost, 'idx': i})
                position = 0
                entry_price = 0
                equity.append(capital)
                continue
        
        # 检查持仓时间
        if position != 0 and entry_idx >= 0 and i - entry_idx >= hold_days:
            pnl = (price - entry_price) / entry_price if position == 1 else (entry_price - price) / entry_price
            cost = SELL_COST if position == 1 else BUY_COST
            capital *= (1 + pnl * position - cost)
            trades.append({'type': 'exit_time', 'pnl': pnl * position - cost, 'idx': i})
            position = 0
            entry_price = 0
            equity.append(capital)
            continue
        
        # 开仓/平仓逻辑
        if position == 0:
            if signal > threshold_long:
                position = 1
                entry_price = price
                entry_idx = i
                capital *= (1 - BUY_COST)
            elif threshold_short is not None and signal < threshold_short:
                position = -1
                entry_price = price
                entry_idx = i
                capital *= (1 - SELL_COST)
        
        equity.append(capital)
    
    # 强制平仓
    if position != 0:
        price = df['close'].iloc[-1]
        pnl = (price - entry_price) / entry_price if position == 1 else (entry_price - price) / entry_price
        cost = SELL_COST if position == 1 else BUY_COST
        capital *= (1 + pnl * position - cost)
        trades.append({'type': 'final', 'pnl': pnl * position - cost, 'idx': len(df)-1})
        equity.append(capital)
    
    return capital - 1, trades, equity


def grid_search_signals(df, feature_cols, label_col='future_return_10d'):
    """网格搜索最优指标组合"""
    results = []
    
    # 基于特征重要性选出的 Top 特征进行组合测试
    top_features = [
        'macd_hist', 'macd', 'rsi_14', 'kdj_j', 'boll_position',
        'close_to_high_20d', 'close_to_low_20d', 'volatility_20d',
        'mom_5d', 'mom_10d', 'mom_20d', 'ma_20_ratio', 'ma_60_ratio',
        'vol_ma5_ratio', 'vol_ma20_ratio', 'amplitude', 'body_ratio',
        'ma_5_slope', 'ma_20_slope', 'atr_14',
    ]
    
    print("\n【单指标择时测试】")
    print("-" * 80)
    print(f"{'指标':<20} {'阈值类型':<10} {'阈值':<8} {'持仓':<6} {'总收益':<10} {'交易次数':<8}")
    print("-" * 80)
    
    # 1. 单指标测试（阈值法）
    for feat in top_features:
        if feat not in df.columns:
            continue
        
        # 买入信号：指标大于其80%分位数
        q80 = df[feat].quantile(0.8)
        q20 = df[feat].quantile(0.2)
        
        for threshold, ttype in [(q80, '高分位'), (q20, '低分位')]:
            for hold in [5, 10, 20]:
                total, trades, _ = backtest_single_stock(df, feat, 
                                                          threshold_long=threshold if ttype == '高分位' else -999,
                                                          threshold_short=threshold if ttype == '低分位' else None,
                                                          hold_days=hold)
                results.append({
                    'feat': feat, 'threshold_type': ttype, 'threshold': threshold,
                    'hold_days': hold, 'total_return': total, 'n_trades': len(trades),
                    'strategy': f'{feat}>{threshold:.3f}' if ttype == '高分位' else f'{feat}<{threshold:.3f}'
                })
    
    # 2. 经典组合测试
    print("\n【经典指标组合测试】")
    print("-" * 80)
    print(f"{'策略':<40} {'持仓':<6} {'总收益':<10} {'交易次数':<8}")
    print("-" * 80)
    
    combos = [
        ('MACD金叉+RSI<70', lambda d: (d['macd'] > d['macd_signal']) & (d['rsi_14'] < 70), 10),
        ('MACD金叉+RSI<50', lambda d: (d['macd'] > d['macd_signal']) & (d['rsi_14'] < 50), 10),
        ('KDJ金叉+RSI<50', lambda d: (d['kdj_k'] > d['kdj_d']) & (d['rsi_14'] < 50), 10),
        ('RSI超卖反弹(30-50)', lambda d: (d['rsi_14'] > 30) & (d['rsi_14'] < 50) & (d['rsi_14'].shift(1) <= 30), 5),
        ('RSI超卖(买入)', lambda d: d['rsi_14'] < 30, 10),
        ('BOLL下轨反弹', lambda d: d['boll_position'] < -0.8, 10),
        ('量价齐升', lambda d: (d['vol_ma5_ratio'] > 1.5) & (d['mom_5d'] > 0.02), 5),
        ('突破20日高', lambda d: d['close_to_high_20d'] > 0.98, 10),
        ('超跌反弹(close_to_low<0.05)', lambda d: d['close_to_low_20d'] < 0.05, 10),
        ('均线多头排列(ma5>ma10>ma20)', lambda d: (d['ma_5_ratio'] > d['ma_10_ratio']) & (d['ma_10_ratio'] > d['ma_20_ratio']), 20),
        ('波动率压缩+突破', lambda d: (d['volatility_20d'] < d['volatility_20d'].rolling(20).quantile(0.2)) & (d['mom_5d'] > 0.02), 10),
    ]
    
    for name, cond_fn, hold in combos:
        df['combo_signal'] = cond_fn(df).astype(float)
        total, trades, _ = backtest_single_stock(df, 'combo_signal', 
                                                  threshold_long=0.5, 
                                                  hold_days=hold)
        results.append({
            'feat': name, 'threshold_type': '组合', 'threshold': 0.5,
            'hold_days': hold, 'total_return': total, 'n_trades': len(trades),
            'strategy': name
        })
        print(f"{name:<40} {hold:<6} {total*100:>8.1f}%  {len(trades):<8}")
    
    # 3. 双指标组合（Top特征交叉）
    print("\n【双指标组合测试（Top5特征）】")
    print("-" * 80)
    
    best_results = sorted([r for r in results if r['threshold_type'] != '组合'], 
                          key=lambda x: x['total_return'], reverse=True)[:5]
    
    for r in best_results:
        print(f"  {r['strategy']:<30} 持仓{r['hold_days']}天  收益{r['total_return']*100:.1f}%  {r['n_trades']}次")
    
    return results


def plot_importance(imp_df, path):
    """绘制特征重要性"""
    fig, ax = plt.subplots(figsize=(12, 10))
    top20 = imp_df.head(20)
    ax.barh(range(len(top20)), top20['importance'], color='steelblue')
    ax.set_yticks(range(len(top20)))
    ax.set_yticklabels(top20['feature'])
    ax.invert_yaxis()
    ax.set_xlabel('Importance (Gain)')
    ax.set_title('688017 LightGBM 特征重要性 Top20')
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"特征重要性图: {path}")


def plot_equity_curves(df, strategies, path):
    """绘制多个策略的净值曲线对比"""
    fig, ax = plt.subplots(figsize=(14, 7))
    
    # 买入持有基准
    bh_return = df['close'].iloc[-1] / df['close'].iloc[0] - 1
    bh_equity = (1 + df['close'].pct_change().fillna(0)).cumprod()
    ax.plot(df['trade_date'], bh_equity, label=f'买入持有 ({bh_return*100:.1f}%)', 
            color='gray', lw=2, ls='--')
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(strategies)))
    for i, (name, signal_col, threshold, hold) in enumerate(strategies):
        total, trades, equity = backtest_single_stock(df, signal_col, 
                                                       threshold_long=threshold, 
                                                       hold_days=hold)
        # 确保长度匹配
        n = min(len(df), len(equity))
        ax.plot(df['trade_date'].iloc[:n], equity[:n], label=f'{name} ({total*100:.1f}%)', 
                color=colors[i], lw=1.5)
    
    ax.set_title('688017 策略净值对比')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"净值对比图: {path}")


def main():
    print("=" * 70)
    print("688017 单股深度分析：因子重要性 + 最优决策指标组合")
    print("=" * 70)
    
    pro = init_tushare()
    
    # 1. 下载数据
    print("\n【1】下载688017两年数据...")
    df = download_stock(pro, '688017', start='20230101', end='20250510')
    if df is None or len(df) < 200:
        print("数据不足，退出")
        return
    print(f"  数据: {len(df)}行, {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    
    # 2. 生成因子
    print("\n【2】生成因子...")
    factors = generate_factors(df)
    feature_cols = [c for c in factors.columns if c not in [
        'trade_date', 'close', 'open', 'high', 'low', 'future_return_5d',
        'future_return_10d', 'future_return_20d', 'next_day_return',
        'signal', 'combo_signal']]
    print(f"  因子数: {len(feature_cols)}")
    
    # 3. 训练模型 + 特征重要性
    print("\n【3】LightGBM模型训练 & 特征重要性...")
    model_5d, imp_5d = train_and_importance(factors, feature_cols, 'future_return_5d')
    model_10d, imp_10d = train_and_importance(factors, feature_cols, 'future_return_10d')
    model_20d, imp_20d = train_and_importance(factors, feature_cols, 'future_return_20d')
    
    print("\n  --- 5日模型 Top15 ---")
    for _, row in imp_5d.head(15).iterrows():
        print(f"    {row['feature']:<20} {row['importance']:.1f}")
    
    print("\n  --- 10日模型 Top15 ---")
    for _, row in imp_10d.head(15).iterrows():
        print(f"    {row['feature']:<20} {row['importance']:.1f}")
    
    print("\n  --- 20日模型 Top15 ---")
    for _, row in imp_20d.head(15).iterrows():
        print(f"    {row['feature']:<20} {row['importance']:.1f}")
    
    # 保存特征重要性图
    plot_importance(imp_10d, os.path.join(OUTPUT_DIR, '688017_importance_10d.png'))
    
    # 4. 使用模型预测分数进行单股择时回测
    print("\n【4】LightGBM预测分数择时回测...")
    
    # 用10日模型预测
    factors['pred_score_10d'] = model_10d.predict(factors[feature_cols].values)
    
    for thresh in [0.5, 0.6, 0.7, 0.8]:
        q = factors['pred_score_10d'].quantile(thresh)
        total, trades, _ = backtest_single_stock(factors, 'pred_score_10d', 
                                                  threshold_long=q, hold_days=10)
        print(f"  阈值 P{int(thresh*100)} ({q:.4f}): 总收益 {total*100:.1f}%, {len(trades)}次交易")
    
    # 5. 经典指标组合网格搜索
    print("\n【5】经典指标组合网格搜索...")
    all_results = grid_search_signals(factors, feature_cols)
    
    # 找出最优策略
    best = max(all_results, key=lambda x: x['total_return'])
    print(f"\n【最优单策略】{best['strategy']}, 持仓{best['hold_days']}天, 收益{best['total_return']*100:.1f}%")
    
    # 6. 绘制Top策略净值曲线
    print("\n【6】绘制净值对比图...")
    top_strategies = [
        ('LightGBM-P80', 'pred_score_10d', factors['pred_score_10d'].quantile(0.8), 10),
        ('MACD金叉+RSI<50', 'combo_signal', 0.5, 10),
        ('量价齐升', 'combo_signal', 0.5, 5),
    ]
    plot_equity_curves(factors, top_strategies, 
                       os.path.join(OUTPUT_DIR, '688017_strategy_compare.png'))
    
    # 7. 综合分析报告
    print("\n" + "=" * 70)
    print("【综合分析报告】")
    print("=" * 70)
    
    # 买入持有基准
    bh_return = factors['close'].iloc[-1] / factors['close'].iloc[0] - 1
    print(f"\n买入持有基准: {bh_return*100:.1f}%")
    
    print("\n【10日模型最重要的5个因子】")
    for i, row in imp_10d.head(5).iterrows():
        print(f"  {i+1}. {row['feature']} (重要性: {row['importance']:.1f})")
    
    print("\n【推荐决策指标组合】")
    print("  基于LightGBM特征重要性和回测结果，688017最有效的决策指标为:")
    
    top5 = imp_10d.head(5)['feature'].tolist()
    for feat in top5:
        desc = {
            'macd_hist': 'MACD柱状图（动量加速/减速）',
            'macd': 'MACD差离值（趋势方向）',
            'rsi_14': 'RSI相对强弱（超买超卖）',
            'kdj_j': 'KDJ J值（极端位置）',
            'boll_position': '布林轨道位置（价格极端程度）',
            'close_to_high_20d': '距20日高点距离',
            'close_to_low_20d': '距20日低点距离',
            'volatility_20d': '20日波动率',
            'mom_5d': '5日动量',
            'mom_10d': '10日动量',
        }.get(feat, feat)
        print(f"    - {feat}: {desc}")
    
    print("\n【最优交易策略】")
    print(f"  策略: {best['strategy']}")
    print(f"  持仓周期: {best['hold_days']}天")
    print(f"  总收益: {best['total_return']*100:.1f}%")
    print(f"  交易次数: {best['n_trades']}")
    
    print("\n" + "=" * 70)
    print("分析完成!")
    print(f"  特征重要性图: 688017_importance_10d.png")
    print(f"  策略对比图: 688017_strategy_compare.png")
    print("=" * 70)


if __name__ == '__main__':
    main()
