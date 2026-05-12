---
title: KSS 项目回顾
tags: [meta, retrospective, project-history]
problem_type: meta
module: docs
created: 2026-05-12
---

# KSS 项目回顾：科创板量化回测框架发展全史

## 一、项目起源与定位

KSS（Keda Stock System）是一个 A 股科创板量化回测与诊断工具链。最初目标是为科创 50 成分股做单股趋势预测，过程中发现"所有看起来很强的因子几乎都是 bias"——Sharpe 从 1.18 一路衰减到 -0.53，唯一活下来的真 alpha 是 `log_mv` 反向（小市值溢价，Sharpe 1.93 / 含 ExecutionModel 实盘可达 1.74）。

KSS 的核心价值不是"造一条 Sharpe 3 的策略"，而是"诚实告诉你哪些 Sharpe 其实是 bias"。它不是实盘下单系统、不是覆盖全 A 股的因子库、不是 backtrader/qlib 竞品。适用范围：个人量化研究者做科创板 51 股小池的横截面因子验证与 walk-forward 回测。35,000 行 Python 代码，459 测试 passed / 3 xfailed / 6 deselected (DL)。

## 二、发展历程：12 轮迭代

### 第 1 轮：单股起点（含 3 重偏差）
在 688017.SH 单股上跑 macd_hist 时序 z-score，Sharpe **1.18**，年化 +57.7%。报告 `688017_deep_report.md` 备注栏已标注"单股样本量小，需结合 Deflated Sharpe 修正"——但当时没人当真。

### 第 2 轮：多因子组合稀释
Sharpe Top5 等权组合 → Sharpe **1.00**，多因子稀释了最强的 macd_hist 信号。Top 5 均为趋势/量价类，相关性高。`688017_combo_report.md` 第 3 节标注"方向同质"问题。

### 第 3a/3b 轮：去静态权重偏差
3a（事后）：全样本算 Top 5 排名 → Sharpe 仍 1.00（含权重偏差）。3b（walk-forward 滚动 200 天重选）→ Sharpe **0.24**，事后偏差吃掉 **76%** 的 Sharpe。引入 `WalkForwardCombiner`（`kss/backtest/walk_forward_combiner.py:35`）作为检测工具。64 格阈值网格最优单点的"+17% Sharpe"也是 selection bias——邻域 robust_sharpe 仅为 1.15 vs 单点 1.38（`single_stock.py:553-554`）。

### 第 4 轮：去单股偏差，横截面验证
51 只科创板截面验证 macd_hist → Sharpe **0.25**，IC=-0.0165（**方向都反了**），t-stat=-1.93。引入 `factor_cross_section_backtest`（`kss/backtest/cross_section.py:29`）作为单股 idea 的必经复测。

### 第 5 轮：LGB 多因子，全样本选因子
用全样本 `cross_section_ic_scan` 选 5d horizon Top 10 因子（log_mv 居首，IC=-0.0511, t=-5.68），喂进 walk-forward LGB（MSE 回归）→ Sharpe **-0.37**。MSE 训练目标与排序需求错配。

### 第 6 轮：去全样本选因子偏差
把 feature_selector 也 walk-forward 化（`engine.py:240-253`）→ Sharpe **-0.53**（ΔSharpe -0.16）。同时引入 LGB Ranker（lambdarank，`kss/models/lightgbm_ranker.py:56`）替换 MSE，但未能扭转多因子整体弱势——瓶颈是因子 IC 弱 + 样本薄，不是模型。

### 第 7 轮（B1/B2）：唯一通过门槛的策略
单 `log_mv` 反向：Sharpe **1.93**，p=0.017，DSR=0.754（n_trials=5 轻度矫正）。B2 叠加 ExecutionModel（涨跌停过滤 + 部分成交 + 开盘滑点，`cost_model.py:87`）：Sharpe **1.74**（实盘可达 -0.19）。年化 +80.1%，回撤 -37.1%，换手 3.1%，IR=1.14。

### 第 8 轮（#44）：DSR 按策略族校准
旧版 DSR 默认 n_trials=10 对"先验单因子"过严。引入 `strategy_family` 机制（`significance.py:22-36`）：prior=1 / single_factor=2 / small_grid=5 / tuned=20 / mined=100。log_mv 从 `single_factor` (n_trials=2) 升到 `prior` (n_trials=1) 后 DSR=1.00，通过 `is_deployable`（`significance.py:278`）三门门槛（Sharpe≥0.5 + p<0.05 + DSR≥0.4）。

