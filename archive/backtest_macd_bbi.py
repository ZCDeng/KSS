#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 688322 & 688017 MACD + BBI 两年回测分析
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import akshare as ak
from datetime import datetime, timedelta
import os

# ============ 配置 ============
STOCKS = {
    '688322': '奥比中光-UW',
    '688017': '绿的谐波'
}
START_DATE = (datetime.now() - timedelta(days=730)).strftime('%Y%m%d')
END_DATE = datetime.now().strftime('%Y%m%d')
INITIAL_CAPITAL = 1_000_000
TRANSACTION_COST = 0.001  # 双边0.1%
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# 设置matplotlib中文显示
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ============ 数据获取 ============
def fetch_data(symbol, start, end):
    """获取股票历史数据（前复权）"""
    cache_file = os.path.join(OUTPUT_DIR, f'data_{symbol}.csv')
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['日期'] = pd.to_datetime(df['日期'])
        print(f"[缓存] {symbol} 从CSV加载 {len(df)} 条记录")
        return df
    
    print(f"[下载] {symbol} 从akshare获取数据...")
    df = ak.stock_zh_a_hist(symbol=symbol, period='daily', start_date=start, end_date=end, adjust='qfq')
    df['日期'] = pd.to_datetime(df['日期'])
    df.to_csv(cache_file, index=False)
    print(f"[完成] {symbol} 获取 {len(df)} 条记录，已缓存")
    return df

# ============ 指标计算 ============
def calculate_macd(df, fast=12, slow=26, signal=9):
    """计算MACD指标"""
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar

def calculate_bbi(df):
    """计算BBI指标"""
    close = df['收盘']
    ma3 = close.rolling(window=3).mean()
    ma6 = close.rolling(window=6).mean()
    ma12 = close.rolling(window=12).mean()
    ma24 = close.rolling(window=24).mean()
    bbi = (ma3 + ma6 + ma12 + ma24) / 4
    return bbi

# ============ 信号生成 ============
def generate_signals(df):
    """生成各类交易信号"""
    df = df.copy()
    
    # MACD
    df['DIF'], df['DEA'], df['MACD_BAR'] = calculate_macd(df)
    df['MACD_GOLDEN'] = (df['DIF'] > df['DEA']) & (df['DIF'].shift(1) <= df['DEA'].shift(1))
    df['MACD_DEATH'] = (df['DIF'] < df['DEA']) & (df['DIF'].shift(1) >= df['DEA'].shift(1))
    
    # BBI
    df['BBI'] = calculate_bbi(df)
    df['BBI_LONG'] = (df['收盘'] > df['BBI']) & (df['收盘'].shift(1) <= df['BBI'].shift(1))
    df['BBI_SHORT'] = (df['收盘'] < df['BBI']) & (df['收盘'].shift(1) >= df['BBI'].shift(1))
    df['ABOVE_BBI'] = df['收盘'] > df['BBI']
    
    return df

# ============ 回测引擎 ============
def backtest_strategy(df, entry_col, exit_col, name, initial_capital=INITIAL_CAPITAL):
    """
    通用回测引擎
    entry_col: 买入信号列名
    exit_col: 卖出信号列名
    """
    trades = []
    capital = initial_capital
    position = 0  # 持仓股数
    entry_price = 0
    entry_date = None
    
    for i, row in df.iterrows():
        price = row['收盘']
        
        # 空仓时找买入信号
        if position == 0 and row[entry_col]:
            shares = int(capital / (price * (1 + TRANSACTION_COST)))
            if shares > 0:
                cost = shares * price * (1 + TRANSACTION_COST)
                if cost <= capital:
                    position = shares
                    entry_price = price
                    entry_date = row['日期']
                    capital -= cost
        
        # 持仓时找卖出信号
        elif position > 0 and row[exit_col]:
            revenue = position * price * (1 - TRANSACTION_COST)
            profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
            return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
            
            trades.append({
                'entry_date': entry_date,
                'exit_date': row['日期'],
                'entry_price': entry_price,
                'exit_price': price,
                'shares': position,
                'profit': profit,
                'return_pct': return_pct,
                'hold_days': (row['日期'] - entry_date).days
            })
            
            capital += revenue
            position = 0
            entry_price = 0
            entry_date = None
    
    # 如果有未平仓持仓，按最后一天平仓
    if position > 0:
        last_row = df.iloc[-1]
        price = last_row['收盘']
        revenue = position * price * (1 - TRANSACTION_COST)
        profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
        return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
        
        trades.append({
            'entry_date': entry_date,
            'exit_date': last_row['日期'],
            'entry_price': entry_price,
            'exit_price': price,
            'shares': position,
            'profit': profit,
            'return_pct': return_pct,
            'hold_days': (last_row['日期'] - entry_date).days
        })
        capital += revenue
    
    trades_df = pd.DataFrame(trades)
    
    # 构建权益曲线
    equity_curve = build_equity_curve(df, trades, initial_capital)
    
    return {
        'name': name,
        'trades': trades_df,
        'final_capital': capital,
        'total_return': (capital - initial_capital) / initial_capital,
        'equity_curve': equity_curve
    }

