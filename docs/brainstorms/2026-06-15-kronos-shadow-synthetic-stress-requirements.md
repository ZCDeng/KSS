---
date: 2026-06-15
topic: kronos-shadow-synthetic-stress
---

# Kronos 接入 KSS 第一里程碑：离线影子部署 + 合成 K 线压测

## Summary

把 Kronos（decoder-only K 线基础模型）立成一个只读离线批处理，对约 50 只科创+创业实盘票产出预测、写进现有存储，与可部署的 `log_mv` 横截面排序零连接。这一里程碑一次交付两个共用该基础设施的能力：**影子通道**（前向预测走 `is_deployable` 闸门、积累无污染战绩，达标前不影响决策）与**合成压测**（用模型生成对抗 regime 压测 `log_mv`，产出闸门假阳性率诊断）。里程碑内含 A 股微调，并用冻结截断日把训练窗口与前向战绩严格隔开。

## Problem Frame

KSS 所有回测的最大软肋是样本太短：约 50 只标的 × 2.3 年，有效 IC 样本极小，在这上面验证出的 `is_deployable` 闸门本身可能过拟合。同时团队对 T+1 涨跌停下的深度/RL 方向预测有据可查的怀疑——LGB-MSE、Transformer-DL、Alpha158 三个方案都在严格偏差防御下衰减到接近 0 被枪毙。

直接把 Kronos 当 alpha 源接进来，会撞上同一堵墙：回测战绩被短样本污染、高 Sharpe 不可信、且方向预测在涨跌停约束下既可能被刷分也不可兑现。绕开这堵墙的办法不是更努力地预测价格，而是把 Kronos 用在它的概率与生成能力真正擅长、且无法被刷分的两处：造对抗数据替短样本补缺口，跑前向影子攒未被污染的证据。

## Key Decisions

- **两个能力作为一个里程碑交付，共用同一离线批处理。** 影子与压测都需要「冻结截断日微调的 Kronos + 离线批量推理」这套基础设施，分开建会重复搭设。
- **影子毕业门槛不放水。** 走与 `log_mv` 完全相同的 `is_deployable` + Deflated Sharpe 闸门，并按 `strategy_family="mined"` 计入 `n_trials`——基础模型携带巨大隐藏试验次数，Deflated Sharpe 的惩罚是目的而非要调走的障碍。
- **里程碑内做 A 股微调，而非零样本起步。** Kronos 是否进入预训练语料未确认、论文未披露 train/test 切分，A 股适配要靠自己微调验证。
- **冻结截断日做隔离。** 微调只用截断日之前数据、模型定住后不再训练；影子与压测只跑截断日之后。前向战绩与训练窗口零重叠，是「无污染前向证据」这个价值主张立得住的前提。
- **合成压测定位为诊断，不是新增硬闸门。** 它量化现有闸门在已知零结构数据上的假阳性率，顺着团队偏差防御纪律走；是否升级为阻断闸门留待有数据后再议。
- **全程不动 `log_mv`。** 唯一可部署策略（Sharpe 1.74）保持原样，Kronos 永不进它的决策路径。

## Key Flows

- F1. 离线批处理主链路
  - **Trigger:** 调度任务触发（频率交 planning），非实盘 cron 关键路径。
  - **Steps:** 加载冻结截断日微调后的 Kronos checkpoint → 批量推理约 50 只票（截断日之后窗口）→ 预测（点估计 + 不确定带）写入现有存储 → 影子打分与合成压测各自消费该存储。
  - **Outcome:** 影子前向战绩日志 + 合成压测诊断报告；对 `log_mv` 实盘产出零影响。
  - **Covered by:** R1, R3, R5, R6, R8, R12

## Requirements

**离线推理基础设施**

- R1. 提供只读离线批处理，对约 50 只科创+创业实盘票产出预测并写入现有存储，与 `log_mv` 排序零连接。
- R2. 复用现有存储层（SQLite / parquet），不新建任何与实盘决策耦合的写路径。
- R3. 使用开放权重 checkpoint（≤102M），CPU 或小显存可跑；不进每日实盘 cron 的关键路径，单独调度。

**A 股微调与无泄漏隔离**

- R4. 在 A 股 K 线上微调 Kronos，产出本里程碑使用的模型。
- R5. 冻结截断日：微调只用截断日之前的数据，模型定住后在本里程碑内不再训练。
- R6. 影子通道与合成压测只在截断日之后的数据上运行，前向战绩与微调训练窗口零重叠。
- R7. 自建无泄漏时间隔离回测——Kronos 未声明 split 与泄漏防护，须断言特征窗口严格早于被预测 bar，并补一条对应的回归测试（现有 `purge_gap` 只挡标签泄漏，不挡特征级 look-ahead）。

**影子通道**

