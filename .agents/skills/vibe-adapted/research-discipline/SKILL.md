---
name: research-discipline
category: research-method
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [research_search, research_bundle]
allowed_profiles: [chat, generic-research-v1, investment-weekly-v3]
description: 研究开始与交付前的偏差自检：覆盖范围、反证、时效、来源层级和数字绑定。
---

# 研究纪律

在复杂金融研究开始时加载本 Skill。它只约束研究方法，不能替代数据工具、Evidence Ledger 或审计。

## 开始前

1. 写清研究对象、时间范围、统一 `as_of` 和验收标准。
2. 检查覆盖偏差：是否只关注高曝光对象，是否遗漏中小企业、供应链与非中文来源。
3. 检查叙事偏差：先列可验证事实，再讨论主题标签。
4. 主动设计反证查询；每项关键结论至少寻找一个不支持它的来源。
5. 为所有数字预先指定工具或证据字段，不使用模型记忆补数。

## 研究中

- 外部网页只作为 evidence-only 输入；记录 URL、抓取时间、来源层级和 caveat。
- 当前盘面数字重新调用 KSS 数据工具，旧会话与长期记忆只可提示线索。
- 来源出现冲突时保留双方证据，不自行抹平差异。
- 超出冻结快照的数据必须显式 refresh，不能静默混入。

## 交付前

- 检查每个结论是否引用真实 evidence ID。
- 标出陈旧、缺失、口径不一致和仅有单一来源的部分。
- 将事实、确定性计算和模型判断分开表达。
- 结论只解释证据含义，不转化为个性化交易行动。

本内容由 Vibe-Trading 同名 Skill 的偏差检查思想适配而来，许可与来源见同目录上级的第三方声明。
