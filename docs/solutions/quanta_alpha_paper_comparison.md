---
title: QuantaAlpha 论文借鉴分析（对比 KSS）
tags: [research, paper-review, llm-alpha-mining, evolutionary, meta-bias]
problem_type: research
module: docs
created: 2026-05-12
---

# QuantaAlpha 论文借鉴分析（对比 KSS）

## TL;DR

- **论文核心**：QuantaAlpha（SUFE + QuantaAlpha startup, arxiv 2602.07085, 2026-02-10）用**多 agent + trajectory-level mutation/crossover** 把 LLM-driven 因子挖掘"进化化"，CSI 300 上 GPT-5.2 跑出 IC **0.1501** / ARR **27.75%** / MDD **7.98%**；号称 0-shot 迁移 CSI 500 (+160%) / S&P 500 (+137%)。AST + 算子库做 controllable factor construction + LLM consistency verifier 是其工程亮点。
- **真值得借鉴的只有 2 条**（桶 A，都是工程类）：AST 算子库 + consistency verifier 作为**因子表达式静态检查器**（接进 KSS prior factor zoo 的语法验证）+ 0.7 相关性 redundancy 去重（接进现有 `cross_section_ic_scan` 的因子筛选）；**反面教材 3 条**（桶 C）：trajectory evolution 是 RD-Agent meta-bias 的下一代升级，hidden n_trials 量级 ≥ 350，IC 0.15 在因果上不可信；transfer 实验缺正确对照；CSRankNorm 标签预处理与 KSS T+1 + 涨跌停场景物理冲突。
- **KSS 第 7/8 层 bias 防御对 QuantaAlpha 的整套方法论已有现成解药**（桶 D 5 条）。Alpha158 在 KSS 51 股池 mined-family DSR (n_trials=158) 下 0 通过——QuantaAlpha 的 350 因子在同口径下大概率结果相同，论文未做这层矫正。
- **整体判断：不抄方法论 / 只局部抄工程**。与 Qlib RD-Agent / FinRL-X / AlphaAgent 同属"LLM 多 agent 自动跑 N 个策略只汇报最优"家族——这一族在 KSS 第 8 层防御视角下都是"自动化 false positive 工厂"。论文展示了更精致的 mutation/crossover wrapper，但**没有解决根本问题**（multiple testing + size β 暴露 + 短样本期）。CSI 500 / S&P 500 转移看着很猛，但缺 deflated metric 不能信。

## 一、论文核心主张拆解

1. **Trajectory-level self-evolution**：每次 end-to-end mining 当作 trajectory τ = (s₀, a₀, …, sₙ)，对低 reward trajectory 做 **mutation**（self-reflection 定位 sub-optimal step + 局部 rewrite + 后续 regenerate）和 **crossover**（高 reward parents 段落重组）。声称比 RD-Agent / AlphaAgent 的"全 trajectory 重生成"更可控。
2. **Controllable factor construction**：hypothesis → semantic description → 符号表达式 f over 算子库 𝒪 → AST T(f) → 编译 code c → LLM verifier 检查 (h, d, f, c) 四者一致性，不一致就 retry。算子库见 Table 6（time-series / cross-sectional / mathematical / technical / logical / auxiliary 六大类，~70 个算子）。
3. **Complexity + redundancy 双闸**：
   - Complexity 𝒞(f) = α₁·SL(f) + α₂·PC(f) + α₃·log(1+|F_f|)（符号长度 / free params / 原始 feature 数）；symbol length ≤ 250、base features ≤ 6、free args ratio < 50%
   - Redundancy 通过 AST 同构子树最大公共节点数 s(fᵢ, fⱼ)，与已存因子池相关性 > 0.7 拒绝
4. **Diversified Planning Initialization**：10 个并行 exploration 方向（price/volume × short/long × momentum/mean-reversion × regime-conditioned），5 iterations × 1 mutation + 1 crossover/iter × 3 expressions/hypothesis
5. **实验设定**：CSI 300，2016-01 ~ 2020-12 train / 2021 valid / 2022-01 ~ 2025-12 test（4 年 OOS）；LightGBM 下游模型；TopkDropout (top=50, drop=5)；buy 0.05% / sell 0.15%；deal_price=open；limit_threshold=9.5%。全文**无 DSR、无 Bonferroni、无 walk-forward 选因子检验、无 n_trials 显式报告**（仅 Appendix 提"iter 11-12 ≈ 350 factors 是最优"）

## 二、4 桶分类（批判性）