### 第 9 轮：Qlib 对比与 4 个借鉴点
跑 Microsoft Qlib + RD-Agent 论文（arxiv 2505.15155）对比分析（`qlib_paper_comparison.md`），实施 4 个借鉴点。结果按模式：**抄工程成功，抄方法论失败**。

| 借鉴点 | 结果 | 教训 |
|--------|------|------|
| #4.1 port Alpha158 因子库 | 97/158 个 |t|≥2，DSR(mined, n=158) **0 个通过**；连 log_mv prior 也被杂化到 DSR=0.014 | 工业因子库在小池上属 `mined` 族，必须最严矫正 |
| #4.2 ExecutionModel 加停牌/ST 过滤 | **唯一正面结果**：Survivorship bias raw_gap +16.24→0.000，Gap 2 RESOLVED | 实盘建模需要完整 universe + PIT 停牌名单（`cost_model.py:357`） |
| #4.3 DDG-DA sample_weight | LGB Ranker Sharpe **-0.05→-0.23**（加权后更差），回撤扩到 -52% | 瓶颈不是漂移，是 51 股小池上 LGB 多因子的根本问题 |
| #4.4 hypothesis log | 延后 | 结构化 idea 日志有价值，但先跑路线图 #33/#37 更重要 |

### 第 10 轮：FinRL-X 论文（arxiv 2603.21330）
FinRL-X 提出 weight-centric unified interface + 4 层 modular pipeline + RL allocator + LLM sentiment。KSS 判定：整体不抄。唯一两条值得抄的都是工程 fail-loud 改进（weight tracking error 度量 + reconciliation 缺日告警），均为小时级工作量。RL allocator/LLM sentiment 属 hidden n_trials 场景（须按 `mined` 族处理），weight-centric 在 A 股涨跌停/T+1 下是 leaky abstraction（target weight 在涨停板物理不可达）。详见 `finrl_x_paper_comparison.md`。

### 第 11 轮：3 篇 LLM-agent 论文（QuantaAlpha / AlphaResearch / AlphaQuanter）
三篇论文同属"LLM 多 agent 自动跑 N 个策略只汇报最优"家族，被 KSS 第 8 层 meta-bias 防御统一驳回：

| 论文 | hidden n_trials 量级 | KSS 判定 |
|------|---------------------|---------|
| QuantaAlpha (arxiv 2602.07085) | ≥350（5 iter × 10 directions × 3 expressions × mutation） | mined 族 DSR 几乎必然 < 0.4 |
| AlphaResearch (arxiv 2511.08522) | finance 域 0 重叠（packing circles mathematica） | 反向证伪价值：证明 LLM autonomous discovery 只在有客观 ground truth 的域 work |
| AlphaQuanter (arxiv 2510.14264) | 5 股 × 122 天 + 3 seeds × ablation（effective n ≥ 数十） | SR 0.65 在 KSS 门槛下不进 deployable；典型小样本 selection bias |

详见 `quanta_alpha_paper_comparison.md` / `alpha_research_paper_comparison.md` / `alpha_quanter_paper_comparison.md`。

## 三、功能全景

当前 35,000 行 Python（`kss/` + `scripts/`），以下为生产级模块。

