---
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
tags: [backtest, cross-validation, significance, sharpe-distribution, cpcv]
module: kss/backtest
---

# CPCV 组合净化交叉验证

## Problem Frame

Walk-forward 给点估计（单条 OOS 净值曲线），掩盖了 Sharpe 在不同窗口上的塌陷风险。实测：科创板单股从 1.93 跌到 -0.53，但 `WalkForwardCombiner.run()` 只报告全期聚合数字，看不到分布。

根本问题：**用一个数字决策部署，把抽样噪声当成信号稳定性**。

Combinatorial Purged CV（CPCV，López de Prado 2018）把 T 个 bar 分成 k 折，选 p 折做测试，产生 C(k,p) 条互不重叠的 OOS 路径；把这些路径的 Sharpe/IC 集合起来，得到分布而非单值，再喂进已有的 `Significance.deflated_sharpe` / `Significance.is_deployable`。

**算力约束是第一约束**：210 条路径（k=10, p=4）× 每条跑完整回测 → 单用户本地机器要求可配折数、可缓存折叠结果、运行时间可感知。

---

## Actors

| 角色 | 职责 |
|------|------|
| **用户（回测分析师）** | 配置 CPCV 参数、读分布结果、决定是否部署 |
| **CPCVBacktester** | 新模块；生成折叠、调度回测、聚合 OOS 路径 |
| **WalkForwardCombiner** | 现有；作为单折回测的执行后端（不改） |
| **Significance** | 现有；`deflated_sharpe` / `is_deployable` 接收分布输入（不改接口，按需包装） |
| **折叠缓存层** | 可选；磁盘 pickle/parquet 缓存单折结果，支持断点续跑 |

---

## Key Flows

### F1：标准 CPCV 跑完整分布

```
用户调用 CPCVBacktester(factor_df, feature_cols, k=10, p=4, combiner_builder=...)
  → 生成 C(k,p) = 210 个测试折组合（含 purge + embargo bar 数）
  → 对每组合：把非测试折数据拼成训练集，跑 WalkForwardCombiner
  → 收集 210 条 OOS returns 序列
  → 对每条算 Sharpe/IC，输出分布（p5/p50/p95 + PBO 概率）
  → 把分布的 p50 Sharpe 喂 Significance.deflated_sharpe（n_trials=210）
```

### F2：轻量模式（k=5, p=2 = 10 条路径）

同 F1，折数收小，适合快速检验或低资源机器。

### F3：缓存续跑

```
单折结果写 storage/cpcv_cache/{run_id}/fold_{i}.pkl
CPCVBacktester 启动时检测已完成的折 → 跳过 → 只跑剩余
```

### F4：并入现有 significance 决策

```
cpcv_result.sharpe_dist  →  Significance.bootstrap_ci（metric_fn=年化Sharpe）
cpcv_result.pbo          →  部署门槛补充判断（PBO < 0.55 为通过建议线）
```

---

## Acceptance Examples

### Example A：分布宽度暴露塌陷

```python
res = CPCVBacktester(
    factor_df=df,
    feature_cols=cols,
    k=10, p=4,
    combiner_builder=WalkForwardCombiner.builder_sharpe_topk(top_k=3),
    purge_days=5,
    embargo_days=2,
).run()

assert res["n_paths"] == 210
assert res["sharpe_p5"] < res["sharpe_p50"] < res["sharpe_p95"]
# 点估计 1.93 但 p5 < 0 → 分布揭示不稳定
assert isinstance(res["pbo"], float)  # Probability of Backtest Overfitting
```

### Example B：PBO 喂进部署门槛

```python
# 把分布 p50 作为代表 Sharpe 送 DSR，n_trials=210（路径数）
dsr = Significance.deflated_sharpe(
    sharpe=res["sharpe_p50"],
    n_trials=res["n_paths"],
    n_obs=res["median_oos_len"],
)
assert 0.0 <= dsr <= 1.0
```

### Example C：缓存续跑（断点后重启不重算）

