#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扩大股票池：科创板50 + 创业板50 = 100只成分股
纯多头 + 每周调仓 + 交易成本 + Walk-forward

对比：仅科创板50 vs 科创板50+创业板50
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

BUY_COST = 0.001
SELL_COST = 0.002
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
    symbol = str(symbol)
    cache = os.path.join(OUTPUT_DIR, f'cs_data_{symbol}.csv')
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
    exchange = 'SH' if symbol.startswith('688') or symbol.startswith('689') else 'SZ'
    df = pro.daily(ts_code=f'{symbol}.{exchange}', start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return None
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    try:
        basic = pro.daily_basic(ts_code=f'{symbol}.{exchange}', start_date=start, end_date=end)
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


def walk_forward(df, feature_cols, train_label, test_label='next_day_return',
                 train_window=120, retrain_freq=5, top_pct=0.2):
    dates = sorted(df['trade_date'].unique())
    results = []
    prev_holdings = set()
    for i in range(train_window, len(dates), retrain_freq):
        train_dates = dates[i-train_window:i]
        test_dates = dates[i:min(i+retrain_freq, len(dates))]
        train_df = df[df['trade_date'].isin(train_dates)].dropna()
        test_df = df[df['trade_date'].isin(test_dates)].dropna()
        if len(train_df) < 500 or len(test_df) < 50:
            continue
        model = train_model(train_df, feature_cols, train_label)
        for test_date in test_dates:
            day_df = test_df[test_df['trade_date'] == test_date].copy()
            if len(day_df) < 20:
                continue
            day_df['pred_score'] = model.predict(day_df[feature_cols].values)
            day_df['daily_rank'] = day_df['pred_score'].rank(pct=True)
            top_mask = day_df['daily_rank'] >= (1 - top_pct)
            top_stocks = set(day_df.loc[top_mask, 'symbol'].tolist())
            if len(prev_holdings) > 0:
                kept = len(prev_holdings & top_stocks)
                turnover = 1 - kept / len(prev_holdings)
            else:
                turnover = 1.0
            portfolio_return = day_df.loc[top_mask, test_label].mean()
            cost = turnover * BLATERAL_COST
            net_return = portfolio_return - cost
            results.append({
                'trade_date': test_date,
                'gross_return': portfolio_return,
                'net_return': net_return,
                'turnover': turnover,
                'cost': cost,
                'n_stocks': top_mask.sum(),
            })
            prev_holdings = top_stocks
        if len(results) > 0 and len(results) % 100 == 0:
            print(f"  已处理 {len(results)} 个交易日, 当前: {test_dates[-1]}")
    return pd.DataFrame(results) if results else None


def calc_metrics(r):
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
            'max_dd': max_dd, 'calmar': calmar, 'win': (r > 0).mean(), 'n': len(r),
            'avg_daily': r.mean(), 'volatility': vol}


def run_backtest(all_data, feature_cols, label_col, name_suffix=''):
    """运行回测并返回结果"""
    res = walk_forward(all_data, feature_cols, label_col,
                       train_window=120, retrain_freq=5, top_pct=0.2)
    if res is None or len(res) == 0:
        return None
    m_gross = calc_metrics(res['gross_return'])
    m_net = calc_metrics(res['net_return'])
    return {
        'df': res,
        'gross': m_gross,
        'net': m_net,
        'suffix': name_suffix,
    }


