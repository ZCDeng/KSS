---
title: "修复 daily_review 次日预测（校准优先）"
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
module: scripts/daily_review
tags: [prediction, calibration, brier, regime, interval-width, stop-loss, daily-review]
---

# 修复 daily_review 次日预测（校准优先）

## Problem Frame

`scripts/daily_review_322_017.py` 生成的次日情形分布段经两周实测已失效：
Brier 0.828（略差于随机 0.80）、方向命中 43%（系统性看反）、80% 区间覆盖仅 53%（目标 80%）。
`scripts/validate_predictions.py` 每周五自动校验，停用判据已明确（连续两周 Brier > 0.8 或方向命中 < 45%）。

诊断出四条根因，对应四组修复：

| 根因 | 来源位置 | 修复方向 |
|------|---------|---------|
| 区间以条件样本 P10/P90 直接定宽，n<20 时严重低估尾部 | `scenario_distribution()` → `_scenario_table()` | 以全样本无条件分位为底，条件样本只允许收窄 |
| 均值回归先验在动量 regime 下方向全反 | `adjusted_scenarios()` 缺 regime 上游开关 | 引入板块 regime 判断，动量态禁用/翻转均值回归乘子 |
| 「5–10 日仍看涨」是常量输出，无区分度 | `_advice_block()` L556–557 | 删除或改为条件化输出 |
| 止损位与中期观点并列无优先级，操作矛盾 | `_advice_block()` L559–561 + L556–557 | 止损改仓位语义，与中期观点显式解耦 |

若修复后校验仍未过判据，强制撤段（段本身从报告中移除），版面交还给关键位与区间。

---

## Actors

- **系统**：`scripts/daily_review_322_017.py` cron，每日 19:00 自动执行
- **校验器**：`scripts/validate_predictions.py`，每周五 19:30 自动执行，推 Telegram 周报
- **用户**：查阅 Telegram 复盘推送 + `storage/daily_review/*.md` 归档，自行判断操作

---

## Key Flows

### F1：预测生成（修复后）

```
daily_review_322_017.py main()
  → stock_section()
      → scenario_distribution(hist, mask)   # 不变
      → widen_interval_unconditional()      # 新增：无条件底线
      → regime_detect()                     # 新增：板块动量 regime 判断
      → adjusted_scenarios(..., regime)     # 修改：regime=momentum 时跳过均值回归乘子
  → _scenario_table(s)                      # 修改：渲染宽化后区间
  → _advice_block(s)                        # 修改：删常量涨观点 + 止损改仓位语义
  → render() → archive_md() → send_to_channels()
```

### F2：周校验判停流程（不变接口，新增停用段逻辑）

```
validate_predictions.py
  → score() → Brier / dir_rate / cov50 / cov80
  → 连续两周: Brier>0.8 OR dir_rate<45%
       → 触发「撤段」判据
  → 用户收到 Telegram 周报 + 明确停用提示
  → 手动（或 hook）从 daily_review 脚本移除情形分布段
```

### F3：停用后报告结构

情形分布段整块删除，`_scenario_table()` 调用点移除。版面由关键位、3 口径次日均值、操作建议三段填充。历史校验仍继续跑（用于未来重启判断）。

---

## Acceptance Examples

### AE-1：区间宽化——尾部覆盖

场景：奥比中光历史 n=18，条件 P10=-3.2%、P90=+4.1%；全样本无条件 P10=-6.8%、P90=+7.3%。

修复前：80% 区间渲染为 `close×(1-3.2%) ~ close×(1+4.1%)`，约 7.3% 宽。  
修复后：区间取 `max(|cond_P10|, |uncond_P10|)` 为底，渲染为 `close×(1-6.8%) ~ close×(1+7.3%)`，约 14.1% 宽。  
校验期望：80% 区间覆盖率从 53% 提升向 ≥65%（短期目标，非一次到位）。

### AE-2：regime 开关——动量态修正翻转

场景：板块连板家数 ≥3（动量 regime 成立），adjusted_scenarios 原逻辑对 D_down×1.3、E_break×1.2。

修复前：乘子照常执行，对「涨停潮」场景系统性压低 A_break。  
修复后：regime=momentum 时，均值回归乘子（D_down、E_break 上调）跳过不执行；牛市乘子（A_break、B_up 上调）保留或按 regime 方向强化。  
校验期望：动量 regime 日的方向命中率不低于中性日。

### AE-3：删常量涨观点

场景：fund_10d>3% 条件为真。

修复前：actions 追加「5-10 日仍看涨 (历史 10d +X.X% · 胜率 YY%)」。  
修复后：该行从 actions 删除，或改为：仅在 `regime != momentum AND dir_rate_rolling > 50%` 时才追加，并注明「条件化输出」。

