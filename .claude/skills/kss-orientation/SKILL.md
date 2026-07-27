---
name: kss-orientation
category: kss-safety
version: 1.0.0
source: kss-bundled
protected: true
required_tools: [get_orientation, get_data_catalog]
allowed_profiles: [chat, generic-research-v1, investment-weekly-v3]
description: 在 KSS 仓库里用 agent 查 A 股盘面/数据时先看这个。一次上手 KSS 的命令图、数据目录、可用剧本与数字纪律——任何盘面问题动手前先 get_orientation，避免凭空假设有哪些数据。
triggers:
  - kss 上手
  - kss orientation
  - 这仓库有哪些数据
  - 怎么查盘面
  - kss 数据目录
---

# KSS 上手（agent 定向）

KSS 是本地 A 股量化研究工作台。盘面/数据问题**不要凭空假设**有哪些表、哪些命令——先用 MCP 工具定向。

## 先做这一步

调 **`get_orientation`**（kss-mcp）：一次拿到
- dispatch 命令图（有哪些工具、各自参数）
- 数据目录摘要（每个数据集的粒度、最近日期）
- 可用编排剧本（确定性复盘 DAG）
- cron 新鲜度 + 关键文档指针

需要某数据集的**列/含义/路径**时再调 **`get_data_catalog`**（全量字典，自动反射 schema + 手维含义 overlay）。

## 工具速查（全部只读）

| 工具 | 用途 |
|---|---|
| `get_orientation` | 一次上手（**首调**） |
| `get_data_catalog` | 全量数据资产字典 |
| `get_snapshot` | 今日总览快照 |
| `get_stock(symbol)` | 单只个股明细，symbol 如 `688008.SH` |
| `get_sector_rotation([date])` | 板块热点轮动快照 |
| `get_theme_leaders` | 主题龙头梯队 |
| `get_discovery_candidates` | 潜力股发现候选 |
| `get_paper_summary` | 模拟盘跟踪汇总 |
| `get_report(path)` | 读 storage 下 markdown 报告 |
| `list_recipes` / `run_recipe` | 编排剧本目录 / 跑一条（见 [[kss-review]]） |

## 数字纪律（硬约束）

所有金融数字（涨跌幅、金额、排名、净流入）**必须来自工具返回值，逐字引用，不得自己算或臆造**。工具没给的就说「工具未返回该值」。透传的 `commentary` 等字段标了 `provenance: llm_prior`，是**未核实先验**，转述须注明、不当事实。

## 边界

你是 operator + explainer，**不是 decider**：解释盘面、复盘逻辑，**不给个性化买卖建议**、不预测涨跌。

复盘个股/板块「为什么动」→ 用 [[kss-review]] 的剧本打法。
