#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多指标共振回测 + 异动预测模型
支持: MACD, BBI, RSI, CCI, KDJ, BOLL, WR, 量比, 换手率, 成交量异动, 均线排列
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import os
import json

# ============ 配置 ============
STOCKS = {
    '688322': '奥比中光-UW',
    '688017': '绿的谐波'
}
INITIAL_CAPITAL = 1_000_000
TRANSACTION_COST = 0.001
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ============ 数据加载 ============
def load_data(symbol):
    """从Tushare CSV加载数据"""
    cache_file = os.path.join(OUTPUT_DIR, f'tushare_{symbol}.csv')
    df = pd.read_csv(cache_file)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 统一列名
    rename_map = {
        'trade_date': '日期',
        'open': '开盘',
        'high': '最高',
        'low': '最低',
        'close': '收盘',
        'pre_close': '昨收',
        'vol': '成交量',
        'amount': '成交额',
        'pct_chg': '涨跌幅',
    }
    df = df.rename(columns=rename_map)
    
    # 计算振幅
    df['振幅'] = (df['最高'] - df['最低']) / df['昨收'] * 100
    
    return df

# ============ 指标计算 ============
def calc_macd(df, fast=12, slow=26, signal=9):
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar

def calc_bbi(df):
    close = df['收盘']
    return (close.rolling(3).mean() + close.rolling(6).mean() + 
            close.rolling(12).mean() + close.rolling(24).mean()) / 4

def calc_rsi(df, period=14):
    close = df['收盘']
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_cci(df, period=14):
    tp = (df['最高'] + df['最低'] + df['收盘']) / 3
    ma_tp = tp.rolling(period).mean()
    md = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma_tp) / (0.015 * md)

def calc_kdj(df, n=9, m1=3, m2=3):
    low_list = df['最低'].rolling(window=n, min_periods=n).min()
    high_list = df['最高'].rolling(window=n, min_periods=n).max()
    rsv = (df['收盘'] - low_list) / (high_list - low_list) * 100
    K = rsv.ewm(alpha=1/m1, adjust=False).mean()
    D = K.ewm(alpha=1/m2, adjust=False).mean()
    J = 3 * K - 2 * D
    return K, D, J

def calc_boll(df, period=20, std_mult=2):
    close = df['收盘']
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower

def calc_wr(df, period=10):
    high = df['最高'].rolling(period).max()
    low = df['最低'].rolling(period).min()
    return (high - df['收盘']) / (high - low) * -100

def calc_ma_alignment(df):
    """均线多头排列评分"""
    ma5 = df['收盘'].rolling(5).mean()
    ma10 = df['收盘'].rolling(10).mean()
    ma20 = df['收盘'].rolling(20).mean()
    ma60 = df['收盘'].rolling(60).mean()
    
    score = 0
    score += (ma5 > ma10).astype(int)
    score += (ma10 > ma20).astype(int)
    score += (ma20 > ma60).astype(int)
    score += (df['收盘'] > ma5).astype(int)
    return score

def calc_volume_signals(df):
    """成交量异动信号"""
    vol = df['成交量']
    vol_ma5 = vol.rolling(5).mean()
    vol_ma10 = vol.rolling(10).mean()
    vol_ma20 = vol.rolling(20).mean()
    
    # 放量倍数
    vol_ratio_5 = vol / vol_ma5
    vol_ratio_10 = vol / vol_ma10
    vol_ratio_20 = vol / vol_ma20
    
    # 成交额趋势
    amount_ma5 = df['成交额'].rolling(5).mean()
    amount_trend = df['成交额'] / amount_ma5
    
    return vol_ratio_5, vol_ratio_10, vol_ratio_20, amount_trend

