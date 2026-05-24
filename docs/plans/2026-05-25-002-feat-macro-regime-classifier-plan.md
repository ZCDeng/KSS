---
title: "feat: 周期阶段分类器（P1 of Bolton 周期框架）"
status: pending
created: 2026-05-25
type: feat
depth: standard
---

## Summary

把 Bolton《稳中求胜》第 3 章的四阶段周期（阶段 I 谷底前 / 阶段 II 扩张 / 阶段 III 顶部 / 阶段 IV 衰退）做成 KSS 的 `MacroRegime` 标签，每日产出一个 `{stage: I|II|III|IV, confidence: float, evidence: dict}`。下游消费者：sector_review LLM prompt、combo_scan 候选过滤、scanner banner。本期只做 rule-based 版本；HMM 等概率模型留后续。

---

## Problem Frame

**现状**：
- P0 已落地分母端数据（shibor / yc / M2 / CPI / PPI）+ Δr_5d / Δr_20d 派生指标
- 但没有"当前处在阶段 X"的统一标签，下游模块各自盲跑
- 板块复盘 LLM prompt 不知道宏观背景，归因质量受限
- combo_scan Top-5 不分阶段，所有时点同一筛子

**目标**：
- 每个交易日产出 `MacroRegime`，落地 `storage/macro/regime_daily.parquet`
- 灌入 `kss/sector/commentary.py` 的 LLM prompt 顶端，作为板块复盘的环境标签
- 暴露 `kss.macro.regime.classify_today()` 给 combo_scan 调用

**非目标**：
- 不做 HMM / 隐马尔可夫 / Bayesian 概率模型（rule-based 先打通）
- 不做实盘择时（只产 tag，仓位决策留人工）
- 不回测分类器准确率（先上线，运行 60 个交易日后再算 hit rate）

---

## Scope Boundaries

### In-Scope

- **新模块** `kss/macro/regime.py`：
  - `MacroRegime` dataclass（stage / confidence / evidence_dict / trade_date）
  - `classify_today(macro_panel, e_panel)` —— 主分类函数
  - `_score_E_trend(panel)` —— 分子端代理（A 股全口径净利润同比、PMI、工业增加值）
  - `_score_r_trend(panel)` —— 分母端代理（Δyld_10y_20d、Δshibor_3m_20d、yc_slope_d20）
  - `_score_liquidity(panel)` —— 流动性代理（M2 同比 d3m、北向资金 d20、两融余额）

- **新分类规则（rule-based）**：

  | 阶段 | E 信号 | r 信号 | 流动性 | 收益率曲线 |
  |------|--------|--------|--------|------------|
  | I 谷底前 | E↑ 加速 | r↓ 或低位震荡 | M2↑ | 凹陡峭化 |
  | II 扩张 | E↑↑ | r↑ 缓 | M2 平 | 凹 |
  | III 顶部 | E↑ 减速 | r↑↑ 加速 | M2↓ | 凹趋平 |
  | IV 衰退 | E↓↓ | r↓ | M2↑（救助） | 凸/水平 |

- **数据源（除 P0 已有外的新增）**：
  - A 股全口径净利润同比：从已有 cs_data 全市场聚合（或 Tushare `fina_indicator` 季度）
  - PMI：Tushare `cn_pmi`（季度补：制造业 PMI 5 大分项）
  - 工业增加值：Tushare `cn_vai` 月度
  - 北向资金 d20：复用 `fetch_moneyflow_hsgt` + 20 日滚动
  - 两融余额：Tushare `margin` 日频

- **集成点**：
  1. `kss/sector/commentary.py` LLM prompt 顶端加入一段："当前宏观阶段：阶段 X（置信度 Y），主要证据：[...]，对应轮动板块应当是 [...]"（P2 提供板块映射）
  2. `scripts/scan_combo_signals.py`（或同名 combo_scan）的 Top-5 选股前置加 `if regime in {'III'}: 降级 entry, 增强 avoid`
  3. `scripts/scanner.py` banner 增加"当前阶段"显示

- **单测覆盖**：
  - rule-based 阈值边界
  - 缺失数据降级（如 PMI 未发布时不应崩）
  - 跨阶段切换的滞后处理（避免单日噪声导致 stage flip）

### Deferred to Follow-Up Work

- HMM / Bayesian 概率分类器（rule-based 跑 60 个交易日积累验证数据后再做）
- 历史回测（2010-2024 历史阶段标注 vs 实际牛熊对应关系，计算 hit rate）
- 阶段切换告警 Telegram 推送（先静默上线，避免误报骚扰）

### Out-of-Scope

- 实盘择时下单
- 港股 / 美股周期同步

---

## Implementation Plan

1. 数据补全：扩展 MacroClient 加 fetch_pmi / fetch_industrial_value_added / fetch_margin
2. 派生指标：在 `kss/macro/derived.py` 加 `compute_E_trend(quarterly_finance) -> Series`、`compute_liquidity_index()`
3. `kss/macro/regime.py` rule-based 分类器
4. 单测 `kss/tests/test_regime.py`，目标 12+ cases
5. 历史回填脚本 `scripts/backfill_regime_history.py`，跑 2018-2024 全量
6. 集成进 sector_review.py prompt
7. 集成进 combo_scan 候选过滤
8. 集成进 scanner.py banner
9. 跑一周观察日志，确认阶段标签稳定再启用 combo_scan 过滤

---

## Verification

- 单测：rule 边界 + 缺数据降级 + 阶段切换滞后
- 历史回测：2020Q1 应判 IV→I 切换；2022Q1 应判 II→III；2024Q4 应判 I 早期
- 板块复盘 LLM 输出：连续 5 个交易日的 prompt 含"当前宏观阶段"段，且阶段标签与实际市场状态合理
- combo_scan 在阶段 III 时 Top-5 entry 候选数应显著少于阶段 I/II

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| rule 阈值需调参 | 留 `kss/config/macro_regime.yaml` 配置文件，不写死代码 |
| 阶段切换噪声大 | 加 5 日滞后确认（连续 3 日同阶段才切换） |
| 全口径净利润数据滞后 | 用 PMI + 工业增加值做近端代理，每季季报后回校 |
| 中国 A 股阶段切换不严格对应美国 Bolton 模型 | 标签命名保持中性（I/II/III/IV 而非"早周期/晚周期"），降低误导 |
