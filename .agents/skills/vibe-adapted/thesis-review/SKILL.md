---
name: thesis-review
category: thesis-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [get_stock, get_report, research_bundle]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 将历史研究命题拆成可证伪假设，使用新证据复核其支持度、冲突和失效条件。
---

# 历史命题复核

长期记忆中的 thesis 只能作为“待复核的历史判断”进入本流程，不能直接成为当前事实。

## 建立命题

- 用一句话定义研究命题；
- 拆成 3–5 个可证伪假设；
- 为每个假设指定指标、证据来源、复核频率和失效条件；
- 记录最初 `as_of`、来源 session/entry 与 evidence IDs；
- 将事实依据与解释性判断分开。

## 定期复核

1. 重新获取当前数据，不复用旧会话数字；
2. 对每个假设登记支持、冲突、未知或已失效；
3. 检查会计口径、业务边界和外部环境是否变化；
4. 主动寻找反证和替代解释；
5. 保留新旧版本差异，不覆盖历史记录。

## 输出

返回命题版本、证据变化、关键冲突、失效条件、待补数据和下一复核时间。健康度只能作为研究完整性摘要，必须附 rubric 与 evidence IDs，不能转化为个性化交易行动。

本内容由 Vibe-Trading `thesis-tracker` Skill 的持续复核思想适配而来，许可与来源见同目录上级的第三方声明。