def backtest_combined_strict(df, initial_capital=INITIAL_CAPITAL):
    """严格组合策略：MACD金叉 + 收盘价在BBI上方"""
    trades = []
    capital = initial_capital
    position = 0
    entry_price = 0
    entry_date = None
    
    for i, row in df.iterrows():
        price = row['收盘']
        
        if position == 0 and row['MACD_GOLDEN'] and row['ABOVE_BBI']:
            shares = int(capital / (price * (1 + TRANSACTION_COST)))
            if shares > 0:
                cost = shares * price * (1 + TRANSACTION_COST)
                if cost <= capital:
                    position = shares
                    entry_price = price
                    entry_date = row['日期']
                    capital -= cost
        
        elif position > 0 and (row['MACD_DEATH'] or not row['ABOVE_BBI']):
            revenue = position * price * (1 - TRANSACTION_COST)
            profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
            return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
            
            trades.append({
                'entry_date': entry_date,
                'exit_date': row['日期'],
                'entry_price': entry_price,
                'exit_price': price,
                'shares': position,
                'profit': profit,
                'return_pct': return_pct,
                'hold_days': (row['日期'] - entry_date).days,
                'exit_reason': 'MACD死叉' if row['MACD_DEATH'] else '跌破BBI'
            })
            
            capital += revenue
            position = 0
            entry_price = 0
            entry_date = None
    
    if position > 0:
        last_row = df.iloc[-1]
        price = last_row['收盘']
        revenue = position * price * (1 - TRANSACTION_COST)
        profit = revenue - (position * entry_price * (1 + TRANSACTION_COST))
        return_pct = profit / (position * entry_price * (1 + TRANSACTION_COST))
        
        trades.append({
            'entry_date': entry_date,
            'exit_date': last_row['日期'],
            'entry_price': entry_price,
            'exit_price': price,
            'shares': position,
            'profit': profit,
            'return_pct': return_pct,
            'hold_days': (last_row['日期'] - entry_date).days,
            'exit_reason': '持仓到期'
        })
        capital += revenue
    
    trades_df = pd.DataFrame(trades)
    equity_curve = build_equity_curve(df, trades, initial_capital)
    
    return {
        'name': 'MACD+BBI严格组合',
        'trades': trades_df,
        'final_capital': capital,
        'total_return': (capital - initial_capital) / initial_capital,
        'equity_curve': equity_curve
    }

