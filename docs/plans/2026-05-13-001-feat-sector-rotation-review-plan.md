---
title: "feat: 收盘后板块热度 + 资金轮动复盘 (含科创板池子持仓标注)"
status: active
created: 2026-05-13
type: feat
depth: standard
---

## Summary

每个交易日收盘后（16:00 cron），拉取全市场行业 / 概念板块的资金流和价格数据，计算「板块热度」「资金持续性」「轮动信号」三类启发式指标，输出 Top 强势板块 + Top 资金涌入板块 + KCB 池子相关板块的 Telegram 推送复盘，作为现有 `paper_trade_log_mv` 选股管线的宏观背景增强。

**关键约束**：
- 启发式排序，**不上模型**（与 KSS 项目 known_bias_gaps 中"小样本+短窗口慎用 ML"的纪律一致）
- 全市场扫描，但在输出中**标注**哪些板块在科创板活跃池子（`storage/stock_names.csv`）里有持仓
- 复用现有 `TushareClient` + 通知通道架构，新增模块独立可测，不侵入 `paper_trade_log_mv.py`
- Cron 失败不影响早盘 9:05 选股推送（两条管线物理隔离）

---

## Problem Frame

**现状**：
- KSS 日内有 9:05 选股推送（`run_paper_trade_daily.sh` → `paper_trade_log_mv.py`）和周五 17:00 周报
- 选股表格已含名称 / 申万行业 / 概念板块（PR #1 / #2 已上线）
- 但**没有**收盘后的板块层面复盘 —— 看不到当日哪些板块最热、资金往哪里走、明天可能轮动到哪里
- 当前个股因子（`log_mv` 反向）是纯横截面，对板块轮动盲

**目标**：
- 在每个交易日 16:00 收盘后产出一份板块复盘，回答三个问题：
  1. 今天**哪些板块最热**（涨幅 + 成交额 + 换手率）？
  2. 今天**资金在往哪些板块流入**（主力净流入 + 北向 + 多日持续性）？
  3. **明天该关注哪些板块**（轮动信号：热度排名上升 + 资金涌入 + 量价配合）？
- 每个板块标注「KCB 池子有 N 只持仓」，把板块复盘和选股管线挂钩

**非目标**（明确不做）：
- 不做 ML 板块预测模型（启发式排序足够）
- 不做实盘交易决策（只做信息推送）
- 不重写 `paper_trade_log_mv.py`（独立脚本，cron 独立）
- 不替换早盘 9:05 选股推送（复盘是补充，不是替代）

---

## Scope Boundaries

### In-Scope
- 新增数据层方法：`TushareClient.fetch_moneyflow_ind_dc` / `fetch_moneyflow_cnt_ths` / `fetch_sw_daily` / `fetch_moneyflow_hsgt`
- 新增模块 `kss/sector/`：板块数据加载、热度评分、资金持续性、轮动信号、KCB 池子叠加
- 新增脚本 `scripts/sector_review.py`（命令行入口）+ `scripts/run_sector_review_daily.sh`（cron 包装）
- 复用 `kss.notifications` 现有 console / telegram 通道
- 新增 cron 条目：每个交易日 16:00 运行（`0 16 * * 1-5`）
- 单元测试覆盖：评分函数、KCB 叠加逻辑、Markdown 格式化、Tushare 客户端方法

### Deferred to Follow-Up Work
- 历史复盘归档（每日 JSON 落地到 `storage/sector_review/YYYY-MM-DD.json`）以及周度趋势统计：本期先打通日推，归档以后再做
- 板块轮动信号回测验证（"轮动信号当天发出，第二天该板块涨幅如何"）：先收集数据，等积累 30+ 个交易日再分析
- 概念板块去重 / 别名合并（同花顺与东财概念命名不一致）：本期只用同花顺一家，避免别名问题

### Out-of-Scope（非本系统职责）
- 实盘下单或仓位调整
- 多家券商行业分类的统一映射
- 实时（盘中）板块监控

---

## Key Technical Decisions

### D1. 板块资金流数据源选同花顺 (`moneyflow_cnt_ths` + `moneyflow_ind_dc`)
- **概念**: 用同花顺 `moneyflow_cnt_ths`（与 `concept_detail` 命名体系一致，stock_names.csv 已经是同花顺的概念名）
- **行业**: 用东财 `moneyflow_ind_dc`（覆盖更全，含 86 个细分行业；申万 `sw_daily` 用于价格 / 涨跌幅，因为申万一级是 stock_names.csv 中 industry 列的来源）
- **拒绝**: 不同时拉东财概念 + 同花顺概念，避免概念名空间打架（推迟到后续做别名合并）

