---
title: Datasette-agent 作为量化数据交互层 + 插件设计模式
date: 2026-05-23
category: architecture-patterns
module: datasette-workbench
problem_type: architecture_pattern
component: tooling
severity: medium
applies_when:
  - 已有 SQLite/CSV/parquet 数据集，想加 LLM 自然语言查询界面
  - 写 datasette-agent plugin 时需要在 display mode / 缓存 / 可视化路线之间选
  - 需要 background agent 跑夜间扫描、异动巡检之类的长任务
  - 多人共享数据但要按角色细粒度授权
tags: [datasette, llm-agent, plugin-design, sqlite, tushare, kss]
---

# Datasette-agent 作为量化数据交互层 + 插件设计模式

## Context

KSS 量化项目里散着 100+ 个 `cs_data_*.csv` + 3 个 parquet（科创板 basic/daily/moneyflow），加上一堆 backtest 脚本和 paper trade 产物。每次想问"688322 这周走势 + 同期北向净流入对比"或"找最近 30 日量价背离的科创板标的"这种问题，都要：

1. 翻 backtest 脚本找数据加载器
2. 写一次性 SQL 或 pandas 查询
3. 画图、看数、得结论
4. 下次有类似问题再来一遍

成本不在单次查询，在每次重建上下文。考虑两条路：

- **A. 自己写 LLM + SQL 接口**：能完全控制，但要造对话状态、工具协议、权限、UI
- **B. 接 Simon Willison 的 datasette-agent**：开箱 3 个 SQL 工具 + 后台 agent + 表 action 菜单，但要适配它的 plugin 协议

选了 B。本文档记录这个集成的关键架构决定和踩坑。

## Guidance

### datasette-agent 三层能力的真实边界

读 datasette-agent 源码（6000 行）才看清的边界——不是 README 说的"3 个工具"那么简单：

| 层 | 真实形态 | 能扩什么 / 不能扩什么 |
|---|---|---|
| 1. 对话工具 `/-/agent` | `list_databases_and_tables` / `describe_table` / `sql_query` 三件套。`sql_query` 有第 4 个隐藏参数 `display` ∈ {model, both, user}，让 LLM 自己声明结果给谁看 | 工具可扩（`register_agent_tools` hook），system_prompt 写死在源码不可扩 |
| 2. 后台 agent | `start_background_agent(goal)` 跑 ≤50 iteration，用 `mark_finished` tool 退出（不靠字符串检测）。`spawn_background_agent` 可以从 chat 里启动后台 agent，完成时往 `agent_pending_notifications` 表插一行，原 conversation 下次 user turn 自动 prefix 注入 | 跨 agent 通信走 SQLite 表，不走 channel/queue。50 iteration 上限改不了不 fork |
| 3. plugin hook | 只导出了 `register_agent_tools` 一个。AgentTool 带 `required_permission` 字段——actor 没权限时**工具从列表中隐去**，LLM 看不到，不是"看到了被拒绝" | 权限是 list-time gating，不是 call-time check；非常省 token 因为模型不会反复尝试调用被拒工具 |

### 5 个核心设计决定

#### 1. display mode：让 LLM 自己选结果给谁看

datasette-agent 的 `sql_query` 工具有这个看起来不起眼但极聪明的参数：

- `model`：rows 只给 LLM，用户看不到——"count rows before join" 这种内部用
- `both`：rows + 渲染表格给用户——"分析最高 PE 的 5 只" 这种 LLM 评论 + 用户看表
- `user`：渲染表格，LLM 只拿到 `columns + row_count`——"show me top 10" 这种纯展示，省 token

**给我们的 plugin tool 也加这个参数**：`tushare_daily` / `tushare_stock_basic` / `tushare_moneyflow_hsgt` 全都接 `display` 参数。实现走同一套：`_apply_display(payload, df, caption, display)` 决定 payload 里塞 `rows` / `_html` / `_rows` 的哪几个。

#### 2. `_html` 侧信道：用户和 LLM 双向带宽隔离

datasette-agent 在 `messages.py:strip_internal_keys` 里有个单一守门员——tool 返回的 JSON 里任何 `_` 开头的 key 在喂给 LLM 前都被删，但保留给前端渲染。这是个**双向带宽控制**机制：

| key | 给谁看 | 用途 |
|---|---|---|
| `_html` | 仅用户 | 渲染富 UI（charts 插件用 Observable Plot；我们用 matplotlib PNG） |
| `_rows` | 仅用户 | display=user 时存全量行，LLM 只看 `row_count` |
| `_edit_sql_url` | 仅用户 | "View SQL query" 链接 |
| 其他 | 双方 | 普通 JSON 字段 |

任何 plugin 想加"只给用户看不喂 LLM"的字段，加 `_` 前缀即可。我们 viz tool 返回的 `<img src="data:image/png;base64,...">` 就走这条侧信道——LLM 拿到 `rows_charted / close_min / close_max / close_last`，用户拿到图。

#### 3. 写回缓存：tushare 调用形成 agent 自扩库循环