### 桶 A：值得借鉴（具体到接口/数据结构）

**A1. AST 算子库 + symbol-length / base-feature 复杂度闸**（半天 ~ 1 天）

- **论文做法**：论文 §4.1.2 把因子写成 AST，叶节点 = `$high`/`$volume`/`$close` 等原始字段，内节点 = `TS_MIN/SMA/RANK` 等算子（Table 6 共 ~70 个）。每个因子表达式必须满足 symbol length ≤ 250、base features ≤ 6、free args ratio < 50% 三个静态闸门。
- **为什么值得抄（限定版）**：KSS 当前 `kss/features/` 是手工 pandas 表达式，无统一的**表达式复杂度度量**。第 9 轮 Alpha158 port 进来后 158 因子里 IC ≥ |0.03| 的 35 个，但大多是 `TS_RANK / RANK / QTLU` 系列——这些公开因子库已经 implicitly 满足"低复杂度 + 高可解释"，所以 KSS 当前用不上 AST。但当未来用户手动加新因子时，**用 symbol length / base feature 数做静态闸门**可以防止"看着很科学的高阶因子"（如 `TS_CORR(TS_RANK(volume, 20), TS_STD(close-open, 30))` 这种 5 层嵌套）。
- **怎么集成进 KSS**：在 `kss/features/pipeline.py` 加一个 `_validate_complexity(expr_str, max_chars=250, max_base_features=6)` 静态闸门，新因子注册时强制过。**不抄 AST 本身**——pandas 表达式字符串 + 简单正则提取原始字段足够。Karpathy #2 simplicity-first：不要为对齐论文引入 ast 模块、operator registry、新的 IR。
- **怎么用 9 轮实证体系验证**：把 Alpha158 跑过的 158 因子的 symbol length / base feature count 统计出来作为 prior 分布——KSS 历史上有效的因子（log_mv 等）复杂度通常很低，可作为闸门门槛的实证基准。如果新因子复杂度超过历史 95 分位，自动 strategy_family 至少升到 `mined`。

**A2. 0.7 相关性 redundancy 去重接入 cross_section_ic_scan**（半天）

- **论文做法**：§5.5 Case Study Setup："a factor is admitted only if its absolute correlation with every factor already in the pool is below 0.7"——greedy by Rank IC desc，pool size capped 50%。
- **为什么值得抄**：第 9 轮 Alpha158 截面 IC scan 里 `SUMN10/SUMD10/SUMP10` 三组高度相关（同源 ±IC），`RANK20/RANK30/QTLU20/QTLU30` 滚动窗口家族化、`MA20/MA30/MA60` 显著共线。当前 `kss/backtest/diagnostics.py` `cross_section_ic_scan` 只按 \|t-stat\| 排序，不去相关——如果未来 LGB Ranker 用 Top K 因子，可能选到 5 个本质上是同一信号。
- **怎么集成进 KSS**：`cross_section_ic_scan` 输出表后加一个 `select_low_corr_topk(scan_df, factor_corr, top_k=10, max_corr=0.7)` 函数，greedy 按 \|t\| desc 入池。**不动 Alpha158 因子库本身**。
- **怎么用 9 轮实证体系验证**：在 `alpha158_screening.md` 数据上重跑——97 个 \|t\| ≥ 2 因子里，0.7 相关性去重后剩余多少？预期 < 20。然后对去重后的因子池跑 mined-family DSR (n_trials = 去重后数量)，看是否有任何因子能通过——这是个对**第 7 层 meta-bias 防御**额外的现实压力测试。

### 桶 B：industrial wrapper（不抄——规模错配 / YAGNI）

**B1. 多 agent 全栈（idea / factor / evaluation / verifier）+ LLM consistency 检查器**：4 个 LLM agent 协作 + 每次 retry 都调用 verifier，按 5 iter × 10 directions × 3 expressions ≈ 150 次 LLM 调用 + 大量 verifier retries。论文跑一次实验显然在 QuantaAlpha 公司内部有 GPU/API 预算支撑；KSS 是个人复盘框架，**LLM 调用成本 + agent orchestration 复杂度** vs 当前手工写 49 个因子 + cross_section_ic_scan 自动筛选的工作流，边际效用比是负的。违反 Karpathy #5（model 只用于判断类任务，不要用 LLM 做路由 / 状态码处理）。

