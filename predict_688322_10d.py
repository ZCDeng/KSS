#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
688322 未来10日走势预测
使用LightGBM 10日模型 + 最新数据
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
import os, warnings
import tushare as ts
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


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


def download_stock(pro, symbol, start='20230101', end=None):
    if end is None:
        from datetime import datetime
        end = datetime.now().strftime('%Y%m%d')
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
    factors['boll_position'] = (c - ma20) / (2 * std20 + 1e-8)
    
    factors['amplitude'] = (h - l) / c
    factors['body_ratio'] = (c - o) / c
    factors['upper_shadow'] = (h - c.clip(lower=o)) / c
    factors['lower_shadow'] = (c.clip(upper=o) - l) / c
    factors['price_vol_corr_20d'] = c.pct_change().rolling(20).corr(v.pct_change())
    
    if 'pe' in df.columns:
        if df['pe'].notna().sum() / len(df) > 0.5:
            factors['pe'] = df['pe']
        if df['pb'].notna().sum() / len(df) > 0.5:
            factors['pb'] = df['pb']
    if 'total_mv' in df.columns:
        factors['log_mv'] = np.log(df['total_mv'] + 1)
    
    return factors


def predict():
    print("=" * 60)
    print("688322 未来10日走势预测")
    print("=" * 60)
    
    pro = init_tushare()
    
    # 1. 下载数据
    print("\n【1】加载最新数据...")
    df = download_stock(pro, '688322', start='20230101')
    if df is None or len(df) < 100:
        print("数据不足")
        return
    
    latest_date = df['trade_date'].max()
    latest_close = df[df['trade_date'] == latest_date]['close'].values[0]
    print(f"  最新日期: {latest_date.strftime('%Y-%m-%d')}")
    print(f"  最新收盘价: {latest_close:.2f}")
    print(f"  历史数据: {len(df)}天")
    
    # 2. 生成因子
    print("\n【2】生成因子...")
    factors = generate_factors(df)
    feature_cols = [c for c in factors.columns if c not in [
        'trade_date', 'close', 'future_return_5d', 'future_return_10d',
        'future_return_20d', 'next_day_return']]
    
    # 3. 计算未来10日收益作为训练标签（用于训练）
    factors['future_return_10d'] = df['close'].shift(-10) / df['close'] - 1
    
    # 4. 训练10日模型
    print("\n【3】训练LightGBM 10日模型...")
    train_df = factors.dropna()
    X = train_df[feature_cols].values
    y = train_df['future_return_10d'].values
    
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
    
    # 5. 用最新数据预测
    print("\n【4】预测未来10日走势...")
    latest_factors = factors.iloc[-1:][feature_cols].values
    pred_return = model.predict(latest_factors)[0]
    
    pred_price = latest_close * (1 + pred_return)
    
    # 6. 输出当前关键指标状态
    print("\n【当前关键指标状态】")
    latest = factors.iloc[-1]
    print(f"  PE: {latest.get('pe', 'N/A')}")
    print(f"  PB: {latest.get('pb', 'N/A')}")
    print(f"  60日波动率: {latest['volatility_60d']:.4f}")
    print(f"  20日波动率: {latest['volatility_20d']:.4f}")
    print(f"  RSI(14): {latest['rsi_14']:.1f}")
    print(f"  MACD柱状图: {latest['macd_hist']:.4f}")
    print(f"  距20日高点: {latest['close_to_high_20d']:.2%}")
    print(f"  距20日低点: {latest['close_to_low_20d']:.2%}")
    print(f"  5日动量: {latest['mom_5d']:.2%}")
    print(f"  10日动量: {latest['mom_10d']:.2%}")
    
    # 7. 预测结果
    print("\n" + "=" * 60)
    print("【预测结果】")
    print("=" * 60)
    print(f"\n  预测日期: {latest_date.strftime('%Y-%m-%d')} + 10个交易日")
    print(f"  当前价格: {latest_close:.2f}")
    print(f"  预测价格: {pred_price:.2f}")
    print(f"  预测涨跌: {pred_return*100:+.2f}%")
    
    # 判断方向
    if pred_return > 0.05:
        trend = "📈 强势上涨"
    elif pred_return > 0.02:
        trend = "📈 温和上涨"
    elif pred_return > -0.02:
        trend = "➡️ 震荡整理"
    elif pred_return > -0.05:
        trend = "📉 温和下跌"
    else:
        trend = "📉 弱势下跌"
    
    print(f"  走势判断: {trend}")
    
    # 8. 信号解读
    print("\n【信号解读】")
    signals = []
    
    if 'pe' in factors.columns and latest.get('pe', 0) > 0:
        pe = latest['pe']
        pe_hist = factors['pe'].quantile([0.25, 0.5, 0.75]).values
        if pe < pe_hist[0]:
            signals.append("  ✅ PE处于历史低位，估值有修复空间")
        elif pe > pe_hist[2]:
            signals.append("  ⚠️ PE处于历史高位，估值承压")
    
    if latest['rsi_14'] < 30:
        signals.append("  ✅ RSI超卖，短期有反弹需求")
    elif latest['rsi_14'] > 70:
        signals.append("  ⚠️ RSI超买，短期有回调压力")
    
    if latest['macd_hist'] > 0 and factors['macd_hist'].iloc[-2] <= 0:
        signals.append("  ✅ MACD金叉，动能转强")
    elif latest['macd_hist'] < 0 and factors['macd_hist'].iloc[-2] >= 0:
        signals.append("  ⚠️ MACD死叉，动能转弱")
    
    if latest['volatility_20d'] > factors['volatility_20d'].quantile(0.8):
        signals.append("  ⚠️ 波动率偏高，注意风险控制")
    
    if latest['mom_5d'] > 0.05:
        signals.append("  ✅ 短期动量强劲")
    elif latest['mom_5d'] < -0.05:
        signals.append("  ⚠️ 短期动量偏弱")
    
    for s in signals:
        print(s)
    
    if not signals:
        print("  当前无明显技术信号")
    
    print("\n" + "=" * 60)
    print("⚠️  免责声明：以上预测基于历史数据训练的单股模型，")
    print("    仅供参考，不构成投资建议。股市有风险，投资需谨慎。")
    print("=" * 60)


if __name__ == '__main__':
    predict()