def build_equity_curve(df, trades, initial_capital):
    """构建权益曲线——逐日跟踪持仓市值"""
    equity = []
    dates = []
    position = 0
    cash = initial_capital
    entry_price = 0
    entry_date = None
    trade_idx = 0
    
    for i, row in df.iterrows():
        price = row['收盘']
        date = row['日期']
        
        # 检查是否当天有买入信号
        if trade_idx < len(trades):
            t = trades[trade_idx]
            if t['entry_date'] == date and position == 0:
                shares = int(cash / (price * (1 + TRANSACTION_COST)))
                if shares > 0:
                    cost = shares * price * (1 + TRANSACTION_COST)
                    position = shares
                    entry_price = price
                    cash -= cost
        
        # 检查是否当天有卖出信号
        if trade_idx < len(trades):
            t = trades[trade_idx]
            if t['exit_date'] == date and position > 0:
                revenue = position * price * (1 - TRANSACTION_COST)
                cash += revenue
                position = 0
                entry_price = 0
                trade_idx += 1
        
        # 计算当前权益
        current_equity = cash + position * price
        equity.append(current_equity)
        dates.append(date)
    
    return pd.DataFrame({'date': dates, 'equity': equity})

# ============ 统计分析 ============
def analyze_trades(result, benchmark_return):
    """分析交易统计"""
    trades = result['trades']
    if len(trades) == 0:
        return {
            '策略名称': result['name'],
            '交易次数': 0,
            '胜率': 0,
            '平均收益': 0,
            '平均亏损': 0,
            '盈亏比': 0,
            '总收益率': result['total_return'],
            '最大单笔盈利': 0,
            '最大单笔亏损': 0,
            '平均持仓天数': 0,
            '最大回撤': 0,
            '相对基准超额': result['total_return'] - benchmark_return
        }
    
    wins = trades[trades['return_pct'] > 0]
    losses = trades[trades['return_pct'] <= 0]
    
    win_rate = len(wins) / len(trades) if len(trades) > 0 else 0
    avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
    avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    
    # 计算最大回撤
    equity = result['equity_curve']['equity']
    max_drawdown = 0
    peak = equity[0]
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_drawdown:
            max_drawdown = dd
    
    return {
        '策略名称': result['name'],
        '交易次数': len(trades),
        '胜率': f"{win_rate:.1%}",
        '平均收益': f"{avg_win:.2%}",
        '平均亏损': f"{avg_loss:.2%}",
        '盈亏比': f"{profit_factor:.2f}",
        '总收益率': f"{result['total_return']:.2%}",
        '最大单笔盈利': f"{trades['return_pct'].max():.2%}",
        '最大单笔亏损': f"{trades['return_pct'].min():.2%}",
        '平均持仓天数': f"{trades['hold_days'].mean():.1f}天",
        '最大回撤': f"{max_drawdown:.2%}",
        '相对基准超额': f"{result['total_return'] - benchmark_return:.2%}"
    }

# ============ 信号后收益分析 ============
def analyze_post_signal_returns(df, signal_col, name):
    """分析信号发生后N日的收益分布"""
    periods = [1, 3, 5, 10, 20]
    results = {}
    
    for p in periods:
        returns = []
        for i in range(len(df) - p):
            if df.iloc[i][signal_col]:
                future_return = (df.iloc[i + p]['收盘'] - df.iloc[i]['收盘']) / df.iloc[i]['收盘']
                returns.append(future_return)
        
        if returns:
            returns = np.array(returns)
            results[f'{p}日'] = {
                '平均收益': f"{returns.mean():.2%}",
                '胜率': f"{(returns > 0).mean():.1%}",
                '最大收益': f"{returns.max():.2%}",
                '最大亏损': f"{returns.min():.2%}",
                '信号次数': len(returns)
            }
    
    return results

