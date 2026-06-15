# Plan: 板块复盘解读层实时数据补强 P1 — 两融 + 北向明细

> **日期**: 2026-06-15
> **状态**: TODO（等 PR #9 合并 + kronos 分支收尾后起）
> **依赖**: P0 龙虎榜接入（PR #8 + PR #9）、`kss/data/dragon_tiger_client.py` 范式、确定性渲染模式
> **复盘参照**: [`docs/solutions/dragon_tiger_integration_retrospective.md`](../solutions/dragon_tiger_integration_retrospective.md)

---

## 1. 问题陈述

对照 `simonlin1212/a-stock-data` 的数据源评估结论：Tushare 回测骨架不动，
实时解读层用 a-stock-data 免费源补强。P0 龙虎榜已接入。P1 补两类席位/杠杆
资金信号，填补当前 `sector_commentary` 的盲点：

- **两融**：杠杆资金情绪。Tushare `margin` 吃积分；现状完全缺。
- **北向板块明细**：现状 KSS 只有北向**总量**单行（`north_money`），
  想要板块级流向。

两者都只进解读层，**严禁回流回测**（非 PIT）。

## 2. 设计目标

给 `sector_commentary` 的"科技板块资金走向"段补杠杆情绪、给北向段补板块粒度。
不改回测、不改 Tushare 链路。完全复用 P0 龙虎榜趟出的 8 步范式。

## 3. 固化范式（每个端点照走，来自 P0）

```
1. 真实响应核字段（写代码前先拉一天数据逐字核对列名）
2. 无鉴权 HTTP client —— 镜像 kss/data/dragon_tiger_client.py
   （失败/网络错/非交易日空响应/必需列缺失 → None 不外抛；2 次尝试 + 退避）
3. SectorSnapshot 加字段 + load_sector_snapshot 串行 fetch + 失败计入 missing
4. 【数字归代码】render_*_line 确定性渲染真值行 —— 数字唯一来源，LLM 不碰
5. build_context 只给定性 payload（方向标签，剥掉全部裸数字）
6. prompt 改判断（"用方向标签点评一句，严禁输出任何数字"）
7. formatter + fallback_text 共用 render_*_line（两路径数字必然一致）
8. 测试含幻觉防护回归（mock LLM 吐假数字，断言最终输出仍以代码真值行收尾）
```

### 不可破的纪律

- **数字归代码，LLM 只做判断** —— P0 实证 LLM 会编造金融数字（龙虎榜
  101→45/64），这是 P1 最高优先级红线。
- 解读层数据**严禁写回 `cs_data` / 任何 backtest 输入**（非 PIT）。
- 东财端点**串行不并发**（每秒 >5 触发封 IP），紧挨现有 HTTP 调用排。
- 外部文本进 LLM payload 前过 `sanitize_llm_input`。
- kronos 等并发会话在跑时，**用 `git worktree` 隔离开发**，不动共享 HEAD。

## 4. P1-a 两融余额（融资融券）— 把握度高，先做

| 项 | 内容 |
|----|------|
| 价值 | 杠杆资金情绪，市场级 |
| 端点 | 东财融资融券：融资余额/买入额/偿还额 + 融券余额/卖出量/偿还量（日级） |
| 新文件 | `kss/data/margin_client.py`（镜像 dragon_tiger_client） |
| Snapshot 字段 | `margin: dict`（全市场融资余额 + 环比） |
| **定性 payload** | `{"margin_trend": "加杠杆/降杠杆/持平"}`（按融资余额环比判定），**不给余额裸数字** |
| 真值行（代码） | `render_margin_line`：`💳 两融：融资余额 X 亿（环比 ±Y%），融券余额 Z 亿` |
| prompt | 第 4 段（资金走向）加：用 `margin_trend` 点评一句杠杆情绪，禁数字 |
| 风险 | 低，东财两融数据稳定公开 |

**验收标准**：dry-run 真值行的融资余额数字 = 独立 payload 探针逐字一致；
LLM 正文无编造数字。

## 5. P1-b 北向板块/个股明细 — 先验数据可得性，别急着写

| 项 | 内容 |
|----|------|
| 价值 | 现状只有北向总量单行，想要板块级流向 |
| ⚠️ **拦路风险** | **2024-05 起沪深港通取消北向实时资金流披露**。a-stock-data 的"北向自缓存"很可能只是**总量**；板块/个股明细或已无公开实时源（只剩港交所 CCASS 持股 T+1，口径是"持股变动"不是"资金流"） |
| **第一步（gate）** | 拉 a-stock-data 北向端点真实响应，确认返回粒度——是板块明细还是只有总量。**不验证清楚不写代码** |
| 若可得 | 扩 `northbound` 加 `by_sector`；`render_northbound_line` 从单行扩成"总量 + 前 3 流入板块" |
| 若不可得 | P1-b 标记 BLOCKED，北向维持现有总量；从清单移除并在复盘文档记一笔 |

## 6. 排期

1. **P1-a 两融**：纯增量、数据稳、低风险 → 一个 PR 走完 8 步范式。
2. **P1-b 北向**：先只做第 5 节的"第一步 gate"（拉真实响应看粒度），
   验完再决定做不做——不确定前不写代码。
3. 两条都**等 PR #9 合并后**起（避免分支叠加 + kronos 并发未收尾）。

## 7. 验收（每条 PR 通用）

- [ ] 真实响应核过字段名
- [ ] 单测含失败降级 + 幻觉防护回归，全套 green
- [ ] dry-run 真值行数字 = payload 探针逐字一致
- [ ] LLM 正文无编造数字、无个股代码泄漏
- [ ] 无任何解读层数据写回 backtest 输入

## 8. 后续（P2，不在本 plan 范围）

题材归因/强势股补强 `hot_reason_tags`——与现有 `ths_hot` 部分重叠，
接入前先确认同花顺概念口径是否与东财/申万命名空间打架
（参 `data_fetcher.py` 既有的命名空间不一致坑）。
