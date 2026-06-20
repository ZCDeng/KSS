---
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
tags: [prediction, ledger, attribution, paper-trade, backtest]
---

# Requirements: 预测生命周期账本（Prediction Lifecycle Ledger）

## Problem Frame

现有预测数据跨三处落盘、互不回流：

| 位置 | 内容 | 缺口 |
|------|------|------|
| `storage/paper_trade/YYYY-MM-DD.json` | 入选时因子值 + 权重 | 无稳定 ID；无 regime/管道标签；无归因 |
| `scripts/validate_predictions.py` 报告 | 50%/80% 带覆盖率、Brier、方向命中 | 一次性打印，不落持久化存储 |
| `storage/daily_review/*.md` | LLM 情形分析 markdown | 结构化字段不可查；失败原因完全丢失 |

`kss_app_bridge.py` 的 `_recommendation_tracking` / `_horizon_return` 每次临时重算 ret1d/ret5d/ret20d，不累积任何归因。归因目前若交给 LLM 自由生成会幻觉数字（龙虎榜事故先例）。

**目标**：建单一事件账本，覆盖从"入选"到"结算+归因"的完整生命周期；下游复盘、回测、IC 统计从这一源头取数。

---

## Actors

| Actor | 职责 |
|-------|------|
| `paper_trade_log_mv.py` | 写入 Ledger — 预测事件（Writer） |
| `validate_predictions.py` | 结算后写回 Ledger — 实际收益 + 校准分 |
| `kss_app_bridge.py` | 读 Ledger — 替代临时重算 `_horizon_return` |
| 分析脚本（IC 计算、回测） | 读 Ledger — 唯一可信数据源 |
| Launchd cron | 在 T+2 之后触发结算 job |

---

## Key Flows

### F1: 预测入账（T 日盘前）

```
paper_trade_log_mv.py
  → 生成 picks（factor_value / rank_pct / rank_position / planned_weight）
  → 追加到 Ledger，字段包括：
      prediction_id  = "{date}_{symbol}"        # 稳定 ID
      prediction_date
      symbol
      strategy        = "log_mv_reverse"
      pipeline_snapshot = {factor_col, top_n, use_execution, …}  # 管道快照
      regime_label    = string | null            # 由代码从 regime 检测结果取，非 LLM
      factor_value
      rank_pct / rank_position / planned_weight
      status          = "open"
```

### F2: 结算（T+2 交易日收盘后 cron）

```
settle_ledger.py（新脚本）
  → 读 Ledger status="open" 且 prediction_date ≤ today-2
  → 从 cs_data_{code}.csv 取 T+1 open / T+2 open（代码确定性渲染，不经 LLM）
  → 计算 realized_ret = T+2_open / T+1_open - 1
  → 写回 Ledger：
      realized_ret
      t1_open / t2_open          # 快照原始价，防止复盘 csv 被更新后漂移
      outcome = "win" | "loss" | "flat"   # 代码判定
      status  = "settled"
```

### F3: 归因（结算后）

```
settle_ledger.py 同一 job 追加归因字段：
  attribution_category = "factor_valid" | "factor_stale" | "regime_shift"
                       | "execution_friction" | "data_missing"
      # 由规则代码决定（见 Acceptance Examples），LLM 不参与数值判定
  attribution_note     = string（LLM 生成，仅自然语言，不含价格数字）
  attribution_generated_at
```

### F4: 下游查询

```
kss_app_bridge._recommendation_tracking()
  → 替换为 read_ledger(status="settled", lookback=N) 聚合
  → 无 cs_data 重扫，无临时重算
```

---

## Acceptance Examples

### AE-1: 正常入账

运行 `paper_trade_log_mv.py --date 2026-06-20`，Ledger 新增记录：
```json
{
  "prediction_id": "2026-06-20_688114.SH",
  "status": "open",
  "factor_value": 14.547,
  "regime_label": "bull_small_cap"
}
```
`storage/paper_trade/2026-06-20.json` 同时保留（向后兼容不删）。