# ============ 可视化 ============
def plot_analysis(df, symbol, name, results, output_dir):
    """绘制综合分析图"""
    fig, axes = plt.subplots(4, 1, figsize=(16, 20), gridspec_kw={'height_ratios': [3, 1, 1, 1]})
    
    # 1. 价格+BBI+买卖点
    ax1 = axes[0]
    ax1.plot(df['日期'], df['收盘'], label='收盘价', color='black', linewidth=1)
    ax1.plot(df['日期'], df['BBI'], label='BBI', color='blue', linewidth=1, alpha=0.7)
    
    # MACD策略买卖点
    macd_trades = results['MACD金叉死叉']['trades']
    if len(macd_trades) > 0:
        ax1.scatter(macd_trades['entry_date'], macd_trades['entry_price'], 
                   marker='^', color='red', s=100, label='MACD买入', zorder=5)
        ax1.scatter(macd_trades['exit_date'], macd_trades['exit_price'], 
                   marker='v', color='green', s=100, label='MACD卖出', zorder=5)
    
    ax1.set_title(f'{symbol} {name} - 价格走势与买卖点', fontsize=14)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    
    # 2. MACD
    ax2 = axes[1]
    ax2.plot(df['日期'], df['DIF'], label='DIF', color='orange', linewidth=1)
    ax2.plot(df['日期'], df['DEA'], label='DEA', color='purple', linewidth=1)
    colors = ['red' if v > 0 else 'green' for v in df['MACD_BAR']]
    ax2.bar(df['日期'], df['MACD_BAR'], color=colors, alpha=0.5, label='MACD柱')
    ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    ax2.set_title('MACD指标', fontsize=12)
    ax2.legend(loc='upper left')
    ax2.grid(True, alpha=0.3)
    
    # 3. BBI位置
    ax3 = axes[2]
    above_bbi = df['收盘'] > df['BBI']
    ax3.fill_between(df['日期'], 0, 1, where=above_bbi, alpha=0.3, color='red', label='BBI上方（多头）')
    ax3.fill_between(df['日期'], 0, 1, where=~above_bbi, alpha=0.3, color='green', label='BBI下方（空头）')
    ax3.set_ylim(0, 1)
    ax3.set_yticks([])
    ax3.set_title('BBI多空区域', fontsize=12)
    ax3.legend(loc='upper left')
    ax3.grid(True, alpha=0.3)
    
    # 4. 收益曲线对比
    ax4 = axes[3]
    for key in ['MACD金叉死叉', 'BBI多空切换', 'MACD+BBI严格组合']:
        if key in results and len(results[key]['equity_curve']) > 0:
            ec = results[key]['equity_curve']
            ax4.plot(ec['date'], ec['equity'], label=key, linewidth=1.5)
    
    # 买入持有基准
    buy_hold = INITIAL_CAPITAL * (df['收盘'] / df['收盘'].iloc[0])
    ax4.plot(df['日期'], buy_hold, label='买入持有', color='gray', linestyle='--', linewidth=1)
    
    ax4.axhline(y=INITIAL_CAPITAL, color='black', linestyle='-', linewidth=0.5)
    ax4.set_title('策略收益曲线对比（初始资金100万）', fontsize=12)
    ax4.legend(loc='upper left')
    ax4.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f'{symbol}_analysis.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图表已保存: {symbol}_analysis.png")

# ============ 主流程 ============
def run_backtest_for_stock(symbol, name):
    """对单只股票执行完整回测流程"""
    print(f"\n{'='*60}")
    print(f"股票: {symbol} {name}")
    print(f"{'='*60}")
    
    # 1. 获取数据
    df = fetch_data(symbol, START_DATE, END_DATE)
    if len(df) < 60:
        print(f"数据不足，跳过")
        return None
    
    # 2. 计算指标和信号
    df = generate_signals(df)
    
    # 3. 执行回测
    results = {}
    results['MACD金叉死叉'] = backtest_strategy(df, 'MACD_GOLDEN', 'MACD_DEATH', 'MACD金叉死叉')
    results['BBI多空切换'] = backtest_strategy(df, 'BBI_LONG', 'BBI_SHORT', 'BBI多空切换')
    results['MACD+BBI严格组合'] = backtest_combined_strict(df)
    
    # 4. 基准收益
    benchmark_return = (df['收盘'].iloc[-1] - df['收盘'].iloc[0]) / df['收盘'].iloc[0]
    
    # 5. 统计分析
    stats = []
    for key, res in results.items():
        stats.append(analyze_trades(res, benchmark_return))
    
    stats_df = pd.DataFrame(stats)
    print("\n【策略统计对比】")
    print(stats_df.to_string(index=False))
    
    # 6. 信号后收益分析
    print("\n【MACD金叉后N日收益分布】")
    macd_post = analyze_post_signal_returns(df, 'MACD_GOLDEN', 'MACD金叉')
    for period, data in macd_post.items():
        print(f"  {period}: 平均{data['平均收益']}, 胜率{data['胜率']}, 信号{data['信号次数']}次")
    
    print("\n【BBI突破后N日收益分布】")
    bbi_post = analyze_post_signal_returns(df, 'BBI_LONG', 'BBI突破')
    for period, data in bbi_post.items():
        print(f"  {period}: 平均{data['平均收益']}, 胜率{data['胜率']}, 信号{data['信号次数']}次")
    
    # 7. 可视化
    plot_analysis(df, symbol, name, results, OUTPUT_DIR)
    
    return {
        'symbol': symbol,
        'name': name,
        'df': df,
        'results': results,
        'stats': stats_df,
        'macd_post': macd_post,
        'bbi_post': bbi_post,
        'benchmark_return': benchmark_return
    }