最让人意外的设计点。我们的 plugin 不止是 read-only 调外部 API：

```
chat: "688322 这周走势"
  ↓
LLM 先 SQL 查 daily 表 → 没数据
  ↓
LLM 调 tushare_daily(ts_code='688322.SH', start, end)
  ↓
plugin 网络调 tushare → 拿到 df
  ↓
plugin 用 datasette 的 db.execute_write_fn() 把 df INSERT OR REPLACE 到 daily 表
  ↓
返回 LLM + 用户
  ↓
**下次 LLM 再问同一个标的 → SQL 直接命中本地，零外部调用**
```

关键约束：

- 写回**走 datasette 的写通道** `db.execute_write_fn(lambda conn: ...)`，复用它的锁，不绕过
- schema 对齐：tushare `daily` 接口缺 `turnover_rate/pe/pb/total_mv` 5 列，缓存层 `_upsert()` 用 `df.reindex(columns=cols)` 补 NULL
- 日期格式转换：tushare 返 `YYYYMMDD`，库内 daily 表是 ISO `YYYY-MM-DD`，在 `_iso_date()` 里统一转换
- `INSERT OR REPLACE` + UNIQUE 索引：单 SQL 语句，原子，不需事务包

**反模式提醒**：不要直接开 sqlite3 connection 写 db.path——会和 datasette UI 端的读发生锁冲突。

#### 4. server-side PNG vs 客户端 web component

datasette-agent-charts 插件（官方示例）的设计很反直觉：**它不在服务端画图**。返回的 `_html` 是这种：

```html
<script src="/-/static-plugins/datasette-agent-charts/datasette-chart.js"></script>
<datasette-chart>
  <script type="application/json">{...config...}</script>
</datasette-chart>
```

`datasette-chart` 是 web component，在浏览器里**自己**调 datasette 的 JSON API 跑 SQL + 用 Observable Plot 画。好处：每次刷新页面拿最新数据。坏处：要写 JS bundle + 部署 static + 客户端能力局限（不支持 candlestick OHLC 4 列堆叠）。

我们的选择：**对 datasette-agent-charts 能做的（line / barY / dot），直接装它用**——不重发明轮子。**对它做不了的（K 线 candlestick / 资金流四档堆叠）**，server-side matplotlib 渲染 PNG → base64 → `<img src="data:...">`。

```python
matplotlib.use("Agg")  # 无 GUI backend
fig, ax = plt.subplots(...)
# 涨红跌绿（中国 convention，跟 US 反）
# ...
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
b64 = base64.b64encode(buf.getvalue()).decode()
return f'<img src="data:image/png;base64,{b64}" style="max-width:100%">'
```

50 行 Python 顶 200 行 JS bundle + 部署管线。

#### 5. background agent goal 模板：把工作流编码到 plugin

`start_background_agent(goal)` 接受一段自由文本作为 goal，agent 跑 ≤50 iteration 自驱完成。但**让用户每次现写多段 goal 是反人类**——我们 `_scan.py:make_anomaly_goal()` 把"KSS 异动扫描"这个固定工作流编码成模板：

```python
def make_anomaly_goal(criteria, anchor_date=None, lookback_days=5, top_n=10):
    return (
        "你是 KSS 科创板异动扫描 agent。在 sqlite 库 kss 上工作。\n\n"
        f"筛选条件：{criteria}\n"
        f"锚日：{anchor_date or '先 SELECT max(trade_date) 取最新'}\n\n"
        "数据布局（必读）：\n"
        "- kc_daily(ts_code, trade_date, open, high, low, close, ...)\n"
        # ... schema 全文喂给 agent
        "\n执行步骤（每步 sql_query；发现候选立刻 append_to_report）：\n"
        "1. 确定锚日 → 报告写一行\n"
        "2. 跑筛选 SQL 找候选 ts_code\n"
        "3. 对前 top_n，逐个查 30 日量价资金 + 写一段\n"
        "4. 汇总段：按净流入排名 top 5\n"
        "5. mark_finished\n"
    )
```

然后 `spawn_kss_anomaly_scan` agent tool 只暴露 4 个参数（criteria / anchor_date / lookback_days / top_n），用户在 chat 里说"启动一个扫描，净流入>2亿 且 涨幅>5%"就触发，goal 模板内部组装。

对照 datasette-agent 源码的 `explorer.start_explorer()`——它也是同样模式：把"探索这张表"的 5 步流程模板化成一个预配置的 background agent。这是 datasette-agent 推荐的**领域化背景任务**写法。

### MiniMax CN 集成的踩坑

走 datasette-llm（基于 simonw/llm）的 OpenAI-compat 路径：

```yaml
# ~/Library/Application Support/io.datasette.llm/extra-openai-models.yaml
- model_id: minimax-m2
  model_name: MiniMax-M2
  api_base: https://api.minimaxi.com/v1   # 国内版，注意 minimaxi 不是 minimax
  api_key_name: minimax
  can_stream: true
  supports_tools: true
```