### AE-2: 结算写回（代码渲染真值）

T+2 数据可得后，settle job 写回：
```json
{
  "status": "settled",
  "t1_open": 12.35,
  "t2_open": 12.58,
  "realized_ret": 0.01862,
  "outcome": "win",
  "attribution_category": "factor_valid"
}
```
attribution_note 中不出现任何价格数字（LLM prompt 禁止注入 t1_open/t2_open）。

### AE-3: 因子失效归因

当月截面 IC 滚动均值 < 0.02，则 attribution_category = "factor_stale"，无论当日涨跌。

### AE-4: 数据缺失保护

cs_data 无 T+1 open（停牌等）→ status 保持 "open"，settle job 跳过，次日重试，7 个交易日后升级为 "data_missing"，不幻觉填值。

### AE-5: 重复入账防护

同一 prediction_id 已存在时 Writer 跳过写入（等价于现有 `force=False` 逻辑），日志警告。

---

## Requirements

### R1: 存储结构

**R1-1** Ledger 以 NDJSON（每行一个 JSON 对象）或 SQLite 单表存储于 `storage/prediction_ledger/`，不与 `storage/paper_trade/` 混存。

**R1-2** 每条记录必须包含 `prediction_id`（`"{YYYY-MM-DD}_{symbol}"`）作为主键，Writer 写入时去重。

**R1-3** `pipeline_snapshot` 字段保存入账时的完整因子管道参数（factor_col, top_n, top_pct, use_execution, freshness_days），供事后归因判断管道变更。

**R1-4** `regime_label` 由代码从现有 regime 检测模块取值（字符串枚举），若检测不可用则写 null，不由 LLM 生成。

### R2: 结算语义

**R2-1** 实际收益 = T+1 open（第 1 个交易日开盘，买入价）到 T+2 open（第 2 个交易日开盘，卖出价），与 `_horizon_return(hold=1)` 定义一致。

**R2-2** `t1_open` / `t2_open` 写入 Ledger 时做快照，不随后续 csv 更新漂移（PIT 快照义务）。

**R2-3** `outcome` 阈值：realized_ret > 0 → "win"；< 0 → "loss"；= 0 → "flat"；由代码判定。

**R2-4** 结算 job 仅处理 status="open" 且 prediction_date ≤ today-2（T+2 语义）的记录；数据缺失超 7 个交易日升级 "data_missing"，不回溯修改 realized_ret。

### R3: 归因规则（代码路由，LLM 只出标签说明）

**R3-1** 归因类别由规则代码（非 LLM）按优先级顺序决定：

```
1. data_missing      → T+1/T+2 open 缺失
2. factor_stale      → 入账日前 20 个交易日截面 IC 滚动均值 < 0.02
3. regime_shift      → 入账日 regime_label 与结算日 regime_label 不一致
4. execution_friction→ |realized_ret| < ExecutionModel 单程成本估算（bps）
5. factor_valid      → 其余（因子正常工作）
```

**R3-2** LLM attribution_note 的 prompt 中不注入任何价格字段（t1_open, t2_open, realized_ret）；只注入 attribution_category、outcome、regime_label、factor_value、rank_pct，生成不超过 2 句的自然语言说明。

**R3-3** attribution_note 生成失败（LLM 不可用）时记录为 null，不阻塞结算写入。

### R4: PIT 红线

**R4-1** Ledger 内任何字段禁止使用"结算日之后才可得"的外部快照数据（如结算日当天收盘后才发布的 Tushare 数据）回填至入账日记录，构成前视偏差。

**R4-2** 调试/回测场景可通过 `--dry-run` flag 预览结算结果，但不写入 Ledger。

### R5: 向后兼容

**R5-1** `storage/paper_trade/*.json` 继续由 `paper_trade_log_mv.py` 写入，不删除；Ledger 是附加层，不替代。