# ============ 生成报告 ============
def generate_report(all_results):
    """生成Markdown分析报告"""
    report = []
    report.append("# A股688322 & 688017 MACD+BBI两年回测分析报告")
    report.append(f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append(f"**回测区间**: {START_DATE} 至 {END_DATE}")
    report.append(f"**交易成本**: 双边{TRANSACTION_COST:.1%}")
    report.append(f"**初始资金**: {INITIAL_CAPITAL/10000:.0f}万")
    
    for res in all_results:
        if res is None:
            continue
        
        report.append(f"\n---\n")
        report.append(f"## {res['symbol']} {res['name']}")
        report.append(f"\n**两年涨跌幅**: {res['benchmark_return']:.2%}")
        
        report.append(f"\n### 策略统计对比")
        report.append(res['stats'].to_markdown(index=False))
        
        report.append(f"\n### MACD金叉后N日收益分布")
        report.append("| 周期 | 平均收益 | 胜率 | 最大收益 | 最大亏损 | 信号次数 |")
        report.append("|------|---------|------|---------|---------|---------|")
        for period, data in res['macd_post'].items():
            report.append(f"| {period} | {data['平均收益']} | {data['胜率']} | {data['最大收益']} | {data['最大亏损']} | {data['信号次数']} |")
        
        report.append(f"\n### BBI突破后N日收益分布")
        report.append("| 周期 | 平均收益 | 胜率 | 最大收益 | 最大亏损 | 信号次数 |")
        report.append("|------|---------|------|---------|---------|---------|")
        for period, data in res['bbi_post'].items():
            report.append(f"| {period} | {data['平均收益']} | {data['胜率']} | {data['最大收益']} | {data['最大亏损']} | {data['信号次数']} |")
        
        report.append(f"\n![{res['symbol']}分析图]({res['symbol']}_analysis.png)")
    
    report.append("\n---\n")
    report.append("## 核心规律总结")
    report.append("\n### 1. MACD规律")
    report.append("- 金叉信号在不同周期后的平均收益和胜率")
    report.append("- 死叉作为卖出信号的有效性")
    report.append("- MACD柱状图背离情况")
    
    report.append("\n### 2. BBI规律")
    report.append("- 股价突破BBI后的短期/中期表现")
    report.append("- BBI作为多空分界线的可靠性")
    report.append("- 结合趋势方向的BBI信号效果")
    
    report.append("\n### 3. 组合规律")
    report.append("- MACD+BBI组合是否优于单指标")
    report.append("- 信号过滤效果（减少假信号）")
    report.append("- 最佳持仓周期建议")
    
    report_text = '\n'.join(report)
    
    with open(os.path.join(OUTPUT_DIR, 'backtest_report.md'), 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f"\n报告已保存: backtest_report.md")
    return report_text

# ============ 执行入口 ============
if __name__ == '__main__':
    print("="*60)
    print("A股 MACD + BBI 两年回测分析")
    print("="*60)
    
    all_results = []
    for symbol, name in STOCKS.items():
        res = run_backtest_for_stock(symbol, name)
        all_results.append(res)
    
    # 生成报告
    generate_report(all_results)
    
    print("\n" + "="*60)
    print("回测完成！")
    print("="*60)