**B2. TopkDropout (top=50, drop=5) 策略包装**：论文用 50 只股 + 每天换 5 只。KSS 池子总共 51 只——直接套等于"满仓持有 + 每天微调"，没有选股意义。**A 股科创板 51 只小池上做"Top K"建仓只能是 Top 5~10 (≈ 10-20%)**，不是 Top 50。当前 KSS `make_ic_topk_selector` + 20% 选股已经是合理 sizing，论文这套不适配。

**B3. CSRankNorm（cross-sectional rank normalization）预处理**：论文 §A.2 对 features 和 labels 都做 rank norm。**对 features 做 rank norm KSS 早就在用**（`make_ic_topk_selector` 用 rank IC）；**对 labels 做 rank norm 在 KSS T+1 + 涨跌停场景下属于一个隐式的 data leak**——LightGBM 学的是 rank quantile 而不是真实 return，模型不会感知"涨停板这个 quantile 等于物理不可达"。KSS `ExecutionModel` 处理的是 raw returns，强行套 rank norm = 又一个 leaky abstraction，违反 README #8。

**B4. 复杂度 𝒞(f) 三项加权公式**：α₁·SL + α₂·PC + α₃·log(1+\|F_f\|) 是漂亮的形式化但 KSS 用 raw symbol length / base feature count 两个独立硬阈值就够了。加权公式还要选 (α₁, α₂, α₃) 三个超参——又是一个 hyper-param trap，违反 README #7。**简化原则**：A1 中直接用两个独立硬阈值，不用加权公式。

### 桶 C：反面教材（KSS 该警惕）

**C1. Trajectory evolution 是 RD-Agent meta-bias 的下一代升级，hidden n_trials 量级 ≥ 350**

- **论文做法**：5 iterations × 10 directions × 3 expressions/hypothesis = 150 次"试"是表面账；Appendix §5.5 自己说"iter 11-12 对应 350 个因子是最优"——意味着实际 trial 数 ≥ 350。每次 mutation 都是一次 implicit trial，每次 crossover 也是。pool 里只保留 top 50% by Rank IC（greedy 选最强的 ~175 个）后再汇报 IC = 0.1501——典型 **selection on outcome**。
- **为什么是反面教材**：
  - 在 KSS 视角下：350 trial 应该按 `strategy_family="mined"` (n_trials ≥ 100) 做 DSR 矫正。论文 IC 0.1501 在 Pearson 框架下对应 t-stat 大概 6-8（4 年日频 ≈ 1000 obs）——单独看显著。但 350 次试验取 top 50% 后挑最强的，bonferroni 矫正 α/350 = 0.000143，要求 t-stat ≥ 3.81 才显著。论文给的 ICIR = 0.9110 对应 Rank ICIR 0.8909——这是 daily rank IC 序列稳定性，与 multiple testing 无关。
  - 与 Alpha158 在 KSS 51 股池上的对照（`alpha158_screening.md`）：97 个 \|t\| ≥ 2 因子全部 DSR (n_trials=158) **0 通过**。QuantaAlpha 350 因子在同口径下大概率结果一样——但论文跑在 CSI 300（n=300 vs KSS 51）、4 年（KSS 2.3 年），样本足够大才让 IC 看起来稳定，**这不代表它通过了 DSR**。
  - 论文比 RD-Agent 危险的地方：mutation/crossover 让搜索更"高效"——bandit + trajectory 重组比单 agent 随机生成的 trial-density 高一个量级，**搜索越高效，selection bias 越严重**（lookahead_bias_lessons.md §3.7）。
- **KSS 已有防御**：第 9 轮 Alpha158 集体筛选实证（97 个 \|t\| ≥ 2 → DSR 0 通过）+ `lookahead_bias_lessons.md` §3.8 已把 RD-Agent 写成反面教材。
- **应该加什么防御**：在 `lookahead_bias_lessons.md` §3.8 追加一段："QuantaAlpha 类 trajectory-evolution 框架是 RD-Agent 升级版，n_trials 实际量级 ≥ 350，**进入 KSS 必须按 `strategy_family="mined"` 处理，并且 n_trials 应取论文 mining 总数而非汇报的 Top-K**"。大约 8 行文字，不需要代码。

**C2. CSI 500 / S&P 500 transfer +160% / +137% 缺正确对照**