# ============ 计算所有指标 ============
def compute_all_indicators(df):
    """计算所有技术指标并添加到df"""
    df = df.copy()
    
    # MACD
    df['DIF'], df['DEA'], df['MACD_BAR'] = calc_macd(df)
    
    # BBI
    df['BBI'] = calc_bbi(df)
    
    # RSI
    df['RSI6'] = calc_rsi(df, 6)
    df['RSI12'] = calc_rsi(df, 12)
    df['RSI24'] = calc_rsi(df, 24)
    
    # CCI
    df['CCI14'] = calc_cci(df, 14)
    
    # KDJ
    df['K'], df['D'], df['J'] = calc_kdj(df)
    
    # BOLL
    df['BOLL_UP'], df['BOLL_MID'], df['BOLL_LOW'] = calc_boll(df)
    df['BOLL_WIDTH'] = (df['BOLL_UP'] - df['BOLL_LOW']) / df['BOLL_MID']
    df['BOLL_POSITION'] = (df['收盘'] - df['BOLL_LOW']) / (df['BOLL_UP'] - df['BOLL_LOW'])
    
    # WR
    df['WR10'] = calc_wr(df, 10)
    df['WR6'] = calc_wr(df, 6)
    
    # 均线
    df['MA5'] = df['收盘'].rolling(5).mean()
    df['MA10'] = df['收盘'].rolling(10).mean()
    df['MA20'] = df['收盘'].rolling(20).mean()
    df['MA60'] = df['收盘'].rolling(60).mean()
    df['MA_ALIGNMENT'] = calc_ma_alignment(df)
    
    # 成交量
    df['VOL_RATIO_5'], df['VOL_RATIO_10'], df['VOL_RATIO_20'], df['AMT_TREND'] = calc_volume_signals(df)
    
    # 价格动量
    df['MOM5'] = df['收盘'].pct_change(5)
    df['MOM10'] = df['收盘'].pct_change(10)
    df['MOM20'] = df['收盘'].pct_change(20)
    
    return df

# ============ 方向信号生成 ============
def generate_direction_signals(df):
    """每个指标给出方向信号: -1(看空), 0(中性), +1(看多)"""
    signals = pd.DataFrame(index=df.index)
    
    # MACD: DIF>DEA看多, 金叉+1, 死叉-1
    signals['macd_dir'] = np.where(df['DIF'] > df['DEA'], 1, -1)
    signals['macd_golden'] = (df['DIF'] > df['DEA']) & (df['DIF'].shift(1) <= df['DEA'].shift(1))
    signals['macd_death'] = (df['DIF'] < df['DEA']) & (df['DIF'].shift(1) >= df['DEA'].shift(1))
    
    # BBI: 收盘价>BBI看多
    signals['bbi_dir'] = np.where(df['收盘'] > df['BBI'], 1, -1)
    signals['bbi_cross_up'] = (df['收盘'] > df['BBI']) & (df['收盘'].shift(1) <= df['BBI'].shift(1))
    signals['bbi_cross_down'] = (df['收盘'] < df['BBI']) & (df['收盘'].shift(1) >= df['BBI'].shift(1))
    
    # RSI: >50看多, <50看空; 超卖(<30)强看多, 超买(>70)强看空
    signals['rsi_dir'] = np.where(df['RSI6'] > 50, 1, -1)
    signals['rsi_dir'] = np.where(df['RSI6'] < 30, 1, signals['rsi_dir'])  # 超卖反弹
    signals['rsi_dir'] = np.where(df['RSI6'] > 70, -1, signals['rsi_dir'])  # 超买回调
    signals['rsi_golden'] = (df['RSI6'] > df['RSI12']) & (df['RSI6'].shift(1) <= df['RSI12'].shift(1))
    
    # CCI: >0看多, <-100超卖反弹, >+100超买回落
    signals['cci_dir'] = np.where(df['CCI14'] > 0, 1, -1)
    signals['cci_dir'] = np.where(df['CCI14'] < -100, 1, signals['cci_dir'])
    signals['cci_dir'] = np.where(df['CCI14'] > 100, -1, signals['cci_dir'])
    
    # KDJ: J>0看多, J<-20强看多, J>100强看空
    signals['kdj_dir'] = np.where(df['J'] > 0, 1, -1)
    signals['kdj_dir'] = np.where(df['J'] < -20, 1, signals['kdj_dir'])
    signals['kdj_dir'] = np.where(df['J'] > 100, -1, signals['kdj_dir'])
    signals['kdj_golden'] = (df['K'] > df['D']) & (df['K'].shift(1) <= df['D'].shift(1))
    
    # BOLL: 收盘价在中轨上方看多, 突破上轨-1, 跌破下轨+1
    signals['boll_dir'] = np.where(df['收盘'] > df['BOLL_MID'], 1, -1)
    signals['boll_dir'] = np.where(df['收盘'] > df['BOLL_UP'], -1, signals['boll_dir'])
    signals['boll_dir'] = np.where(df['收盘'] < df['BOLL_LOW'], 1, signals['boll_dir'])
    
    # WR: >-20超买看空, <-80超卖看多
    signals['wr_dir'] = np.where(df['WR10'] < -80, 1, np.where(df['WR10'] > -20, -1, 0))
    
    # 均线排列: 0~4分, 转为方向信号
    signals['ma_dir'] = np.where(df['MA_ALIGNMENT'] >= 3, 1, np.where(df['MA_ALIGNMENT'] <= 1, -1, 0))
    
    # 量比: >1.5放量看多(需结合趋势), <0.5缩量观望
    signals['vr_dir'] = np.where(df['volume_ratio'] > 1.5, 1, np.where(df['volume_ratio'] < 0.5, -1, 0))
    
    # 换手率异动: 显著高于近期平均
    turnover_ma = df['turnover_rate'].rolling(20).mean()
    signals['to_dir'] = np.where(df['turnover_rate'] > turnover_ma * 1.5, 1, 0)
    
    # 成交量异动
    signals['vol_spike'] = np.where(df['VOL_RATIO_5'] > 2, 1, np.where(df['VOL_RATIO_5'] < 0.5, -1, 0))
    
    # 动量
    signals['mom_dir'] = np.where(df['MOM5'] > 0, 1, -1)
    
    return signals

