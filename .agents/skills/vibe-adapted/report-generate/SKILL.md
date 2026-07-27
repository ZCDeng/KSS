---
name: report-generate
category: delivery
version: 1.0.0-kss.1
source: vibe-trading
upstream_commit: 4cede84635df372e56ad4fb0a0647f19be56c892
protected: false
required_tools: [get_report, research_bundle]
allowed_profiles: [generic-research-v1, investment-weekly-v3]
description: 将已验证 Claim、Metric 与 Evidence 编排成结构化研究报告，不直接生成绕过审计的最终 HTML。
---

# 研究报告编排

模型只生成结构化叙事，不直接输出最终 HTML。Delivery Compiler 负责模板、转义、锚点、CSP、manifest 和发布门。

## 建议结构

1. 摘要：最重要的已支持结论与限制；
2. 范围与 `as_of`：对象、时期、数据口径；
3. 核心证据：按 Criterion 组织 Claim 与 evidence ID；
4. 数据与图表：仅引用 Metric Ledger；
5. 矛盾与风险：展示未解决冲突和反证；
6. 方法：来源层级、计算版本与抽样规则；
7. 审计附录：覆盖率、新鲜度、缺口和哈希。

## 文字纪律

- 事实、计算和判断使用不同措辞；
- 每个关键结论附真实 evidence ID；
- 金融数字不得直接写入自由文本，使用 `metric_id` 引用；
- 对陈旧或待复核内容加显式标签；
- 不隐藏失败节点、可选依赖缺失或数据不一致；
- 结论保持解释性，不形成个性化交易行动。

正式产物只在 Audit pass 后发布；审计未通过时仅允许带水印草稿。

本内容由 Vibe-Trading `report-generate` Skill 的报告结构思想适配而来，许可与来源见同目录上级的第三方声明。
