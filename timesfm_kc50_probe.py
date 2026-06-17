"""TimesFM 零样本 vs 朴素基线 —— 科创50 指数最小可行性验证.

问题：TimesFM 在 A 股科创50 上零样本预测，是否真的比"随机游走/掷硬币"强？
做法：滚动前向窗口，每点用过去 CONTEXT 天收盘价 → 预测未来 HORIZON 天，
     与实际对比。指标：
     - 价格 MAPE：TimesFM vs 朴素(末值持平)
     - 方向准确率：未来 1 日 / 5 日涨跌方向，TimesFM vs 50% 基准
不入库，纯探针。
"""
from __future__ import annotations
import numpy as np
import pandas as pd

CTX = 256       # context 天数
H = 5           # 预测 horizon
CSV = "/Users/zcdeng/projects/KSS/idx_000688_SH.csv"

df = pd.read_csv(CSV)
df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
df = df.sort_values("trade_date").reset_index(drop=True)
close = df["close"].to_numpy(dtype=float)
N = len(close)
print(f"科创50: {N} 个交易日 {df.trade_date.iloc[0].date()} → {df.trade_date.iloc[-1].date()}")

# 滚动评估点：留足 context + horizon
starts = list(range(CTX, N - H, 6))
print(f"评估点数: {len(starts)}  (context={CTX}, horizon={H})")

import timesfm
print("加载 TimesFM 2.5 200M (torch)，首次会从 HF 下权重...")
model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
    timesfm.TimesFM_2p5_200M_torch.DEFAULT_REPO_ID)
model.compile(timesfm.ForecastConfig(
    max_context=CTX, max_horizon=H,
    normalize_inputs=True, use_continuous_quantile_head=False,
))
print("模型就绪，开始批量预测...")

contexts = [close[s - CTX:s] for s in starts]
point, _ = model.forecast(horizon=H, inputs=contexts)
point = np.asarray(point)  # [n_points, H]

# 评估
tf_dir1, tf_dir5, naive_mape, tf_mape = [], [], [], []
for i, s in enumerate(starts):
    last = close[s - 1]
    actual = close[s:s + H]
    fc = point[i]
    # 方向：未来 1 日 / 5 日
    tf_dir1.append(np.sign(fc[0] - last) == np.sign(actual[0] - last))
    tf_dir5.append(np.sign(fc[-1] - last) == np.sign(actual[-1] - last))
    # MAPE（5 日路径）
    tf_mape.append(np.mean(np.abs((fc - actual) / actual)))
    naive_mape.append(np.mean(np.abs((last - actual) / actual)))  # 末值持平

tf_dir1 = np.array(tf_dir1); tf_dir5 = np.array(tf_dir5)
n = len(starts)

# 实际涨跌的基准分布（A 股是否本就偏多/偏空）
up1 = np.mean([np.sign(close[s] - close[s-1]) > 0 for s in starts])
up5 = np.mean([np.sign(close[s+H-1] - close[s-1]) > 0 for s in starts])

print("\n" + "="*56)
print("结果（科创50 零样本，n={} 个滚动窗口）".format(n))
print("="*56)
print(f"价格 5 日路径 MAPE:  TimesFM {np.mean(tf_mape)*100:.2f}%  vs  朴素末值 {np.mean(naive_mape)*100:.2f}%")
print(f"  → TimesFM {'更准' if np.mean(tf_mape)<np.mean(naive_mape) else '没更准'}（差 {(np.mean(naive_mape)-np.mean(tf_mape))*100:+.2f}pp）")
print()
print(f"方向准确率（vs 50% 掷硬币）:")
print(f"  未来 1 日涨跌:  {tf_dir1.mean()*100:.1f}%   (实际上涨占比 {up1*100:.0f}%)")
print(f"  未来 5 日涨跌:  {tf_dir5.mean()*100:.1f}%   (实际上涨占比 {up5*100:.0f}%)")
# 二项检验：方向准确率显著高于 0.5 吗
from scipy import stats
p1 = stats.binomtest(int(tf_dir1.sum()), n, 0.5, alternative="greater").pvalue
p5 = stats.binomtest(int(tf_dir5.sum()), n, 0.5, alternative="greater").pvalue
print(f"  方向>50% 的 p 值:  1日 p={p1:.3f}   5日 p={p5:.3f}  (>0.05 = 与掷硬币无显著差异)")
print("="*56)