| 模块 | 文件 | 功能 | 成熟度 |
|------|------|------|--------|
| 数据层 | `data/` | Tushare + AKShare 双源，CSV + SQLite (`storage/kss_quotes.db`) 缓存 | 生产可用 |
| 因子工程 | `features/alpha158.py` + `pipeline.py` | Qlib Alpha158 移植（158 因子）+ KSS 原生 49 因子 + 行业市值中性化 | alpha158 需 mined 族筛；pipeline 稳定 |
| Walk-forward 引擎 | `backtest/engine.py:35` | 滚动训练 LGB + Top-Pct 选股 + T+1 开盘/T+2 换仓约定 | 生产可用 |
| WalkForwardCombiner | `backtest/walk_forward_combiner.py:35` | 滚动 Top K 组合，去事后权重偏差 | 生产可用 |
| DSR + 显著性 | `backtest/significance.py` | t-test / Newey-West / Deflated Sharpe / Bootstrap CI / is_deployable 三门门槛 | 生产可用 |
| 诊断 | `backtest/diagnostics.py` | IC/分位/单调性/cross_section_ic_scan | 生产可用 |
| Benchmark | `backtest/benchmark.py:110` | alpha_beta 单基准分解 | 部分可用（缺 BARRA 多因子归因） |
| ExecutionModel | `backtest/cost_model.py:90` | 涨跌停/停牌/ST/零成交/部分成交/开盘滑点+T+1 建仓 | 生产可用（缺真实 Tushare suspend_d） |
| 策略注册 | `strategies/registry.py:60` | 上线前强制过 `is_deployable`，按 `strategy_family` 选 n_trials | 生产可用 |
| 截面预测 | `prediction/cross_sectional_forecast.py` | ranking-based 截面选股（推荐） | 生产可用 |
| LGB Ranker | `models/lightgbm_ranker.py:56` | lambdarank 解决 MSE 训练-排序错配 | 已集成到 engine |
| 对抗测试 | `tests/test_adversarial.py` | 随机噪声/look-ahead/幸存者/末段集中 6 场景 | 16 pass / 5 xfail |
| 通知 | `notifications/telegram_bot.py` | Telegram 推送（HTTP + requests，零异步依赖） | 生产可用 |
| 纸交易 | `scripts/paper_trade_log_mv.py` | 每日选股推送 + JSON 日志累计 + summary 对比 | 生产可用 |
| 周报 | `scripts/weekly_summary.py` | 回撤/SR/换手监控 + 告警 | 生产可用 |
| 单股分析 | `backtest/single_stock.py` | 单股复盘/idea generation + 横截面复测双轨 | 生产可用 |

## 四、方法论体系

### 4.1 Look-ahead Bias 防御（8 层）

| # | 层名 | 检测工具 | 文件:行 |
|---|------|---------|---------|
| 1 | 单股选股偏差 | `factor_cross_section_backtest` | `cross_section.py:29` |
| 2 | 阈值优化偏差 | `threshold_grid_search` 的 `robust_sharpe` 列 | `single_stock.py:553-554` |
| 3 | 静态权重偏差 | `WalkForwardCombiner` 滚动重选 vs 全样本对比 | `walk_forward_combiner.py:35` |
| 4 | 全样本选因子偏差 | `make_ic_topk_selector` + `walk_forward(feature_selector=...)` | `cross_section_selection.py:23`, `engine.py:240-253` |
| 5 | 行业/市值暴露偏差 | `Benchmark.alpha_beta` | `benchmark.py:110`（多因子归因特建） |
| 6 | 实盘成交偏差 | `CostModel` + `ExecutionModel` T+1 开盘/T+2 换仓 + 涨跌停/停牌 | `cost_model.py` |
| 7 | 多策略选择偏差 | `deflated_sharpe` + `strategy_family` | `significance.py:96-156` |
| 8 | 自动化研究循环 meta-bias | `mined` 族 (n_trials=100+) + 对抗测试 | `significance.py:35`, `test_adversarial.py` |

### 4.2 DSR 策略族门槛

5 个 `strategy_family` 与对应的 n_trials（`significance.py:22-36`）：prior=1 / single_factor=2 / small_grid=5 / tuned=20 / mined=100。三门硬性门槛（DSR≥0.4, p<0.05, Sharpe≥0.5）通过 `is_deployable`（`significance.py:278`）做硬性拒绝。`StrategyRegistry.register`（`registry.py:60`）在注册时自动检查。第 9 轮 Alpha158 实证：97 个 |t|≥2 因子在 mined 族下 **0 个通过**。

### 4.3 "抄工程不抄方法论"经验律

3 轮论文对比（Qlib #4.1-#4.4、FinRL-X、QuantaAlpha/AlphaResearch/AlphaQuanter）的实证规律：

- **成功**：ExecutionModel 停牌建模（`cost_model.py:357`）——工程基础设施。
- **失败**：Alpha158 因子库 port（因子数翻 3 倍，DSR 全灭）、sample_weight 漂移加权（Sharpe 变差）、RL allocator/LLM sentiment（hidden n_trials + 规模错配）。
- **规律**：抄工程基础设施（数据/执行/度量/静态闸门）成功率高；抄方法论/因子库/模型/agent 自动循环失败率高。**抽象不创造 alpha，bias 防御才创造 alpha。**

### 4.4 9 条已验证纪律（README + lookahead_bias_lessons 互补子集）

