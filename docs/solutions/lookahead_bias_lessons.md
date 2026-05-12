---
title: 七轮实验：事后偏差的层层剥离
tags: [backtest, look-ahead-bias, methodology, equity-quant, selection-bias, deflated-sharpe]
problem_type: bias-detection
module: kss/backtest
created: 2026-05-12
---

# 七轮实验：事后偏差的层层剥离

## TL;DR

KSS 仓库过去几周跑了 7 轮回测，从单股票 macd_hist Sharpe **1.18** 起步，每加一层 bias 检查 Sharpe 就掉一截，最终在 LGB 多因子 walk-forward 后跌到 **-0.53**。唯一通过全部检查的真 alpha 是 `log_mv` 反向单因子（Sharpe **1.93**, p=0.017, DSR=0.754, 科创板小市值效应）。这条衰减曲线本身就是方法论：**任何高 Sharpe 数字未经八层逐一证伪前都应视为虚高**。本文把这 8 层 bias 与 KSS 对应检测工具固化下来，供任何新策略复用。

> **更新 2026-05-12**：新增第 8 层（自动化研究循环 meta-bias 极致），基于对 Microsoft RD-Agent-Quant 论文（arxiv 2505.15155）的方法论审视。详见第 3.8 节。

## 一、八层事后偏差清单

| # | 层名 | 在哪里发生 | KSS 检测工具 | 哪一轮揭示 |
|---|------|-----------|--------------|-----------|
| 1 | 单股票选股偏差 | 在 1 只票上回测得高 Sharpe，结论无法推广 | `kss/backtest/cross_section.py:29` `factor_cross_section_backtest` | 第 4 轮 macd cross_section |
| 2 | 阈值优化偏差 | z-score 阈值在全样本网格搜索后选单点最优 | `kss/backtest/single_stock.py:478` `threshold_grid_search` 的 `robust_sharpe` 列 | 第 3 轮 combo v3 网格 |
| 3 | 静态权重偏差 | 用全样本 scan_table / IC 算因子权重再回放 | `kss/backtest/walk_forward_combiner.py:35` `WalkForwardCombiner` | 第 3 轮 combo v3 WF |
| 4 | 全样本选因子偏差 | 截面 IC 在整段历史上选 Top K 因子，再喂进 walk-forward | `kss/features/cross_section_selection.py:23` `make_ic_topk_selector` + `BacktestEngine.walk_forward(feature_selector=...)` | 第 6 轮 kcb50 WF |
| 5 | 行业 / 市值暴露偏差 | "α" 实际只是 size factor / 行业 β 暴露 | `kss/backtest/benchmark.py:110` `Benchmark.alpha_beta` + 残差归因（待建） | 第 5 轮 kcb50 LGB（log_mv 主导） |
| 6 | 实盘成交偏差 | 信号日 close 即时成交 / 忽略买卖费率 / T+0 假设 | `kss/backtest/cost_model.py` + `engine.py` T+1 开盘建仓 / T+2 换仓约定 | 全部 7 轮均显式设 cost = 0.10% / 0.20% |
| 7 | 多策略选择偏差（meta） | 跑了 N 个策略只发一个最优的 → DSR 必须扣 n_trials | `kss/backtest/significance.py:96` `Significance.deflated_sharpe` + `strategy_family` API | 第 5/6/7 轮 log_mv DSR=0.754 |
| 8 | 自动化研究循环 meta-bias 极致 | 多 agent / bandit 自动跑 N 个策略发"最优"——meta-bias 工业化版本 | `Significance.is_deployable(strategy_family="mined", n_trials=100+)` + `StrategyRegistry` 硬性拒 | Qlib RD-Agent 论文（arxiv 2505.15155）案例分析 |

## 二、Sharpe 衰减时间线

