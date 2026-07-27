---
name: corporate-events
category: event-analysis
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [research_bundle, get_stock]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 对并购、股权变化、激励、融资、监管与退市风险进行事实时间线和情景核验。
---

# 公司事件分析

事件研究先建立可追溯时间线，不从标题直接推断影响。

## 证据顺序

1. 交易所公告、监管文件与公司正式披露；
2. 后续补充、问询、审批和实施进展；
3. 财务报表中的相关项目；
4. 新闻与第三方解读，仅作交叉证据。

## 事件模板

- `event_type`、主体、首次披露时间和当前状态；
- 法律与监管条件；
- 对价、股份、现金、资产或债务口径；
- 关键日期、尚未满足的先决条件；
- 对资产负债表、利润、现金流与治理的潜在影响；
- 失败、延期、稀释、整合和信息不完整风险；
- evidence IDs 与矛盾项。

涉及价格、比例、数量或日期时逐字段引用来源；情景计算登记公式与输入，不把尚未完成事项写成既成事实。

输出解释事件机制与不确定性，不形成个性化交易行动。

本内容由 Vibe-Trading `corporate-events` Skill 适配而来，许可与来源见同目录上级的第三方声明。
