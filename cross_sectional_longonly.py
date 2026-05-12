#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨截面Rank + 纯多头 + 每周调仓 + 交易成本

改进：
1. 股票池扩大到科创板50全部50只成分股
2. 纯多头策略（放弃做空，解决融券障碍）
3. 每周调仓（降低交易频率，减少成本）
4. 计入交易成本：买入0.1% + 卖出0.2%（含印花税）= 双边0.3%
5. Walk-forward滚动训练
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import matplotlib.pyplot as plt
import os, warnings
import tushare as ts

warnings.filterwarnings('ignore')
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 交易成本设置
BUY_COST = 0.001   # 买入：佣金0.03% + 冲击成本0.07%
SELL_COST = 0.002  # 卖出：佣金0.03% + 印花税0.1% + 冲击成本0.07%
BLATERAL_COST = BUY_COST + SELL_COST


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
    except Exception as exc:
        print(f"  ⚠️ daily_basic 合并失败: {exc}（继续使用 daily 行情数据）")
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
    factors['close'] = c  # 保留收盘价用于计算次日收益
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
    # 训练目标
    for p in [5, 10, 20]:
        factors[f'future_return_{p}d'] = c.shift(-p) / c - 1
    # 回测用次日收益
    factors['next_day_return'] = c.shift(-1) / c - 1
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
    n = len(X)
    split = int(n * 0.8)
    train_data = lgb.Dataset(X[:split], y[:split])
    valid_data = lgb.Dataset(X[split:], y[split:])
    model = lgb.train(params, train_data, num_boost_round=500,
                      valid_sets=[valid_data], valid_names=['valid'],
                      callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)])
    return model


def walk_forward_longonly(df, feature_cols, train_label_col, 
                          train_window=120, retrain_freq=5, top_pct=0.2):
    """
    Walk-forward纯多头回测（每周调仓）
    - 只做多Top20%，不做空
    - 计入交易成本
    """
    dates = sorted(df['trade_date'].unique())
    results = []
    print(f"Walk-forward: 总交易日{len(dates)}, 训练窗口{train_window}, 重训频率{retrain_freq}")
    
    prev_holdings = set()  # 上周持仓
    
    for i in range(train_window, len(dates), retrain_freq):
        train_dates = dates[i-train_window:i]
        test_dates = dates[i:min(i+retrain_freq, len(dates))]
        
        train_df = df[df['trade_date'].isin(train_dates)].dropna()
        test_df = df[df['trade_date'].isin(test_dates)].dropna()
        
        if len(train_df) < 300 or len(test_df) < 30:
            continue
        
        model = train_model(train_df, feature_cols, train_label_col)
        
        for test_date in test_dates:
            day_df = test_df[test_df['trade_date'] == test_date].copy()
            if len(day_df) < 10:
                continue
            
            day_df['pred_score'] = model.predict(day_df[feature_cols].values)
            day_df['daily_rank'] = day_df['pred_score'].rank(pct=True)
            
            # 选出Top20%
            top_mask = day_df['daily_rank'] >= (1 - top_pct)
            top_stocks = set(day_df.loc[top_mask, 'symbol'].tolist())
            
            # 计算换手率
            if len(prev_holdings) > 0:
                kept = len(prev_holdings & top_stocks)
                turnover = 1 - kept / len(prev_holdings) if len(prev_holdings) > 0 else 1.0
            else:
                turnover = 1.0
            
            # 计算组合次日收益（等权）
            portfolio_return = day_df.loc[top_mask, 'next_day_return'].mean()
            
            # 扣除交易成本
            # 假设每次调仓的换手率都产生双边成本
            cost = turnover * BLATERAL_COST
            net_return = portfolio_return - cost
            
            results.append({
                'trade_date': test_date,
                'gross_return': portfolio_return,
                'net_return': net_return,
                'turnover': turnover,
                'cost': cost,
                'n_stocks': top_mask.sum(),
                'n_kept': kept if len(prev_holdings) > 0 else 0,
            })
            
            prev_holdings = top_stocks
        
        if len(results) > 0 and len(results) % 50 == 0:
            print(f"  已处理 {len(results)} 个交易日, 当前: {test_dates[-1]}")
    
    return pd.DataFrame(results) if results else None