**R5-2** `kss_app_bridge._recommendation_tracking` 可从 Ledger 读取聚合结果（已 settled 记录直接取 realized_ret，不重扫 csv），未 settled 记录回退到原有 `_horizon_return` 逻辑（渐进迁移）。

### R6: 可验证性

**R6-1** `pytest tests/test_prediction_ledger.py` 涵盖：写入去重、结算幂等、归因类别优先级、LLM 不注入价格字段。

**R6-2** Ledger 文件可用 `jq` / pandas 直接读取，无私有二进制格式。

---

## Scope Boundaries

**IN scope**

- Ledger 写入模块（Writer）
- 结算脚本 `settle_ledger.py`（含归因规则路由）
- `kss_app_bridge._recommendation_tracking` 读取 Ledger 的渐进迁移
- Launchd plist（T+2 结算触发，平日盘后）

**OUT of scope（明确排除）**

- SwiftUI 前端展示（账本先建，UI 后续单独 feat）
- 多策略 Ledger（本期仅覆盖 `log_mv_reverse`，策略扩展留 `strategy` 字段占位）
- 自动重训因子（账本只记录归因标签，不触发模型重训）
- `daily_review/*.md` 情形分析结构化回流（parsing 难度高，列为 deferred）

---

## Key Decisions

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储格式 | NDJSON append-only（优先）或 SQLite | NDJSON 对 git diff 友好，无依赖；SQLite 查询更快——实现时确认选择 |
| 归因触发时机 | 结算 job 内同步 | 避免两步 cron 之间状态不一致 |
| LLM 注入字段 | 只注入定性标签，禁止价格数字 | 龙虎榜事故先例；数字幻觉风险不可接受 |
| `paper_trade/*.json` 去留 | 保留（向后兼容） | bridge 现有消费者短期不迁移 |
| T+2 结算语义 | 沿用 `_horizon_return(hold=1)` 定义 | 与现有 bridge/summary 指标对齐，不引入新偏差 |

---

## Open Questions

### Blocking（需决策后才能实现）

**OQ-1** Ledger 存储格式最终选 NDJSON 还是 SQLite？

- NDJSON：git 可 diff，单文件，无额外依赖；并发写入需文件锁
- SQLite：`kss_quotes.db` 已有先例，查询方便；需迁移脚本
- 建议：量级 < 10k 记录优先 NDJSON；如 bridge 查询延迟可感知再迁移

**OQ-2** `regime_label` 当前代码路径在哪？项目内是否已有 regime 检测模块输出枚举字符串？若不存在，R1-4 降级为 null，不新建 regime 检测。

**OQ-3** IC 滚动计算（R3-1 factor_stale 判定）是否有现成模块？若无，结算初期先只实现 data_missing / factor_valid 两类，其余归因标签留 null。

### Deferred（实现后解决）

**OQ-4** `daily_review/*.md` 情形分析回流：结构化 parse 难度较高，成本收益待积累足够复盘记录后再评估。

**OQ-5** Ledger 与 `validate_predictions.py` 校准分（Brier、cov50、dir_rate）的整合：校准分目前基于 daily_review md 解析，接入 Ledger 需先解决 OQ-4。

---

## Success Criteria

1. 任意预测日的 `prediction_id` 可在 Ledger 中唯一检索，字段完整（factor_value / regime_label / realized_ret / attribution_category 均有值或有明确 null 原因）。
2. `kss_app_bridge._recommendation_tracking` 从 Ledger 读已结算记录，不重扫 cs_data csv，响应时间比现有实现减少 ≥50%（可测量）。
3. 连续 10 个交易日内，结算 job 0 条漏结算（status="open" 且 prediction_date ≤ today-2 的记录为 0）。
4. attribution_note 中出现价格数字的概率：CI 抽查 100 条 = 0。
5. `pytest tests/test_prediction_ledger.py` 全通过，含归因优先级顺序验证。