### D2. 热度评分用线性加权启发式，权重写在配置文件
- 默认权重：`涨跌幅 0.5 + 主力净流入率 0.3 + 换手率 0.2`
- 配置文件 `storage/sector_review_config.json`，权重 / 阈值 / Top N 都可调
- 拒绝 ML / IC 加权 ——避免引入回测偏差，启发式更易解释

### D3. KCB 池子叠加用 stock_names.csv 现有字段
- "板块在 KCB 池子有持仓" 的判定：
  - 行业匹配：板块名 == `stock_names.csv` 中某只活跃池股票的 `industry` 字段
  - 概念匹配：板块名 ∈ 某只活跃池股票 `concept` 字段（按 ` / ` 切分后）
- 活跃池 = `cs_data_688*.csv` 中存在的代码 ∩ stock_names.csv 中存在的代码（约 51 只，与 paper_trade 一致）
- 拒绝引入新的映射表 —— 复用 PR #1 / #2 已经落地的字段

### D4. 失败容错走"降级，不外抛"
- Tushare 失败 → 跳过该板块，在推送末尾加 `⚠️ N 个数据点缺失` 提示
- Telegram 失败 → console 通道仍然输出，cron 退出非零让系统监控接管
- 与 `paper_trade_log_mv.py` 现有 `_send_notification` 模式一致

### D5. Cron 时间选 17:30（A 股 15:00 收盘 + Tushare pro 盘后数据实时性 buffer）
- A 股 15:00 收盘，盘后资金流 / 板块汇总数据 Tushare pro 通常 16:30+ 准备完毕
- 选 17:30 给 Tushare 充分 buffer，避免 16:00 触发时数据未到位导致整份报告空
- 周五 17:30 也跑，与现有 17:00 周报推送（`run_paper_trade_weekly.sh`）错开 30 分钟，两条消息互不干扰
- 拒绝盘中 / 15:30 / 16:00 触发 —— Tushare pro 板块层面 API 实时性不稳，过早触发命中"无数据"概率高

---

## Patterns to Follow

- **`kss/data/tushare_client.py`** —— 新增 `fetch_*` 方法时复用 `_fetch_with_retry` 装饰器：失败返回 `None`、不抛异常、3 次指数退避
- **`kss/data/industry_mapping.py`** —— 数据层「文件不存在 → 空映射 + warning」的容错模式
- **`scripts/paper_trade_log_mv.py:250-289`** —— `_send_notification` 多通道分发 + cron 友好（永不外抛）
- **`scripts/run_paper_trade_daily.sh`** —— `.env` 加载 + 绝对路径 Python + 时间戳日志的 cron 包装范式
- **`kss/prediction/cross_sectional_forecast.py:format_pool_markdown`** —— Markdown 表格按可选列条件拼装的格式化范式
- **`kss/tests/test_data.py`** —— 数据层测试用 `tempfile.TemporaryDirectory()` + `monkeypatch` 隔离

---

## High-Level Technical Design

*This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
┌─────────────────────────────────────────────────────────────────┐
│  16:00 cron: scripts/run_sector_review_daily.sh                 │
│    → loads .env, calls scripts/sector_review.py                 │
└─────────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  kss/sector/data_fetcher.py                                  │
│    fetch_industry_flow(date)  ─→ moneyflow_ind_dc            │
│    fetch_concept_flow(date)   ─→ moneyflow_cnt_ths           │
│    fetch_industry_price(date) ─→ sw_daily                    │
│    fetch_northbound(date)     ─→ moneyflow_hsgt              │
│   (失败 → None + warning，不抛)                                │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  kss/sector/scorer.py                                        │
│   heat_score      = w1·pct_chg + w2·net_mf_pct + w3·turnover │
│   persistence     = sum_{i=1..N} 1[net_mf_i > 0]             │
│   rotation_signal = rank_change_d1d3 + flow_strength bonus   │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  kss/sector/kcb_overlay.py                                   │
│   reads storage/stock_names.csv (active pool ∩ KCB)          │
│   → returns {sector_name: [ts_codes_in_pool, ...]}           │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  kss/sector/formatter.py                                     │
│   format_review_markdown(scores, overlays) → 复盘 markdown     │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│  Reuse kss.notifications (console + telegram)                │
│  Same _send_notification pattern as paper_trade_log_mv       │
└──────────────────────────────────────────────────────────────┘
```

**消息体结构（草图）**:
```
📊 板块复盘 2026-05-13

