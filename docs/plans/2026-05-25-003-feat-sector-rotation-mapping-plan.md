---
title: "feat: 申万部门轮换映射表（P2 of Bolton 周期框架）"
status: pending
created: 2026-05-25
type: feat
depth: light
---

## Summary

把 Bolton《稳中求胜》第 4 章附录的美式部门轮换表（按阶段 I/II/III/IV）本土化为申万一级行业映射表，让 combo_scan 在选 Top-5 候选时按 P1 给出的当前阶段加权对应行业。这是 P1 → 实盘的连接器。

---

## Problem Frame

**现状**：
- combo_scan Top-5 全市场扫描，候选 = bootstrap-validated 模式命中
- 不区分行业属性，不知道当前阶段应该偏好哪些行业
- 书里反复强调：同样的信号在不同阶段表现差异巨大（例：钢铁/煤炭在阶段 II 是冠军，在阶段 III 末是杀手）

**目标**：
- 建立"阶段 → 申万一级行业池"的静态映射表（人工 + 历史回测调参）
- combo_scan 候选打分公式增加 `+ rotation_bonus`：当前阶段对应行业 +0.2 分，反向行业 -0.2 分
- 暴露 `kss.macro.rotation.get_preferred_industries(stage)` 给调用方

---

## Scope Boundaries

### In-Scope

- **新模块** `kss/macro/rotation.py`：
  - `ROTATION_TABLE: dict[str, dict[str, list[str]]]` —— 阶段 → {"preferred": [...], "avoid": [...]}
  - `get_preferred_industries(stage, level="L1")` —— 返回该阶段优先池
  - `score_industry_fit(industry_name, stage)` —— 返回 [-1, 1] 范围内的偏好分

- **静态映射表（初版，需人工调参 + 60 个交易日验证）**：

  ```yaml
  # kss/config/sector_rotation.yaml
  阶段I_谷底前:
    preferred:  [汽车, 房地产, 家用电器, 建筑材料, 非银金融]
    avoid:      [食品饮料, 公用事业, 医药生物-中药]
    rationale: "利率敏感性高 + 被压抑需求释放"
  阶段II_扩张:
    preferred:  [石油石化, 有色金属, 钢铁, 煤炭, 交通运输-航空, 机械设备]
    avoid:      [食品饮料, 公用事业]
    rationale: "上游资源 + 商品价格 + 周期股盈利顶峰"
  阶段III_顶部:
    preferred:  [食品饮料-白酒, 美容护理, 半导体设备, 国防军工, 银行]
    avoid:      [钢铁, 煤炭, 房地产, 建筑材料]
    rationale: "财富效应 + 资本品扩张 + 利率敏感行业承压"
  阶段IV_衰退:
    preferred:  [公用事业, 食品饮料-必选, 医药生物, 通信运营, 国防军工]
    avoid:      [汽车, 房地产, 钢铁, 有色金属, 煤炭]
    rationale: "防御性 + 利率敏感反向 + 必需消费"
  ```

- **集成点**：
  1. `scripts/scan_combo_signals.py` Top-5 选股时按 `score_industry_fit(stk_industry, regime.stage)` 加权
  2. `kss/sector/commentary.py` LLM prompt 喂入"本阶段优先板块 / 应回避板块"
  3. `scripts/scanner.py` banner 增加"本阶段优先行业"显示

- **单测** `kss/tests/test_rotation.py`：
  - 映射表加载（YAML 配置）
  - 阶段切换时 preferred/avoid 变化
  - 未识别行业返回 0（中性）

### Deferred

- 申万二级 / 三级行业的细化映射
- 概念板块映射（同花顺概念命名空间不稳定，先只做申万一级）
- 部门轮换有效性回测（需 P1 上线 60 个交易日后做）

### Out-of-Scope

- 自动调整映射表（仍是静态 YAML，调参靠人工）
- 跨市场（港股 / A50 期货）部门轮换

---

## Implementation Plan

1. 起草 `kss/config/sector_rotation.yaml`（先用上面草表）
2. `kss/macro/rotation.py` loader + 查询函数
3. 单测 12+ cases
4. 集成到 scan_combo_signals.py（依赖 P1 的 regime tag）
5. 集成到 commentary.py prompt（依赖 P1）
6. 跑一周观察 combo_scan Top-5 输出的板块分布是否与阶段一致

---

## Verification

- 阶段 I 时 Top-5 至少 60% 落在 preferred 池
- 阶段 III 时 avoid 池行业占比 < 20%
- 配置文件可热加载（不需 restart）

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| 中美行业差异（如银行在中国阶段 II/III 都涨） | YAML 配置预留 country-specific 字段，初版按中国市场调 |
| 静态表无法覆盖个股异质性 | 只作 ±0.2 加权，不作硬过滤；个股层面仍由 combo_scan 主导 |
| 不同周期的映射会变 | 留 git history + plan 文档 + 调参日志，便于回溯 |
