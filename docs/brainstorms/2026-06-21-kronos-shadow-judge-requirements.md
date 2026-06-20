---
title: Kronos 影子裁判：regime/不确定性弃权门
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
module: kss/kronos
tags: [kronos, regime, shadow, uncertainty, gate, log_mv, pit]
---

# Kronos 影子裁判：regime/不确定性弃权门

## Problem Frame

Kronos K 线基模型已 vendored（`kss/kronos/` 下全为 `.pyc`）并完成一次小样本微调（64 samples、5 symbols、epoch=1，freeze 日期 2026-02-13），但与主选股流完全断联：

- `storage/kronos/predictions.sqlite` 只有 15 行试跑记录，无任何下游消费
- `kss/kronos/` 内含 `shadow`、`batch_infer`、`adapter` 等模块，均无 `.py` 源码、零被 import
- 直接用于选股的路径被先例堵死：`timesfm_feasibility.md` 确立了「外部预训练模型举证 PIT 清白在使用方，举证不出则不可入回测」的纪律

当前唯一通过 DSR 门槛的选股策略是 `CrossSectionalForecast`（`log_mv` 反向截面 rank，Sharpe 1.74 / DSR 0.754），没有任何 regime 感知或不确定性弃权机制。高波动 / 结构切换日，策略照常满仓持有，回撤保护靠止损而非主动弃权。

**目标**：让 Kronos 只承担「弃权裁决」职责——输出每日不确定性分布宽度或 regime 信号，叠加为 `log_mv` 选股的弃权门（uncertainty 高 → 当日降仓或放弃），不参与选谁。先纯影子运行并量化边际，无边际即弃。

---

## Actors

| Actor | 角色 |
|---|---|
| `kss/kronos/batch_infer` | 每日批量推理，产出 `point_ret` + `lower/upper_close` 区间 |
| `kss/kronos/shadow` | 现有影子运行模块（.pyc），待接入 |
| `kss/kronos/adapter` | 推理结果 → 标准化信号转换层 |
| `CrossSectionalForecast` | `log_mv` 选股器，弃权门的下游消费方 |
| `storage/kronos/predictions.sqlite` | 推理结果落盘，列：`ts_code`, `base_date`, `target_date`, `horizon`, `point_ret`, `lower_close`, `upper_close`, `cutoff_d`, `model_id` |
| 预测生命周期账本（#2 想法） | 影子期对比数据写入账本，量化 Kronos 弃权是否有边际收益 |

---

## Key Flows

### F1：每日影子推理（纯观测，不接实盘）

```
每日收盘后
  batch_infer 拉取当日截面 → Kronos 推理 5 日 horizon
  → 落盘 predictions.sqlite（追加）
  → 计算当日不确定性指标 U(t)：
      U(t) = (upper_close - lower_close) / point_close  # 区间宽度相对比
  → 写影子信号表：{date, U(t), regime_label, would_abstain}
  → 不触发任何实盘动作
```

### F2：弃权门定义（离线标定，影子期确定阈值）

```
影子期（≥60 个交易日）结束后：
  取 U(t) 分位分布
  标定阈值 τ（初步：U(t) > P75 为高不确定性）
  回算「若弃权则实际收益如何变化」
  → 通过边际测试 → 才接入 CrossSectionalForecast
```

### F3：接入弃权门（通过边际测试后）

```
CrossSectionalForecast.predict_pool(panel) 调用时：
  读当日 would_abstain 信号
  if abstain → 返回空仓 / 降仓 N%（权重参数）
  else → 正常 log_mv 截面 rank
  → 最终仓位写账本，带 abstain_reason 字段
```

### F4：边际量化（与账本对接）

```
影子期每日记录：
  {date, abstain_signal, log_mv_would_select, actual_t1_ret, U(t)}
事后对比：
  高 U(t) 日实际 log_mv 收益 vs 弃权日假设收益（0）
  → 若高 U(t) 日平均收益显著低于非弃权日 → 有边际
  → 否 → 弃，不接实盘
```

---

## Acceptance Examples

**Ex1：高不确定性日弃权（影子期）**

```
日期：2026-07-10
batch_infer 推理结果：U(t) = 0.18（> τ=0.14）
影子信号：would_abstain = True
实盘动作：无（影子期不触发）
账本记录：{date: 20260710, U: 0.18, abstain: True, log_mv_picks: [688017, 688041], actual_ret: -0.034}
```

**Ex2：低不确定性日正常通过**

```
日期：2026-07-11
U(t) = 0.07（< τ）
would_abstain = False
实盘动作：正常 log_mv 截面选股
```

**Ex3：边际量化报告（影子期结束）**

```
影子期 60 日，abstain 触发 18 天（30%）
高 U(t) 日 log_mv 平均收益：-1.2%
非弃权日 log_mv 平均收益：+0.6%
结论：有边际 → 进入 F3 接入流程
```

**Ex4：无边际 → 弃**

```
高 U(t) 日与低 U(t) 日收益无显著差异（t-test p > 0.1）
→ 不接实盘，Kronos 影子模块保留但冻结
```

---

## Requirements

### R1：不确定性信号计算（blocking on PIT 举证，见 Open Questions）

- `batch_infer` 每日推理产出 `lower_close`、`upper_close`、`point_close`
- 适配层计算 `U(t) = (upper - lower) / point`，存入影子信号表
- 信号表 schema：`(date TEXT, ts_code TEXT, U REAL, regime_label TEXT, would_abstain INTEGER, threshold_used REAL)`
- `would_abstain` 在影子期为记录字段，不触发实盘