| # | 报告 | Sharpe | 关键改动 | 衰减来源 |
|---|------|--------|---------|----------|
| 1 | `688017_deep_report.md` | **1.18** | macd_hist 单股票单因子时序 z-score | 起点（含层 1+2+3 三重偏差） |
| 2 | `688017_combo_report.md` | **1.00** | Sharpe Top 5 等权组合 | 多因子稀释 |
| 3a | `688017_combo_v3_report.md`（事后） | **1.00** | 同样 Top 5 等权（全样本算 Sharpe 排名） | 仍含层 3 权重偏差 |
| 3b | `688017_combo_v3_report.md`（WF） | **0.24** | 滚动 200 天历史选 Top K | 去层 3 后 Sharpe -76% |
| 4 | `macd_cross_section_report.md` | **0.25** | 51 只科创板截面验证 macd_hist | 去层 1 后 IC=-0.0165 反向 |
| 5 | `kcb50_lgb_cross_section_report.md` | **-0.37** | LGB Top 10（全样本 IC 选因子） | 多弱因子 + MSE 训练 |
| 6 | `kcb50_wf_factor_selection_report.md` | **-0.53** | feature_selector 也 walk-forward | 去层 4 后 ΔSharpe -0.16 |
| 7 | `kcb50_lgb_cross_section_report.md` 内 baseline | **+1.93** | 单 `log_mv` 反向（小市值因子） | 唯一通过所有层的真 alpha |

**核心观察**：1.18 → -0.53 的衰减不是因子变差，是把藏在数字里的 bias 一层层抠出来；唯一活下来的 `log_mv` 反向 t-stat=2.40, p=0.017, DSR=0.754（n_trials=5 矫正后），年化 +88.4%，换手 2.9%，最大回撤 -36.9%，IR=1.24。

## 三、每层偏差详解

### 3.1 单股票选股偏差

**现象**：在 1 只票（688017）上 macd_hist 时序 z-score 策略拿到 Sharpe 1.18 / 年化 +57.7%；放到 51 只科创板做截面验证，Sharpe 跌到 0.25，截面 IC=-0.0165（**方向都反了**），t-stat=-1.93。

**原因**：单股噪声 + 该股自身趋势性吃掉了"看似很强"的因子表现。`688017_deep_report.md` 备注里早就标注过 "单股样本量小，单因子 IC 与策略 Sharpe 都存在 selection bias，需结合 Deflated Sharpe 修正"——但没人当真。

**检测**：用 `kss/backtest/cross_section.py:29` 的 `factor_cross_section_backtest` 在 ≥50 只股票池上重跑同一因子，比较时序 Sharpe vs 截面 Sharpe。

**修复**：单股票回测仅作 idea generation 用，**任何投产决策必须基于横截面验证**。

### 3.2 阈值优化偏差

**现象**：第 3 轮 combo v3 对 macd_hist 在 (upper, lower) ∈ {0.25...2.0} × {-2.0...-0.25} 网格搜索，最优点 (u=0.75, l=-1.0) Sharpe **1.38**，比默认 ±1 的 1.18 提升 +17%。看起来是免费 alpha。

**原因**：64 格里挑一格的最大值天然带有 selection bias；邻域中位数才是更可信的实盘可达 Sharpe。

**检测**：`kss/backtest/single_stock.py:478` 的 `threshold_grid_search` 输出含 `robust_sharpe` 列（同 upper 或同 lower 相邻 2 格 Sharpe 中位数，见 `single_stock.py:553-554`）。看到单点 1.38 但 `robust_sharpe` 只有 1.15 → 邻域差异大就警惕过拟合。

**修复**：阈值要么 prior 选定（如 ±1σ 经验值），要么用 walk-forward 滚动选；in-sample 网格最优单点严禁直接投产。

**反面教材（2026-05 AlphaQuanter 论文 ablation, arxiv 2510.14264）**：
论文在最终配置 θ=0.05 附近做 θ±0.005（即 θ ∈ {0.045, 0.05, 0.055}）的
ablation"灵敏度分析"，三档结果相近就声称模型稳健. 但 ±0.005 是同一网格里
**相邻格点**，等价于本节"邻域 robust_sharpe" 替代真稳健性检验——in-sample
阈值调参伪装成 robustness 论证. 真稳健要做的是 walk-forward 阈值滚动选
+ DSR 矫正，而非在最优 θ 旁边平移 1%. 详见 `alpha_quanter_paper_comparison.md` 桶 C3.