1. 不要再加技术指标 LGB（第 5/6/7 轮实证瓶颈是数据不是模型）。
2. 不要再做单股阈值优化（第 3 轮 64 格网格：+17% Sharpe 是 selection bias）。
3. 看到高 Sharpe 第一反应是"哪层 bias 没去"，不是"上线"（8 问清单）。
4. 不在裸 Sharpe/裸 p-value 上做决策，永远走 `is_deployable`。
5. 只有 prior 信念因子才能用 n_trials=1；调过的策略至少 `tuned` (n_trials=20)。
6. 不要 port 工业因子库就直接上（Alpha158 9 轮验证：mined 族 0 通过）。
7. 不要把"加 hyper-param"当"调优"（sample_weight A/B 实证：加权后更差）。
8. 不要为对齐论文做 premature interface abstraction（当前的 1 个 deployable 策略不需要统一接口）。
9. 单股回测仅作 idea generation，投产决策必须基于横截面验证（第 4 轮：IC 反向）。

## 五、使用方法

### 5.1 安装与依赖
```bash
cd kss && pip install -e ".[dev]"
# 依赖: pandas/numpy/scipy/statsmodels/lightgbm/matplotlib/tushare/akshare/requests 等
```

### 5.2 数据准备
```bash
kss update --pool kcb50   # 拉科创板 51 股日线，写 cs_data_688*.csv + SQLite
```

### 5.3 单因子回测
```python
from kss.backtest.cross_section import factor_cross_section_backtest
result = factor_cross_section_backtest(df_wide, factor_col="log_mv", reverse=True)
```

### 5.4 Walk-forward 截面回测
```python
from kss.backtest.engine import BacktestEngine
engine = BacktestEngine(cost_model).walk_forward(
    train_window=120, retrain_freq=5, top_pct=0.2,
    feature_selector=make_ic_topk_selector(top_k=10),
    neutralize=True, ranker=LightGBMRanker(),
    execution=ExecutionModel(limit_up_pct=0.20),  # 科创板 20%
)
```

### 5.5 DSR 上线门槛
```python
from kss.backtest.significance import Significance
Significance.is_deployable(net_returns, strategy_family="prior")
# → True 或 False（可选 return_details=True 看失败原因）
```

### 5.6 Paper Trade 每日推送
```bash
python3 scripts/paper_trade_log_mv.py --channel all   # 单日选股 + 推送
python3 scripts/paper_trade_log_mv.py --summary       # 累计 real vs theory
# cron: 0 9 * * 1-5 cd /path/to/KSS && python3 scripts/paper_trade_log_mv.py --channel all
```

### 5.7 论文阅读防御链
任何外部论文声称"XX 策略 Sharpe 2.0"→ 先带入 `Significance.deflated_sharpe` 用论文隐含的 n_trials 重算 → 检查是否有 walk-forward/walk-forward 选因子 → 归因 size/行业 β 暴露 → 再判断是否值得借鉴。参考 `docs/solutions/` 下 5 篇 paper_comparison 的 4 桶分类（桶 A: 值得抄 / 桶 B: YAGNI / 桶 C: 反面教材 / 桶 D: KSS 已覆盖）。

## 六、Lessons Learned

### 6.1 技术教训

- **低样本量的残酷性**：51 股 × 2.3 年（~29,000 行），LGB 训练窗 120 天 ≈ 6,000 行。信噪比弱到多数工业级因子（Alpha158 97/158 个 |t|≥2）在 DSR 矫正下全灭。这是所有 KSS 结论的约束性前提：**在更大池子上结论可能不同**。
- **横截面 vs 单股的路径依赖**：第 1 轮在 688017 上拿到 Sharpe 1.18 → 第 4 轮截面验证 IC 反向。单股回测是 bias 温床，但作为 idea generation 有价值——前提是必须横截面复测才信。
- **LGB 在小池上的上限**：LGB + Ranker + neutralize + execution 全 buff 后 Sharpe 仍仅 -0.35（#34-#36 报告）。瓶颈在因子 IC 弱（除 log_mv 外几乎无 IC>3%），不在模型复杂度。
- **中性化的作用边界**：行业+市值中性化（`pipeline.py:196`）能剥离 size β 暴露，但科创板 51 股做完中性化后剩余的有效 IC 极稀薄——在样本已极小的池子上再砍维度，信号几乎归零。
- **ExecutionModel 建模层级**：涨跌停过滤 + 部分成交 + 停牌 + ST + 零成交（`cost_model.py:283-408`）的工程复杂度随场景指数增长。当前第 4 层建模已够用，但在实盘触板、ST 实时识别上仍有 gap（README 缺陷 #7）。
- **Cron 部署的地雷**：cron 不继承 zshrc → 必须用 wrapper 脚本从 `.env` grep 变量（`run_paper_trade_daily.sh`）。source 整个 .env 会炸 bash（cookie/jwt 等特殊字符）。详见 `telegram_deployment.md` Troubleshooting 节。

