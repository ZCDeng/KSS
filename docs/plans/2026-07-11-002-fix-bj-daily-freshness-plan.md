---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "BJ Daily Freshness - Plan"
date: 2026-07-11
---

# BJ Daily Freshness - Plan

## Goal Capsule

- **Objective:** App 内北证个股 **K 线 / 池子** 所用日线不再停在 6 月；交易日自动增量刷新到最近可得交易日。
- **Product authority:** Product Contract（本文件）；用户路径 = App 北证个股，节奏 = **每日自动**。
- **Open blockers:** None。

---

## Product Contract

### Summary

桌面端打开北证标的时，日线末日期应接近最近交易日。数据真源为 `storage/bj_cache/*_daily.csv`（App 读此缓存，**不是**主池 `cs_data_*.csv`）。修复扫描拉数硬截止与缓存陈旧，并保证交易日自动增量刷新。

### Problem Frame

- 用户：北证股票数据停在 6 月、一直未更新。
- 实测（2026-07-11 前）：`storage/bj_cache/*_daily.csv` 多数末日期 **2026-06-05**（约 50/53）；仅少数到 7 月。
- 扫描脚本对 Tushare 请求使用**硬编码** `end_date=20260607`（及 20260601 财务类），且缓存命中时 `--force-refresh` 外不更新 → 即便 cron 跑了也拉不到 7 月。
- 已有 `refresh-bj-daily` / `scripts/refresh_bj_daily.py` 按**今天**增量拉数，但**未**保证每日自动、且无法消掉扫描硬截止对「强制全量重扫」的污染。

### Key Decisions

- **KD1.** 产品验收对象 = **App 北证个股日线**（股票浏览器 / 导入 / 北证相关卡片依赖的 `bj_cache`）。
- **KD2.** 节奏 = **每个交易日自动**增量刷新；不依赖用户手动点「刷新北证日线」作为常态。
- **KD3.** 扫描与刷新不得再写死「2026-06」类截止日；结束日 = 运行日/最近交易日语义。
- **KD4.** 一次性把现有陈旧缓存补到最新，再进入日更稳态。
- **KD5.** 主池 `cs_data` 是否纳入全量北证个股 **不在本期**（除非实现中发现 App 实际读 cs_data 而非 bj_cache）。

### Requirements

- **R1.** 北证日线拉取的 `end_date` 不得固定在历史日历日；须随运行日更新。
- **R2.** 交易日自动任务能对北证 50（或当前扫描池）成分执行增量刷新，写入 `bj_cache`。
- **R3.** 修复落地后：抽 ≥5 只原停在 6 月的缓存，`max(trade_date)` 达到最近可得交易日（允许 T+0/T+1 数据源延迟）。
- **R4.** App 打开这些标的时，日线末日期与缓存一致且不再显示 6 月停滞。
- **R5.** 失败可观测（日志/任务结果），不全盘静默失败。

### Scope Boundaries

**In:** `bj_cache` 新鲜度、扫描硬截止、日更自动任务接线、一次性补数。  
**Deferred:** 北证接入 Longbridge 实时；全市场 BJ 进 `cs_data` 主池；财务/股东缓存硬截止的精细化（可与日线一并改掉若同源）。  
**Outside:** 资讯雷达要点 UI（另 plan）；沪深主池日更策略。

### Success Criteria

- **SC1.** 随机 5 只 920xxx 缓存末日期 ≥ 最近交易日（或 Tushare 当日可得日）。
- **SC2.** App 股票页对上述标的展示的日线末日期与 SC1 一致。
- **SC3.** 下一交易日自动任务后无需手点，缓存继续前移。
- **SC4.** 不再出现「全市场统一卡在 2026-06-0x」的批量模式。

### Sources

- `storage/bj_cache/*_daily.csv` 末日期分布
- `scripts/scan_bj50.py` 硬编码 `end_date="20260607"`
- `scripts/refresh_bj_daily.py`（按 today 增量，缺日更保证）
- App bridge：北证历史读 `bj_cache`

---

## Planning Contract

### Summary (HOW)

去掉 `scan_bj50` 硬编码 `end_date`；日扫 wrapper 末尾接 `refresh_bj_daily`（或独立 launchd）；一次性 `--force`/refresh 补齐缓存。App 无协议变更。

### Key Technical Decisions

- **KTD1.** `scan_bj50` 统一 `_end_date_yyyymmdd()` = 运行日（或 env 覆盖），替换 20260607/20260601。  
- **KTD2.** `run_scan_bj50_daily.sh` 成功后调用 `refresh_bj_daily.py`，保证 App 用缓存日更。  
- **KTD3.** 一次性：本机执行 refresh（需 TUSHARE_TOKEN）验证 SC1。  
- **KTD4.** 不把全量 BJ 并入 `cs_data` 主池（本期）。

### Implementation Units

### U1. Dynamic end_date in scan_bj50

- **Goal:** R1, SC4  
- **Files:** modify `scripts/scan_bj50.py`  
- **Approach:** 常量/helper 替换所有硬编码 end_date；`--force-refresh` 用新截止重拉。  
- **Test scenarios:** helper 返回今日 YYYYMMDD；无字面量 20260607 残留（grep）。

### U2. Daily auto refresh wiring

- **Goal:** R2, SC3  
- **Files:** modify `scripts/run_scan_bj50_daily.sh`；optional new `run_refresh_bj_daily.sh` + launchd 若扫描失败也需独立刷新  
- **Approach:** 扫描后 always refresh cache to today；日志写入 `storage/logs/cron/`。  
- **Verification:** 脚本 dry 路径可读；plist 仍指向 wrapper 即可。

### U3. One-shot backfill + verify

- **Goal:** R3–R4, SC1–SC2  
- **Approach:** 跑 `refresh_bj_daily.py`；抽 5 只 `*_daily.csv` 末日期。  
- **Test expectation:** runtime smoke。

### Verification Contract

- grep 无 20260607 硬截止  
- 5 只缓存 max date 近最新交易日  
- App 打开北证票日线末日期一致  

### Definition of Done

- [ ] U1–U3  
- [ ] SC1–SC4  

### Product Contract preservation

Product Contract unchanged。
