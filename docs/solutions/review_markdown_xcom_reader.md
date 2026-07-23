---
title: AI 复盘 Markdown xcom 阅读皮
module: kss-desktop
tags: [xcom, markdown, reviews, typography]
problem_type: ui-pattern
date: 2026-07-23
---

# AI 复盘正文 · xcom thread 阅读皮

## 问题

壳层已 xcom，正文 `markdown.html` 仍是衬线杂志皮；板块/妖板面板字阶与任务区不一致；文内 h1 与 Swift 标题重复。

## 解法（P0–P3）

| 层 | 改动 |
|----|------|
| P0 | `data-reader=xcom`：600 栏宽 / 15 字阶 / 无衬线标题；个股详情去卡片 |
| P1 | MD 表 hairline + 横向滚动；Swift 明细表去圆角填色卡 |
| P2 | 板块/妖板 meta 行、section 12.5、点评与覆盖胶囊对齐 `SettingsFormStyle` |
| P3 | 文首 h1 标 `doc-title-dup` 并 `display:none`（壳层已有标题） |

## 复盘 MD 写作约定（软约束）

- **一篇一主标题**：文件名/壳层已有 title 时，正文**不要再写一级 `# 标题`**，从 `##` 起笔。
- 优先：`## 结论` → `## 盘面` → `## 资金/事件` → `## 风险`。
- 表格：首列名称、其余列数字；渲染侧会右对齐非首列。
- 少用 clay 风格大段引用；短结论用列表。

## 代码入口

- `Sources/KSSDesktop/Resources/markdown.html` — `polishXcomDom` / `data-reader`
- `Sources/KSSDesktop/Views/ReviewsView.swift` — stockDetail / Sector / Hotspot 面板