### AE-4：止损仓位语义

场景：止损位 stop=320.10，中期观点「5-10 日看涨」。

修复前（矛盾）：
```
• 5-10 日仍看涨 (历史 10d +4.2% · 胜率 72%)
• 止损位 *320.10* (今日最低 -1%)
```

修复后（解耦）：
```
• 止损触发 → 减半仓留底仓，不全清（与中期观点解耦）
• 止损位 *320.10* (今日最低 -1%)；破位后若中期看涨则保留 50% 底仓观察
```

### AE-5：撤段触发

场景：validate_predictions 连续两周返回 Brier=0.831、dir_rate=0.43。

修复前：情形分布段继续渲染，用户看到无信息量的概率表。  
修复后：Telegram 周报出现明确停用提示；下一个交易日起，`_scenario_table()` 调用点被移除（手动 or flag），报告不再渲染情形分布段。

---

## Requirements

### R1：区间底线（无条件波动分位）

**优先级：P0**

- 在 `scenario_distribution()` 外，新增 `unconditional_interval(df)` 函数，计算全历史 `fwd_1d` 的 P10 / P25 / P75 / P90。
- 在 `_scenario_table()` 渲染前，对条件样本分位与无条件分位取宽：
  - `p10_eff = min(cond_p10, uncond_p10)`（取更负值）
  - `p90_eff = max(cond_p90, uncond_p90)`（取更正值）
  - 同理 p25/p75。
- n < 20 时强制使用无条件分位（不允许条件样本收窄区间）。
- 渲染注释中注明「区间底线: 全样本 P10/P90」，使用户可追溯。
- 金融数字由代码渲染（`f"{cl*(1+p10_eff):.2f}"`），不由 LLM 复述。

### R2：regime 开关

**优先级：P0**

- 新增 `detect_regime(idx_dfs: dict) -> str` 函数，返回 `"momentum"` 或 `"neutral"`。
  - 判据：板块（科创100 / 机器人 ETF 任一）5 日涨幅 > +8%，OR 当日连板家数（若有数据）≥ 3。
  - 无法计算时默认 `"neutral"`（fail safe）。
- `adjusted_scenarios()` 增加 `regime: str` 参数。
  - `regime == "momentum"` 时：跳过 MACD 缩柱均值回归乘子（D_down×1.3、E_break×1.2 不执行）；A_break / B_up 乘子保留或小幅强化（×1.1）。
  - 其他 regime 下行为与现在一致。
- 情形分布表头注明当日 regime 状态：`n=XX, 基于 YY, regime=动量` 或 `regime=中性`。

### R3：删除常量「5–10 日仍看涨」输出

**优先级：P1**

- 删除 `_advice_block()` L556–557 的「5-10 日仍看涨」追加逻辑。
- 替代方案（可选，在 R3 实现周期内决策）：保留该输出但加条件门槛：
  - 仅在 `regime == "neutral"` 且 validate_predictions 最近一次 `dir_rate > 0.50` 时才追加。
  - 若最近校验数据不可得，不输出（静默，不报错）。
- 不论选哪条路，输出字面文本中不得出现无条件的「仍看涨」表述。

### R4：止损仓位语义与中期观点解耦

**优先级：P1**

- 修改止损动作文本：从「止损位 *X.XX*（今日最低 -1%）」改为「止损触发 → 减半仓留底仓 *X.XX*（今日最低 -1%）；若中期仍看多则保留底仓观察，不强制全清」。
- 删除止损行与中期观点行在 actions 列表中的并列，改为层级关系：先列中期观点（如有），再列止损仓位语义，并注明「两者不矛盾」。
- 止损价格数值仍由代码计算（`lv['low_today'] * 0.99`），不改变计算逻辑。

### R5：撤段机制

**优先级：P0**（与 R1/R2 同批交付）

- 在 `daily_review_322_017.py` 顶部增加 `SCENARIO_ENABLED: bool = True` 开关。
- `render()` 中 `_scenario_table(s)` 调用处：`if SCENARIO_ENABLED: lines.extend(_scenario_table(s))`。
- `validate_predictions.py` 在 Telegram 周报末段：若判停条件成立（连续两周 Brier > 0.8 OR dir_rate < 0.45），追加明确提示：
  ```
  ⛔ 情形分布停用判据已触发。建议将 SCENARIO_ENABLED = False 并重启 cron。
  ```
- 撤段为手动操作（用户修改 flag），不自动写文件（单用户本地，避免 cron 自改代码）。

### R6：校验指标扩展（Brier 分解）

**优先级：P2**（独立可推迟）