- **论文做法**：§5.4 把 CSI 300 mined factors 0-shot 迁移到 CSI 500 + S&P 500，cumulative excess return 4 年 +160% / +137%。
- **为什么是反面教材**：
  - **CSI 300 → CSI 500 不算"distribution shift"**：两个池都是 A 股大-中盘，因子语义（overnight gap / volatility / momentum）共享。CSI 500 是 CSI 300 之后的 500 只中盘，2022-2025 期间小盘 / 中盘 alpha 比大盘强是公开事实——任何在 CSI 300 上挖到的 size / volatility / mean-reversion 因子在 CSI 500 上**自动获得 size β 加成**。这与 KSS 第 5 层 bias（size 暴露）一脉相承。
  - **S&P 500 +137% 没有对照基准**：论文图 1 只画了 cumulative excess return curve，没有给 LightGBM / Alpha158 / XGBoost 在 S&P 500 同期的对照（CSI 300 主表有，跨市场没有）。S&P 500 2022-2025 经历了 +60%（SPY ETF），如果"超额"是相对于 SPY，那 LightGBM Alpha158 baseline 在 S&P 500 上的同期表现需要单独验证才能说明 QA "+137%" 是真 alpha 还是市场 β。
  - 论文宣称 "factors transfer beyond the source market" 但**没有提交本市场 OOS holdout 检验**——CSI 300 训练 2016-2020 / 测试 2022-2025 后，没有保留一段 unseen 段做最终验证。所有 350 因子都见过 2022-2025 test set 才会被纳入 pool（greedy by Rank IC），test set 本身已经成了 selection set。
- **KSS 不能学**：当前路线图 #38 "严格 holdout：留出 2026-01 之后 6 个月不喂任何回测"——继续保持。`log_mv` 反向在跨市场（路线图 #37：主板 / 创业板 / 中证 800）的迁移**必须用 KSS 自己的 prior factor n_trials=1 跑 DSR**，不能模仿 QA 那种"4 年 cumulative curve 好看就说迁移有效"。
- **关联**：README "不要做的事" #6（不要 port 工业因子库就直接上）+ #7（不要把加 hyper-param 当调优）的精神延伸。

**C3. CSRankNorm 标签预处理在 A 股 T+1 + 涨跌停场景下是 leaky abstraction**

- **论文做法**：§A.2 "applying cross-sectional rank normalization (CSRankNorm) to both features and labels"——把次日 return `y_t = P^close_{t+2} / P^close_{t+1} - 1` 转成 rank quantile 后训练。
- **为什么是反面教材**：
  - rank norm 的 label 让模型学的是"次日相对涨跌排名"。但**涨跌停板在 A 股上是物理硬约束**——top quantile 里大量股票次日涨停（接近 +10%）你想做多但**根本买不进**；bottom quantile 里大量跌停股你**想做空也跌停打不进**。模型看到的 "top 20% quantile = positive label" 信号 vs 实盘可达的 actually-tradable top 20% 之间有显著 gap。
  - 这与 FinRL-X 论文的 weight-centric 抽象在 A 股上是 leaky abstraction 一样（`finrl_x_paper_comparison.md` 桶 C2）——**在 US equities + T+0 + 无涨跌停的场景下没问题，套到 A 股就漏**。论文用 Qlib 提供的 default Exchange (limit_threshold=9.5%) 部分缓解，但训练时已经用 rank-normalized label 了。
- **KSS 不能学**：KSS 当前 `LightGBMRanker` 用 lambdarank 处理排序训练，但 label 用的是 raw next-day return（`kss/backtest/engine.py`）。**保持不做 label rank norm**——A 股需要保留 raw return 信号让 ExecutionModel 在回测时判断"这只票次日是否触板 / 部分成交 / 0 量"。
- **关联 lookahead_bias_lessons.md §3.6**：实盘成交偏差——T+1 开盘建仓 / T+2 换仓 + 涨跌停过滤是 KSS 的护城河，不能被"看起来更稳定的训练信号"诱惑。

### 桶 D：KSS 已覆盖（自信加分）

论文声称的 mining 维度 vs KSS 现状：

| 论文声称的 | KSS 现状 | 状态 |
|------------|---------|------|
| 1. Hypothesis-driven factor construction（LLM 把"小盘 size effect"翻成 expression） | KSS 直接用 `log_mv` 反向作为 prior factor（不需要 LLM 翻译） | **已覆盖**（且更便宜：单行 pandas 表达式） |
| 2. Backtesting-based refinement | `BacktestEngine.walk_forward` + `Significance.is_deployable` 上线门槛 | **已覆盖且更严**（论文无 DSR） |
| 3. Factor diversity / redundancy filter | A2 borrowed 后即覆盖；当前 `cross_section_ic_scan` 已有 \|t-stat\| 排序，相关性筛是增量 | **部分覆盖**（A2 补完即关闭） |
| 4. Multi-market transfer | 路线图 #37（log_mv 跨市场验证）+ 严格 holdout #38 | **设计上更严**（要求 prior + DSR，不只看 cumulative curve） |
| 5. Alpha decay 监控 | `WalkForwardCombiner` retrain_freq + `lookahead_bias_lessons.md` §3.7 | **已覆盖**（论文 §5.4 alpha decay 分析停留在描述层面） |

