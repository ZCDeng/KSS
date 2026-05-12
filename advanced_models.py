#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多任务Transformer + 注意力可视化 + 全市场扫描
"""

import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, roc_auc_score
import matplotlib.pyplot as plt
import os
import json
from datetime import datetime
from deep_learning_models import (
    load_stock_with_market, compute_features, device, OUTPUT_DIR
)

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# ============ 多任务序列数据集 ============
class MultiTaskDataset(Dataset):
    def __init__(self, X, y_dict):
        self.X = torch.FloatTensor(X)
        self.y_dict = {k: torch.LongTensor(v) for k, v in y_dict.items()}
    
    def __len__(self):
        return len(self.X)
    
    def __getitem__(self, idx):
        return self.X[idx], {k: v[idx] for k, v in self.y_dict.items()}


# ============ 带注意力提取的多任务Transformer ============
class MultiTaskTransformer(nn.Module):
    def __init__(self, input_dim, d_model=64, nhead=4, num_layers=2, dropout=0.3, num_tasks=3):
        super().__init__()
        self.input_fc = nn.Linear(input_dim, d_model)
        self.pos_encoder = PositionalEncoding(d_model)
        
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model*4,
            dropout=dropout, batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        self.dropout = nn.Dropout(dropout)
        self.shared_fc = nn.Linear(d_model, 32)
        
        # 多任务输出头
        self.heads = nn.ModuleDict({
            '5d': nn.Linear(32, 2),
            '10d': nn.Linear(32, 2),
            '20d': nn.Linear(32, 2),
        })
        self.relu = nn.ReLU()
    
    def forward(self, x, return_attention=False):
        # x: (batch, seq_len, input_dim)
        x = self.input_fc(x)
        x = self.pos_encoder(x)
        
        # 提取注意力权重
        if return_attention:
            # 手动通过每一层以提取注意力
            attention_weights = []
            for layer in self.transformer.layers:
                # 自注意力前向传播
                src = x
                src2, attn_weights = layer.self_attn(
                    src, src, src, attn_mask=None,
                    key_padding_mask=None, need_weights=True, average_attn_weights=True
                )
                src = src + layer.dropout1(src2)
                src = layer.norm1(src)
                src2 = layer.linear2(layer.dropout(layer.activation(layer.linear1(src))))
                src = src + layer.dropout2(src2)
                x = layer.norm2(src)
                attention_weights.append(attn_weights)
            
            # 平均池化
            pooled = x.mean(dim=1)
        else:
            x = self.transformer(x)
            pooled = x.mean(dim=1)
            attention_weights = None
        
        pooled = self.dropout(pooled)
        shared = self.relu(self.shared_fc(pooled))
        
        outputs = {k: head(shared) for k, head in self.heads.items()}
        return outputs, attention_weights


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=500):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe.unsqueeze(0))
    
    def forward(self, x):
        return x + self.pe[:, :x.size(1)]


# ============ 多任务数据构建 ============
def build_multitask_sequences(df, seq_len=20):
    """构建多任务序列数据集（5日/10日/20日同时预测）"""
    feature_cols = [
        'return_1d', 'volatility_5d', 'volatility_20d',
        'macd_dif', 'macd_dea', 'macd_bar',
        'rsi6', 'rsi12', 'cci14', 'kdj_k', 'kdj_d', 'kdj_j',
        'boll_width', 'ma5_gt_ma10', 'ma10_gt_ma20', 'close_gt_ma5',
        'volume_ratio', 'turnover_rate',
        'sh_return_1d', 'sh_return_5d', 'sh_volatility', 'sh_macd_dif', 'sh_rsi6',
        'cy_return_1d', 'kc_return_1d',
        'relative_sh', 'relative_cy', 'relative_kc',
        'market_bull'
    ]
    
    # 多任务标签
    for days in [5, 10, 20]:
        df[f'future_return_{days}d'] = df['close'].shift(-days) / df['close'] - 1
        df[f'label_{days}d'] = (df[f'future_return_{days}d'] > 0).astype(int)
    
    valid = df[feature_cols + ['label_5d', 'label_10d', 'label_20d']].notna().all(axis=1)
    df_valid = df[valid].reset_index(drop=True)
    
    X, y_dict, dates = [], {'5d': [], '10d': [], '20d': []}, []
    
    for i in range(seq_len, len(df_valid)):
        X.append(df_valid[feature_cols].iloc[i-seq_len:i].values)
        for k in ['5d', '10d', '20d']:
            y_dict[k].append(df_valid[f'label_{k}'].iloc[i])
        dates.append(df_valid['trade_date'].iloc[i])
    
    return np.array(X), y_dict, dates, feature_cols


# ============ 多任务训练 ============
def train_multitask(model, train_loader, val_loader, epochs=100, lr=0.001, patience=15):
    model = model.to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    
    best_val_loss = float('inf')
    best_model_state = None
    patience_counter = 0
    
    for epoch in range(epochs):
        # 训练
        model.train()
        train_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(device)
            y_batch = {k: v.to(device) for k, v in y_batch.items()}
            
            optimizer.zero_grad()
            outputs, _ = model(X_batch)
            
            # 多任务损失 = 各任务损失之和
            loss = sum(criterion(outputs[k], y_batch[k]) for k in outputs.keys())
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        
        train_loss /= len(train_loader)
        
        # 验证
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch = X_batch.to(device)
                y_batch = {k: v.to(device) for k, v in y_batch.items()}
                outputs, _ = model(X_batch)
                loss = sum(criterion(outputs[k], y_batch[k]) for k in outputs.keys())
                val_loss += loss.item()
        
        val_loss /= len(val_loader)
        scheduler.step(val_loss)
        
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model_state = model.state_dict().copy()
            patience_counter = 0
        else:
            patience_counter += 1
        
        if (epoch + 1) % 10 == 0:
            print(f"  Epoch {epoch+1}/{epochs} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f}")
        
        if patience_counter >= patience:
            print(f"  早停于 Epoch {epoch+1}")
            break
    
    if best_model_state is not None:
        model.load_state_dict(best_model_state)
    
    return model


# ============ 多任务评估 ============
def evaluate_multitask(model, test_loader, symbol):
    model.eval()
    all_probs = {'5d': [], '10d': [], '20d': []}
    all_preds = {'5d': [], '10d': [], '20d': []}
    all_labels = {'5d': [], '10d': [], '20d': []}
    
    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(device)
            outputs, _ = model(X_batch)
            for k in outputs.keys():
                probs = torch.softmax(outputs[k], dim=1)[:, 1].cpu().numpy()
                preds = outputs[k].argmax(dim=1).cpu().numpy()
                all_probs[k].extend(probs)
                all_preds[k].extend(preds)
                all_labels[k].extend(y_batch[k].numpy())
    
    print(f"\n  [{symbol}] 多任务Transformer评估:")
    results = {}
    for k in ['5d', '10d', '20d']:
        y_true = np.array(all_labels[k])
        y_pred = np.array(all_preds[k])
        y_prob = np.array(all_probs[k])
        
        acc = accuracy_score(y_true, y_pred)
        auc = roc_auc_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.5
        
        # 一致性：三个任务预测方向一致的比例
        results[k] = {'accuracy': acc, 'auc': auc, 'probs': y_prob, 'preds': y_pred}
        print(f"    {k}预测: 准确率{acc:.1%} | AUC:{auc:.3f}")
    
    # 三任务一致性
    agree = ((np.array(all_preds['5d']) == np.array(all_preds['10d'])) & 
             (np.array(all_preds['10d']) == np.array(all_preds['20d']))).mean()
    all_up = (np.array(all_preds['5d']) == 1) & (np.array(all_preds['10d']) == 1) & (np.array(all_preds['20d']) == 1)
    all_down = (np.array(all_preds['5d']) == 0) & (np.array(all_preds['10d']) == 0) & (np.array(all_preds['20d']) == 0)
    print(f"    三任务预测一致率: {agree:.1%}")
    print(f"    三任务同时看多: {all_up.mean():.1%} ({all_up.sum()}个)")
    print(f"    三任务同时看空: {all_down.mean():.1%} ({all_down.sum()}个)")
    
    results['agree'] = agree
    results['all_up'] = all_up
    results['all_down'] = all_down
    results['all_labels'] = all_labels
    
    return results


# ============ 注意力可视化 ============
def visualize_attention(model, X_sample, feature_cols, seq_len, symbol, output_dir):
    """可视化Transformer的自注意力权重"""
    model.eval()
    with torch.no_grad():
        X_tensor = torch.FloatTensor(X_sample).unsqueeze(0).to(device)
        outputs, attn_weights = model(X_tensor, return_attention=True)
    
    if attn_weights is None or len(attn_weights) == 0:
        print("未能提取注意力权重")
        return
    
    # 取最后一层的注意力权重 (1, seq_len, seq_len)
    last_layer_attn = attn_weights[-1][0].cpu().numpy()  # (seq_len, seq_len)
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    
    # 1. 注意力热力图
    ax1 = axes[0]
    im = ax1.imshow(last_layer_attn, cmap='hot', aspect='auto')
    ax1.set_title(f'{symbol} - Transformer最后一层自注意力权重', fontsize=12)
    ax1.set_xlabel('被关注的时间步（过去→现在）')
    ax1.set_ylabel('当前时间步')
    # 标记最后一天
    ax1.axvline(x=seq_len-1, color='cyan', linestyle='--', alpha=0.7, label='当前日')
    ax1.axhline(y=seq_len-1, color='cyan', linestyle='--', alpha=0.7)
    plt.colorbar(im, ax=ax1)
    ax1.legend()
    
    # 2. 当前日对各历史日的关注度
    ax2 = axes[1]
    current_day_attention = last_layer_attn[-1]  # 当前日对所有历史日的关注
    days = list(range(-seq_len+1, 1))
    ax2.bar(days, current_day_attention, color='steelblue', alpha=0.7)
    ax2.set_title('当前日对历史各日的注意力权重分布', fontsize=12)
    ax2.set_xlabel('相对天数（0=今天，-1=昨天...）')
    ax2.set_ylabel('注意力权重')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f'{symbol}_attention_visualization.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  注意力可视化已保存: {symbol}_attention_visualization.png")


# ============ 主流程 ============
def run_multitask_for_stock(symbol, name, seq_len=20):
    print(f"\n{'='*70}")
    print(f"多任务Transformer: {symbol} {name}")
    print(f"{'='*70}")
    
    df = load_stock_with_market(symbol)
    df = compute_features(df)
    
    X, y_dict, dates, feature_cols = build_multitask_sequences(df, seq_len)
    print(f"总样本: {len(X)}, 特征: {X.shape[2]}, 序列: {X.shape[1]}")
    
    # 标准化
    scaler = StandardScaler()
    X_reshaped = X.reshape(-1, X.shape[2])
    X_scaled = scaler.fit_transform(X_reshaped).reshape(X.shape)
    
    # 分割
    n = len(X_scaled)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    
    X_train, X_val, X_test = X_scaled[:train_end], X_scaled[train_end:val_end], X_scaled[val_end:]
    y_train = {k: np.array(v[:train_end]) for k, v in y_dict.items()}
    y_val = {k: np.array(v[train_end:val_end]) for k, v in y_dict.items()}
    y_test = {k: np.array(v[val_end:]) for k, v in y_dict.items()}
    
    print(f"训练: {len(X_train)} | 验证: {len(X_val)} | 测试: {len(X_test)}")
    
    # DataLoader
    train_dataset = MultiTaskDataset(X_train, y_train)
    val_dataset = MultiTaskDataset(X_val, y_val)
    test_dataset = MultiTaskDataset(X_test, y_test)
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32)
    test_loader = DataLoader(test_dataset, batch_size=32)
    
    # 训练
    print(f"\n--- 多任务Transformer训练 ---")
    model = MultiTaskTransformer(input_dim=X.shape[2], d_model=64, nhead=4, num_layers=2, dropout=0.3)
    model = train_multitask(model, train_loader, val_loader, epochs=100, lr=0.001, patience=15)
    
    # 评估
    results = evaluate_multitask(model, test_loader, symbol)
    
    # 注意力可视化（取测试集最后一个样本）
    print(f"\n--- 注意力可视化 ---")
    visualize_attention(model, X_test[-1], feature_cols, seq_len, symbol, OUTPUT_DIR)
    
    # 保存模型
    model_path = os.path.join(OUTPUT_DIR, f'multitask_transformer_{symbol}.pt')
    torch.save({
        'model_state': model.state_dict(),
        'scaler': scaler,
        'feature_cols': feature_cols,
        'seq_len': seq_len,
    }, model_path)
    print(f"  模型已保存: {model_path}")
    
    return model, scaler, feature_cols, results


# ============ 全市场扫描 ============
def scan_market():
    """扫描科创板全部股票，输出共振评分和异动信号"""
    import tushare as ts
    import os
    
    ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
    pro = ts.pro_api()
    
    print(f"\n{'='*70}")
    print("科创板全市场扫描")
    print(f"{'='*70}")
    
    # 获取科创板股票列表
    stocks_df = pro.stock_basic(exchange='', list_status='L', market='科创板')
    print(f"科创板共 {len(stocks_df)} 只股票")
    
    # 获取上证指数作为参考
    sh_index = pro.index_daily(ts_code='000001.SH', start_date='20260501', end_date='20260510')
    sh_index = sh_index.sort_values('trade_date')
    latest_sh_pct = sh_index['pct_chg'].iloc[0] if len(sh_index) > 0 else 0
    print(f"最新上证指数涨跌: {latest_sh_pct:.2f}%")
    
    results = []
    
    # 批量处理（限制50只避免超时）
    for i, row in stocks_df.head(50).iterrows():
        ts_code = row['ts_code']
        symbol = ts_code.split('.')[0]
        name = row['name']
        
        try:
            # 获取最近25天数据
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=35)).strftime('%Y%m%d')
            
            df_daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df_daily is None or len(df_daily) < 20:
                continue
            
            df_basic = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end)
            df = df_daily.merge(df_basic[['trade_date','volume_ratio','turnover_rate']], 
                               on='trade_date', how='left')
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # 快速计算关键指标
            close = df['close']
            if len(close) < 20:
                continue
            
            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd_dir = 1 if dif.iloc[-1] > dea.iloc[-1] else -1
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            rsi = 100 - (100 / (1 + gain.rolling(6).mean().iloc[-1] / loss.rolling(6).mean().iloc[-1]))
            rsi_dir = 1 if rsi > 50 else -1
            if rsi < 30: rsi_dir = 1
            if rsi > 70: rsi_dir = -1
            
            # 均线
            ma5 = close.rolling(5).mean().iloc[-1]
            ma10 = close.rolling(10).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma_dir = 1 if (close.iloc[-1] > ma5 and ma5 > ma10) else (-1 if close.iloc[-1] < ma5 else 0)
            
            # 量比
            vr = df['volume_ratio'].iloc[-1] if pd.notna(df['volume_ratio'].iloc[-1]) else 1.0
            vr_dir = 1 if vr > 1.5 else (-1 if vr < 0.5 else 0)
            
            # 涨跌幅
            pct = df['pct_chg'].iloc[-1]
            
            # 简化共振评分
            score = (macd_dir * 1.5 + rsi_dir * 1.0 + ma_dir * 1.2 + vr_dir * 0.5) / 4.2 * 10
            
            # 异动条件
            is_anomaly = score > 5 and vr > 1.2 and pct > 2
            is_oversold = rsi < 30 and pct < -3
            
            results.append({
                'symbol': symbol,
                'name': name,
                'close': close.iloc[-1],
                'pct_chg': pct,
                'volume_ratio': vr,
                'rsi': rsi,
                'score': score,
                'macd_dir': macd_dir,
                'ma_dir': ma_dir,
                'is_anomaly': is_anomaly,
                'is_oversold': is_oversold,
            })
            
        except Exception as e:
            continue
    
    results_df = pd.DataFrame(results)
    if len(results_df) == 0:
        print("未获取到有效数据")
        return
    
    # 排序输出
    results_df = results_df.sort_values('score', ascending=False)
    
    print(f"\n📊 扫描完成，有效股票: {len(results_df)} 只")
    
    print(f"\n🔥 共振评分Top 10（看多）:")
    top10 = results_df.head(10)[['symbol','name','close','pct_chg','volume_ratio','rsi','score']]
    print(top10.to_string(index=False))
    
    print(f"\n❄️ 共振评分Bottom 5（看空）:")
    bottom5 = results_df.tail(5)[['symbol','name','close','pct_chg','volume_ratio','rsi','score']]
    print(bottom5.to_string(index=False))
    
    anomalies = results_df[results_df['is_anomaly']]
    if len(anomalies) > 0:
        print(f"\n🚀 异动信号 ({len(anomalies)}只):")
        print(anomalies[['symbol','name','close','pct_chg','volume_ratio','score']].to_string(index=False))
    
    oversold = results_df[results_df['is_oversold']]
    if len(oversold) > 0:
        print(f"\n💡 超卖反弹信号 ({len(oversold)}只):")
        print(oversold[['symbol','name','close','pct_chg','rsi','score']].to_string(index=False))
    
    # 保存结果
    results_df.to_csv(os.path.join(OUTPUT_DIR, 'market_scan_results.csv'), index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: market_scan_results.csv")
    
    return results_df


# ============ cron配置生成 ============
def generate_cron_setup():
    """生成定时任务配置"""
    script_path = os.path.join(OUTPUT_DIR, 'market_scanner.py')
    log_path = os.path.join(OUTPUT_DIR, 'cron.log')
    
    cron_content = f"""#!/bin/bash