key 用 `llm keys set minimax`。metadata.yml 里 `default_model: minimax-m2`。

**踩坑 1**：datasette 1.x 是 alpha 版，`uv pip install` 需要 `--prerelease=allow`。

**踩坑 2**：MiniMax M2 是推理模型，输出会自带 `<think>...</think>` 段。在 system_prompt 里写"不要输出 <think>"**压不住**——这是模型训练层面的特性。datasette-agent SSE 流式把每个 token 单独渲染，`<` 和 `hink>` 被拆开显示，观感稍乱但功能完全正常。如果非要去掉，要 fork datasette-agent 在 `text_chunk` 事件里过滤 `<think>...</think>` 范围。

**踩坑 3**：MiniMax 国内版 base_url 是 `api.minimaxi.com`（多一个 i），国际版才是 `api.minimax.io`。key 前缀 `sk-cp-*` 是国内版标识。

## Why This Matters

1. **每次问"数据怎么样"的成本降一个量级**：从"写 pandas 脚本 → 跑 → 看图 → 转述"变成"在 chat 里中文问 → agent 自动 SQL → 表/图返回"
2. **数据自动扩张**：tushare_* 工具每次调用都写回库，跑 1 个月后 kss.db 自动覆盖你常问的所有标的，外部调用归零
3. **背景 agent = 夜跑工作流**：把"每天扫一遍异动"从 cron + python 脚本变成"chat 一句话启动 + 早上看报告"
4. **plugin 永久反哺**：写一次 tushare/viz/scan tool，所有未来的 chat session 都能用，不需要 prompt engineering

不做这件事的代价：**重复造对话状态、tool 协议、权限、UI**——而 datasette-agent 已经替我们做了 6000 行的工程。

## When to Apply

| 场景 | 用 datasette-agent？ |
|---|---|
| 临时一次性查询 + 已经在 Python 环境里 | 不用，pandas 更快 |
| 同类问题反复出现（"XX 标的最近走势"、"今天哪些股放量"） | **强烈建议** |
| 数据规模 < 几亿行，SQLite 装得下 | 用 |
| 实时流数据 / TB 级 | 别用，SQLite 单文件扛不住 |
| 多人共享数据 + 要审计 + 按角色授权 | 用，datasette 的 actor + permission 体系成熟 |
| LLM 主导的探索（不知道要查什么） | 用，background agent 比人写 prompt 高效 |
| 已知精确指标 + 要历史回溯 | 不用，写 backtest 脚本反而清楚 |

## Examples

### 例 1：让 agent 自己 SQL 查最近交易日

real session output:

```
user: 数据库里有几张表？daily 表最近一个交易日是哪天？请用 SQL 查证。

[LLM] <think>我需要先列表，再查 max(trade_date)...</think>
[Tool] list_databases_and_tables
[Result] {kss: [daily, kc_basic, kc_daily, kc_moneyflow]}
[Tool] sql_query("SELECT max(trade_date) FROM daily")
[Result] {rows: [{"max(trade_date)": "2026-05-22"}]}
[LLM] daily 表最近一个交易日：2026-05-22
```

3 步全自动完成。MiniMax M2.7 即使 system_prompt 压制了 `<think>` 依然输出推理段，但不影响 tool calling 正确性。

### 例 2：plugin 调用形成自扩库循环

```python
async def _daily(datasette, actor, ts_code, start_date, end_date, display="both"):
    df = await _run(lambda: _pro_api().daily(...))  # 网络调用
    cached = await _write_back(datasette, _cache.upsert_daily, df)  # 写回 daily 表
    payload = {"ts_code": ts_code, "cached_rows": cached, ...}
    _apply_display(payload, df, caption, display)  # display mode dispatch
    return json.dumps(payload, ensure_ascii=False)
```

下次同标的的 SQL 查询直接命中本地，零外部调用。

### 例 3：候选目录结构

```
KSS/datasette/
├── build_db.py           # csv + parquet → kss.db
├── metadata.yml          # 表/列中文描述 + system_prompt
├── serve.sh              # 一键启动
└── plugins/datasette_kss_tushare/
    ├── pyproject.toml
    ├── datasette_kss_tushare/
    │   ├── __init__.py   # 6 个 agent tool 注册 + register_actions
    │   ├── _cache.py     # 写回缓存 (纯 sqlite3，可单测)
    │   ├── _scan.py      # background agent goal 模板 + spawner
    │   └── _viz.py       # matplotlib server 端 PNG 渲染
    └── tests/             # 48 tests，全绿
```

## Related Issues

- 历史 commit: `d4ab0f7 feat(datasette): add datasette-agent workbench + tushare plugin`
- 上游 repo: <https://github.com/datasette/datasette-agent>
- 现成图表插件（不要重造）: <https://github.com/datasette/datasette-agent-charts>
- MiniMax CN endpoint 备忘: 国内版 `api.minimaxi.com`，国际版 `api.minimax.io`，key 前缀 `sk-cp-*` 区分