**论文未声称、KSS 独有的护城河**：

- **DSR + `strategy_family` 自动 n_trials**（`significance.py:96` + `registry.py:60`）——QuantaAlpha 全文 0 提
- **Alpha158 在 51 股池 mined-family 0 通过实证**（`alpha158_screening.md`）——直接对应论文 350 因子 pool 的同口径预测
- **`SuspensionData` + `ExecutionModel` 涨跌停 / T+1 / 部分成交 / 停牌**（`cost_model.py:87`）——论文用 Qlib default Exchange 但 label rank norm 训练已经先漏一刀
- **8 层 bias 防御链 + 对抗测试 6 场景**——论文最多防到第 6 层（实盘成交），第 7/8 层完全没有

## 三、对比 Qlib + FinRL-X 上 2 轮教训

| # | 论文 | 借鉴桶 A 数量 | 实际产出 |
|---|------|--------------|----------|
| 第 9 轮 Qlib + RD-Agent (arxiv 2505.15155) | 4 条 | 1 成功（ExecutionModel 停牌）+ 3 失败（Alpha158 / sample_weight / hypothesis log 延后） |
| 第 10 轮 FinRL-X (arxiv 2603.21330) | 2 条 | 2 都是"半天 fail-loud 工程改进"，未实施前先 PoC |
| 第 11 轮 QuantaAlpha (本轮) | **2 条** | A1（symbol-length 闸门）+ A2（0.7 相关性去重），都是工程类 surgical change |

**模式延续**：每轮 paper 借鉴里**只有"工程基础设施"类（执行 / 数据 / 静态闸门 / 度量）成功**，"方法论 / 因子库 / agent 自动循环"类全部失败或缓做。本轮符合该经验律。

**与 RD-Agent / AlphaAgent 同族判定**：QuantaAlpha 是 "LLM-driven 多 agent 自动跑 N 个因子" 这条路上的 **第 3 篇**（RD-Agent 2025-05、AlphaAgent KDD 2025、QuantaAlpha 2026-02），mutation/crossover 比 RD-Agent 的 bandit / AlphaAgent 的 regularization 更"高级"但**没有解决 hidden n_trials 问题**——只把 trial-density 推得更高。KSS 第 8 层 bias 防御对这一族都同样有效。

**预先要做的最小验证**：A1 实施前先在 Alpha158 的 158 因子上回算"symbol length / base feature count"分布，看 KSS 历史有效因子（log_mv 等 49 个）的复杂度分位——**如果发现历史有效因子复杂度普遍很低（symbol_length < 50），论文 250 字符上限对 KSS 完全多余，A1 自动降级为简单的"硬上限 50"**。10 分钟工作量。

## 四、推荐行动清单（带 Karpathy 滤镜）

