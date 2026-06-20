---
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
tags: [factor, ic, icir, health, walk-forward, alert, backtest]
---

# 因子健康度闭环 — Requirements

## Problem Frame

KSS 现有因子/信号体系缺失时间维度上的自我监控。当前状态：

- `scripts/validate_predictions.py` 每周计算 Brier/覆盖率/方向命中，但只看预测准确度，不追踪因子本身的 IC 轨迹。
- `kss/backtest/significance.py` 提供 t-stat/DSR/Newey-West，但只在单次回测结束后输出，没有滚动时间窗口。
- `kss/backtest/walk_forward_combiner.py` 的 `WalkForwardCombiner` 每个 retrain 点选权重，但没有把各 retrain 点的因子 IC 写进任何持久化存储。
- `kss/backtest/cross_section.py` 的 `factor_cross_section_backtest` 返回逐日净值，因子日级别 IC 序列没有落库。
- 没有"某因子在某时间段表现崩掉"的结构化记录，复盘依赖人工回忆。

外部先例：alphalens-reloaded 用滚动 ICIR 做因子淘汰门；AlphaAgent 用 ICIR 退化触发换因子。KSS 需要同等能力，但要贴合本地单用户算力约束和 PIT 纪律。

---

## Actors

| ID | Actor | 说明 |
|----|-------|------|
| A1 | **FactorHealthTracker** | 新模块（`kss/backtest/factor_health.py`），负责滚动 IC/ICIR 计算与落库 |
| A2 | **FactorCrashRegistry** | 新存储层，结构化记录因子崩盘事件（SQLite 表或 JSON-Lines 文件） |
| A3 | **AlertEngine** | 现有 `kss/notifications/manager.py`，扩展告警触发点 |
| A4 | **WalkForwardCombiner** | 现有 `kss/backtest/walk_forward_combiner.py`，在每个 retrain 点调用 A1 写入 IC 快照 |
| A5 | **BacktestRunner** | 调用 `factor_cross_section_backtest` 或 `BacktestEngine` 的脚本，回测结束后调用 A1 写入总结 |
| A6 | **用户/研究员** | 触发回测、查看告警、决定是否剔除因子 |

---

## Key Flows

### F1 — 滚动 IC 计算与落库

1. A4/A5 在每个 retrain 点（或回测结束时）调用 `FactorHealthTracker.record_ic_snapshot(factor_id, date, ic_series)`。
2. `ic_series` 是该训练窗口内逐日的 Rank-IC（因子值 cross-sectional rank 与次日收益 rank 的 Spearman 相关）。
3. Tracker 计算该窗口的 IC-mean、IC-std、ICIR（= mean/std），以及 1d/5d/20d 的 IC 半衰期（以 IC 滚动均值的对数衰减拟合）。
4. 结果写入持久化存储（按 `factor_id + window_end_date` 主键）。

### F2 — ICIR 衰减告警

1. 每次新 IC 快照写入后，Tracker 查询该因子最近 60 日（交易日）的 ICIR 滚动序列。
2. 若 60 日滚动 ICIR 跌破基线阈值（阈值由 walk-forward 标定，见 R5），触发：
   a. 将该因子状态标记为 `PENDING_REVIEW`；
   b. 通过 A3 发送 console 告警（可选 Telegram）；
   c. 写入 FactorCrashRegistry 一条告警记录。

### F3 — 因子崩盘登记

1. 每次回测（cross-section 或 walk-forward）发现某因子在某时间窗口内：
   - IC 绝对值连续 N 期 < 阈值（N 和阈值均 walk-forward 标定），或
   - ICIR 跌破基线触发 F2，
   A5 调用 `FactorCrashRegistry.log_crash(...)` 写入一条崩盘记录。
2. 崩盘记录字段：`factor_id, window_start, window_end, ic_mean, icir, crash_type, notes`。
3. 登记库可被查询：`FactorCrashRegistry.query(factor_id=..., since=...)`。

### F4 — 因子剔除触发

1. 用户或定时任务查询当前 `PENDING_REVIEW` 因子列表。
2. 用户确认后，可将该因子标记为 `RETIRED`（不再参与选权）。
3. WalkForwardCombiner 在构造 `feature_cols` 时跳过 `RETIRED` 因子。
4. 剔除操作写入 FactorCrashRegistry 的 `lifecycle_events` 记录（含剔除时间、操作人=local）。

### F5 — 阈值 Walk-Forward 标定

1. 新增脚本 `scripts/calibrate_factor_thresholds.py`。
2. 在 out-of-sample 历史段用滑动窗口计算"何时 ICIR 低于 X 能预警后续因子失效"，选出最优 X。
3. 输出标定结果写入配置文件（`kss/config/factor_health_thresholds.yaml`），供 F2 读取。
4. 标定过程本身受 PIT 纪律约束：标定窗口只能用标定截止日之前的数据。

---

## Acceptance Examples

### AE1 — 基本 IC 落库

给定因子 `momentum_5d`，在 retrain 点 2026-03-31，训练窗口 200 日：