### 3.3 静态权重偏差

**现象**：第 3 轮事后 `Sharpe 加权 Top5` 组合 Sharpe **1.09**，walk-forward 化以后掉到 **0.42**（ΔSharpe -0.67，年化 -32.9pp，事后偏差吃掉约 60% 的 Sharpe）。等权 Top5 更惨：1.00 → 0.24（事后偏差吃掉 **76%**）。

**原因**：scan_factors / ic_table 用全样本统计算出 "Top 5 因子" → 这本身就是偷看了未来。

**检测**：`kss/backtest/walk_forward_combiner.py:35` 的 `WalkForwardCombiner.run()`：每 retrain_freq 个交易日只用过去 train_window 历史重选 Top K 并构造 combiner，对比 WF Sharpe 与事后 Sharpe 的差距就是这一层偏差的量化值。

**修复**：所有 "选 Top K" 的逻辑必须 walk-forward；或者直接 prior 锁定单因子，不做基于样本统计的选择。

### 3.4 全样本选因子偏差

**现象**：第 5 轮 kcb50 LGB 用 `cross_section_ic_scan` 在**全样本**上选 5d horizon Top 10 因子（log_mv 居首），喂进 walk-forward LGB → Sharpe -0.37。第 6 轮把选因子也 walk-forward 化（`feature_selector` 滚动重选）→ Sharpe **-0.53**（ΔSharpe -0.16）。

**原因**：截面 IC 排名本身也是样本统计。即便后续 LGB 训练是 WF，选因子环节如果偷看了全样本仍然偏。

**检测**：`kss/features/cross_section_selection.py:23` 的 `make_ic_topk_selector` 工厂返回 `(train_df) -> list[str]`，把它传入 `kss/backtest/engine.py:149` `BacktestEngine.walk_forward(feature_selector=...)`（接管逻辑见 `engine.py:240-253`），每次 retrain 只用当窗 train_df 选因子。

**修复**：所有依赖样本统计的 hyper-parameter（因子集合、阈值、权重）一律滚动重选；prior 信念因子（如已知小市值效应）可绕过该层。

### 3.5 行业 / 市值暴露偏差

**现象**：第 5/6 轮看到 `log_mv` 反向独占鳌头（IC=-0.0511, t=-5.68 @ 5d；-0.0778, t=-8.39 @ 10d）→ 这其实就是经典 size factor / 小市值效应，并非新发现的 alpha。如果不在 base benchmark 里扣掉 size β，所有横截面 "alpha" 都可能只是 size 暴露的副产品。

**原因**：科创板等权 Top 20% 选股天然偏小盘；ML 模型会把小市值学进去当 "alpha"。

**检测**：`kss/backtest/benchmark.py:110` `Benchmark.alpha_beta` 给出对单一基准的 α/β 分解（log_mv 反向：年化 α=+102.8%, β=0.12, R²=0.01, IR=1.24）。**目前 KSS 还没做 BARRA 风格的多因子归因**——这是已知缺口（见第六节）。

**修复**：短期靠常识识别（出现 log_mv / pe / pb 主导时直接归因为风格暴露）；长期需要叠一层行业 / 市值中性化。

### 3.6 实盘成交偏差

**现象**：本仓库 7 轮回测全部显式约定：T 日 close 后产生信号，**T+1 开盘建仓，T+2 开盘换仓**（用 next_day_return），买入 0.10% / 卖出 0.20%（含印花税）。688017 全样本 cost 累计 24.0% — 不算成本会再虚高一截。

**原因**：close 即时成交、零成本、T+0 假设是新手最容易踩的坑；A 股 T+1 + 印花税 + 滑点 / 流动性约束都会侵蚀 paper Sharpe。