### 6.2 方法论教训

- **DSR 为什么不是可选的**：第 7 轮 log_mv 裸 Sharpe 1.93 / p=0.017 / DSR=0.754——若只用裸指标判断，看起来强。但它是 7 轮里唯一 DSR>0.5 的策略。第 5 轮 LGB Top 10 Sharpe -0.37 的 DSR=0.036、第 4 轮 macd 截面 Sharpe 0.25 的 DSR=0.118。**DSR 是"假阳性过滤器"，不是"加分项"**。
- **strategy_family 为什么是生死器**：第 9 轮 Alpha158 在 `single_factor` (n_trials=2) 下 log_mv DSR=1.00，在 `mined` (n_trials=158) 下 DSR=0.014。n_trials 的字面数字差异让同一个策略从"通过"变"拒死"——选择哪个 family 不是偏好，是生死决策（`significance.py:22-36`）。
- **"抄工程不抄方法论"的两轮实证**：Qlib 4 个借鉴点 1 成功/3 失败（#4.2 工程成功、#4.1/#4.3/#4.4 方法论失败）+ FinRL-X 2 条借鉴都是工程 fail-loud + QuantaAlpha/AlphaQuanter 无工程可抄 → **3 轮 × 5 篇论文的模式一致**。
- **论文阅读的批判性框架（4 桶分类）**：桶 A: 值得借鉴（具体到接口/数据结构） / 桶 B: industrial wrapper（规模错配/YAGNI） / 桶 C: 反面教材（KSS 该警惕） / 桶 D: KSS 已覆盖。从 Qlib 起在 `docs/solutions/` 下 5 篇 paper_comparison 统一使用该框架。
- **"不要做的事"清单的进化逻辑**：README 8 条是逐轮血泪教训的浓缩——#1 从第 5/6 轮来（LGB 调参无效）、#2 从第 3 轮来（网格最优单点）、#3 从 8 层 bias 防御中来、#6 从第 9 轮 Alpha158 来、#7 从 #4.4 sample_weight 来、#8 从第 10 轮 FinRL-X 来。每条背后都有具体的负 Sharpe 数字。

### 6.3 工程教训

- **Cron + env 的坑**：cron wrapper 用 `grep` 逐行提取 TELEGRAM_* 变量而非 source 整个 .env——后者含 `cookie="..."` 等含特殊字符的行会炸 bash。`run_paper_trade_daily.sh` 已封装好。
- **通知通道的脆弱性**：微信 iLink 被 rate-limit（每日上限 100 条，穿透后会静默丢消息）→ 切 Telegram 云 API（`telegram_bot.py:27`），零运维、零 docker、零 api_id。自建 telegram-bot-api server 仅在需文件 > 50MB 或完全离线时考虑——已撤回自建方案，默认走 `api.telegram.org`。
- **自托管 vs 云的决策边界**：telegram server 自建需 `api_id`/`api_hash`（my.telegram.org 注册 app），国内手机号经常被拒 + TDLib 启动慢（数十秒）+ 版本升级运维成本。当前云 API 满足所有需求，自建 step 已 deprecated 到 git 历史。
- **git 初始化时机**：本仓库在已有 7 轮实验 + 3 轮论文对比后才初始化 git。早 git 化的好处是可追溯每个 Sharpe 数字对应的代码版本；坏处是早期探索阶段的频繁重构会产生大量噪声 commit。对 solo 研究者而言，"稳定后再 git" 是可接受的折中。

### 6.4 组织/协作教训