### R2：影子运行落盘

- 每日追加写 `storage/kronos/shadow_signals.db`（独立于 `predictions.sqlite`）
- 包含字段：`base_date`, `U_mean`（池内均值）, `U_p75`, `abstain_count`, `pool_size`
- 推理失败不阻塞主流程，写 `ERROR` 记录后退出

### R3：弃权门接口（边际测试通过后方可启用）

- `CrossSectionalForecast` 新增可选参数 `regime_gate: KronosGate | None = None`
- `KronosGate.should_abstain(date) -> bool`，读 shadow_signals.db
- 默认 `None`（现有行为不变），门控由配置开关控制
- 弃权时权重归零或按 `abstain_weight`（0.0–1.0）缩减，不改变排序逻辑

### R4：边际量化报告

- 影子期结束后可运行 `kss/kronos/eval_margin.py`（或 notebook）
- 输入：shadow_signals.db + 账本 T+1 实际收益
- 输出：高/低 U(t) 日平均收益对比 + t-test + 弃权覆盖率
- 无边际则输出明确结论「不接实盘」

### R5：cron 集成

- 影子推理接入现有日终 cron（收盘后），推理耗时不超过 30 秒（CPU MPS）
- 不影响 `log_mv` 选股主流程的交付时间

---

## Scope Boundaries

**In scope：**
- 不确定性信号计算与落盘（F1）
- 影子运行记录与边际量化（F4）
- 弃权门接口设计（F3，边际通过后）

**Out of scope：**
- Kronos 重新微调或更换基模型（PIT 问题不因微调消失）
- 用 Kronos 做选股排序（先例已否决）
- 扩大 predictions.sqlite 覆盖股票池（影子期先用现有 5 支）
- 向 Telegram 推送 Kronos 信号（影子期不对外输出）
- 开源或分发 Kronos vendor 代码

---

## Key Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| 弃权而非预测 | 弃权门（0/1）+ 权重缩减 | PIT 不可证时举证负担最小；「When Alpha Breaks」G(t) 框架 |
| U(t) 指标 = 区间宽度比 | `(upper - lower) / point` | 已在 predictions.sqlite 中直接可取，无需新推理字段 |
| 阈值标定在影子期后 | 离线标定，非硬编码 | 硬编码门是 KSS 历史 bug（etf_flow 教训） |
| 影子期最短 60 交易日 | 约 3 个月 | 低于此样本量 t-test 无效；优先量化边际再决定接入 |
| 接入方式 = 可选参数 | `regime_gate=None` 默认关 | 保持现有行为不变，不破坏 DSR 通过的基线策略 |

---

## Open Questions

### Blocking（必须解决才能接入弃权门）

**OQ-1：PIT 清白举证**

Kronos 基模型 `NeoQuasar/Kronos-base` 预训练语料和知识截止日期未披露（与 TimesFM 同类问题）。微调 freeze 日期为 2026-02-13，但基模型 cutoff 未知。

- 举证方向 A：联系 NeoQuasar 仓库维护者，确认预训练数据不含 2026-02-13 之后的 A 股价格数据
- 举证方向 B：限定弃权门只在 2026-02-13 之后的前向日期生效（无历史可验证，影子期只看新数据）
- 举证方向 C：视 U(t) 为「模型内在发散度」而非「价格预测」——弃权门不依赖预测方向准确，只依赖区间宽窄与市场波动的相关性；此路线的 PIT 负担较轻但仍需明确论证

**在 OQ-1 解决前**：影子运行（F1、F4）可推进；F3 弃权门代码可设计但不可启用。

### Deferred（可后置）

**OQ-2：regime 标签化**

`regime_label` 字段当前空置。可后续引入趋势 / 震荡 / 崩盘三态标签，细化弃权逻辑（如仅在震荡 regime 弃权）。依赖 #1 因子健康度闭环的 IC 滑轨数据。

**OQ-3：扩大股票池**

当前推理仅覆盖 5 支股票（300002、688017 等试跑样本）。接入弃权门时需覆盖 `log_mv` 全池。推理时间与 OQ-1 同时评估。

**OQ-4：alpha 衰减告警联动**

若 `log_mv` 的 60 日滚动 ICIR 跌破阈值（#1 想法的输出），是否自动触发全日弃权？待 #1 落地后再接。

---

## Success Criteria

影子期（≥60 交易日，PIT 举证通过后起算）：

| 指标 | 门槛 |
|---|---|
| 影子推理成功率 | ≥ 95%（不含市场停盘日） |
| 推理延迟 | 单日 ≤ 30 秒（CPU MPS） |
| U(t) 覆盖率 | 每日覆盖 log_mv 选股池 ≥ 80% 股票 |

边际测试通过标准（接入实盘的前置门）：

| 指标 | 门槛 |
|---|---|
| 高 U(t) 日 vs 低 U(t) 日 T+1 收益差 | p < 0.05（单尾，方向：高 U(t) 日收益更低） |
| 弃权覆盖率（高 U(t) 触发频率） | 5%–40%（过高 = 噪声，过低 = 无用） |
| 不弃权日 log_mv 表现无显著变差 | 对比历史基线 Sharpe 衰减 < 10% |

无法在 120 个交易日影子期内通过边际测试 → 冻结，不接实盘。