```python
r1 = CPCVBacktester(..., cache_dir="storage/cpcv_cache/run01").run()
# 假设中途中断，重启后
r2 = CPCVBacktester(..., cache_dir="storage/cpcv_cache/run01").run()
assert r1["n_paths"] == r2["n_paths"]  # 结果一致，不重跑已缓存折
```

### Example D：轻量模式

```python
res_fast = CPCVBacktester(factor_df=df, ..., k=5, p=2).run()
assert res_fast["n_paths"] == 10  # C(5,2)
```

---

## Requirements

### R1：折叠生成（核心）

- R1.1 输入 `k`（总折数）、`p`（测试折数），生成所有 `C(k,p)` 个测试折集合，时间顺序连续（不随机打乱 bar）。
- R1.2 **purge**：每个训练/测试边界两侧各去掉 `purge_days` 个 bar，防 label 泄漏（next_day_return 向前看 1 bar，至少 purge=1）。
- R1.3 **embargo**：测试集结束后的训练数据额外排除 `embargo_days` 个 bar，防泄漏方向二。
- R1.4 折叠边界按 bar 数等分（不按日历月），确保每折样本量可控。
- R1.5 暴露 `min_train_bars` 参数：若某组合训练样本不足此值，跳过该路径并记录 warning。

### R2：回测调度

- R2.1 对每个路径，用训练折数据（purge/embargo 后）实例化 `WalkForwardCombiner`，调用 `.run()`，取 `net_return` 序列中属于测试折的段落。
- R2.2 路径失败（WFC 抛异常 / 训练不足）→ 记录 failed_paths，不中断整体；最终报告中标注。
- R2.3 支持 `max_workers` 参数（默认 1，顺序跑）；若 `max_workers > 1` 用 `concurrent.futures.ProcessPoolExecutor`（受限于 GIL，多进程）。但默认单进程以保证本地机器不卡死。

### R3：算力配置（单用户本地约束）

- R3.1 `k` 默认 6，`p` 默认 2（C(6,2)=15 条路径），轻量优先；用户可升到 k=10,p=4。
- R3.2 运行前输出预估路径数 + 单折耗时估算（基于 factor_df 行数 × retrain_freq）。
- R3.3 `cache_dir` 可选：指定后单折结果写 pickle，再次实例化同参数自动跳过已算折。
- R3.4 折叠缓存 key = hash(factor_df shape + feature_cols + k + p + purge + embargo + combiner_builder.__name__)，参数变化自动失效。

### R4：OOS 路径聚合

- R4.1 每条路径输出年化 Sharpe、最大回撤、Calmar、净收益序列长度。
- R4.2 聚合指标：`sharpe_p5 / p25 / p50 / p75 / p95`（百分位）+ `sharpe_mean` + `pct_positive`（Sharpe>0 的路径占比）。
- R4.3 **PBO（Probability of Backtest Overfitting）**：用 CPCV 标准定义——将 C(k,p) 条路径按训练期 Sharpe 排序，统计测试期 Sharpe < 中位数的比例；接近 0.5 = 过拟合严重。
- R4.4 输出 `n_paths_valid`（成功路径数）、`n_paths_failed`，总数 < C(k,p) × 0.7 时发出 UserWarning。

### R5：与现有接口对接

- R5.1 `CPCVBacktester` 接受与 `WalkForwardCombiner` 相同的 `combiner_builder: CombinerBuilder` 签名（类型复用，不改 `CombinerBuilder` 定义）。
- R5.2 `Significance.deflated_sharpe` 直接可用，`n_trials` 传 `n_paths_valid`；不改 significance.py 接口。
- R5.3 `Significance.bootstrap_ci` 可对 `sharpe_values`（210 个浮点）做分布 CI，但 CPCV 路径本身已是无放回抽样，bootstrap_ci 在此为可选补充而非必须。
- R5.4 `is_deployable` 可接收 `strategy_family="mined"` 搭配 CPCV 分布输入使用，文档说明推荐组合。

### R6：与 walk-forward 并存

- R6.1 CPCV 是**可选更严验证**，不替换 walk-forward。典型用法：WF 先跑（快），CPCV 在 WF 通过后跑（慢但更严）。
- R6.2 两者共用 `CombinerBuilder` 工厂，无额外适配。
- R6.3 不在 `WalkForwardCombiner` 内部调用 CPCV（方向单向：CPCV 调 WFC，反之不成立）。

