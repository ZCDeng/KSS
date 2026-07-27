---
name: risk-analysis
category: risk-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [get_data_catalog, run_sql_query]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 使用回撤、波动、尾部损失和压力情景评估历史风险，明确样本与模型限制。
---

# 风险分析

风险指标描述给定样本和假设下的历史暴露，不是未来结果保证。

## 输入检查

- 明确价格类型、复权方式、频率、时区、缺失值和基准；
- 记录样本起止、观测数与异常值处理；
- 禁止混用不同频率或不同 `as_of` 的序列；
- 先查数据目录，再运行确定性查询。

## 指标组

1. 波动率与下行波动；
2. 最大回撤、回撤持续期和恢复期；
3. 历史分位损失与尾部平均损失；
4. 与基准的 beta、相关性和跟踪误差；
5. 流动性、集中度和数据缺口。

每个数字登记 `metric_id/formula/version/input_refs/unit/precision/as_of`。不得把正态假设、独立同分布或历史稳定性当成事实。

## 压力测试

- 历史情景：使用可核对的事件窗口；
- 参数情景：分别改变价格、波动、相关性和流动性；
- 组合情景：说明冲击顺序和依赖关系；
- 反向情景：寻找何种冲击会突破给定风险边界。

输出同时展示基准情景、压力结果、敏感性和模型局限，不形成个性化交易行动。

本内容由 Vibe-Trading `risk-analysis` Skill 适配而来，许可与来源见同目录上级的第三方声明。
