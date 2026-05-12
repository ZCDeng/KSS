#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数网格搜索 + 机器学习预测模型
目标：找出最优的共振阈值、持仓周期、止损止盈参数
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, classification_report
from itertools import product
import os
import json
from datetime import datetime

from multi_indicator_backtest import (
    load_data, compute_all_indicators, generate_direction_signals,
    calc_resonance_score, detect_anomaly, INITIAL_CAPITAL, TRANSACTION_COST
)

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# ============ 带止损止盈的回测引擎 ============
def backtest_with_rules(df, entry_mask, hold_days=20, stop_loss=0.08, stop_profit=0.20, trailing_stop=False):
    """
    增强回测：支持固定持仓、止损、止盈、移动止损
    """
    trades = []
    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    entry_date = None
    highest_price = 0
    
    for i, row in df.iterrows():
        price = row['收盘']
        date = row['日期']
        
        # 买入逻辑
        if position == 0 and entry_mask.iloc[i]:
            shares = int(capital / (price * (1 + TRANSACTION_COST)))
            if shares > 0:
                cost = shares * price * (1 + TRANSACTION_COST)
                if cost <= capital:
                    position = shares
                    entry_price = price
                    entry_date = date
                    highest_price = price
                    capital -= cost
        
        # 持仓时检查卖出条件
        elif position > 0:
            # 更新最高价（用于移动止损）
            if price > highest_price:
                highest_price = price
            
            current_return = (price - entry_price) / entry_price
            max_return = (highest_price - entry_price) / entry_price
            
            exit_reason = None
            
            # 固定持仓天数
            hold_period = (date - entry_date).days
            if hold_period >= hold_days:
                exit_reason = f'持仓{hold_days}天'
            
            # 止损
            elif current_return <= -stop_loss:
                exit_reason = f'止损{-stop_loss:.0%}'
            
            # 止盈
            elif current_return >= stop_profit:
                exit_reason = f'止盈{stop_profit:.0%}'
            
            # 移动止损（回撤stop_loss即卖出）
            elif trailing_stop and max_return > 0.10 and (highest_price - price) / highest_price >= stop_loss:
                exit_reason = f'移动止损'
            
            if exit_reason:
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
                    'hold_days': hold_period,
                    'exit_reason': exit_reason
                })
                
                capital += revenue
                position = 0
                entry_price = 0
                entry_date = None
                highest_price = 0
    
    # 尾盘平仓
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
            'hold_days': (date - entry_date).days,
            'exit_reason': '到期平仓'
        })
        capital += revenue
    
    trades_df = pd.DataFrame(trades)
    return {
        'trades': trades_df,
        'final_capital': capital,
        'total_return': (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        'trade_count': len(trades_df)
    }


# ============ 网格搜索最优参数 ============
def grid_search_best_params(df, anomaly, symbol, name):
    """网格搜索共振看多策略的最优参数"""
    print(f"\n{'='*60}")
    print(f"[{symbol}] 参数网格搜索...")
    
    # 参数空间
    thresholds = [3, 4, 5, 6, 7]
    hold_days_list = [5, 10, 15, 20, 30, 60]
    stop_losses = [0.05, 0.08, 0.10, 0.15]
    stop_profits = [0.10, 0.15, 0.20, 0.30, 0.50]
    
    results = []
    benchmark = (df['收盘'].iloc[-1] - df['收盘'].iloc[0]) / df['收盘'].iloc[0]
    
    total_combos = len(thresholds) * len(hold_days_list) * len(stop_losses) * len(stop_profits)
    print(f"总组合数: {total_combos}")
    
    for threshold, hold_days, sl, sp in product(thresholds, hold_days_list, stop_losses, stop_profits):
        entry_mask = calc_resonance_score(generate_direction_signals(df)) > threshold
        res = backtest_with_rules(df, entry_mask, hold_days, sl, sp)
        
        trades = res['trades']
        if len(trades) >= 3:  # 至少3次交易才有统计意义
            wins = trades[trades['return_pct'] > 0]
            win_rate = len(wins) / len(trades)
            avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
            losses = trades[trades['return_pct'] <= 0]
            avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0
            pf = abs(avg_win / avg_loss) if avg_loss != 0 else 999
            
            # 综合评分 = 总收益 * 胜率 * min(盈亏比, 5)
            score = res['total_return'] * win_rate * min(pf, 5)
            
            results.append({
                'threshold': threshold,
                'hold_days': hold_days,
                'stop_loss': sl,
                'stop_profit': sp,
                'trade_count': len(trades),
                'win_rate': win_rate,
                'total_return': res['total_return'],
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': pf,
                'score': score,
                'excess': res['total_return'] - benchmark
            })
    
    results_df = pd.DataFrame(results)
    if len(results_df) == 0:
        print("无有效结果")
        return None
    
    # 按综合评分排序
    results_df = results_df.sort_values('score', ascending=False)
    
    print(f"\n前10名最优参数组合:")
    print(results_df.head(10)[['threshold','hold_days','stop_loss','stop_profit','trade_count','win_rate','total_return','excess']].to_string(index=False))
    
    return results_df


# ============ 机器学习预测模型 ============
def build_ml_dataset(df, signals, score, anomaly, future_periods=[1, 3, 5, 10]):
    """构建机器学习数据集"""
    df = df.copy()
    
    features = pd.DataFrame(index=df.index)
    features['macd_dir'] = signals['macd_dir']
    features['rsi_dir'] = signals['rsi_dir']
    features['cci_dir'] = signals['cci_dir']
    features['kdj_dir'] = signals['kdj_dir']
    features['boll_dir'] = signals['boll_dir']
    features['wr_dir'] = signals['wr_dir']
    features['ma_dir'] = signals['ma_dir']
    features['vr_dir'] = signals['vr_dir']
    features['vol_spike'] = signals['vol_spike']
    features['mom_dir'] = signals['mom_dir']
    
    # 数值特征
    features['RSI6'] = df['RSI6']
    features['CCI14'] = df['CCI14']
    features['J'] = df['J']
    features['MACD_BAR'] = df['MACD_BAR']
    features['volume_ratio'] = df['volume_ratio']
    features['turnover_rate'] = df['turnover_rate']
    features['BOLL_POSITION'] = df['BOLL_POSITION']
    features['MA_ALIGNMENT'] = df['MA_ALIGNMENT']
    features['MOM5'] = df['MOM5']
    features['resonance_score'] = score
    features['振幅'] = df['振幅']
    
    # 滞后特征
    features['RSI6_lag1'] = features['RSI6'].shift(1)
    features['MACD_BAR_lag1'] = features['MACD_BAR'].shift(1)
    
    datasets = {}
    for p in future_periods:
        future_return = df['收盘'].shift(-p) / df['收盘'] - 1
        y = (future_return > 0).astype(int)  # 1=涨, 0=跌
        
        valid = features.notna().all(axis=1) & y.notna()
        X = features[valid]
        y = y[valid]
        
        datasets[p] = {'X': X, 'y': y}
    
    return datasets


def train_predict_models(datasets, symbol):
    """训练并评估多个预测模型"""
    print(f"\n{'='*60}")
    print(f"[{symbol}] 机器学习预测模型...")
    
    models = {
        'LogisticRegression': LogisticRegression(max_iter=1000, class_weight='balanced'),
        'RandomForest': RandomForestClassifier(n_estimators=100, max_depth=5, class_weight='balanced', random_state=42),
        'GradientBoosting': GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42),
    }
    
    all_results = {}
    
    for period, data in datasets.items():
        X = data['X']
        y = data['y']
        
        if len(y) < 50:
            continue
        
        # 时间序列分割（避免数据泄露）
        split_idx = int(len(X) * 0.7)
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
        
        if len(y_test) < 20:
            continue
        
        period_results = {}
        
        for model_name, model in models.items():
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            y_prob = model.predict_proba(X_test)[:, 1]
            
            acc = accuracy_score(y_test, y_pred)
            prec = precision_score(y_test, y_pred, zero_division=0)
            rec = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)
            auc = roc_auc_score(y_test, y_prob) if len(np.unique(y_test)) > 1 else 0.5
            
            period_results[model_name] = {
                'accuracy': acc,
                'precision': prec,
                'recall': rec,
                'f1': f1,
                'auc': auc,
                'model': model,
                'feature_importance': dict(zip(X.columns, model.feature_importances_)) if hasattr(model, 'feature_importances_') else None
            }
        
        all_results[period] = {
            'results': period_results,
            'test_size': len(y_test),
            'baseline': y_test.mean()  # 随机基准
        }
        
        print(f"\n  {period}日预测 (测试集{len(y_test)}条, 基准胜率{y_test.mean():.1%}):")
        for model_name, res in period_results.items():
            print(f"    {model_name:20s}: 准确率{res['accuracy']:.1%} | 精确率{res['precision']:.1%} | 召回率{res['recall']:.1%} | F1:{res['f1']:.3f} | AUC:{res['auc']:.3f}")
    
    return all_results


