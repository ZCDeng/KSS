---
name: correlation-analysis
category: quantitative-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [get_data_catalog, run_sql_query]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 对共振、相关、滚动相关与协整关系进行受控研究，避免把统计关系误写成因果。
---

# 相关与协整研究

## 数据准备

- 使用相同交易日历、频率、时区和复权口径；
- 对缺失值、停牌和极端值处理留审计记录；
- 价格水平通常先转收益率；使用水平序列时说明原因；
- 样本内筛选与样本外验证分离。

## 分析层次

1. Pearson 与 Spearman，比较线性与秩相关；
2. 20/60/120 个观测窗口的滚动结果；
3. 按市场状态、行业和事件窗口分组；
4. 对候选关系执行平稳性和协整检验；
5. 报告半衰期、结构突变与样本外稳定性。

## 纪律

- 相关不代表因果；机制解释必须有独立证据。
- 不仅报告最高值，也报告筛选总体、阈值和多重检验风险。
- 对前视、幸存者偏差与数据挖掘做显式检查。
- 只展示绑定 Metric Ledger 的数字。

输出用于解释共振结构、分散化失效与研究假设，不生成自动交易信号或个性化交易行动。

本内容由 Vibe-Trading `correlation-analysis` Skill 适配而来，许可与来源见同目录上级的第三方声明。