def plot_comparison(results_50, results_100, period, path):
    """对比图：科创板50 vs 100只"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # 累计收益对比
    ax = axes[0, 0]
    for r, label in [(results_50, '科创板50'), (results_100, '科创50+创业板50')]:
        dates = r['df']['trade_date']
        cum_net = (1 + r['df']['net_return']).cumprod() - 1
        ax.plot(dates, cum_net, label=label, lw=2)
    ax.set_title(f'净收益对比 ({period}日模型)')
    ax.legend(); ax.grid(True, alpha=0.3)
    
    # 关键指标对比
    ax = axes[0, 1]
    metrics = ['sharpe', 'calmar', 'win']
    x = np.arange(len(metrics))
    width = 0.35
    for i, (r, label) in enumerate([(results_50, '科创板50'), (results_100, '科创50+创业板50')]):
        vals = [r['net'][m] for m in metrics]
        ax.bar(x + i*width, vals, width, label=label)
    ax.set_xticks(x + width/2)
    ax.set_xticklabels(metrics)
    ax.set_title('关键指标对比（净收益）')
    ax.legend(); ax.grid(True, alpha=0.3)
    
    # 日收益分布
    ax = axes[1, 0]
    for r, label in [(results_50, '科创板50'), (results_100, '科创50+创业板50')]:
        ax.hist(r['df']['net_return'].dropna(), bins=30, alpha=0.6, label=label)
    ax.set_title('日收益分布（净收益）')
    ax.legend()
    
    # 换手率对比
    ax = axes[1, 1]
    for r, label in [(results_50, '科创板50'), (results_100, '科创50+创业板50')]:
        ax.plot(r['df']['trade_date'], r['df']['turnover'], alpha=0.7, label=label)
    ax.axhline(results_50['df']['turnover'].mean(), color='blue', ls='--', alpha=0.5)
    ax.axhline(results_100['df']['turnover'].mean(), color='orange', ls='--', alpha=0.5)
    ax.set_title('换手率对比')
    ax.legend(); ax.grid(True, alpha=0.3)
    
    plt.tight_layout(); plt.savefig(path, dpi=150, bbox_inches='tight'); plt.close()
    print(f"对比图: {path}")


def main():
    print("=" * 70)
    print("扩大股票池：科创板50 + 创业板50 = 100只成分股")
    print("=" * 70)
    
    pro = init_tushare()
    
    # 读取成分股
    kcb = pd.read_csv(os.path.join(OUTPUT_DIR, 'kcb50_symbols.csv'))['symbol'].tolist()
    cyb = pd.read_csv(os.path.join(OUTPUT_DIR, 'cyb50_symbols.csv'))['symbol'].tolist()
    
    # 去重（防止有重叠）
    all_symbols = list(dict.fromkeys(kcb + cyb))
    print(f"科创板50: {len(kcb)}只, 创业板50: {len(cyb)}只, 合并后: {len(all_symbols)}只")
    
    # 下载数据
    print("\n加载数据...")
    all_data = []
    for symbol in all_symbols:
        df = download_stock(pro, symbol)
        if df is not None and len(df) > 100:
            df['symbol'] = symbol
            all_data.append(df)
            print(f"  {symbol}: {len(df)}条 ✓")
        else:
            print(f"  {symbol}: 无数据 ✗")
    
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
    
    feature_cols = [c for c in factor_df.columns if c not in [
        'trade_date', 'symbol', 'close', 'future_return_5d', 'future_return_10d',
        'future_return_20d', 'next_day_return']]
    factor_df = cs_normalize(factor_df, feature_cols)
    print(f"因子: {len(feature_cols)}, 样本: {len(factor_df)}")
    
    # 拆分科创板50和全部100只的数据
    kcb_mask = factor_df['symbol'].isin(kcb)
    factor_kcb50 = factor_df[kcb_mask].copy()
    factor_all = factor_df.copy()
    
    # 回测对比
    for period in [10, 20]:  # 只测10日和20日（5日表现差）
        label = f'future_return_{period}d'
        print(f"\n{'='*60}\n{period}日模型 - 科创板50 vs 100只对比\n{'='*60}")
        
        # 科创板50回测
        print("\n[科创板50回测...]")
        res_50 = run_backtest(factor_kcb50, feature_cols, label, '_kcb50')
        
        # 100只回测
        print("\n[100只回测...]")
        res_100 = run_backtest(factor_all, feature_cols, label, '_100')
        
        if res_50 and res_100:
            # 打印结果
            for name, res in [('科创板50', res_50), ('科创50+创业板50', res_100)]:
                m = res['net']
                print(f"\n[{name}]")
                print(f"  净收益: {m['total']*100:.1f}% 年化:{m['annual']*100:.1f}% "
                      f"夏普:{m['sharpe']:.2f} 回撤:{m['max_dd']*100:.1f}% "
                      f"Calmar:{m['calmar']:.2f} 胜率:{m['win']*100:.1f}% "
                      f"换手:{res['df']['turnover'].mean():.1%} 成本:{res['df']['cost'].sum()*100:.1f}%")
            
            # 画图
            plot_comparison(res_50, res_100, period,
                           os.path.join(OUTPUT_DIR, f'cs_compare_{period}d.png'))
    
    print("\n" + "=" * 70 + "\n扩大股票池回测完成!\n" + "=" * 70)


if __name__ == '__main__':
    main()