# 科创板多指标共振扫描定时任务
# 建议添加到crontab: crontab -e
# 每日15:30运行（收盘后）

# 环境变量
export TUSHARE_TOKEN=$(cat ~/.tushare_token 2>/dev/null || echo "")

# 运行扫描
cd {OUTPUT_DIR}
/opt/homebrew/bin/python3.11 {script_path} >> {log_path} 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 扫描完成" >> {log_path}
"""
    
    cron_sh_path = os.path.join(OUTPUT_DIR, 'run_scanner.sh')
    with open(cron_sh_path, 'w', encoding='utf-8') as f:
        f.write(cron_content)
    os.chmod(cron_sh_path, 0o755)
    
    crontab_line = f"""# 科创板共振扫描 - 每日15:30运行
30 15 * * 1-5 {cron_sh_path}
"""
    
    crontab_path = os.path.join(OUTPUT_DIR, 'crontab.txt')
    with open(crontab_path, 'w', encoding='utf-8') as f:
        f.write(crontab_line)
    
    print(f"\n{'='*70}")
    print("定时任务配置已生成")
    print(f"{'='*70}")
    print(f"运行脚本: {cron_sh_path}")
    print(f"crontab配置: {crontab_path}")
    print(f"\n启用方式:")
    print(f"  1. chmod +x {cron_sh_path}")
    print(f"  2. crontab -e")
    print(f"  3. 添加内容: 30 15 * * 1-5 {cron_sh_path}")
    print(f"\n或一键安装:")
    print(f"  crontab {crontab_path}")


# ============ 入口 ============
if __name__ == '__main__':
    print("="*70)
    print("多任务Transformer + 注意力可视化 + 全市场扫描")
    print("="*70)
    
    # 1. 多任务Transformer训练
    all_results = []
    for symbol, name in [('688322', '奥比中光-UW'), ('688017', '绿的谐波')]:
        res = run_multitask_for_stock(symbol, name, seq_len=20)
        all_results.append((symbol, name, res))
    
    # 2. 全市场扫描
    print(f"\n{'='*70}")
    scan_market()
    
    # 3. 生成cron配置
    generate_cron_setup()
    
    print("\n" + "="*70)
    print("全部完成！")
    print("="*70)
