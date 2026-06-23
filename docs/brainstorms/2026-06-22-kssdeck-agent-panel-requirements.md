---
module: desktop, mcp, llm, agent
tags: [agent-loop, mcp-skill, in-app-assistant, human-in-the-loop, number-discipline]
problem_type: feature-requirements
date: 2026-06-22
scope: deep-feature
source_ideation: docs/ideation/2026-06-22-agent-reasoning-framework-ideation.html
---

# KSSDeck 内置 AI 复盘助手面板 — 需求

## 一句话

在 KSSDeck 桌面 app 里放一个 AI 复盘助手面板:用户用中文问盘面,一个真·工具调用 loop
(自主多轮)调用 MCP 工具回答,所有金融真值由代码渲染。把 agent 从「只有 Claude Code 开发者能用」
搬进桌面 app 给本人使用的个人驾驶舱。

## 目标与用户

- **用户:本人单用户**。无多用户、鉴权、合规话术负担。
- **核心结果:** 不开终端,在 app 里用一句中文完成一次带真值的多步盘面问答 / 复盘。
- **今天的替代方案(counterfactual):** 开 Claude Code 调 MCP 工具。面板把这条只有开发者会走的路,
  变成 app 内人人(其实就是本人)能走的路。

## 锁定的决定

| 维度 | 决定 |
|---|---|
| 机制 | 真·工具调用 loop(自主多轮),非固定路由。对 ideation #4 的有意升级,因单用户而合理。 |
| 写权限档位 | 读 + paper + live 全档开放给 loop 调用面 |
| live 闸 | **人在环内,逐次批准**。loop 自由读 + 跑 paper;每个 live 真写(下单 / 启禁 cron)弹 UI 由本人 tap 确认。**loop 不能自设 `confirm=True` 走 live。** |
| 边界性质 | solo 个人工具 —— 合规红线(不给他人投资建议)不适用;保留的是**执行安全闸**(防幻觉触发真金白银),非合规闸。 |
| 数字纪律 | 复用 U7 + sanitizer:loop 叙事中的数字必须来自 tool 返回值,禁止 LLM 凭空生成,检出即中和(provenance)。 |

## 范围内

1. **面板 UI** —— KSSDeck 内的聊天界面:输入中文、流式渲染 loop 多轮过程、live 写时弹确认弹窗。
2. **工具 loop** —— 自主多轮,挂在 MCP 工具集上(现有 13 tool + 下方 #1–#3 新工具)。
3. **复用 `bridge.dispatch`** —— SwiftUI / MCP 同源那条缝(`scripts/kss_app_bridge.py:3123`),零逻辑 fork。
4. **provenance 守卫** —— 复用 U7 风格,守 loop 自由叙事里的数字。
5. **live 人在环内确认** —— 拦截 loop 对 WRITE_COMMANDS 的 live 调用,UI 逐次批准。

## 依赖(前置,必做且注册为公开 MCP 工具)

本面板站在这三块地基上。三者均须注册成**公开 MCP 工具/skill**,供本面板 loop、Claude Code、
及未来任意 agent 共用。建议各开独立 plan,简单者优先落。

- **#1 数据目录(data catalog)** —— 自动从代码反射出全量数据资产字典(列名 / 含义 / 粒度 / 刷新源 /
  最近日期),产出 `storage/data_catalog.json`,launchd 每日刷新。当前**零数据字典**,agent 只能猜
  parquet 列名。只读,公开安全。
- **#2 `get_orientation` 定向包** —— 单次返回能力图 + 数据目录(#1)+ cron 新鲜度 + 文档指针。
  把「读 4 处才上手」压成「1 个工具调用上手」。只读,公开安全。
- **#3 编排剧本(recipes)** —— 高频多步任务固化成确定性 DAG,agent 选剧本不自由发挥。
  **公开为 MCP 工具时须自声明 read / write:** 只读剧本(复盘解释链)公开安全;触发 paper / live 的
  写剧本继续走 `_LIVE` + `confirm` 闸,read / write 剧本分开注册。

> 关系:#1–#3 是公开工具地基,#4(本面板)是挂在其上的 loop runtime。#4 的价值依赖 #1–#3 先落。

## 边界与安全

- **执行安全闸优先于自动化便利:** loop 永不自行触发 live 写。`_LIVE`(启动读一次)+ 每 live 调用
  人在环内 UI 确认,双层。
- **数字防幻觉:** 自由聊天 loop 比模板 commentary 更难守 U7 —— 叙事天然想内联数字。tool 返回值
  (代码源)可信;LLM 凭空造的、tool 结果里没有的数字,检出即中和 + fail-loud 记日志。
- **注入面:** 经 sanitizer 的外部源(同花顺等)纪律对 loop 输入同样适用。

## 待选型 / 待 spike(不阻塞落档,plan 期定)

**Loop runtime 选型是真 fork,建议 plan 期一次小 spike,不现在拍板:**

- **A. 薄 loop(自建)** —— LLM 原生 function-calling + while loop + MCP client。carrying cost 最低,
  最合简单律。适合:复盘本质若是少跳查询。
- **B. DeerFlow 2.0** —— deep-research 式多步规划 / 重规划框架。若「复盘」本质是多跳研究(大概率是),
  框架的规划能力值这个 carrying cost。偏重。
- **C. Pi 类 agent runtime** —— 同属框架方案,权衡类似 B。

判据:框架内置的多步规划 / 重规划能力,是否值得单用户工具的 carrying cost。

## 待解问题

- **Q1 provenance 严格度:** 硬中和(同 U7)vs 标注来源放行 —— 自由聊天里哪种体验/安全更优,plan 期定。
- **Q2 loop 步数 / 超时上限:** 防失控空转的硬上限,plan 期定。
- **Q3 runtime 选型:** A/B/C,见上,建议 spike。
- **Q4 会话持久化:** 单任务内保上下文必需;跨会话记忆 solo 下是否需要,待定。

## 成功标准

- 非终端场景:在 KSSDeck 里一句中文完成一次多步、带代码真值的复盘问答。
- 冷启动 agent 首调 `get_orientation` 后后续零误调(不调不存在的 tool / 列)。
- 诱导 loop 自动执行 live 写时,稳定被人在环内确认拦截。
- loop 叙事里每个数字可点回某 tool 返回值 / catalog 字段。

## 明确不做(本轮)

- ideation #5(红线工程化,solo 下降级)、#6(provenance 已并入本档)、#7(eval-ledger 引用,数据门控)
  —— 本轮忽略。
- 多用户 / 公开分发版 —— 明确 solo。