**检测**：`kss/backtest/cost_model.py`（买卖费率配置）+ `kss/backtest/engine.py` 默认 T+1 开盘 / T+2 换仓约定。换手率高的策略（LGB Top 10 换手 33.7%）要做敏感性扫描。

**修复**：cost 至少 0.10% / 0.20% 起步；高换手策略额外加 5-10bp 滑点假设；停牌、涨跌停过滤是下一步。

### 3.7 多策略选择偏差（meta-bias）

**现象**：本项目 7 轮跑了至少 5 套主策略（macd_hist 时序、Top5 等权、Top5 Sharpe 加权、LGB 全因子、LGB Top 10、单 log_mv 反向…），只汇报 Sharpe 最高的那个 = **典型 multiple testing**。log_mv 反向裸 Sharpe 1.93 / t-stat 2.40 / p=0.017 很漂亮，但跑了 N 个策略后任何一个 "看起来 p<0.05" 都可能是噪声。

**原因**：N 次试验取最大值的分布右尾比单次试验厚得多；裸 Sharpe / 裸 p 都低估 false positive。

**检测**：`kss/backtest/significance.py:96` `Significance.deflated_sharpe`（López de Prado 2014 PSR/DSR，输入 `n_trials` 矫正）+ `kss/backtest/significance.py:146` `bootstrap_ci`。log_mv 反向 DSR=**0.754**（n_trials=5 轻度矫正后仍显著），是 7 轮里唯一 DSR > 0.5 的策略。第 5 轮 LGB Top 10 DSR=0.036，第 4 轮 macd 截面 DSR=0.118 — 都不显著。

**修复**：发现"最优"策略后必须用 DSR 扣 trials，**裸 Sharpe / 裸 p-value 不作数**；样本期 ≥ 5 年才能稍微放心。

### 3.8 自动化研究循环的 meta-bias 极致（RD-Agent 案例）

**现象**：Microsoft 2025-05 论文 *R&D-Agent-Quant*（arxiv 2505.15155）用多 agent + Co-STEER 代码生成 + multi-armed bandit 调度器**自动迭代**因子 / 模型组合，号称 **2× 年化收益、因子数 -30%**。论文挂在 Qlib 上，号称"工业级方法"。

**为什么这是教科书反面教材**：

1. **没有 DSR**：论文全文未提 Deflated Sharpe / Probabilistic Sharpe / multiple testing 矫正。
2. **没有 walk-forward 选因子检验**：因子由 agent 自动生成，但论文未做"选因子也要 walk-forward"的第 4 层 bias 防御（见 3.4）。
3. **没有 n_trials 报告**：agent 实际尝试了多少个 idea？bandit 探索 N 个臂里取最优 = N 次试验取最大值；论文给的是最优 arm 的回测 Sharpe，但 n_trials 不在表格里。
4. **跑得越多反而越假**：bandit 调度的目的是"加速发现"——但在没有 DSR 防御下，调度越高效，selection bias 越严重。

**在 KSS 视角下重算**：若 agent 实际尝试 N=200 个因子 + 模型组合（保守估计），`Significance.deflated_sharpe(sharpe, n_trials=200, ...)` 会让原本 Sharpe 1.8 的"最优"被压到 DSR < 0.2，**直接拒绝上线**。

**真实可借鉴的部分**：
- 结构化 hypothesis log（每个 idea 记录 `{hypothesis, sharpe, dsr, n_trials, deployable}` JSONL）
- LLM 把领域知识转 task
- bandit 调度的探索 / 利用平衡

**真不可借鉴的部分**：
- 全自动跑 N 个策略只汇报最优
- 用裸 Sharpe / 年化作为效果指标
- 不暴露 n_trials 给 reader