# ============ 共振评分模型 ============
def calc_resonance_score(signals):
    """多指标共振评分: 综合所有方向信号的加权得分"""
    weights = {
        'macd_dir': 1.5,
        'bbi_dir': 1.0,
        'rsi_dir': 1.0,
        'cci_dir': 1.0,
        'kdj_dir': 1.0,
        'boll_dir': 0.8,
        'wr_dir': 0.8,
        'ma_dir': 1.2,
        'vr_dir': 0.5,
        'to_dir': 0.5,
        'vol_spike': 0.5,
        'mom_dir': 0.8,
    }
    
    score = pd.Series(0, index=signals.index)
    for col, w in weights.items():
        if col in signals.columns:
            score += signals[col].fillna(0) * w
    
    # 归一化到 -10 ~ +10
    max_possible = sum(abs(v) for v in weights.values())
    score = score / max_possible * 10
    return score

# ============ 异动检测 ============
def detect_anomaly(df, signals, score):
    """检测异动信号"""
    anomaly = pd.DataFrame(index=df.index)
    
    # 类型1: 多指标共振看多 (评分>5)
    anomaly['resonance_long'] = score > 5
    
    # 类型2: 多指标共振看空 (评分<-5)
    anomaly['resonance_short'] = score < -5
    
    # 类型3: 量价齐升 (放量+上涨+MACD金叉)
    anomaly['volume_price_up'] = (
        (df['volume_ratio'] > 1.5) & 
        (df['涨跌幅'] > 3) & 
        signals['macd_golden']
    )
    
    # 类型4: 缩量回调后放量突破 (量比从<0.5跳升至>1.5)
    anomaly['volume_breakout'] = (
        (df['volume_ratio'] > 1.8) & 
        (df['volume_ratio'].shift(1) < 1.0) &
        (df['涨跌幅'] > 2)
    )
    
    # 类型5: 超卖共振反弹 (RSI<30 + CCI<-100 + WR<-80 + 放量)
    anomaly['oversold_bounce'] = (
        (df['RSI6'] < 35) & 
        (df['CCI14'] < -80) & 
        (df['WR10'] < -80) &
        (df['volume_ratio'] > 1.0)
    )
    
    # 类型6: 超买共振回落 (RSI>70 + CCI>100 + WR>-20)
    anomaly['overbought_fall'] = (
        (df['RSI6'] > 70) & 
        (df['CCI14'] > 100) & 
        (df['WR10'] > -20)
    )
    
    # 类型7: BOLL带宽收窄后突破 (布林带收窄+放量突破上轨)
    boll_width_ma = df['BOLL_WIDTH'].rolling(20).mean()
    anomaly['boll_squeeze_break'] = (
        (df['BOLL_WIDTH'] < boll_width_ma * 0.8) &
        (df['收盘'] > df['BOLL_UP']) &
        (df['volume_ratio'] > 1.2)
    )
    
    # 类型8: 均线粘合后发散 (MA_ALIGNMENT从0~1跳升至4)
    anomaly['ma_burst'] = (
        (df['MA_ALIGNMENT'] >= 4) &
        (df['MA_ALIGNMENT'].shift(3) <= 1)
    )
    
    return anomaly

