---
name: longbridge-realtime
category: kss-data-source
version: 1.0.0
source: kss-bundled
protected: true
required_tools: [get_longbridge_quote, get_longbridge_quotes, get_intraday_snapshot]
allowed_profiles: [chat, generic-research-v1, investment-weekly-v3]
description: 用 Longbridge CLI（经 KSS 只读代理）拉取沪深实时行情 quote / kline 和静态信息，补强复盘。
tags: [realtime, longbridge, cli, seesaw, review]
triggers: ["此刻", "现在多少", "实时价", "盘中", "快照", "分钟线", "分时", "longbridge", "当前行情"]
---

# Longbridge 实时行情（Track B）

用 Longbridge 官方 CLI **经 KSS 只读代理**（`scripts/longbridge_ro.py`）取实时行情。
代理三支护栏确保只读：子命令白名单、symbol 校验（防注入）、凭据 entitlement 门
（paper/quote-only token 前置）。**不得调裸 `longbridge` 二进制**。

## 触发场景

- 复盘问「此刻 / 现在 / 盘中」的价量
- 需要实时快照或最新分钟 bar
- 需要标的静态信息（名称、行业、市值等）

## 可用命令（只读白名单）

| 命令 | CLI 子命令 | 用途 | 示例 |
|------|-----------|------|------|
| 实时快照 | `quote` | 最新价、涨跌幅、量 | `python scripts/longbridge_ro.py quote 688008.SH` |
| 分钟 K 线 | `kline` | OHLCV 分钟线 | `python scripts/longbridge_ro.py kline 688008.SH` |
| 静态信息 | `static-info` | 标的名称/行业/市值 | `python scripts/longbridge_ro.py static-info 688008.SH` |

## 禁用命令（硬闸拒绝，非零退出）

**绝不调用**：`buy` `sell` `cancel` `replace` `order` `orders` `positions` `asset` `account` `balance` `margin` `trade` `submit` `amend`

## 覆盖边界

- **陆股通标的**（沪深主板 / 科创 / 创业 / ETF / 指数）：全部覆盖（ChinaConnect LV1 实时）
- **北交所**（.BJ）：不覆盖，无实时路径
- **数据性质**：forward_observed（前向观察），非 PIT，只用于当日盘面解读

## 与其他工具的分工

| 需求 | 用这个 |
|------|--------|
| 此刻实时价 | `longbridge_ro.py quote` |
| 分钟线 | `longbridge_ro.py kline` |
| 日线 / 存量指标 | `kss-mcp: get_stock` / Seesaw `get_stock` |
| KSS 本地实时 | `kss-mcp: get_longbridge_quote` / Seesaw `get_longbridge_quote`（Track A bridge 面） |

## 硬约束

1. **只调 KSS 代理**，不调裸 `longbridge`——裸二进制含交易命令，代理是唯一入口。
2. **凭据已在 U6 注入**（Keychain → sidecar env），不以任何方式内联或回显 token。
3. 代理拒绝交易子命令（非零退出）——若返回 `REJECT`，如实说明「交易命令被代理拒绝」，不要尝试绕过。
4. 非陆股通/北交所标的若返回空，诚实说明「该标的无实时行情」。

## 安装

见 `docs/solutions/longbridge_cli_setup.md`：`longbridge auth login`（OAuth 浏览器授权，Token 自动持久化）。

## Token 到期

当前 token exp = 2026-10-05。过期后 SDK 无自动刷新（deferred），需手动续。