| # | 借鉴点 | KSS 改动 | 工作量 | 风险 / 与 KSS 纪律的关系 | 建议 |
|---|--------|---------|--------|--------------------------|------|
| 1 | A1 symbol length / base feature 静态闸门 | `kss/features/pipeline.py` 加 `_validate_complexity()` 函数；新因子注册时调用 | 半天 | 低风险，单文件 surgical；与 Karpathy #2 simplicity-first 一致 | **先 10 分钟 PoC**——在 Alpha158 上看复杂度分布，再决定阈值；分布很窄就降级硬上限 |
| 2 | A2 0.7 相关性 redundancy 接 cross_section_ic_scan | `kss/backtest/diagnostics.py` 加 `select_low_corr_topk()` 函数 | 半天 | 低风险，纯增量函数；不动现有 scan 接口 | **立即做**——直接在 alpha158_screening 数据上回算，预期 97 → < 20，提交即用 |
| 3 | 在 `lookahead_bias_lessons.md` §3.8 追加 8 行 "QuantaAlpha 是 RD-Agent 升级版，n_trials 量级 ≥ 350" | 文档 patch | 半小时 | 0 风险 | **立即做** |
| 4 | 把 QuantaAlpha 全套 trajectory mutation/crossover 抄进 KSS | 引入 LLM agent + AST + verifier | ≥ 2 周 | 高风险：(a) LLM 调用成本 + (b) Karpathy #5 违反（model 不该做路由）+ (c) 第 8 层 meta-bias 极致版 + (d) 51 股池本身 trial-density 限制比 LLM 算力小 | **不做**——规模错配 + 方法论上是 false positive 工厂 |
| 5 | AST 算子库 + verifier 全栈搬运 | 引入新 IR + operator registry + 4 个 LLM agent | ≥ 1 周 | 高风险：premature interface abstraction（违反 README #8）；KSS 49 因子全是手写 pandas 表达式，足够覆盖小池场景 | **不做**——YAGNI |
| 6 | CSRankNorm label 预处理上 KSS | `BacktestEngine` 训练前对 label 做 rank norm | ≥ 1 天 | 高风险：A 股涨跌停场景下 leaky abstraction（桶 C3）；与 KSS `ExecutionModel` 物理冲突 | **不做**——破坏 raw return 信号 |
| 7 | TopkDropout (top=50, drop=5) 策略 | 改 `engine.py` 默认 portfolio 构建 | 半天 | 中风险：51 股池上 Top 50 = 全持有，没意义；当前 Top 20%（10 只）是合理 sizing | **不做**——参数与池子规模错配 |
| 8 | 引用 CSI 300 → CSI 500 +160% transfer 数字论证"factor 可迁移" | 在 KSS 文档里支持"log_mv 跨市场迁移"假设 | 0 | 高风险：论文 transfer 实验缺正确对照 + selection on outcome 嫌疑（桶 C2） | **不做**——保留路线图 #37 / #38 的严格 holdout 标准 |

## 五、结论

**这篇论文对 KSS 的实质增量价值低**。QuantaAlpha 本质是 SUFE + QuantaAlpha startup 把 RD-Agent (Microsoft 2025-05) / AlphaAgent (KDD 2025) 沿"LLM 多 agent 自动跑 N 个策略"路线推到下一代——mutation/crossover trajectory 调度比 bandit 更"高效"，但**没有解决根本问题：multiple testing 矫正缺失 + size β 暴露未归因 + 短样本期 / 单池 selection on outcome**。论文给的 IC 0.1501 / ARR 27.75% 在 KSS 视角下应当先按 `strategy_family="mined"`, n_trials = 350 重算 DSR 才能信。

更具体的不抄理由：

- **池子规模错配**：QA 在 CSI 300 + LightGBM + GPT-5.2 上跑出的因子，套到科创板 51 股 + 2.3 年样本上**信噪比立刻塌陷**——这一点 KSS 第 9 轮 Alpha158 实证（97 个 \|t\| ≥ 2 → DSR 0 通过）已经给出明确证据。
- **方法论同源问题**：QuantaAlpha 与 RD-Agent / AlphaAgent / FinRL-X 同属"LLM 多 agent 自动跑"家族，第 8 层 meta-bias 防御（`lookahead_bias_lessons.md` §3.8）对这一族都同样有效，**不需要为每一篇都新写一套防御**——只需要追加 8 行把它纳入既有论述。
- **A 股专用建模冲突**：CSRankNorm label + TopkDropout (top=50) 都是 US equities / CSI 300 大池的合理选择，套到 KSS 51 股 + T+1 + 涨跌停场景下都是 leaky abstraction（同 FinRL-X weight-centric 的诊断）。

唯一两条值得抄的（symbol length 静态闸门 + 0.7 相关性去重）都是小时级工作量的 surgical 工程改进，且必须先做 10 分钟 PoC 验证信息量。

**优先级判断**：先把上轮 Qlib 借鉴里 RESOLVED 的 Gap 2 完整闭环（拉真实 Tushare suspend_d 数据，README 现有缺陷 #3 未办项）+ 路线图 #33 / #37 / #38 跑完——**远比从 QuantaAlpha 抄任何东西重要**。

不要被 "evolutionary self-evolution" / "trajectory-level mutation" / "controllable factor mining" 这类标题词迷惑——这是论文修辞，不是 KSS 真问题的解药。"跑得更高效"在没有 DSR 防御下等价于"更快生产 false positive"。

---

_引用：论文 <https://arxiv.org/html/2602.07085v1>（QuantaAlpha, SUFE + QuantaAlpha, 2026-02-10）；KSS 文件引用以 `path:line` 形式给出。上两轮对比见 `qlib_paper_comparison.md`、`finrl_x_paper_comparison.md`。同族论文：RD-Agent (arxiv 2505.15155) / AlphaAgent (KDD 2025 Tang et al.)。_
