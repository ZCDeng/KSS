---
status: deferred
branch: feat/kronos-shadow-synthetic-stress
as_of: 2026-07-10
---

# DEFER: Kronos 影子压测 / 合成 K 线

## Why deferred

- 分支相对 `main` **超前 11 / 落后 ~320**，已严重分叉。
- 含 WIP（`wip(kronos)`）与 `cs_data` 样本扩池，不宜与日常 desk 发布列车一并合入。
- 与当前主路径（Longbridge 实时、资讯雷达、趋势统一月历）无阻塞依赖。

## Resume as separate PR

1. 从最新 `main` 开新分支 `feat/kronos-shadow-…`（勿直接 rebase 旧 tip 盲推）。
2. Cherry-pick / 重放 U1–U8 有效提交，丢弃过期 `cs_data` 二进制噪声。
3. 单独立 PR，标题前缀 `feat(kronos):`，CI 与 dry-run 探针通过后再 merge。

## Remote

保留 `origin/feat/kronos-shadow-synthetic-stress` 作为归档 tip，**不删除**，直到单独 PR 合入或明确废弃。