---

## Scope Boundaries

**In scope**
- `kss/backtest/cpcv.py`：`CPCVBacktester` 类 + 折叠生成 + 调度 + 聚合
- 折叠缓存（pickle，单文件 per fold）
- PBO 计算
- 文档：使用示例 notebook 或 docstring

**Out of scope（明确排除）**
- 跨股票截面 CPCV（本次只支持单股票时序，与 WFC 对齐）
- GPU 加速 / 分布式计算
- 实时流式更新折叠
- 修改 `significance.py` / `walk_forward_combiner.py`（只读接口）
- UI / Desktop 集成（先 CLI / notebook）

---

## Key Decisions

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 折叠边界方式 | 等 bar 数（不按日历） | 回测样本量一致，Sharpe 可比；日历折在 A 股非交易日分布不均 |
| 默认 k=6,p=2 | 15 条路径 | 本地 5 分钟内可跑完；k=10,p=4=210 条作为严格模式 |
| 并发默认单进程 | max_workers=1 | 避免 Mac 本地 fork 内存炸，用户自行升并发 |
| PBO 定义 | CPCV 标准（训练Sharpe排序→测试中位以下占比） | 与 LdP 原著一致，易解释 |
| 缓存 key | 参数 hash（不含 combiner 内部随机种子） | combiner_builder 是 lambda/closure，__name__ 有限；需文档提醒换参数要改 cache_dir |
| WFC 作为调度后端 | 直接实例化 WFC | 不重写信号/仓位逻辑，bug 修复自动继承 |

---

## Open Questions

### Blocking（实现前必须明确）

- **OQ-1**：purge_days 的默认值——`next_day_return` 向前看 1 bar，purge=1 够不够？还是需要考虑滚动 z-score 的 rolling_window 边界泄漏？（推测：rolling z-score 在每条 bar 上只用历史，不额外泄漏，purge=1 应够；需确认 `SingleStockAnalyzer._rolling_zscore` 是严格因果的。）
- **OQ-2**：测试折的 OOS returns 截取方式——WFC.run() 返回全期 `net_return`，如何精确截取属于当前测试折的段落？（WFC 内部以 bar 索引控制，fold 边界需转换为 factor_df 的整数位置索引后传入，或拆分 factor_df 再传入——两种方式资源消耗不同，需决策。）

### Deferred（不阻塞初版，后续迭代）

- **OQ-3**：IC 分布（per path 算 IC 均值/ICIR）是否纳入初版？当前需求只含 Sharpe；IC 分布是自然延伸但会增加接口复杂度。
- **OQ-4**：折叠缓存的失效粒度——目前按整个 run hash，未来是否支持"只换 combiner_builder 复用已缓存的 factor 折叠"？需要把折叠缓存与回测结果缓存分层。
- **OQ-5**：多股票批量 CPCV（遍历 stock_id list）是否包进 CLI 入口？截面 CPCV 超出本 issue 范围，但批量单股 CPCV 是合理扩展。

---

## Success Criteria

| 标准 | 验证方法 |
|------|----------|
| C(k,p) 路径数正确 | `assert res["n_paths"] == math.comb(k, p)` |
| purge/embargo 无泄漏 | 对已知 look-ahead 数据集验证：purge=0 时 Sharpe 虚高，purge=purge_days 后回落 |
| 分布宽度 > 0 | `assert res["sharpe_p95"] > res["sharpe_p5"]`（真实 factor_df 上） |
| PBO ∈ [0,1] | 数值范围检查 + 随机信号 PBO 应接近 0.5 |
| 缓存续跑结果一致 | 完整跑 vs 中断后续跑，最终 sharpe_p50 diff < 1e-9 |
| 默认配置本地 < 5min | k=6,p=2，factor_df 约 500 bar × 5 因子，MacBook Pro M 系列 |
| 不改现有接口 | `WalkForwardCombiner` / `Significance` 的测试套件全绿 |
