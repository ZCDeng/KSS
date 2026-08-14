---
name: cn-hk-equity-research
category: kss-workflow
version: 1.0.0
source: kss-bundled
protected: true
required_tools: [resolve_listing, run_equity_coverage]
allowed_profiles: [chat]
description: |
  在 KSSDeck 对话里对一只沪/深/北或港股做机构级深度覆盖：九章研报、财报模式、脚本估值、检查器、结论与 Kelly-lite，默认 PDF。
  触发：研究一下 / 深度覆盖 / 估值 / 值不值得买 / 财报 / 业绩 / 指引 / 电话会。
  不适用：为什么动 / 为什么涨 / 为什么跌 / 一句话报价（用 kss-review）；美股与在美上市 ADR；周报包。
---

# A/港个股深度覆盖

只覆盖解析后的 `.SH` `.SZ` `.BJ` `.HK`。美股/ADR 用 `resolve_listing` 确认后停止，说「超出范围」。

## 硬规则

1. 先 `resolve_listing`。门控只看后缀。同一中文名优先 A/港（阿里巴巴 → 09988.HK，不是 BABA）。
2. 一句两只票 → 「超出范围」，不要默默做第一只。
3. 调 `run_equity_coverage`。标签、动作、Kelly-lite **只能引用工具 JSON**。缺字段写「未获取到」。
4. 港股中资：VIE/结构风险未定价则该侧观望，禁止买入/Kelly。
5. A/H 缺一侧 KSS 盘面 → 该侧观望；网页报价不得当该侧买入依据。
6. 「为什么动/涨/跌」不要走本 skill。
7. 报告交出后只引用冻结数字；新覆盖必须用户再次明确研究意图。
8. 默认 PDF + Markdown 侧车。聊天给相对路径，不要甩假设 JSON。
9. 做不完就说「无法完成 / 证据不足 / 超出范围」，不要半篇备忘。

手册分页：`read_skill_resource` 读 `references/` 与 `industries/`。本文件只做路由，不执行脚本。