- **文档的诚实定价**：README 含"已知缺陷"7 条 + "不要做的事"8 条 + 各轮衰减曲线（1.18→-0.53→+1.93）。大厂量化 paper 不会公开这些负结果，但 KSS 的核心价值恰好在此——**诚实文档是研究框架的护城河，不是弱点**。
- **测试的基线价值**：459 passed / 3 xfailed / 6 deselected DL，回归测试覆盖对抗性 6 场景（`test_adversarial.py`）。每次改 ExecutionModel/Significance/engine 后跑全量测试能立刻发现退化——第 9 轮 Gap 2 RESOLVED 就是靠 `test_survivorship_bias_inflates_returns` 从 xfail 转 pass 验收的。
- **知识沉淀的格式选择**：`docs/solutions/` 下统一使用 frontmatter（title/tags/problem_type/module/created）+ 4 桶分类框架。`storage/reports/` 保留各轮原始报告含图表。这种分离让"方法论沉淀"与"一次性报告数据"在文件系统级别区分开，搜索时不会混。

## 七、当前局限与未来路线

### 7.1 已知局限

| 局限 | 严重程度 | 何时能解 | 证据强度 |
|------|---------|---------|---------|
| BARRA 风格归因未实现（log_mv 1.74 里 size β vs specific α 无法分解） | 高 | Wave 3 集成 | 信念：size β 占大头 |
| 样本期仅 2.3 年，未跨完整牛熊 | 高 | 需 ≥ 2021 年数据 | limited evidence |
| 跨市场未验证（所有结论仅在科创板 51 只上） | 高 | 路线图 #37 | limited evidence |
| 未做 6 个月严格 holdout | 中-高 | 路线图 #38 | 信念：需验证 |
| 停牌/退市数据缺真实 Tushare suspend_d（当前用 amount=0 代用） | 中 | 需 5000 积分 | limited evidence |
| 特征级 look-ahead 未防御（`test_lookahead_factor_caught_by_purge_gap` 为 xfail） | 中 | `docs/solutions/known_bias_gaps.md` 已记录 | 信念：purge_gap 防了 label leak 但没防 feature leak |
| 行业映射粗糙（fallback_kcb 三分类） | 低 | 需接 Tushare sw_l1 | limited evidence |

### 7.2 短期路线图（按优先级）

- **#33** 纸交易日志 ≥ 30 个交易日 → 真实 vs 理论换手/Sharpe 对比（当前可执行，等时间）
- **#37** 跨市场验证：log_mv 反向放到主板/创业板/中证 800（需拉新数据池）
- **#38** 严格 holdout：留出 2026-01 之后 6 个月不喂任何回测（等时间）
- **Wave 3** BARRA 风格归因（size/value/momentum/volatility 残差）
- **退市/停牌建模**：拉真实 Tushare `suspend_d`/`namechange` 数据，补 `BacktestEngine.report_universe_health()`

### 7.3 明确 NOT-doing（有实证支撑）

- 加更多技术指标（边际为零，第 5/6/7 轮实证）
- 调 LGB 超参（瓶颈非模型，第 5/6 轮实证）
- 加深度学习模型（同上 + 51 股样本量不足）
- Port 工业因子库（第 9 轮 Alpha158 0 通过）
- 接 LLM agent 自动跑策略（第 8 层 meta-bias 防御 + 第 11 轮 3 篇论文反面教材）
- 重构为"统一接口"（当前 1 个 deployable 策略，YAGNI）

### 7.4 可泛化结论 vs 场景限定结论

| 结论 | 性质 | 证据 |
|------|------|------|
| DSR + strategy_family 是多策略选择偏差的必要防御 | **可泛化** | 3 论论文对比 + 2 轮 KSS 实证；与样本量无关，是统计原理 |
| 8 层 bias 防御链的架构在新池子上可复用 | **可泛化** | 对抗测试 6 场景覆盖多类 bias |
| "抄工程不抄方法论"是论文阅读的可靠经验律 | **可泛化** | 1 成功 + 4 失败的 5 篇论文对照 |
| log_mv 反向是科创板上的有效 alpha | **场景限定** | 仅 51 股 × 2.3 年验证 |
| LGB 多因子在科创板 51 股上打不过单因子 | **场景限定** | 不排除全市场大池上 LGB 有效 |
| Alpha158 在科创板 51 股上无有效因子 | **场景限定** | 不排除在 CSI 300/500 上有效 |

---

_Last updated: 2026-05-12. 基于 12 轮迭代 + 9 份文档 + 13 份回测报告。不 commit，待 review。_