- `FactorHealthTracker.record_ic_snapshot("momentum_5d", "2026-03-31", ic_series)` 写入一条记录。
- 查询 `factor_id="momentum_5d", window_end="2026-03-31"` 能返回 `ic_mean, icir, half_life_5d`。

### AE2 — ICIR 告警触发

- 因子 `pb_ratio` 最近 60 日滚动 ICIR = 0.18，低于标定基线 0.25。
- 系统标记 `pb_ratio` 为 `PENDING_REVIEW`，console 输出告警，FactorCrashRegistry 新增一条类型为 `ICIR_BELOW_BASELINE` 的记录。
- 再次查询该因子状态，返回 `PENDING_REVIEW`。

### AE3 — 崩盘登记可查

- 回测脚本 `screen_alpha158_dsr.py` 完成后，FactorCrashRegistry 写入 3 条崩盘记录（对应 3 个表现最差因子）。
- `FactorCrashRegistry.query(factor_id="rsi_14")` 能返回该因子历次崩盘窗口列表。

### AE4 — 阈值标定不泄漏

- `calibrate_factor_thresholds.py --calibration-end 2025-12-31` 只使用 2025-12-31 之前的数据，不使用之后行情。
- 标定后的阈值写入 YAML，不硬编码在主逻辑中。

### AE5 — 有效 n 正确

- 计算 IC 统计量时，显著性检验的有效 n 取去重日期数（即截面期数），不取股票×日期事件总数。
- 例：200 日训练窗口，20 支股票，有效 n = 200，而非 4000。

---

## Requirements

| ID | 要求 | 优先级 |
|----|------|--------|
| R1 | `FactorHealthTracker` 提供 `record_ic_snapshot(factor_id: str, window_end: str, ic_series: pd.Series) -> ICSnapshot` 接口，写入持久化存储，主键为 `(factor_id, window_end)`，覆盖写（幂等）。 | P0 |
| R2 | IC 快照记录字段至少包含：`ic_mean, ic_std, icir, ic_positive_rate, n_periods`（有效日期数，非事件数），以及 `half_life_1d, half_life_5d, half_life_20d`（可为 null 若拟合失败）。 | P0 |
| R3 | 提供 `rolling_icir(factor_id, window_days=60) -> pd.Series` 查询接口，返回该因子以 60 日为窗口的滚动 ICIR 时间序列，按 `window_end` 升序排列。 | P0 |
| R4 | `FactorCrashRegistry` 提供 `log_crash(...)` 写入和 `query(factor_id=None, since=None, crash_type=None) -> list[CrashRecord]` 查询接口。崩盘记录持久化到 SQLite（复用 `kss/data/sqlite_store.py` 已有模式）或 JSON-Lines（若 SQLite 扩展成本过高）。 | P0 |
| R5 | ICIR 基线阈值不硬编码；由 `calibrate_factor_thresholds.py` walk-forward 标定后写入 `kss/config/factor_health_thresholds.yaml`；告警逻辑从该 YAML 读取。标定脚本首次运行可输出建议值供用户确认。 | P0 |
| R6 | 所有 IC 计算使用 **Spearman Rank-IC**（截面 rank 相关），有效 n = 去重日期数（`ic_series.index.nunique()`），IC 显著性检验的 t-stat = ic_mean / ic_std * sqrt(n)。禁止将事件数（股票×日期）作为有效 n。 | P0 |
| R7 | PIT 纪律：IC 计算只能使用截面日 t 可观测的因子值和 t+1 可观测的收益（即 `next_day_return` 列，与 `FactorPipeline` 和 `WalkForwardCombiner` 现有约定一致）。标定脚本的滑动窗口不得引用标定截止日之后的数据。 | P0 |
| R8 | 单用户本地算力约束：增量模式优先。`record_ic_snapshot` 若发现该 `(factor_id, window_end)` 已存在，直接跳过重算（幂等）。全量重算由显式 `--force-recompute` 参数触发，不做每日全市场全因子扫描。 | P1 |
| R9 | 告警发送复用 `kss/notifications/manager.send_to_channels`，默认 channel=console，不新增推送依赖。告警内容格式：`[FACTOR ALERT] {factor_id} ICIR={val:.2f} 低于基线 {baseline:.2f}（60日窗口，{n}期）`。 | P1 |
| R10 | WalkForwardCombiner 在每个 retrain 点完成 `time_series_ic` 计算后，自动调用 `FactorHealthTracker.record_ic_snapshot`（通过可选 hook 参数，默认 None，不改变现有调用方）。 | P1 |
| R11 | 因子状态机：`ACTIVE → PENDING_REVIEW → RETIRED`（单向）；状态存入 FactorCrashRegistry 的 `factor_lifecycle` 表/字段；`WalkForwardCombiner` 的 `feature_cols` 构造时过滤 `RETIRED` 因子。 | P1 |
| R12 | 1d/5d/20d IC 半衰期计算方法：对滚动 IC 均值序列取对数（绝对值），拟合线性衰减，半衰期 = ln(2) / 衰减斜率；拟合样本不足（< 20 期）或斜率非负时返回 null，不强行输出。 | P2 |
| R13 | 提供 CLI 入口 `scripts/report_factor_health.py --factor <id>` 输出该因子的：滚动 ICIR 图（ASCII）或 CSV、历次崩盘记录、当前状态，无需 GUI。 | P2 |