**修复**：任何 "agent / 自动化 / LLM 跑出来的策略"都必须 strapped 上：
1. `strategy_family="mined"` (n_trials=100+) 的 DSR 矫正（见 `kss/backtest/significance.py:_FAMILY_TRIALS`）
2. `StrategyRegistry.register` 硬性拒绝（见 `kss/strategies/registry.py:60`）
3. 在报告里**明确暴露**实际尝试的全部组合数

**类似场景延伸（2026-05 FinRL-X 论文对比补）**：DRL allocator 与 LLM
sentiment 信号同属 hidden n_trials 范畴——DRL 每轮 policy update 都是一次
implicit trial、LLM prompt-engineering 调一版就是一次 trial，但论文里通常
不报告这两类 trial 数。**进入 KSS 必须按 `mined` 族 (n_trials ≥ 100) 处理**，
不是按 `tuned` 或 `single_factor`. 详见 `finrl_x_paper_comparison.md` 桶 C1.

**2026-05 第 11 轮 3 篇论文同范式案例集（hidden n_trials 教科书）**：

| 论文 | 范式 | hidden n_trials 来源 | 论文报告值 | mined 矫正后预估 |
|------|------|---------------------|-----------|------------------|
| QuantaAlpha (arxiv 2602.07085) | LLM mutation/crossover 因子挖掘 | iter 11-12 ≈ 350 候选因子 | CSI 300 IC 0.1501 | DSR(n=350) 大概率 < 0.4 |
| AlphaResearch (arxiv 2511.08522) | LLM idea→verify→optimize 双环境 RM 训练 | peer-review RM 隐式过滤 N 轮 | 8 数学问题 2/8 超 human | finance 域 0 重叠，不适用 |
| AlphaQuanter (arxiv 2510.14264) | GRPO + ReAct + Qwen2.5 7B 单 agent | 3 seeds × ablation × hyperparam | 5 股 122 天 ARR 34.94% | effective n≈2-3, DSR 不可信 |

**共同特征**：(a) 跑 N 次报告最优、(b) N 不在表格里、(c) 无 DSR / 无 multiple
testing 矫正 / 无 α-β 拆分. **检验它们的统一姿势**：把论文表面 Sharpe
带入 `Significance.deflated_sharpe(sharpe, n_trials=<上表 N>, T=<样本天数>)`
重算——本节 RD-Agent 案例已演示 N=200 让 Sharpe 1.8 → DSR < 0.2 的算式.

详见 `qlib_paper_comparison.md` / `finrl_x_paper_comparison.md` / `quanta_alpha_paper_comparison.md` / `alpha_research_paper_comparison.md` / `alpha_quanter_paper_comparison.md` 桶 C 系列.

**口号**：跑得快 → bias 多 → 必须用更严的门槛抵消。否则就是"自动化生产 false positive 的工厂"。

## 四、看到高 Sharpe 第一反应清单

按这个顺序逐项问，每跳过一项就在脑子里给 Sharpe 打折：

1. **样本是 1 只票还是 ≥50 只？** 单股 → 至少打 5 折，且必须横截面复测（第 4 轮把 1.18 打到 0.25）。
2. **阈值 / hyper-param 是 prior 选的还是网格搜的？** 网格搜的 → 看 `robust_sharpe` 邻域中位数而非单点（第 3.2 节）。
3. **因子权重 / 选择是不是全样本统计的结果？** 是 → walk-forward 化，预期 Sharpe 衰减 50-75%（第 3.3 节 688017 v3 数据）。
4. **因子集合本身是全样本筛的吗？** 是 → 第 4 层再砍一刀（kcb50 第 6 轮 ΔSharpe -0.16）。
5. **是不是 size / 行业 / β 暴露假扮 α？** 单看主导因子，出现 log_mv / pe / pb / 行业 dummy 就警惕。
6. **成交假设合理吗？** T+1 开盘 / 买 10bp + 卖 20bp / 高换手加滑点。
7. **跑了多少个候选策略？只汇报最优的一个？** DSR 把 n_trials 填进去；DSR < 0.5 直接判否。
8. **样本期跨牛熊吗？** < 3 年 → 置信区间宽到不可投产；本项目 2.2 年实际上都偏短。
9. **是不是 LLM agent / bandit / 自动化跑出来的？** 是 → 必须 `strategy_family="mined"` (n_trials=100+) 跑 DSR；agent 跑 N 个 idea 只汇报 Sharpe 最高的那个，meta-bias 量级 ≥ 单兵手动跑（见第 3.8 节 RD-Agent 案例）。

