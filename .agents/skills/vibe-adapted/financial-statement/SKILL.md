---
name: financial-statement
category: fundamental-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [get_data_catalog, run_sql_query, research_bundle]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 以三表勾稽、现金质量、资本效率和异常信号为主线的财务报表研究方法。
---

# 财务报表研究

先用 `get_data_catalog` 确认本地字段、期间与口径；本地缺失时用 `research_bundle` 获取公告或正式披露。任何展示数字必须绑定工具字段或 Evidence Ledger。

## 分析顺序

1. **利润表**：拆分收入、毛利、期间费用、非经常项目与归母结果。
2. **资产负债表**：检查现金、应收、存货、固定资产、商誉、有息负债和权益变化。
3. **现金流量表**：比较经营现金净流量、净利润、资本开支和筹资流量。
4. **勾稽关系**：核对资产恒等式、现金期初期末变化、利润与权益变动。
5. **跨期与同业**：至少使用三个可比期间，并明确同业口径差异。

## 确定性指标

- 盈利现金比：经营现金净流量 ÷ 净利润；
- 应收与收入增速差；
- 存货与成本增速差；
- 资本回报拆解：利润率 × 资产周转 × 财务杠杆；
- 自由现金流：经营现金净流量 − 资本性支出。

计算结果登记 `formula/version/input_refs/unit/precision/as_of`。分母为零、负值或口径改变时不得套用常规解释。

## 异常清单

- 利润增长而经营现金持续背离；
- 应收、存货或合同资产显著快于收入；
- 关联交易、减值、资产处置或公允价值变动贡献异常；
- 审计意见、会计政策、合并范围或管理层口径发生变化；
- 同一指标在不同来源间无法对齐。

这些信号只触发进一步核验，不直接证明违规或形成交易行动。

本内容由 Vibe-Trading `financial-statement` Skill 适配而来，许可与来源见同目录上级的第三方声明。