🔥 行业 Top 5 强势 (涨幅 + 主力流入)
| 排名 | 板块名 | 涨幅 | 主力净流入率 | 换手率 | 综合分 | KCB 池 |
| 1 | 半导体 | +3.2% | +2.1% | 4.5% | 0.82 | ⭐12 |
| ...

💰 行业 Top 3 资金涌入 (连续 N 日)
| 排名 | 板块名 | 近 3 日累计净流入 | 连涨天数 | KCB 池 |

🎯 概念 Top 5 强势 (同花顺)
| 排名 | 概念名 | 涨幅 | 净流入 | 综合分 | KCB 池 |

🌍 北向资金: 净流入 +XX.X 亿元 / 净流出 -XX.X 亿元

⚠️ 缺失数据点: 0 个 (or N 个 板块名...)
```

---

## Implementation Units

### U1. 扩展 `TushareClient`：新增 4 个板块级 API 方法

**Goal:** 在 `kss/data/tushare_client.py` 中新增 4 个 fetch 方法，封装 Tushare 板块层面的资金流和价格 API，全部走现有 `_fetch_with_retry` 装饰器。

**Requirements:** D1, D4

**Dependencies:** 无

**Files:**
- `kss/data/tushare_client.py` (修改)
- `kss/tests/test_data.py` (修改，新增 4 个测试)

**Approach:**
- 新增方法签名（与现有 `fetch_daily` 风格一致）：
  - `fetch_moneyflow_ind_dc(trade_date: str) -> pd.DataFrame | None` —— 东财行业资金流，单日切片
  - `fetch_moneyflow_cnt_ths(trade_date: str) -> pd.DataFrame | None` —— 同花顺概念板块资金流，单日切片
  - `fetch_sw_daily(trade_date: str, level: str = "L1") -> pd.DataFrame | None` —— 申万行业日线（L1/L2/L3 用 level 参数）
  - `fetch_moneyflow_hsgt(trade_date: str) -> pd.DataFrame | None` —— 沪深港通资金流向（北向南向）
- 全部 lambda 包到 `_fetch_with_retry`，label 含日期方便 log 追踪
- 不做缓存层 ——CacheManager 是按 ts_code 的，板块数据按 trade_date 切片，缓存语义不同，留给 U2 决定

**Patterns to follow:** `kss/data/tushare_client.py:127-171` (`fetch_daily` / `fetch_daily_basic`)

**Test scenarios:**
- `fetch_moneyflow_ind_dc` 返回非空 DataFrame 时含 `ts_code` / `industry` / `pct_change` / `net_amount` / `net_amount_rate` 列
- `fetch_moneyflow_cnt_ths` 返回非空时含概念代码 / 概念名 / 涨跌幅 / 资金净流入字段
- `fetch_sw_daily(level="L1")` 返回申万一级（28 个或最新数量）行业的日线
- `fetch_moneyflow_hsgt` 返回单日北向 / 南向净流入金额
- API 抛异常时 3 次重试后返回 `None`，不外抛
- API 返回空 DataFrame 视作"无数据"，返回 `None`，不重试
- mock Tushare client 用 `monkeypatch` 注入 fake `pro` 实例，断言重试次数 / 退避时间

**Verification:** `pytest kss/tests/test_data.py -v` 全部通过；mock 调用次数符合预期。

---

### U2. 新增 `kss/sector/` 模块基础设施：`data_fetcher.py`

**Goal:** 在新模块 `kss/sector/` 中封装板块数据获取，提供统一接口 `load_sector_snapshot(trade_date)`，返回行业 + 概念 + 北向三张表，失败降级。

**Requirements:** D1, D4

**Dependencies:** U1

**Files:**
- `kss/sector/__init__.py` (新建)
- `kss/sector/data_fetcher.py` (新建)
- `kss/tests/test_sector_data_fetcher.py` (新建)

**Approach:**
- 定义 `SectorSnapshot` 数据类（`@dataclass`），三个字段：`industry: pd.DataFrame | None`、`concept: pd.DataFrame | None`、`northbound: dict | None`
- 主入口 `load_sector_snapshot(trade_date: str, client: TushareClient | None = None) -> SectorSnapshot`：内部调 U1 的 4 个方法（行业资金流 + 行业价格 join 在 `industry` 字段，概念资金流单独，北向汇总成 dict）
- 行业表 join 逻辑：`moneyflow_ind_dc` 按 `name` 列 join `sw_daily`（申万一级行业名匹配东财行业名时连接；命名不一致时优先保留资金流表，价格字段置 NaN 并 warning）
- 单个方法失败 → 该字段为 `None`，不让其他字段失败

**Patterns to follow:** `kss/data/industry_mapping.py` 的「文件 / API 缺失 → warning + 空对象」容错风格

**Test scenarios:**
- 三个 Tushare 方法全部成功 → `SectorSnapshot` 三个字段都有数据
- `moneyflow_ind_dc` 返回 `None` → `industry` 字段为 `None`，`concept` / `northbound` 不受影响
- 申万行业名与东财行业名不一致时 → 价格列为 NaN 但 join 不丢行，warning 记录到 logger
- mock `TushareClient` 子类 / 用 `monkeypatch.setattr` 替换方法

**Verification:** `pytest kss/tests/test_sector_data_fetcher.py -v` 通过；部分失败场景下返回结构稳定。

---

### U3. 板块评分：`kss/sector/scorer.py`

**Goal:** 实现三类启发式指标 —— 热度评分、资金持续性、轮动信号 —— 给定 N 日板块数据，返回打分后的 DataFrame。

**Requirements:** D2

**Dependencies:** U2

**Files:**
- `kss/sector/scorer.py` (新建)
- `kss/tests/test_sector_scorer.py` (新建)
- `storage/sector_review_config.json` (新建默认权重配置)

**Approach:**
- `compute_heat_score(snapshot_today: pd.DataFrame, weights: dict) -> pd.DataFrame`：
  - 输入板块单日 DataFrame（含 pct_change、net_amount_rate、turnover_rate）
  - 各字段先 min-max 归一化到 [0, 1]（避免量纲不同被涨幅主导）
  - 加权求和 → `heat_score` 列；按 score 降序排
- `compute_flow_persistence(history: list[pd.DataFrame], n_days: int = 3) -> pd.DataFrame`：
  - 输入最近 N 日的板块资金流切片
  - 统计每个板块的「连续净流入天数」、「N 日累计净流入率」
  - 返回 DataFrame: `[sector, persist_days, cum_inflow_rate]`
- `compute_rotation_signal(today: pd.DataFrame, prev_days: list[pd.DataFrame]) -> pd.DataFrame`：
  - 「轮动」 = 今日热度排名相比 N 日前显著上升（>= 5 位）且资金净流入率 > 0
  - 返回有轮动信号的板块及上升幅度
- `weights` / `n_days` / 阈值从 `storage/sector_review_config.json` 加载，文件不存在用默认

**Patterns to follow:** `kss/prediction/cross_sectional_forecast.py` 的「输入 panel → 输出打分后 DataFrame」函数式风格

**Test scenarios:**
- 热度评分：构造 3 个板块，涨幅分别 +3% / +1% / -2%，net_amount_rate 全 1%，turnover 全 5% → 评分顺序符合涨幅排名
- 权重全为 0 时 score 全为 0
- 资金持续性：连续 3 天净流入 → `persist_days == 3`；中间一天净流出 → `persist_days == 1`（断了重计）
- 轮动信号：构造一个板块今日排名第 1，3 日前排名第 20 → 触发轮动信号；排名变化 < 阈值时不触发
- 配置文件缺失时用默认权重，logger.info 提示
- 边界：单板块输入 / 空 DataFrame 输入返回空结果，不报错

**Verification:** `pytest kss/tests/test_sector_scorer.py -v` 通过；评分结果可解释。

---

### U4. KCB 池子持仓叠加：`kss/sector/kcb_overlay.py`

**Goal:** 给定板块名列表，返回每个板块在科创板活跃池子里命中的 ts_code 列表（按行业匹配 + 概念匹配两个维度），用于推送时标注「⭐N 只在池」。

**Requirements:** D3

**Dependencies:** 无（独立可测）

**Files:**
- `kss/sector/kcb_overlay.py` (新建)
- `kss/tests/test_sector_kcb_overlay.py` (新建)

**Approach:**
- 主函数 `build_kcb_overlay(stock_names_path: Path, active_pool: list[str] | None = None) -> KcbOverlay`
- `KcbOverlay` 数据类，提供：
  - `industry_to_codes: dict[str, list[str]]` —— 行业名 → 池中 ts_code 列表
  - `concept_to_codes: dict[str, list[str]]` —— 概念名（同花顺切分后）→ 池中 ts_code 列表
  - `count_for_industry(name) -> int` / `count_for_concept(name) -> int` —— 便捷查询
- 活跃池默认从 `cs_data_688*.csv` 文件名 glob 取（与 paper_trade_log_mv.py 一致，复用同样的 regex 避免重蹈 S620 的解析 bug —— 用 `r'cs_data_(688\d+)\.csv'` 精确匹配）
- 概念字段按 ` / ` 切分（与 stock_names.csv 现有格式一致），strip 后建索引
- `industry` / `concept` 字段为空时跳过（PR #2 提到 25/51 有概念数据）

**Patterns to follow:** `kss/data/industry_mapping.py` 的"加载 → 字典查询"扁平结构

**Test scenarios:**
- `stock_names.csv` 含 3 只池股，行业分别为半导体 / 半导体 / 软件服务 → `industry_to_codes["半导体"]` 长度为 2
- `concept` 字段 "集成电路概念 / 转融券标的" → 两个概念都映射到该 ts_code
- `concept` 字段为空字符串 → 不进入 concept 索引，不报错
- `stock_names.csv` 缺少 `concept` 列（PR #1 前的旧格式兼容）→ `concept_to_codes` 为空字典 + warning，不外抛
- `active_pool` 显式传入 → 只索引交集内的代码
- 文件不存在 → 返回空 overlay + warning（数据层容错）
- ts_code regex 误匹配回归测试：传入文件名 `cs_data_688688008.csv`（无意义）应**不**被当成 ts_code = 688008.SH

**Verification:** `pytest kss/tests/test_sector_kcb_overlay.py -v` 通过；KCB 持仓计数与 stock_names.csv 一致。

---

### U5. Markdown 格式化：`kss/sector/formatter.py`

**Goal:** 将 U3 评分结果 + U4 KCB 叠加结果组装成 Telegram / 控制台可读的 Markdown 复盘报告。

**Requirements:** Summary 输出格式

**Dependencies:** U3, U4

**Files:**
- `kss/sector/formatter.py` (新建)
- `kss/tests/test_sector_formatter.py` (新建)

**Approach:**
- 主函数 `format_review_markdown(snapshot, scores, overlay, trade_date, config) -> str`
- 五个 section（顺序固定）：
  1. 标题 + 日期
  2. 🔥 行业 Top 5 强势（含 KCB 持仓数列 ⭐N）
  3. 💰 行业 Top 3 资金涌入（持续性视角）
  4. 🎯 概念 Top 5 强势（同花顺）
  5. 🌍 北向资金净流入（单行汇总）
  6. ⚠️ 缺失数据提示（如有）
- 表头复用现有「条件列拼装」风格 —— Top N 数由 config 决定
- KCB 列：N == 0 时显示 "—"，N > 0 时显示 `⭐N`（鼓励视觉扫描）
- 缺失数据降级：任一 snapshot 字段为 None → 对应 section 显示 "数据暂缺"，不让整份报告失败

**Patterns to follow:** `kss/prediction/cross_sectional_forecast.py:format_pool_markdown` 的列表拼接式表头构造（S620 第 2045 号 bug 教训：用 list join，不要字符串 += `"|"`）

**Test scenarios:**
- 三个 section 数据齐全时返回完整 markdown，包含全部 5 段
- 北向资金为 None 时 → 该 section 显示「数据暂缺」，其他段正常
- KCB 持仓数 == 0 → 显示 "—"；> 0 → 显示 `⭐N`
- 概念为空（Tushare 概念 API 失败）→ 整段 "🎯 概念 Top 5" 显示 "数据暂缺"，但行业 + 北向不受影响
- 表头 pipe 分隔符正确（回归 S620 的表头 bug：所有列之间必须有 `|`）
- 浮点数格式：涨跌幅 / 流入率统一保留 2 位小数 + `%` 后缀

**Verification:** `pytest kss/tests/test_sector_formatter.py -v` 通过；人工查看 sample 输出在 Telegram 客户端中表格不错位。

---

### U6. 命令行入口 + Cron 包装脚本

**Goal:** 提供 `scripts/sector_review.py` 命令行入口（支持 `--date` / `--channel` / `--dry-run`）和 `scripts/run_sector_review_daily.sh` cron 包装。

**Requirements:** D5

**Dependencies:** U2, U3, U4, U5

**Files:**
- `scripts/sector_review.py` (新建)
- `scripts/run_sector_review_daily.sh` (新建)
- `kss/tests/test_sector_review_script.py` (新建)

**Approach:**
- `sector_review.py` 参数（参考 `paper_trade_log_mv.py:298-326`）：
  - `--date YYYY-MM-DD`（默认今日，自动回退到最近交易日）
  - `--channel {console,telegram,all}`（默认 `console`）
  - `--dry-run`（跳过 Telegram，只 print）
  - `--lookback-days N`（资金持续性的回看窗口，默认 3）
- 流程：load_sector_snapshot → 取最近 N 日历史给 scorer → build_kcb_overlay → compute scores → format → send
- 复用 `paper_trade_log_mv.py:_send_notification` 的多通道发送函数 —— **抽取到 `kss/notifications/manager.py` 的 `send_to_channels()` 公共函数**（U6 内附带轻量重构，单元测试覆盖）
- `run_sector_review_daily.sh` 完整复制 `run_paper_trade_daily.sh` 的 `.env` 解析 / 绝对路径 Python / 时间戳日志结构，只换最末 `exec` 行

**Patterns to follow:**
- `scripts/paper_trade_log_mv.py:297-441` (main entry argparse + multi-channel notify)
- `scripts/run_paper_trade_daily.sh` (wrapper 范式)

**Test scenarios:**
- `--dry-run` 不调用 telegram，但 stdout 含完整 markdown
- `--date` 传未来日期 → 退化为「该日期数据暂缺」推送，退出码 0
- `--channel telegram` 时 console 不输出
- Telegram 推送失败 → 退出码非 0（让 cron 系统能感知）；但 console 通道独立成功
- mock Tushare 全部失败 → 推送内容含 "⚠️ 数据全部缺失"，退出码非 0
- `send_to_channels()` 重构不破坏 `paper_trade_log_mv.py` —— 跑 `pytest kss/tests/test_paper_trade_notify.py -v` 全绿

**Verification:** `bash scripts/run_sector_review_daily.sh --dry-run` 端到端跑通；手动指定历史日期能产出完整推送；现有 455 测试全部通过。

---

### U7. Cron 注册 + 端到端验证

**Goal:** 把 17:30 cron 加入用户 crontab，并用一个历史交易日做端到端 dry-run + 真实 Telegram 验证。

**Requirements:** D5

**Dependencies:** U6

**Files:**
- `crontab.txt` (修改 —— 项目根目录的 crontab 备份)
- `docs/solutions/sector_review_deployment.md` (新建，沉淀部署经验)

**Approach:**
- 在用户 `crontab -l` 中追加：
  ```
  # 板块复盘 (KSS) - 每个交易日 17:30 收盘后（Tushare pro 数据延迟 buffer）
  30 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_sector_review_daily.sh >> /tmp/kss_sector_review.log 2>&1
  ```
- `crontab.txt` 同步更新
- 手动跑一个历史日（如上一交易日）真实推送到 Telegram，目视确认表格无错位
- 写 `docs/solutions/sector_review_deployment.md`：cron 时机选择、降级行为、数据源说明、未来扩展点（与 `docs/solutions/paper_trade_deployment.md` 风格对齐）

**Test scenarios:**
- Cron 加进去后 `crontab -l | grep sector_review` 能 hit
- 模拟 cron 环境运行（`env -i bash scripts/run_sector_review_daily.sh`）能正确读 `.env`
- Telegram 真实推送一条历史日复盘，目视确认：表格 pipe 分隔正确 / KCB 列显示 ⭐N / 北向数字合理
- `/tmp/kss_sector_review.log` 含时间戳 + 推送成功标识

**Verification:**
- Cron 注册生效
- 手动 / 模拟 cron 两条路径均跑通
- 一次真实 Telegram 推送可查
- 解决方案文档落入 `docs/solutions/`

---

## System-Wide Impact

| 受影响面 | 影响内容 | 风险 |
|---------|---------|-----|
| Tushare 积分配额 | 新增 4 个 API 每天 1 次调用（板块层面 day-level）| 低 —— Tushare pro 单日 5000 次额度足够 |
| Cron 任务 | 新增 16:00 任务（已有 8:30/9:05/15:30/周五 17:00）| 低 —— 完全独立，失败不影响其他任务 |
| Telegram bot | 新增一条收盘后消息（每个交易日 16:00 一条）| 低 —— 用同一 bot/chat，不超频 |
| `kss/notifications/manager.py` | U6 抽取 `send_to_channels()` 公共函数 | 中 —— 需保证 `paper_trade_log_mv.py` 行为不变（覆盖测试 `test_paper_trade_notify.py`） |
| `storage/` | 新增 `sector_review_config.json` | 低 —— 配置文件，不影响现有数据 |

---

## Risk Analysis & Mitigation

| 风险 | 概率 | 影响 | 缓解 |
|-----|-----|-----|-----|
| Tushare 板块 API 积分不足（部分板块 API 需要 2000+ 积分）| 中 | 高 —— 整份报告无数据 | U2 失败降级；deploy 前先在 Python REPL 试调一次每个 API；不行就降级用 `daily` + 自己 group by 行业聚合 |
| 申万行业名 vs 东财行业名不一致导致 join 失败 | 高 | 中 —— 涨幅列为 NaN | U2 outer join + warning；价格缺失时热度评分自动只用资金流 + 换手率两维度 |
| 同花顺概念命名空间 vs stock_names.csv 概念命名不一致 | 中 | 中 —— KCB 概念叠加全 0 | U4 测试覆盖；提供 `concept` 字段缺失/不匹配时退化为「概念维度无 KCB 标注」 |
| 17:30 数据仍未准备好（Tushare 极端延迟）| 低 | 高 —— 报告空 | 部署后第一周观察 log；必要时再延后到 18:00 |
| 抽取 `send_to_channels()` 时破坏 `paper_trade_log_mv` 通知行为 | 低 | 高 —— 早盘选股推送失败 | 抽取前先跑 `test_paper_trade_notify.py` 留 baseline；抽取后回归全测试 |
| KCB 池子定义漂移（cs_data_688*.csv 文件数增减）| 低 | 低 —— overlay 计数变化 | 与 paper_trade_log_mv.py 共用同一 glob + regex，漂移则同步漂移，不引入新源 |

---

## Open Questions & Deferred Decisions

### Resolved during planning
- ✅ 数据源选 同花顺概念 + 东财行业（D1）
- ✅ 评分权重写在 `storage/sector_review_config.json`（D2，默认值 0.5/0.3/0.2）
- ✅ KCB 叠加复用现有 stock_names.csv（D3）
- ✅ Cron 时机选 17:30（D5，避开 Tushare pro 数据延迟）

### Deferred to implementation
- 申万行业名映射东财行业名的具体策略：U2 实施时根据真实数据决定（可能是名称完全匹配 / 模糊匹配 / 维护一张映射表）。先观察实际命名空间差异，再决定。
- 是否对北向资金做行业切分（`moneyflow_hsgt_board` API 需要更高积分）：U6 实施时若 API 可用则纳入北向板块流向，否则只汇总日级别北向。

### Deferred to follow-up work
- 历史复盘归档 + 周度趋势统计（写到 `storage/sector_review/`）
- 板块轮动信号回测（信号当天发出 → T+1 板块涨幅）
- 概念别名合并（同花顺 vs 东财）

---

## Verification

完成所有 U1-U7 后，整体验收：
1. `pytest kss/tests/ -v` 全部通过（含新增的 5 个测试文件 + 现有 455 个测试无回归）
2. 手动 `bash scripts/run_sector_review_daily.sh --dry-run` 端到端跑通，stdout 有完整 5 段 markdown
3. 手动 `bash scripts/run_sector_review_daily.sh --date 2026-05-12 --channel all` 真实推送一条到 Telegram，目视检查表格正确
4. `crontab -l | grep sector_review` 能 hit 新条目
5. `docs/solutions/sector_review_deployment.md` 落地，含部署步骤 + 已知限制

---

## Origin

无上游 brainstorm 文档；规划直接源于用户请求「增加每个交易日后的复盘,需要关注板块热度和资金的板块轮动预测」 + Phase 0.7 内部草稿的三个 fork 决策（concept 数据源、热度权重、KCB 叠加定义）。
