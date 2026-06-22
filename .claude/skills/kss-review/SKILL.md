---
name: kss-review
description: 在 KSS 仓库里复盘「某只个股/某个板块今天为什么动」时用。优先走确定性编排剧本（run_recipe explain_stock_today / sector_context）一束取齐数据，再用工具真值复盘——比零散调多个工具省 token、可复现。
triggers:
  - kss 复盘
  - 这只今天为什么动
  - 为什么上榜
  - 板块复盘
  - kss recipe
  - explain_stock_today
---

# KSS 复盘打法（个股 / 板块）

复盘多步、固定套路的问题，**优先用编排剧本**（`run_recipe`）而非自己零散串工具——剧本是确定性 DAG，一束取齐、可复现、省 token。不确定有哪些剧本先 `list_recipes`。先上手见 [[kss-orientation]]。

## 个股「为什么动 / 为什么上榜」

调 **`run_recipe`**，name=`explain_stock_today`，args=`{"symbol":"688008.SH"}`。一束返回：
- `stock`：个股明细 + 当日 move
- `sectorContext`：所属板块轮动上下文
- `themeLeaders`：主题龙头梯队
- `discoveryHit`：发现候选命中（含 reason/score；未命中 ≠ 出错）

足以回答「为什么动」，通常无需再追加调用。

## 板块 / 轮动复盘

`run_recipe` name=`sector_context`（args 可选 `{"date":"YYYYMMDD"}`）：轮动快照 + 近期历史 + 主题龙头梯队。
或直接 `get_sector_rotation` / `get_sector_rotation_history` / `get_theme_leaders`。

## 复盘时务必遵守

- **数字全部引剧本/工具返回值**，逐字引用，不自己算、不臆造；工具没给就说没给。
- 透传的 `commentary` 标 `provenance: llm_prior` = **未核实先验**，转述注明来源、不当核实过的事实。
- **operator/explainer，不是 decider**：复盘逻辑与依据，不下买卖结论、不预测涨跌。
- 部分失败：剧本某区返回 `{error,hint}` + 顶层 `partial/failedSteps` 时，用拿到的区复盘、说明哪区缺。

## 写操作

`list_recipes` 里 `write:false` 的剧本才经 MCP 放行。真正的写（跑任务/改 cron）须人在环内确认，agent 不自行触发（见 KSSDeck 面板的写闸）。