---

## Scope Boundaries

### In Scope
- 单股票时序 IC（`WalkForwardCombiner` retrain 点）
- 横截面单因子 IC（`factor_cross_section_backtest` 结束后）
- Rank-IC / ICIR 滚动计算与持久化
- IC 半衰期（1d/5d/20d，拟合失败则 null）
- ICIR 衰减告警 + 因子状态机
- 崩盘登记库（结构化查询）
- 阈值 walk-forward 标定脚本
- CLI 报告脚本

### Out of Scope
- Alpha 衰减可视化（SwiftUI 前端集成）— 纯 CLI/数据层先行
- 多因子组合层面的"组合 IC"——组合层由 `MultiFactorCombiner` 负责，本模块只做单因子
- 实时/盘中 IC 更新——KSS 为 EOD 工作流，日级更新
- 自动剔除因子（无人工确认）——F4 保留人工确认步骤

### Deferred
- SwiftUI 因子健康度页面
- 因子崩盘与宏观/行业周期的关联分析
- 多因子 IC 相关矩阵滚动追踪（因子间多重共线性监控）

---

## Key Decisions + Rationale

| 决策 | 选择 | 理由 |
|------|------|------|
| **存储格式** | SQLite（优先）或 JSON-Lines（降级） | SQLite 支持范围查询，`kss/data/sqlite_store.py` 已有封装可复用；若依赖引入成本高则降级 JSON-Lines |
| **IC 类型** | Spearman Rank-IC，不用 Pearson | A 股截面收益厚尾，Rank-IC 更稳健；与 alphalens 主流一致 |
| **有效 n** | 去重日期数，不是事件数 | A 股横截面高相关，事件数会严重高估 t-stat；内存记录 `llm-numbers-deterministic-rendering` 规则，IC 数字必须代码计算 |
| **阈值硬编码禁止** | 所有阈值写 YAML + walk-forward 标定 | 与 `significance.py` 的 `strategy_family` 动态 n_trials 设计理念一致；避免又一层硬编码门 |
| **增量模式** | 幂等写入，按 (factor_id, window_end) 主键 | 单用户本地算力有限，全量重算仅 --force-recompute 触发 |
| **告警通道** | 复用现有 `notifications.manager` | 不引入新依赖；Telegram 推送约束（需系统级代理）已知，console 为默认安全回退 |
| **人工确认剔除** | F4 保留人工确认 | 单用户工具，自动剔除风险过高；告警 + 标记已足够自动化 |

---

## Open Questions

| ID | 问题 | 类型 |
|----|------|------|
| OQ1 | `kss/data/sqlite_store.py` 现有接口是否直接支持动态表创建，还是需要 schema migration？需确认后决定存储选型。 | **blocking** |
| OQ2 | `WalkForwardCombiner.run()` 内部的 `time_series_ic` 调用位置？需确认 hook 注入点的行号，以免破坏现有调用方接口。 | **blocking** |
| OQ3 | 崩盘判定标准：「IC 绝对值连续 N 期 < 阈值」的 N 值初始建议是多少？标定脚本第一次跑之前需要一个合理起点（建议 N=5，阈值=0.02）。 | deferred |
| OQ4 | `report_factor_health.py` 的 ASCII ICIR 图是否有现有工具可复用（如 `rich` 库），还是直接输出 CSV 即可？ | deferred |
| OQ5 | 因子 ID 命名规范：是否与 `FactorPipeline.generate()` 输出的列名一一对应（如 `momentum_5d`、`rsi_14`）？需统一 ID 命名空间避免歧义。 | **blocking** |

---

## Success Criteria

1. **IC 落库完整性**：完整跑一次 `WalkForwardCombiner.run()`（任意单股票，train_window ≥ 60 日），FactorHealthTracker 写入的 IC 快照数等于 retrain 点数，无漏记。
2. **ICIR 告警触发**：人工构造一个 ICIR 持续低于阈值的 ic_series 输入，系统在 60 日窗口累积后产生 `PENDING_REVIEW` 状态和告警，崩盘登记库有对应记录。
3. **有效 n 正确**：`record_ic_snapshot` 写入的 `n_periods` 等于 `ic_series.index.nunique()`，与 `len(ic_series)` 不同时两者不相等（用事件数构造测试用例验证）。
4. **PIT 不泄漏**：`calibrate_factor_thresholds.py --calibration-end T` 运行后，标定结果对应的样本 `window_end` 全部 ≤ T，无 T 之后数据引用。
5. **幂等写入**：对同一 `(factor_id, window_end)` 连续调用 `record_ic_snapshot` 两次，存储中只有一条记录，第二次调用耗时 < 10ms（走跳过分支）。
6. **阈值外化验证**：删除 `kss/config/factor_health_thresholds.yaml` 后运行告警逻辑，系统明确报错（`FileNotFoundError` 或自定义异常），不回退到硬编码值。
