---
title: 分钟级 PIT-safe 数据层 —— 内容寻址 blob + 版本化 canonical + 两态隔离的存储模式
tags: [PIT, intraday, look-ahead-bias, sqlite, content-addressed, data-integrity, provenance, ce-pipeline]
problem_type: architecture-pattern
module: kss/data
created: 2026-06-22
---

# 分钟级 PIT-safe 数据层 —— 内容寻址 blob + 版本化 canonical + 两态隔离的存储模式

## TL;DR

- 非-PIT 实时源（AKShare/东财分钟流）要进 KSS,必须**结构上隔离、不可回流回测**——延续既有红线（[[dragon_tiger_integration_retrospective]]「解读层数据严禁回流回测」），这次给出**存储层的具体机制**而非只是约定。
- 可复用的 PIT 版本化存储模式 = 内容寻址 blob 去重 + per-run observation 全记 + 版本化 canonical（冲突→更高 revision,**绝不 `INSERT OR REPLACE`**）+ 时间版本化快照评估（manifest-SHA 认证,后 revision 不继承前 revision 绿评估）。
- **隔离靠类型,不靠约定**：`complete`（采集即得）与 `reconciled`（次周期对账）是两个状态,pit_backtest 强制 reconciled;只读复盘走独立 `ReviewBar` 类型,结构上不可喂给任何回测入口。
- 历史 PIT 不可证明时,用**保守偏晚的 `available_from_ts` 代理**（标 proxy-PIT）——偏晚方向天然抗 look-ahead,顶多低估策略不会高估。
- 两个易踩的开环：① 发布延迟漂移检测必须在**生产路径**(收盘自报 `retrieved_at - bar_end`),挂在可选 watch 模式=开环;② 滚动端点的窗内追补必须**外科式限定到缺失日**,否则改写已 complete 的 PIT 冻结历史。
- 流程经验:CE 全链路两轮对抗审(doc-review PRD → ce-plan → doc-review plan),**feasibility + adversarial 跨视角独立命中同一 P1 = 最强真洞信号**。

来源工件:`docs/plans/intraday-data/prd-intraday-data-layer.md`、`docs/plans/2026-06-22-005-feat-intraday-data-layer-plan.md`(均经两轮 ce-doc-review)。

---

## Context

KSS 现有 `SQLiteStore`（`kss/data/sqlite_store.py`）以 `(ts_code, trade_date)` 为主键、`INSERT OR REPLACE` 更新,适合日频缓存,**无法**保存「来源版本」和「何时可得」。要接分钟数据又不污染 PIT 回测骨架,需要一套新的存储语义。`docs/solutions/` 此前有 PIT 红线的*原则*（[[dragon_tiger_integration_retrospective]]）和 look-ahead 的*检测*（[[lookahead_bias_lessons]]、`known_bias_gaps.md`),但没有**分钟级 + 内容寻址 + 版本化**的存储层模式。本条补这个空。

## Guidance

### 1. 非-PIT 实时源结构隔离(延续红线,落到存储层)

实时抓取源(AKShare/东财)给的是「当下快照」,不是「历史某天的时点值」。用它回填历史 = 直接注入 look-ahead/幸存者偏差。**机制**:这类 bar 标 `availability_class=forward_observed`/provenance,独立库(`intraday_quotes.db`,挂 `STATE_ROOT/storage`),与日频表零 schema 重叠;「可进回测」是存储层**强制**的属性,不是调用方记得遵守的约定。

### 2. PIT 版本化存储四件套

- **内容寻址 blob**:`payload_blobs(payload_sha256 PRIMARY KEY, response_zlib, ...)` 去重字节;`payload_observations` 记**每一次** HTTP observation(run_id、retrieved_at、redacted_request)。同 payload 两次 run → 一 blob 两 observation。
- **版本化 canonical**:`canonical_bars` 唯一索引 `(instrument_id, bar_end_ts, interval, source, revision)`;冲突→**更高 revision**,旧 revision 保持可查。**绝不 `INSERT OR REPLACE`**(日频表那套覆盖语义恰恰给不了此不变式)。
- **写一次-PIT 守卫**:canonical 版本一旦写入,后续 run 不得静默覆盖 PIT 冻结值(同 [[known_bias_gaps]] 的 settle 重结算守卫精神)。
- **时间版本化快照评估**:`coverage_assessments.canonical_manifest_sha256` = 排序后 canonical JSON 列的 SHA-256;`load_asof` 先按 as_of 选行→推导 manifest→仅当存在同 manifest 且 `assessed_at <= as_of_ts` 的评估才准入。**后 revision 不能继承前 revision 的绿评估**。
- SQLite 连接:`PRAGMA foreign_keys=ON; journal_mode=WAL; busy_timeout`(launchd writer + app reader 并发);ingest 走**显式单事务**,失败闭合无可查部分行。

### 3. complete vs reconciled 两态 + 类型隔离