- R8. Kronos 前向预测每日记录、只读，达标前不影响任何决策或仓位。
- R9. 影子战绩走与 `log_mv` 相同的 `is_deployable` + Deflated Sharpe 闸门，按 `strategy_family="mined"` 计 `n_trials`，不为基础模型放宽。
- R10. 毕业条件 = 通过闸门 **且** 满一个固定前向窗口（窗口长度交 planning 决定）。
- R11. 复用 `scripts/validate_predictions.py` 的周校验给影子打分（Brier / 方向准确率 / 区间覆盖率），并套用其连续两周不达标拉黑的停用规则。

**合成压测**

- R12. 用冻结后的微调 Kronos 条件化真实历史，生成对抗 regime 路径（涨跌停连锁、流动性枯竭、板块急跌）。
- R13. 合成数据严格隔离：只用于对抗压测，永不进训练，永不作为实盘信号。
- R14. 把 `log_mv` 及候选策略在合成路径上跑现有 walk-forward，产出诊断报告——量化闸门在已知零结构数据上的假阳性率；不作为新增的硬阻断闸门。
- R15. 采信任何压测结论前，先验证合成 K 线带有 A 股微结构（T+1 跳空、涨跌停截断）；不带则该批压测判为无效。

**交付与监控**

- R16. 任何 Telegram 推送复用 `_md_v1_escape()` 转义股票名/概念名，避免 MD-V1 解析失败导致的静默丢推送。
- R17. 复用 `SuspensionData` / `is_tradable` 过滤停牌、ST、退市、零成交标的，与 `log_mv` 同口径。

## Acceptance Examples

- AE1. **Covers R6.** 给定微调截断日为 D，当影子通道运行时，其前向战绩只统计 D 之后的交易日；任何 D 及之前的预测不计入毕业判定。
- AE2. **Covers R9, R10.** 当影子在固定窗口内的 Deflated Sharpe 未通过 `is_deployable`（按 mined 计 `n_trials`）时，即使原始 Sharpe 看起来高，也判未毕业、维持只读。
- AE3. **Covers R14.** 当合成压测跑完，输出是一份「闸门假阳性率」诊断（在已知零结构数据上闸门误判 deployable 的比例），而非一个阻止某策略上线的 PASS/FAIL 门。
- AE4. **Covers R15.** 当某批合成 K 线缺失涨跌停截断或 T+1 跳空特征时，该批压测结论被标记无效、不进诊断报告。
- AE5. **Covers R7.** 当构造 Kronos 输入窗口时，断言窗口末尾严格早于被预测 bar；构造出跨越预测 bar 的窗口应被回归测试捕获。

## Success Criteria

- 一个季度（或定稿窗口）后，存在一段未被微调样本污染、可被现有校验体系打分的影子前向战绩。
- 合成压测能给出一个量化数字：现有 `is_deployable` 闸门在已知零结构数据上的假阳性率。
- `log_mv` 实盘产出在整个里程碑期间零变化（影子与压测均不触及其决策路径）。
- 任何 Kronos 相关推送无静默丢失（MD-V1 转义 + dry-run 探针验证）。

## Scope Boundaries

**Deferred for later**

- ideation 其余 5 条方向：波动率定盘仓位/成本（I3）、残差异常监控（I4）、涨跌停可交易性过滤（I5）、量价泄漏探针（I6）、蒸馏标量特征喂 LightGBM（I7）。
- 实盘资金分配——本里程碑只到影子 + paper-trade，不下真单。
- 滚动再微调——本里程碑用单一冻结截断日；定期重训留待影子证明价值后再设计。

**Outside this product's identity**

- 用 Kronos 替换或挑战 `log_mv` 作为可部署 alpha。
- 把 Kronos 的方向点预测直接当交易信号——正是团队已枪毙的路径。

## Dependencies / Assumptions

- Kronos 开放权重（mini / small / base，≤102M）+ MIT 许可，`torch>=2.0`；large 闭源不可用。
- 假设：A 股未确认在 Kronos 预训练语料内，故需微调适配（未被官方材料证实，作为载重假设记录）。
- 复用现有模块：`kss/backtest/significance.py`(`is_deployable`/Deflated Sharpe)、`kss/backtest/engine.py`(walk-forward)、`kss/backtest/cost_model.py`、`kss/data/suspension_data.py`、`scripts/validate_predictions.py`、`scripts/run_paper_trade_daily.sh`。
- 假设：合成压测的价值取决于生成数据能否继承 A 股微结构——R15 是这条假设的验证关卡。

## Outstanding Questions

**Deferred to Planning**

- 固定前向窗口的具体长度（~1 季度 / 60 交易日是设想值，未定）。
- 选哪个 checkpoint（mini / small / base）做微调与推理。
- 微调数据跨度与冻结截断日的具体取值。
- 合成 K 线 A 股微结构验证（R15）的具体校验方法与判定阈值。
- 离线批处理的调度频率（日 / 周）与挂载方式（复用 `run_paper_trade_daily.sh` 式 wrapper 还是新建）。