八问全过 → 才能称为"候选 alpha"；任何一问含糊就退回去重做。

## 五、KSS 工具速查

| 偏差类型 | KSS 工具与定位 |
|---------|--------------|
| 单股结论不普适 | `kss/backtest/cross_section.py:29` `factor_cross_section_backtest` |
| 阈值过拟合 | `kss/backtest/single_stock.py:478` `SingleStockAnalyzer.threshold_grid_search`（`robust_sharpe` 邻域稳健见 `single_stock.py:553-554`） |
| 权重事后偏差 | `kss/backtest/walk_forward_combiner.py:35` `WalkForwardCombiner` |
| 选因子事后偏差 | `kss/features/cross_section_selection.py:23` `make_ic_topk_selector` + `kss/backtest/engine.py:149` `BacktestEngine.walk_forward(feature_selector=...)` |
| 统计假阳性 / 多策略 | `kss/backtest/significance.py:96` `Significance.deflated_sharpe`, `kss/backtest/significance.py:146` `bootstrap_ci` |
| 信号质量诊断 | `kss/backtest/diagnostics.py:29` `SignalDiagnostics`（`ic_series:71`, `quantile_returns:173`, `monotonicity_score:244`, `cross_section_ic_scan:270`） |
| 基准超额验证 | `kss/backtest/benchmark.py:110` `Benchmark.alpha_beta` |
| 成本约束 | `kss/backtest/cost_model.py` + `engine.py` T+1/T+2 约定 |

## 六、还没解决的问题（诚实清单）

1. **BARRA 风格归因未实现**：现在只有单基准 α/β。`log_mv` 反向的 Sharpe 1.93 里有多少是 size factor 暴露、多少是真 specific α？无法分解。下一步建议增加 BARRA-CNE 风格的 size / value / momentum / volatility 多因子残差归因。
2. **样本期偏短**：2023-01-03 ~ 2026-05-08 仅 2.2 年，跨牛熊不足。`log_mv` 反向需要至少 2018-2020 那种小盘崩盘段做压力测试。
3. **股票池幸存者偏差**：`cs_data_688*.csv` 是当前科创板成分股，不含已退市与上市 < 100 天的新股。这一层 bias 在第 4-6 轮都明确标注但未消除。
4. **跨市场未验证**：所有结论只在科创板 51 只票上跑过；主板 / 创业板 / 中证 800 表现未知。
5. **行业 / 市值中性化未做**：第 5/6 轮都标了"未做行业 / 市值中性化"；下一步 `Robustness` 模块叠加。
6. **DSR n_trials 取值经验**：当前 n_trials=5 是手工设的；理论上应统计本项目实际跑过的全部参数组合（含网格点），实际值应在数百量级，DSR 会进一步收紧。
7. **`log_mv` 反向高换手日的成交假设**：换手 2.9% 算低，但极端日（建仓日）需要验证开盘流动性。
8. **行业映射数据缺失**：做行业中性化需要稳定的行业归属数据，目前 KSS 还没接入。

## 参考资料

- López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*. Journal of Portfolio Management.
- Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. (2014). *Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance*. Notices of the AMS.
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). *...and the Cross-Section of Expected Returns*. Review of Financial Studies. （多重检验在因子动物园里的经典讨论。）
- 本仓库 7 轮报告：`storage/reports/688017_deep_report.md`, `688017_combo_report.md`, `688017_combo_v3_report.md`, `macd_cross_section_report.md`, `kcb50_lgb_cross_section_report.md`, `kcb50_wf_factor_selection_report.md`。
