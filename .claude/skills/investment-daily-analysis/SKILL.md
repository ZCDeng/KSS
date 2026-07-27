---
name: investment-daily-analysis
category: investment-research
version: 1.0.0
source: kss-bundled
protected: true
required_tools: [research_bundle]
allowed_profiles: [investment-daily-v1]
description: 以受控分析师语料、已验证精判卡和确定性公式生成投资分析日报；缺少真实证据时只报告缺口。
---

# 投资分析日报

本 Skill 只负责研究方法和任务入口，不能充当市场证据，也不能直接生成正式金融数字。

## 使用前提

- 选择一份通过 `analyst-corpus-v1` 校验的本地语料；
- 原始消息具有来源、发布时间、内容哈希和可核验引用区间；
- `precision-card-v1` 已由独立 checker 通过；
- 卖方转发与分析师原生观点已经隔离。

任何一项缺失，都应把 Goal 标记为 `insufficient_evidence` 或 `blocked`，并解释缺口；不得用模型常识补齐卡片或数字。

## 日报步骤

1. 冻结交易日、统一 `as_of` 与语料对象哈希；
2. 读取已验证卡片，排除卖方转发和无明确置信表达的记录；
3. 由确定性代码计算市场温度、主题共识、风险严重度和催化剂状态；
4. 把精判卡、来源和未通过校验的记录分别列示；
5. 生成方法论、公式版本、配置哈希、输入哈希与审计摘要；
6. 仅在 Evidence、数字账本和 Delivery Compiler 审计全部通过后标记完成。

输出只解释证据与变化，不给出买入、卖出、仓位、目标价或个性化交易行动。
