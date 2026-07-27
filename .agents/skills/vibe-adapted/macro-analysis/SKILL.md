---
name: macro-analysis
category: macro-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [research_bundle, run_sql_query]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 按增长、通胀、流动性、政策与跨市场传导组织宏观研究，并强制标注时点和修订风险。
---

# 宏观分析

宏观判断必须绑定统一 `as_of`。公布日、数据所属期和后续修订日期要分开记录。

## 五层框架

1. **增长**：产出、工业、消费、投资、就业与景气调查。
2. **通胀**：总量、核心、生产端和大宗商品传导。
3. **流动性**：货币、信用、资金利率、期限结构和汇率。
4. **政策**：正式文件、会议表述与实际操作分开记录。
5. **市场映射**：只解释利率、汇率、盈利预期和风险偏好的传导路径。

## 工作流

- 每个维度至少取一个一手来源与一个交叉来源。
- 对同比、环比、季调、累计值和两年复合口径逐项标注。
- 区分已发生数据、市场预期和模型推断。
- 为每条关键传导链写出反例或失效条件。
- 不以单个阈值机械定义周期阶段；结合趋势、扩散度和数据质量。

输出使用“事实 → 机制 → 可证伪判断 → caveat”的结构，不把宏观判断转换为个性化交易行动。

本内容由 Vibe-Trading `macro-analysis` Skill 适配而来，许可与来源见同目录上级的第三方声明。
