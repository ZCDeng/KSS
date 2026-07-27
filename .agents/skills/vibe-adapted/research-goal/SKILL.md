---
name: research-goal
category: research-flow
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [research_bundle]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 把复杂金融问题转成目标、验收标准、任务、证据和完成审计的受控研究流程。
---

# 目标驱动研究

适用于多步骤对比、审计、历史命题复核和需要持续到证据充分的研究。简单的一次性事实查询不必启用。

## 合同

研究开始前明确：

- objective：要回答的决策问题；
- scope：对象、日期、市场和排除项；
- criteria：3–6 条可审计验收标准；
- snapshot：统一数据时点；
- budget：时间、节点和 provider token 上限。

## 执行

1. 冻结输入与快照。
2. 按 Profile 的确定性 DAG 执行；模型不得自行增加受保护节点。
3. 每个任务只提交结构化 `claims/evidence_refs/artifact_refs/open_questions/warnings`。
4. 证据必须来自成功工具结果、受控导入或确定性计算。
5. 失败、超时与证据不足保留真实状态，不能用模型文字替代完成门。

## 完成

只有 Research Audit 可以将 Goal 标为完成。必需 Criterion 的证据数量、新鲜度、验证状态、关键矛盾、数字账本和产物哈希必须全部通过。

本 Skill 不授予数据写入、任务图修改或发布权限，也不输出个性化交易行动。

本内容由 Vibe-Trading 的 goal-driven research 工作流适配而来，许可与来源见同目录上级的第三方声明。