- `complete` = session 形状有效(端点到齐、OHLC 合法、无重复),收盘即可产出。
- `reconciled` = 次周期对独立日频 OHLCV 核对在容差内通过(最早 T+1)。
- `pit_backtest` 准入**强制 reconciled**;只读复盘走独立非-PIT 路径,返回独立 `ReviewBar` 类型——**结构上**不可喂给任何回测/walk-forward 入口。隔离靠类型系统,不靠开发者记得调哪个函数。
- 对账积压闭合:complete 日每周期重试 reconciliation;K 周期仍无 → `reconciliation_stalled` 告警(否则日频源瞬时中断=静默 PIT 覆盖空洞)。

### 4. 历史 PIT 用保守代理

供应商分钟历史 API 给得出 bar、给不出「每根 bar 何时首次可得」。强求 per-bar 厂商证明=该路径永不可满足。改用**文档化偏晚代理**:`available_from_ts = bar_end + 最坏发布延迟`(或 `trade_date+1` 收盘),标 `proxy-PIT` 而非 proven-PIT。偏晚方向抗 look-ahead——顶多低估策略,不会让它读到尚不可得的数据。

### 5. 两个开环陷阱

- **发布延迟漂移**:`publication_delay` 取探针 p95+裕度后,漂移检测必须在**生产路径**做(收盘自报 `retrieved_at - bar_end`,超阈写 `publication_delay_exceeded`)。挂在可选/默认关的 watch 模式 = 收盘 15:05 永不观测盘中滞后 = 开环,delay 变大时执行闸静默放进尚不可发布的 bar。
- **滚动端点窗内追补**:免费端点一次返回近 ~5 天。追补缺失日时,canonical 抽取/revision 分配必须**仅对无 complete 评估的 trade_date 运行**,已 complete 日在 canonical 层就跳过——否则同一响应里的已 complete 日被改写,毁掉 PIT 冻结历史。「不盲覆盖」是意图,外科式限定才是机制。

### 6. 停机恢复语义不同于日频

分钟快照漏跑**不可由 launchd 重触发恢复**(快照已逝,见 [[launchd-no-catchup-after-shutdown]] 记忆)。所以:窗内缺失靠每 run 主动追补;窗外缺失标 `permanent_gap` + 告警(仅历史 proxy 可填);影子验收门除 run 成功率外**必须加数据持久性维度**(零未恢复窗内 gap),否则 19/20「成功」可能藏不可恢复的洞。

### 7. 流程:两轮对抗审的跨视角收敛

CE 全链路 doc-review(PRD)→ ce-plan → doc-review(plan)。plan 轮里 **feasibility 和 adversarial 各自独立命中同一条 P1**(发布延迟开环、追补改写冻结历史)——这种跨视角收敛是最可信的「真洞」信号,远强于单视角发现。doc-review 对 plan(非只对 PRD)有独立价值:PRD 审 WHETHER/scope,plan 审 HOW/机制实现。

## Why This Matters

PIT 安全是整个量化工作台可信度的根。一旦非-PIT 数据漏进回测,owner 会在一个幻象回测上做真金白银决策。把安全属性**编码进存储层(类型 + 不变式 + 评估闸)**而非散文约定,是唯一能扛住「未来某个调用方图省事走错路径」的方式。本模式的每一条都对应一个具体的、被对抗审挖出来的失败路径。

## When to Apply

- 接任何**非-PIT 实时源**(实时行情、快照、滚动窗端点)且数据可能进回测/信号时。
- 需要「同一事实有多个版本 + 必须可审计哪个版本在何时可得」的存储场景。
- 免费/滚动端点(有限历史窗 + 一次返回多天)的前向沉淀。
- 区分「可展示」与「可回测」两类消费且必须防混用时。

## Examples

**反例(日频表语义,给不了 PIT)**:
```
INSERT OR REPLACE INTO stock_quotes ...   -- 覆盖,丢版本与时点
```

**正例(版本化 + 快照认证)**:
```
INSERT INTO canonical_bars (..., revision) VALUES (..., 冲突时取更高 revision)
-- load_asof: 选候选 → 推导 canonical_manifest_sha256 →
--   仅当 ∃ assessment(同 manifest, kind='reconciled', assessed_at <= as_of_ts) 才准入
-- 后 revision 不继承前 revision 的绿评估
```

**类型隔离(防绕过 reconciled 闸)**:
```
复盘面板读取 → ReviewBar(无任何路径进 load_asof 消费方)
pit_backtest 读取 → load_asof(eligibility='pit_backtest_eligible') → 强制 reconciled
```

相关:[[dragon_tiger_integration_retrospective]](PIT 红线原则)、[[lookahead_bias_lessons]](八层偏差 + 运行期 guard)、[[known_bias_gaps]](SQLite WAL/写一次守卫)、[[verify-data-source-before-building]](探针先行)。
