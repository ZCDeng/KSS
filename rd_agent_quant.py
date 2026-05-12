#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RD-Agent(Q) 风格量化回测
借鉴 arxiv:2505.15155 核心思想:
1. 自动因子挖掘与生成
2. IC评估与因子去重
3. 因子-模型协同优化
4. 多臂老虎机调度
5. Rank-based多空回测
"""

import pandas as pd
import numpy as np
import lightgbm as lgb
from scipy import stats
import matplotlib.pyplot as plt
import os
from datetime import datetime

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# ============ 数据加载 ============
def load_data(symbol):
    """加载个股数据"""
    df = pd.read_csv(os.path.join(OUTPUT_DIR, f'tushare_{symbol}.csv'))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    df = df.rename(columns={
        'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘',
        'vol': '成交量', 'amount': '成交额', 'pct_chg': '涨跌幅'
    })
    df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
    df['turnover_rate'] = df['turnover_rate'].fillna(0.0)
    return df


# ============ 自动因子生成器 ============
def generate_alpha_factors(df):
    """
    自动生成大量候选因子（Alpha101风格）
    返回: factor_df (每列是一个因子)
    """
    o = df['开盘']
    h = df['最高']
    l = df['最低']
    c = df['收盘']
    v = df['成交量']
    vr = df['volume_ratio']
    
    factors = pd.DataFrame(index=df.index)
    
    # 1. 价格动量类
    factors['mom_1d'] = c.pct_change()
    factors['mom_5d'] = c.pct_change(5)
    factors['mom_10d'] = c.pct_change(10)
    factors['mom_20d'] = c.pct_change(20)
    factors['mom_60d'] = c.pct_change(60)
    
    # 2. 波动率类
    factors['volatility_5d'] = c.pct_change().rolling(5).std()
    factors['volatility_20d'] = c.pct_change().rolling(20).std()
    factors['atr_14'] = (h - l).rolling(14).mean() / c
    
    # 3. 成交量类
    factors['vol_ma5_ratio'] = v / v.rolling(5).mean()
    factors['vol_ma20_ratio'] = v / v.rolling(20).mean()
    factors['amount_ma5_ratio'] = df['成交额'] / df['成交额'].rolling(5).mean()
    factors['vr'] = vr
    
    # 4. 价格位置类
    factors['close_to_high_20d'] = c / h.rolling(20).max()
    factors['close_to_low_20d'] = c / l.rolling(20).min()
    factors['close_to_high_60d'] = c / h.rolling(60).max()
    factors['close_to_low_60d'] = c / l.rolling(60).min()
    
    # 5. 均线类
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    
    factors['ma5_ratio'] = c / ma5
    factors['ma10_ratio'] = c / ma10
    factors['ma20_ratio'] = c / ma20
    factors['ma60_ratio'] = c / ma60
    factors['ma5_gt_ma10'] = (ma5 > ma10).astype(float)
    factors['ma10_gt_ma20'] = (ma10 > ma20).astype(float)
    factors['ma5_gt_ma20'] = (ma5 > ma20).astype(float)
    
    # 6. 均线距离
    factors['ma5_ma10_dist'] = (ma5 - ma10) / ma10
    factors['ma10_ma20_dist'] = (ma10 - ma20) / ma20
    factors['ma5_ma60_dist'] = (ma5 - ma60) / ma60
    
    # 7. 振幅类
    factors['amplitude'] = (h - l) / c
    factors['amplitude_ma5'] = factors['amplitude'].rolling(5).mean()
    factors['amplitude_ratio'] = factors['amplitude'] / factors['amplitude'].rolling(20).mean()
    
    # 8. 价格区间
    factors['hl_range_5d'] = (h.rolling(5).max() - l.rolling(5).min()) / c
    factors['hl_range_20d'] = (h.rolling(20).max() - l.rolling(20).min()) / c
    
    # 9. 日内走势
    factors['intraday_return'] = (c - o) / o
    factors['intraday_return_ma5'] = factors['intraday_return'].rolling(5).mean()
    factors['open_gap'] = (o - c.shift(1)) / c.shift(1)
    
    # 10. 量价关系
    factors['price_vol_corr_5d'] = c.pct_change().rolling(5).corr(v.pct_change())
    factors['price_vol_corr_20d'] = c.pct_change().rolling(20).corr(v.pct_change())
    
    # 11. RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    factors['rsi_6'] = 100 - (100 / (1 + gain.rolling(6).mean() / loss.rolling(6).mean()))
    factors['rsi_12'] = 100 - (100 / (1 + gain.rolling(12).mean() / loss.rolling(12).mean()))
    factors['rsi_24'] = 100 - (100 / (1 + gain.rolling(24).mean() / loss.rolling(24).mean()))
    
    # 12. MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    factors['macd_dif'] = dif
    factors['macd_dea'] = dea
    factors['macd_bar'] = 2 * (dif - dea)
    factors['macd_bar_ma5'] = factors['macd_bar'].rolling(5).mean()
    
    # 13. 布林带
    boll_mid = c.rolling(20).mean()
    boll_std = c.rolling(20).std()
    factors['boll_up'] = (boll_mid + 2 * boll_std) / c
    factors['boll_low'] = (boll_mid - 2 * boll_std) / c
    factors['boll_width'] = 4 * boll_std / boll_mid
    factors['boll_position'] = (c - (boll_mid - 2 * boll_std)) / (4 * boll_std)
    
    # 14. KDJ
    low_n = l.rolling(9).min()
    high_n = h.rolling(9).max()
    rsv = (c - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    factors['kdj_k'] = k
    factors['kdj_d'] = d
    factors['kdj_j'] = 3 * k - 2 * d
    
    # 15. CCI
    tp = (h + l + c) / 3
    ma_tp = tp.rolling(14).mean()
    md = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    factors['cci'] = (tp - ma_tp) / (0.015 * md)
    
    # 16. WR
    wr10_high = h.rolling(10).max()
    wr10_low = l.rolling(10).min()
    factors['wr10'] = (wr10_high - c) / (wr10_high - wr10_low) * -100
    
    # 17. 非线性变换
    factors['return_sq'] = factors['mom_1d'] ** 2
    factors['return_cube'] = factors['mom_1d'] ** 3
    factors['log_volume'] = np.log(v + 1)
    
    # 18. 交互特征
    factors['mom_x_vol'] = factors['mom_1d'] * factors['vol_ma5_ratio']
    factors['rsi_x_boll'] = factors['rsi_6'] * factors['boll_position']
    factors['macd_x_vol'] = factors['macd_bar'] * factors['vol_ma5_ratio']
    
    # 19. 趋势强度
    factors['trend_strength_20d'] = (c - c.shift(20)) / c.shift(20) / factors['volatility_20d']
    factors['trend_strength_60d'] = (c - c.shift(60)) / c.shift(60) / c.pct_change().rolling(60).std()
    
    # 20. 换手率
    factors['turnover'] = df['turnover_rate']
    factors['turnover_ma5_ratio'] = df['turnover_rate'] / df['turnover_rate'].rolling(5).mean()
    
    return factors


# ============ IC评估与因子筛选 ============
def compute_ic(factors, returns, method='spearman'):
    """计算每个因子的IC（信息系数）"""
    ic_results = {}
    for col in factors.columns:
        valid = factors[col].notna() & returns.notna()
        if valid.sum() < 30:
            continue
        f = factors[col][valid]
        r = returns[valid]
        if method == 'spearman':
            ic, pvalue = stats.spearmanr(f, r)
        else:
            ic, pvalue = stats.pearsonr(f, r)
        ic_results[col] = {'ic': ic, 'pvalue': pvalue, 'abs_ic': abs(ic)}
    return pd.DataFrame(ic_results).T


def deduplicate_factors(factors, ic_df, corr_threshold=0.95, ic_threshold=0.02):
    """
    因子去重：保留IC高且与其他因子相关性低的因子
    """
    # 先按abs_ic排序
    sorted_factors = ic_df.sort_values('abs_ic', ascending=False).index.tolist()
    
    selected = []
    selected_names = []
    
    for fname in sorted_factors:
        if ic_df.loc[fname, 'abs_ic'] < ic_threshold:
            continue
        
        if len(selected) == 0:
            selected.append(fname)
            selected_names.append(fname)
            continue
        
        # 计算与已选因子的相关性
        is_redundant = False
        for sname in selected:
            corr = factors[fname].corr(factors[sname])
            if abs(corr) > corr_threshold:
                is_redundant = True
                break
        
        if not is_redundant:
            selected.append(fname)
            selected_names.append(fname)
    
    return selected_names


# ============ Rank-based回测 ============
def rank_backtest(df, predictions, transaction_cost=0.001):
    """
    单股票方向预测回测
    预测>0.5做多，<0.5做空/空仓
    """
    df_eval = df.copy()
    df_eval['pred'] = predictions
    df_eval['future_return'] = df_eval['收盘'].shift(-1) / df_eval['收盘'] - 1
    
    returns = []
    positions = []
    
    for i in range(len(df_eval) - 1):
        row = df_eval.iloc[i]
        next_return = df_eval.iloc[i + 1]['future_return']
        
        if pd.isna(row['pred']) or pd.isna(next_return):
            returns.append(0)
            positions.append(0)
            continue
        
        # 预测概率>0.5做多，<0.5做空
        if row['pred'] > 0.5:
            pos = 1
        else:
            pos = -1
        
        # 收益
        if pos == 1:
            ret = next_return - transaction_cost
        else:
            ret = -next_return - transaction_cost
        
        returns.append(ret)
        positions.append(pos)
    
    returns = np.array(returns)
    valid_mask = ~np.isnan(returns)
    returns = returns[valid_mask]
    
    if len(returns) == 0:
        return {
            'returns': np.array([0]),
            'cum_returns': np.array([1.0]),
            'positions': [],
            'total_return': 0,
            'annual_return': 0,
            'volatility': 0,
            'sharpe': 0,
            'max_drawdown': 0,
            'calmar': 0,
            'ir': 0,
            'buyhold_annual': 0,
        }
    
    cum_returns = (1 + returns).cumprod()
    
    # 统计指标
    total_return = cum_returns[-1] - 1
    annual_return = (1 + total_return) ** (252 / len(returns)) - 1
    volatility = returns.std() * np.sqrt(252)
    sharpe = annual_return / volatility if volatility > 0 else 0
    
    peak = np.maximum.accumulate(cum_returns)
    drawdown = (peak - cum_returns) / peak
    max_drawdown = drawdown.max()
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0
    
    buyhold = (df_eval['收盘'].iloc[-1] / df_eval['收盘'].iloc[0]) ** (252 / len(df_eval)) - 1
    
    return {
        'returns': returns,
        'cum_returns': cum_returns,
        'positions': positions,
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': volatility,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'calmar': calmar,
        'ir': 0,
        'buyhold_annual': buyhold,
    }


# ============ 主流程：RD-Agent(Q)风格回测 ============
def run_rd_agent_style(symbol, name, predict_days=5):
    print(f"\n{'='*70}")
    print(f"RD-Agent(Q) 风格回测: {symbol} {name}")
    print(f"预测周期: {predict_days}天")
    print(f"{'='*70}")
    
    # 1. 加载数据
    df = load_data(symbol)
    print(f"数据长度: {len(df)} 天")
    
    # 2. 生成因子
    print("\n[1/5] 自动因子生成...")
    factors = generate_alpha_factors(df)
    print(f"生成因子数: {len(factors.columns)}")
    
    # 3. 计算未来收益标签
    future_return = df['收盘'].shift(-predict_days) / df['收盘'] - 1
    
    # 4. IC评估
    print(f"\n[2/5] IC评估...")
    ic_df = compute_ic(factors, future_return)
    print(f"有效因子数: {len(ic_df)}")
    print(f"Top 10 IC因子:")
    print(ic_df.sort_values('abs_ic', ascending=False).head(10)[['ic', 'abs_ic']].to_string())
    
    # 5. 因子去重
    print(f"\n[3/5] 因子去重 (IC>0.02, corr<0.95)...")
    selected_factors = deduplicate_factors(factors, ic_df, corr_threshold=0.95, ic_threshold=0.02)
    print(f"筛选后因子数: {len(selected_factors)}")
    
    # 6. 准备训练数据
    X = factors[selected_factors]
    y = (future_return > 0).astype(int)
    
    valid = X.notna().all(axis=1) & y.notna()
    X = X[valid]
    y = y[valid]
    future_return_valid = future_return[valid]
    dates = df['trade_date'][valid].reset_index(drop=True)
    
    # 时间序列分割
    n = len(X)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    
    X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
    X_val, y_val = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
    X_test, y_test = X.iloc[val_end:], y.iloc[val_end:]
    
    print(f"\n[4/5] LightGBM模型训练...")
    print(f"训练: {len(X_train)} | 验证: {len(X_val)} | 测试: {len(X_test)}")
    
    # 7. LightGBM训练
    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
    
    params = {
        'objective': 'binary',
        'metric': 'auc',
        'boosting_type': 'gbdt',
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'verbose': -1,
        'seed': 42,
    }
    
    model = lgb.train(
        params, train_data, num_boost_round=100,
        valid_sets=[train_data, val_data],
        callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)]
    )
    
    # 特征重要性
    importance = pd.DataFrame({
        'feature': model.feature_name(),
        'importance': model.feature_importance(importance_type='gain')
    }).sort_values('importance', ascending=False)
    print(f"\nTop 10 重要特征:")
    print(importance.head(10).to_string(index=False))
    
    # 8. 预测
    print(f"\n[5/5] Rank-based回测...")
    pred_proba = model.predict(X_test)
    
    # 评估预测能力
    from sklearn.metrics import roc_auc_score, accuracy_score
    auc = roc_auc_score(y_test, pred_proba)
    acc = accuracy_score(y_test, (pred_proba > 0.5).astype(int))
    print(f"测试集AUC: {auc:.3f} | 准确率: {acc:.1%}")
    
    # 9. 回测
    test_df = df.iloc[val_end:val_end + len(X_test)].reset_index(drop=True)
    backtest_result = rank_backtest(test_df, pred_proba)
    
    print(f"\n{'='*50}")
    print(f"回测结果 ({predict_days}日预测):")
    print(f"{'='*50}")
    print(f"总收益: {backtest_result['total_return']:.2%}")
    print(f"年化收益: {backtest_result['annual_return']:.2%}")
    print(f"波动率: {backtest_result['volatility']:.2%}")
    print(f"夏普比率: {backtest_result['sharpe']:.2f}")
    print(f"最大回撤: {backtest_result['max_drawdown']:.2%}")
    print(f"Calmar比率: {backtest_result['calmar']:.2f}")
    print(f"信息比率: {backtest_result['ir']:.2f}")
    print(f"买入持有年化: {backtest_result['buyhold_annual']:.2%}")
    
    return {
        'symbol': symbol,
        'name': name,
        'model': model,
        'importance': importance,
        'ic_df': ic_df,
        'selected_factors': selected_factors,
        'backtest': backtest_result,
        'auc': auc,
        'accuracy': acc,
    }


# ============ 可视化 ============
def plot_results(results_5d, results_10d, results_20d, output_dir):
    """绘制RD-Agent(Q)回测结果对比"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    symbols = [r['symbol'] for r in results_5d]
    
    # 1. 不同预测周期的夏普比率
    ax1 = axes[0, 0]
    x = np.arange(len(symbols))
    width = 0.25
    for i, (label, results) in enumerate([('5日', results_5d), ('10日', results_10d), ('20日', results_20d)]):
        sharpes = [r['backtest']['sharpe'] for r in results]
        ax1.bar(x + i*width, sharpes, width, label=label)
    ax1.set_xlabel('股票')
    ax1.set_ylabel('夏普比率')
    ax1.set_title('不同预测周期的夏普比率')
    ax1.set_xticks(x + width)
    ax1.set_xticklabels(symbols)
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # 2. 收益曲线对比
    ax2 = axes[0, 1]
    for results in [results_5d, results_10d, results_20d]:
        for r in results:
            cum = r['backtest']['cum_returns']
            ax2.plot(cum, label=f"{r['symbol']} {r['predict_days']}d")
    ax2.set_title('策略累计收益曲线')
    ax2.set_xlabel('交易日')
    ax2.set_ylabel('累计收益')
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)
    
    # 3. 因子IC分布
    ax3 = axes[1, 0]
    for r in results_5d:
        ic_values = r['ic_df']['ic'].values
        ax3.hist(ic_values, bins=30, alpha=0.5, label=r['symbol'])
    ax3.set_title('因子IC分布')
    ax3.set_xlabel('IC')
    ax3.set_ylabel('频数')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.axvline(x=0, color='black', linestyle='--', linewidth=0.5)
    
    # 4. 关键指标对比
    ax4 = axes[1, 1]
    metrics = ['annual_return', 'sharpe', 'max_drawdown', 'calmar']
    for r in results_5d:
        values = [r['backtest'][m] for m in metrics]
        ax4.plot(metrics, values, marker='o', label=r['symbol'])
    ax4.set_title('关键指标对比 (5日预测)')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'rd_agent_results.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"结果图已保存: rd_agent_results.png")


# ============ 入口 ============
if __name__ == '__main__':
    print("="*70)
    print("RD-Agent(Q) 风格量化回测")
    print("借鉴 arxiv:2505.15155")
    print("="*70)
    
    stocks = [('688322', '奥比中光-UW'), ('688017', '绿的谐波')]
    all_results = {'5d': [], '10d': [], '20d': []}
    
    for symbol, name in stocks:
        for days, key in [(5, '5d'), (10, '10d'), (20, '20d')]:
            res = run_rd_agent_style(symbol, name, predict_days=days)
            res['predict_days'] = days
            all_results[key].append(res)
    
    # 可视化
    plot_results(all_results['5d'], all_results['10d'], all_results['20d'], OUTPUT_DIR)
    
    # 生成报告
    print("\n" + "="*70)
    print("RD-Agent(Q) 回测完成！")
    print("="*70)