def calc_metrics(r):
    r = r.dropna()
    if len(r) == 0:
        return {}
    cum = (1 + r).cumprod()
    total = cum.iloc[-1] - 1
    annual = (1 + total) ** (252 / len(r)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = annual / vol if vol > 0 else 0
    rm = cum.cummax()
    dd = (cum - rm) / rm
    max_dd = dd.min()
    calmar = annual / abs(max_dd) if max_dd != 0 else 0
    return {
        'total': total, 'annual': annual, 'sharpe': sharpe,
        'max_dd': max_dd, 'calmar': calmar, 'win': (r > 0).mean(), 'n': len(r),
        'avg_daily': r.mean(), 'volatility': vol,
    }


def plot_results(df, period, path):
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    dates = df['trade_date']
    
    # 累计收益
    cum_gross = (1 + df['gross_return']).cumprod() - 1
    cum_net = (1 + df['net_return']).cumprod() - 1
    axes[0,0].plot(dates, cum_gross, label='毛收益（未扣成本）', lw=2)
    axes[0,0].plot(dates, cum_net, label='净收益（扣成本）', lw=2)
    axes[0,0].set_title(f'累计收益 ({period}日模型, 纯多头, 每周调仓)')
    axes[0,0].legend(); axes[0,0].grid(True, alpha=0.3)
    
    # 交易成本累积
    axes[0,1].plot(dates, df['cost'].cumsum(), label='累积交易成本', color='red')
    axes[0,1].set_title('累积交易成本')
    axes[0,1].legend(); axes[0,1].grid(True, alpha=0.3)
    
    # 日收益分布
    axes[1,0].hist(df['gross_return'].dropna(), bins=30, alpha=0.6, label='毛收益')
    axes[1,0].hist(df['net_return'].dropna(), bins=30, alpha=0.6, label='净收益')
    axes[1,0].set_title('日收益分布'); axes[1,0].legend()
    
    # 换手率
    axes[1,1].plot(dates, df['turnover'], label='换手率', color='orange')
    axes[1,1].axhline(y=df['turnover'].mean(), color='red', ls='--', label=f'平均:{df["turnover"].mean():.1%}')
    axes[1,1].set_title('换手率'); axes[1,1].legend(); axes[1,1].grid(True, alpha=0.3)
    
    plt.tight_layout(); plt.savefig(path, dpi=150, bbox_inches='tight'); plt.close()
    print(f"图: {path}")


def main():
    print("=" * 70)
    print("跨截面Rank + 纯多头 + 每周调仓 + 交易成本")
    print(f"交易成本: 买入{BUY_COST*100:.2f}% + 卖出{SELL_COST*100:.2f}% = 双边{BLATERAL_COST*100:.2f}%")
    print("=" * 70)
    
    pro = init_tushare()
    symbols_df = pd.read_csv(os.path.join(OUTPUT_DIR, 'kcb50_symbols.csv'))
    symbols = symbols_df['symbol'].tolist()
    print(f"科创板50成分股: {len(symbols)}只")
    
    # 下载/加载数据
    print("\n加载数据...")
    all_data = []
    for symbol in symbols:
        df = download_stock(pro, symbol)
        if df is not None and len(df) > 100:
            df['symbol'] = symbol
            all_data.append(df)
            print(f"  {symbol}: {len(df)}条 ✓")
        else:
            print(f"  {symbol}: 无数据 ✗")
    
    if len(all_data) < 20:
        print("数据不足，退出")
        return
    
    combined = pd.concat(all_data, ignore_index=True)
    print(f"\n合并: {len(combined)}行, {combined['symbol'].nunique()}只股票")
    
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
    feature_cols = [c for c in factor_df.columns if c not in [
        'trade_date', 'symbol', 'close', 'future_return_5d', 'future_return_10d',
        'future_return_20d', 'next_day_return']]
    factor_df = cs_normalize(factor_df, feature_cols)
    print(f"因子: {len(feature_cols)}, 样本: {len(factor_df)}")
    
    # Walk-forward回测
    for period in [5, 10, 20]:
        train_label = f'future_return_{period}d'
        print(f"\n{'='*50}\n{period}日模型 - 纯多头Walk-forward\n{'='*50}")
        
        res = walk_forward_longonly(factor_df, feature_cols, train_label,
                                    train_window=120, retrain_freq=5, top_pct=0.2)
        if res is None or len(res) == 0:
            print("空结果"); continue
        
        m_gross = calc_metrics(res['gross_return'])
        m_net = calc_metrics(res['net_return'])
        
        print(f"\n[毛收益] 总:{m_gross['total']*100:.1f}% 年化:{m_gross['annual']*100:.1f}% "
              f"夏普:{m_gross['sharpe']:.2f} 回撤:{m_gross['max_dd']*100:.1f}% "
              f"Calmar:{m_gross['calmar']:.2f} 胜率:{m_gross['win']*100:.1f}% 天数:{m_gross['n']}")
        print(f"[净收益] 总:{m_net['total']*100:.1f}% 年化:{m_net['annual']*100:.1f}% "
              f"夏普:{m_net['sharpe']:.2f} 回撤:{m_net['max_dd']*100:.1f}% "
              f"Calmar:{m_net['calmar']:.2f} 胜率:{m_net['win']*100:.1f}% "
              f"平均换手:{res['turnover'].mean():.1%} 总成本:{res['cost'].sum()*100:.1f}%")
        
        plot_results(res, period, os.path.join(OUTPUT_DIR, f'cs_longonly_{period}d.png'))
    
    print("\n" + "=" * 70 + "\n纯多头回测完成!\n" + "=" * 70)


if __name__ == '__main__':
    main()