# ============ 回测引擎 ============
def backtest(df, entry_mask, exit_mask, name):
    """通用回测"""
    trades = []
    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    entry_date = None
    
    for i, row in df.iterrows():
        price = row['收盘']
        date = row['日期']
        
        if position == 0 and entry_mask.iloc[i]:
            shares = int(capital / (price * (1 + TRANSACTION_COST)))
            if shares > 0:
                cost = shares * price * (1 + TRANSACTION_COST)
                if cost <= capital:
                    position = shares
                    entry_price = price
                    entry_date = date
                    capital -= cost
        
        elif position > 0 and exit_mask.iloc[i]:
            revenue = position * price * (1 - TRANSACTION_COST)
            profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
            return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
            
            trades.append({
                'entry_date': entry_date,
                'exit_date': date,
                'entry_price': entry_price,
                'exit_price': price,
                'profit': profit,
                'return_pct': return_pct,
                'hold_days': (date - entry_date).days
            })
            
            capital += revenue
            position = 0
            entry_price = 0
            entry_date = None
    
    if position > 0:
        last_row = df.iloc[-1]
        price = last_row['收盘']
        date = last_row['日期']
        revenue = position * price * (1 - TRANSACTION_COST)
        profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
        return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
        
        trades.append({
            'entry_date': entry_date,
            'exit_date': date,
            'entry_price': entry_price,
            'exit_price': price,
            'profit': profit,
            'return_pct': return_pct,
            'hold_days': (date - entry_date).days
        })
        capital += revenue
    
    trades_df = pd.DataFrame(trades)
    return {
        'name': name,
        'trades': trades_df,
        'final_capital': capital,
        'total_return': (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL
    }

def build_equity_curve(df, result):
    """构建权益曲线"""
    trades = result['trades'].to_dict('records') if len(result['trades']) > 0 else []
    equity = []
    cash = INITIAL_CAPITAL
    position = 0
    trade_idx = 0
    
    for i, row in df.iterrows():
        price = row['收盘']
        date = row['日期']
        
        if trade_idx < len(trades):
            t = trades[trade_idx]
            if t['entry_date'] == date and position == 0:
                shares = int(cash / (price * (1 + TRANSACTION_COST)))
                if shares > 0:
                    cost = shares * price * (1 + TRANSACTION_COST)
                    position = shares
                    cash -= cost
            if t['exit_date'] == date and position > 0:
                revenue = position * price * (1 - TRANSACTION_COST)
                cash += revenue
                position = 0
                trade_idx += 1
        
        equity.append(cash + position * price)
    
    return pd.DataFrame({'date': df['日期'], 'equity': equity})

# ============ 统计分析 ============
def analyze_result(result, benchmark_return):
    trades = result['trades']
    if len(trades) == 0:
        return {
            '策略': result['name'], '交易次数': 0, '胜率': '0%', 
            '平均收益': '0%', '平均亏损': '0%', '盈亏比': '0',
            '总收益': f"{result['total_return']:.2%}", '最大单笔盈': '0%',
            '最大单笔亏': '0%', '平均持仓': '0天', '超额收益': f"{result['total_return']-benchmark_return:.2%}"
        }
    
    wins = trades[trades['return_pct'] > 0]
    losses = trades[trades['return_pct'] <= 0]
    win_rate = len(wins) / len(trades)
    avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
    avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0
    pf = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    
    return {
        '策略': result['name'],
        '交易次数': len(trades),
        '胜率': f"{win_rate:.1%}",
        '平均收益': f"{avg_win:.2%}",
        '平均亏损': f"{avg_loss:.2%}",
        '盈亏比': f"{pf:.2f}",
        '总收益': f"{result['total_return']:.2%}",
        '最大单笔盈': f"{trades['return_pct'].max():.2%}",
        '最大单笔亏': f"{trades['return_pct'].min():.2%}",
        '平均持仓': f"{trades['hold_days'].mean():.1f}天",
        '超额收益': f"{result['total_return']-benchmark_return:.2%}"
    }

# ============ 信号后收益分析（预测模型验证） ============
def analyze_signal_prediction(df, signal_mask, signal_name):
    """分析信号作为预测模型的效果"""
    periods = [1, 3, 5, 10, 20]
    results = {}
    
    for p in periods:
        returns = []
        up_correct = 0  # 预测上涨且实际上涨
        down_correct = 0  # 预测下跌且实际下跌
        total_up_pred = 0
        total_down_pred = 0
        
        for i in range(len(df) - p):
            if signal_mask.iloc[i]:
                future_return = (df.iloc[i + p]['收盘'] - df.iloc[i]['收盘']) / df.iloc[i]['收盘']
                returns.append(future_return)
                
                # 假设信号是看多
                if future_return > 0:
                    up_correct += 1
                total_up_pred += 1
        
        if returns:
            returns = np.array(returns)
            results[f'{p}日'] = {
                '信号名': signal_name,
                '平均收益': f"{returns.mean():.2%}",
                '胜率': f"{(returns > 0).mean():.1%}",
                '最大收益': f"{returns.max():.2%}",
                '最大亏损': f"{returns.min():.2%}",
                '信号次数': len(returns),
                '上涨预测准确率': f"{up_correct/total_up_pred:.1%}" if total_up_pred > 0 else "N/A"
            }
    
    return results

# ============ 特征重要性分析 ============
def feature_importance_analysis(df, signals, score, periods=[5, 10, 20]):
    """分析各指标与未来收益的相关性"""
    importance = []
    
    feature_cols = ['macd_dir', 'bbi_dir', 'rsi_dir', 'cci_dir', 'kdj_dir', 
                    'boll_dir', 'wr_dir', 'ma_dir', 'vr_dir', 'to_dir', 
                    'vol_spike', 'mom_dir']
    
    for p in periods:
        future_return = df['收盘'].shift(-p) / df['收盘'] - 1
        
        for col in feature_cols:
            if col in signals.columns:
                valid = signals[col].notna() & future_return.notna()
                if valid.sum() > 10:
                    corr = np.corrcoef(signals[col][valid], future_return[valid])[0, 1]
                    if not np.isnan(corr):
                        importance.append({
                            '特征': col,
                            f'{p}日收益相关性': corr,
                        })
    
    # 汇总平均相关性
    imp_df = pd.DataFrame(importance)
    if len(imp_df) > 0:
        avg_corr = imp_df.groupby('特征').mean().abs().sort_values(by='5日收益相关性', ascending=False)
        return avg_corr
    return pd.DataFrame()

# ============ 可视化 ============
def plot_multi_indicator(df, signals, score, anomaly, symbol, name, results, output_dir):
    fig = plt.figure(figsize=(20, 28))
    gs = fig.add_gridspec(7, 2, hspace=0.3, wspace=0.2)
    
    # 1. 价格+买卖点
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(df['日期'], df['收盘'], color='black', linewidth=1, label='收盘价')
    ax1.plot(df['日期'], df['BBI'], color='blue', linewidth=0.8, alpha=0.6, label='BBI')
    ax1.plot(df['日期'], df['BOLL_UP'], color='gray', linewidth=0.5, alpha=0.5, linestyle='--')
    ax1.plot(df['日期'], df['BOLL_LOW'], color='gray', linewidth=0.5, alpha=0.5, linestyle='--')
    
    # 标记异动信号
    anomaly_dates = df['日期'][anomaly['resonance_long']]
    anomaly_prices = df['收盘'][anomaly['resonance_long']]
    ax1.scatter(anomaly_dates, anomaly_prices, marker='*', color='red', s=200, 
                label='共振看多异动', zorder=5, alpha=0.8)
    
    vp_dates = df['日期'][anomaly['volume_price_up']]
    vp_prices = df['收盘'][anomaly['volume_price_up']]
    ax1.scatter(vp_dates, vp_prices, marker='^', color='orange', s=100, 
                label='量价齐升', zorder=5)
    
    ax1.set_title(f'{symbol} {name} - 价格走势与异动信号', fontsize=14)
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)
    
    # 2. MACD
    ax2 = fig.add_subplot(gs[1, :])
    ax2.plot(df['日期'], df['DIF'], label='DIF', color='orange', linewidth=1)
    ax2.plot(df['日期'], df['DEA'], label='DEA', color='purple', linewidth=1)
    ax2.bar(df['日期'], df['MACD_BAR'], color=['red' if v > 0 else 'green' for v in df['MACD_BAR']], alpha=0.5)
    ax2.axhline(y=0, color='black', linewidth=0.5)
    ax2.set_title('MACD', fontsize=12)
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)
    
    # 3. RSI + CCI
    ax3 = fig.add_subplot(gs[2, :])
    ax3.plot(df['日期'], df['RSI6'], label='RSI6', color='red', linewidth=1)
    ax3.plot(df['日期'], df['RSI12'], label='RSI12', color='orange', linewidth=0.8, alpha=0.7)
    ax3.plot(df['日期'], df['CCI14']/10, label='CCI14/10', color='blue', linewidth=1)
    ax3.axhline(y=70, color='red', linestyle='--', alpha=0.5, label='超买')
    ax3.axhline(y=30, color='green', linestyle='--', alpha=0.5, label='超卖')
    ax3.axhline(y=0, color='black', linewidth=0.5)
    ax3.set_title('RSI + CCI', fontsize=12)
    ax3.legend(loc='upper left', fontsize=8)
    ax3.grid(True, alpha=0.3)
    
    # 4. KDJ
    ax4 = fig.add_subplot(gs[3, :])
    ax4.plot(df['日期'], df['K'], label='K', color='blue', linewidth=1)
    ax4.plot(df['日期'], df['D'], label='D', color='orange', linewidth=1)
    ax4.plot(df['日期'], df['J'], label='J', color='red', linewidth=0.8, alpha=0.7)
    ax4.axhline(y=80, color='red', linestyle='--', alpha=0.5)
    ax4.axhline(y=20, color='green', linestyle='--', alpha=0.5)
    ax4.set_title('KDJ', fontsize=12)
    ax4.legend(loc='upper left', fontsize=8)
    ax4.grid(True, alpha=0.3)
    
    # 5. 量比 + 换手率
    ax5 = fig.add_subplot(gs[4, :])
    ax5.bar(df['日期'], df['volume_ratio'], color='steelblue', alpha=0.6, label='量比')
    ax5_twin = ax5.twinx()
    ax5_twin.plot(df['日期'], df['turnover_rate'], color='red', linewidth=1, label='换手率%')
    ax5.axhline(y=1.5, color='orange', linestyle='--', alpha=0.5, label='放量线')
    ax5.axhline(y=0.5, color='green', linestyle='--', alpha=0.5, label='缩量线')
    ax5.set_title('量比 + 换手率', fontsize=12)
    ax5.legend(loc='upper left', fontsize=8)
    ax5_twin.legend(loc='upper right', fontsize=8)
    ax5.grid(True, alpha=0.3)
    
    # 6. 共振评分
    ax6 = fig.add_subplot(gs[5, :])
    ax6.fill_between(df['日期'], -10, 10, alpha=0.1, color='gray')
    ax6.fill_between(df['日期'], score, 0, where=score > 0, alpha=0.5, color='red', label='看多共振')
    ax6.fill_between(df['日期'], score, 0, where=score < 0, alpha=0.5, color='green', label='看空共振')
    ax6.plot(df['日期'], score, color='black', linewidth=1)
    ax6.axhline(y=5, color='red', linestyle='--', alpha=0.7, label='看多阈值')
    ax6.axhline(y=-5, color='green', linestyle='--', alpha=0.7, label='看空阈值')
    ax6.axhline(y=0, color='black', linewidth=0.5)
    ax6.set_ylim(-10, 10)
    ax6.set_title('多指标共振评分', fontsize=12)
    ax6.legend(loc='upper left', fontsize=8)
    ax6.grid(True, alpha=0.3)
    
    # 7. 收益曲线对比
    ax7 = fig.add_subplot(gs[6, :])
    colors = plt.cm.tab10(np.linspace(0, 1, len(results)))
    for i, (key, res) in enumerate(results.items()):
        if 'equity' in res and len(res['equity']) > 0:
            ax7.plot(res['equity']['date'], res['equity']['equity'], 
                    label=key, linewidth=1.5, color=colors[i])
    
    buy_hold = INITIAL_CAPITAL * (df['收盘'] / df['收盘'].iloc[0])
    ax7.plot(df['日期'], buy_hold, label='买入持有', color='gray', linestyle='--', linewidth=1)
    ax7.axhline(y=INITIAL_CAPITAL, color='black', linewidth=0.5)
    ax7.set_title('策略收益曲线对比', fontsize=12)
    ax7.legend(loc='upper left', fontsize=8, ncol=3)
    ax7.grid(True, alpha=0.3)
    
    plt.savefig(os.path.join(output_dir, f'{symbol}_multi_indicator.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  多指标分析图已保存: {symbol}_multi_indicator.png")

# ============ 主流程 ============
def run_stock(symbol, name):
    print(f"\n{'='*70}")
    print(f"股票: {symbol} {name}")
    print(f"{'='*70}")
    
    df = load_data(symbol)
    df = compute_all_indicators(df)
    signals = generate_direction_signals(df)
    score = calc_resonance_score(signals)
    anomaly = detect_anomaly(df, signals, score)
    
    benchmark_return = (df['收盘'].iloc[-1] - df['收盘'].iloc[0]) / df['收盘'].iloc[0]
    print(f"两年涨跌幅: {benchmark_return:.2%}")
    
    # 回测多种策略
    results = {}
    
    # 1. MACD金叉死叉
    res = backtest(df, signals['macd_golden'], signals['macd_death'], 'MACD金叉死叉')
    res['equity'] = build_equity_curve(df, res)
    results['MACD金叉死叉'] = res
    
    # 2. BBI多空切换
    res = backtest(df, signals['bbi_cross_up'], signals['bbi_cross_down'], 'BBI多空切换')
    res['equity'] = build_equity_curve(df, res)
    results['BBI多空切换'] = res
    
    # 3. RSI金叉（RSI6上穿RSI12）
    rsi_golden = (df['RSI6'] > df['RSI12']) & (df['RSI6'].shift(1) <= df['RSI12'].shift(1))
    rsi_death = (df['RSI6'] < df['RSI12']) & (df['RSI6'].shift(1) >= df['RSI12'].shift(1))
    res = backtest(df, rsi_golden, rsi_death, 'RSI金叉死叉')
    res['equity'] = build_equity_curve(df, res)
    results['RSI金叉死叉'] = res
    
    # 4. KDJ金叉
    res = backtest(df, signals['kdj_golden'], 
                   (df['K'] < df['D']) & (df['K'].shift(1) >= df['D'].shift(1)), 'KDJ金叉死叉')
    res['equity'] = build_equity_curve(df, res)
    results['KDJ金叉死叉'] = res
    
    # 5. 多指标共振看多（评分>5）
    res = backtest(df, anomaly['resonance_long'], anomaly['resonance_short'], '共振看多')
    res['equity'] = build_equity_curve(df, res)
    results['共振看多'] = res
    
    # 6. 量价齐升
    res = backtest(df, anomaly['volume_price_up'], 
                   signals['macd_death'] | signals['bbi_cross_down'], '量价齐升')
    res['equity'] = build_equity_curve(df, res)
    results['量价齐升'] = res
    
    # 7. 超卖反弹
    res = backtest(df, anomaly['oversold_bounce'], 
                   (df['RSI6'] > 60) | (df['CCI14'] > 50), '超卖反弹')
    res['equity'] = build_equity_curve(df, res)
    results['超卖反弹'] = res
    
    # 8. BOLL带宽突破
    res = backtest(df, anomaly['boll_squeeze_break'], 
                   (df['收盘'] < df['BOLL_MID']) | signals['macd_death'], 'BOLL收窄突破')
    res['equity'] = build_equity_curve(df, res)
    results['BOLL收窄突破'] = res
    
    # 统计分析
    print("\n【策略回测统计】")
    stats = [analyze_result(res, benchmark_return) for res in results.values()]
    stats_df = pd.DataFrame(stats)
    print(stats_df.to_string(index=False))
    
    # 信号预测分析
    print("\n【异动信号预测验证】")
    predictions = {}
    for col, label in [
        ('resonance_long', '共振看多'),
        ('volume_price_up', '量价齐升'),
        ('oversold_bounce', '超卖反弹'),
        ('boll_squeeze_break', 'BOLL收窄突破'),
        ('volume_breakout', '放量突破'),
        ('ma_burst', '均线发散'),
    ]:
        pred = analyze_signal_prediction(df, anomaly[col], label)
        predictions[label] = pred
        if pred:
            print(f"\n{label}:")
            for period, data in pred.items():
                print(f"  {period}: 平均{data['平均收益']} 胜率{data['胜率']} 信号{data['信号次数']}次 预测准确率{data['上涨预测准确率']}")
    
    # 特征重要性
    print("\n【指标与未来收益相关性（绝对值）】")
    importance = feature_importance_analysis(df, signals, score)
    if len(importance) > 0:
        print(importance.head(10).to_string())
    
    # 可视化
    plot_multi_indicator(df, signals, score, anomaly, symbol, name, results, OUTPUT_DIR)
    
    return {
        'symbol': symbol, 'name': name, 'df': df, 'signals': signals,
        'score': score, 'anomaly': anomaly, 'results': results,
        'stats': stats_df, 'predictions': predictions, 'importance': importance,
        'benchmark_return': benchmark_return
    }

# ============ 生成报告 ============
def generate_report(all_results):
    lines = []
    lines.append("# 多指标共振异动预测模型 - 回测分析报告")
    lines.append(f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**分析股票**: 688322 奥比中光-UW, 688017 绿的谐波")
    lines.append(f"**回测区间**: 2024-05-10 至 2026-05-10")
    lines.append(f"**指标库**: MACD, BBI, RSI, CCI, KDJ, BOLL, WR, 量比, 换手率, 成交量异动, 均线排列")
    
    for res in all_results:
        lines.append(f"\n---\n")
        lines.append(f"## {res['symbol']} {res['name']}")
        lines.append(f"\n**两年基准涨跌幅**: {res['benchmark_return']:.2%}")
        
        lines.append(f"\n### 策略回测对比")
        lines.append(res['stats'].to_markdown(index=False))
        
        lines.append(f"\n### 异动信号预测效果")
        for label, pred in res['predictions'].items():
            if pred:
                lines.append(f"\n**{label}**")
                lines.append("| 周期 | 平均收益 | 胜率 | 最大收益 | 最大亏损 | 信号次数 | 预测准确率 |")
                lines.append("|------|---------|------|---------|---------|---------|----------|")
                for period, data in pred.items():
                    lines.append(f"| {period} | {data['平均收益']} | {data['胜率']} | {data['最大收益']} | {data['最大亏损']} | {data['信号次数']} | {data['上涨预测准确率']} |")
        
        lines.append(f"\n### 指标相关性排名")
        if len(res['importance']) > 0:
            lines.append(res['importance'].head(8).to_markdown())
        
        lines.append(f"\n![{res['symbol']}多指标分析]({res['symbol']}_multi_indicator.png)")
    
    lines.append("\n---\n")
    lines.append("## 核心发现")
    lines.append("\n### 异动信号有效性排序")
    lines.append("- 共振看多信号、量价齐升、超卖反弹等信号的中期胜率")
    lines.append("- 哪些指标组合最能预测未来5~20日涨跌")
    lines.append("- 量比/换手率在异动检测中的作用")
    
    lines.append("\n### 预测模型结论")
    lines.append("- 最优信号组合及阈值")
    lines.append("- 最佳持仓周期")
    lines.append("- 需要规避的假信号类型")
    
    report = '\n'.join(lines)
    with open(os.path.join(OUTPUT_DIR, 'multi_indicator_report.md'), 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: multi_indicator_report.md")
    return report

# ============ 入口 ============
if __name__ == '__main__':
    print("="*70)
    print("多指标共振异动预测模型 - 回测分析")
    print("="*70)
    
    all_results = []
    for symbol, name in STOCKS.items():
        res = run_stock(symbol, name)
        all_results.append(res)
    
    generate_report(all_results)
    
    print("\n" + "="*70)
    print("全部完成！")
    print("="*70)