def plot_feature_importance(ml_results, symbol, output_dir):
    """绘制特征重要性"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    axes = axes.flatten()
    
    for idx, (period, data) in enumerate(ml_results.items()):
        if idx >= 4:
            break
        
        rf_results = data['results'].get('RandomForest')
        if rf_results and rf_results['feature_importance']:
            fi = pd.Series(rf_results['feature_importance']).sort_values(ascending=True).tail(15)
            ax = axes[idx]
            fi.plot(kind='barh', ax=ax, color='steelblue')
            ax.set_title(f'{symbol} - {period}日预测特征重要性 (RandomForest)', fontsize=11)
            ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f'{symbol}_feature_importance.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  特征重要性图已保存: {symbol}_feature_importance.png")


# ============ 主流程 ============
def main():
    print("="*70)
    print("参数优化 + 机器学习预测模型")
    print("="*70)
    
    all_grid_results = {}
    all_ml_results = {}
    
    for symbol, name in STOCKS.items():
        df = load_data(symbol)
        df = compute_all_indicators(df)
        signals = generate_direction_signals(df)
        score = calc_resonance_score(signals)
        anomaly = detect_anomaly(df, signals, score)
        
        # 1. 网格搜索
        grid_results = grid_search_best_params(df, anomaly, symbol, name)
        all_grid_results[symbol] = grid_results
        
        # 2. 机器学习
        datasets = build_ml_dataset(df, signals, score, anomaly)
        ml_results = train_predict_models(datasets, symbol)
        all_ml_results[symbol] = ml_results
        
        # 3. 特征重要性图
        if ml_results:
            plot_feature_importance(ml_results, symbol, OUTPUT_DIR)
    
    # 保存最优参数
    best_params = {}
    for symbol, grid_df in all_grid_results.items():
        if grid_df is not None and len(grid_df) > 0:
            best = grid_df.iloc[0]
            best_params[symbol] = {
                'threshold': int(best['threshold']),
                'hold_days': int(best['hold_days']),
                'stop_loss': float(best['stop_loss']),
                'stop_profit': float(best['stop_profit']),
                'expected_return': float(best['total_return']),
                'win_rate': float(best['win_rate']),
            }
    
    with open(os.path.join(OUTPUT_DIR, 'best_params.json'), 'w', encoding='utf-8') as f:
        json.dump(best_params, f, indent=2, ensure_ascii=False)
    
    print(f"\n最优参数已保存: best_params.json")
    print(json.dumps(best_params, indent=2, ensure_ascii=False))
    
    print("\n" + "="*70)
    print("参数优化与机器学习建模完成！")
    print("="*70)


STOCKS = {'688322': '奥比中光-UW', '688017': '绿的谐波'}

if __name__ == '__main__':
    main()