- `validate_predictions.py` `score()` 中追加 Brier 校准-分辨分解：
  - 校准项（calibration loss）= 每桶 `(mean_prob - hit_rate)²`的加权和。
  - 分辨项（resolution）= 每桶 `(hit_rate - base_rate)²` 的加权和。
- `render_summary()` 中新增一行：`校准损失: X.XXX / 分辨: X.XXX`，帮助区分「预测太自信」与「预测无区分度」两类失效。
- 不改变现有 Brier 计算逻辑，仅追加分解输出。

---

## Scope Boundaries

**在范围内：**
- `scripts/daily_review_322_017.py` 中的 `scenario_distribution()`、`adjusted_scenarios()`、`_scenario_table()`、`_advice_block()` 函数
- `scripts/validate_predictions.py` 的停用提示文本 + Brier 分解（R6）
- `SCENARIO_ENABLED` flag 机制

**不在范围内：**
- 改变 validate_predictions 的 `parse_review()` 和 `score()` 核心逻辑（除 R6 追加）
- 修改 `kss/prediction/daily_forecast.py`（ML 流水线，与复盘脚本独立）
- 引入外部数据源（连板家数）的 API 调用——R2 判据退化到纯指数分位即可
- 回测验证框架（单用户本地，用 validate_predictions 滚动校验就够）
- 多股票覆盖扩展（STOCKS 列表不变）

---

## Key Decisions

| 决策 | 选项 | 结论 | 理由 |
|------|------|------|------|
| 区间宽化策略 | isotonic 后验校准 vs 无条件分位底线 | 无条件分位底线 | isotonic 需足够样本且引入 sklearn 依赖；无条件底线 2 行代码，透明可审计 |
| regime 判据 | 连板家数（需外部 API）vs 指数 5 日涨幅（本地已有） | 指数 5 日涨幅 | cs_data 和 idx_*.csv 本地已有；连板家数 API 未验证字段，违反「先验数据源再建代码」原则 |
| 常量涨观点 | 删除 vs 条件化 | 删除（R3 首选），条件化保留为可选 | 历史 IC≈0，条件化实现需依赖 validate 结果文件，耦合复杂度高；直接删更外科 |
| 撤段触发 | 自动写 flag vs 手动 | 手动（Telegram 提示 + 用户改 flag） | 单用户本地工作台，cron 自改代码风险高；Telegram 周报提示足够 |
| Brier 分解 | 有 vs 无 | 有（R6，P2 可推迟） | 校准 vs 分辨分解是定位修复有效性的必要工具；不影响主路径 |

---

## Open Questions

### Blocking

- **OQ-1**：R2 regime 判据中，科创100 5 日涨幅 >+8% 的阈值是否合适？实测动量 regime 覆盖率未知（若覆盖率 <5% 则判据太严，若 >30% 则太松）。建议实现前在 idx_000698_SH.csv 上跑一次分布确认阈值，再固化。

- **OQ-2**：R3 条件化路径若保留，需要读取 validate_predictions 最近一次运行的 dir_rate。目前该值没有落盘（只推 Telegram）。若选条件化，需先决定落盘格式（JSON 还是 markdown frontmatter），否则 R3 条件化无法实现，只能走「删除」路径。

### Deferred

- **OQ-3**：isotonic regression 后验校准（气象预报标准做法）能否在样本 n≥50 后替换当前分位法？留待两个月后 validate_predictions 积累足够数据后评估。

- **OQ-4**：情形分布段撤段后，版面是否需要补充其他内容（如向量相似度最近邻历史复盘）？本次不做，但留口子（SCENARIO_ENABLED=False 后版面仅靠关键位+3 口径均值+操作建议维持）。

---

## Success Criteria

| 指标 | 当前实测 | 短期目标（修复后 4 周） | 停用判据（仍生效） |
|------|---------|----------------------|----------------|
| 多类 Brier | 0.828 | ≤ 0.80（不差于随机） | 连续两周 > 0.80 → 撤段 |
| 方向命中率 | 43% | ≥ 48% | 连续两周 < 45% → 撤段 |
| 80% 区间覆盖 | 53% | ≥ 65% | — （监控，非判据） |
| 50% 区间覆盖 | 37% | 40%~60% | — |
| 校准损失（R6） | 未计算 | < 0.05（可测量即可） | — |
| 「撤段」可执行 | 无 flag | SCENARIO_ENABLED=False 生效 | — |

短期目标为方向性，不是硬承诺。若 4 周后仍未达标，按判据执行撤段，不再迭代修复。

---

*Generated by claude-sonnet-4-6 · KSS 单用户本地工作台 · 非投资建议*
