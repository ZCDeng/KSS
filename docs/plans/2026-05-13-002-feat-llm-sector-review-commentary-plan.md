---
title: "feat: 17:30 板块复盘改造为 LLM 驱动的投顾叙事 + 加减仓建议"
status: active
created: 2026-05-13
type: feat
depth: standard
---

## Summary

把 17:30 cron 推送的 5 个机械数字表格，**完全替换**为投资顾问语气的段落式当日复盘文字。新增大盘指数（沪深主板/科创板/创业板）量价摘要、强化科技板块资金走向与"十五五"六大主题轮动叙述，结尾产出一句**可执行的加减仓建议**（建议持仓动作 + 具体板块/概念名）。生成方式走 LLM（复用 Hermes `.env` 里的 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`），数据缺失时优雅降级到结构化要点。

**关键约束**：
- LLM 输出走 Telegram **HTML 模式**（escape 面只剩 `< > &`，比 MarkdownV1 的 `_ * \` [` 安全得多 —— 见 `docs/solutions/telegram_markdown_v1_silent_drop.md`）
- 单次推送 ≤ 4096 字符（LLM prompt 显式约束 + 后置兜底裁剪）
- 不动 `kss/sector/data_fetcher.py / scorer.py / kcb_overlay.py` 的现有逻辑，只在装载结果之上加一层 commentary
- 主题清单走配置文件，用户可调；候选清单在本计划随附（U1）等待用户审核
- LLM 调用失败 → fallback 走结构化文本（保底有内容，不静默失败）

---

## Problem Frame

**现状**（参见会话上下文与今天 17:30 实际推送）：
- `scripts/sector_review.py` → `kss/sector/formatter.py` 输出 5 个机械表格（行业 Top、资金涌入、概念 Top、轮动信号、北向）
- 多源数据缺失（`industry_index`, `northbound`）时直接打 `_数据暂缺_`，整张推送进一步降质
- 干瘪数字没有解读语境，用户原话「**数据并无实际意义**」
- KSS 项目**当前没有任何 LLM 依赖**（grep `anthropic|openai|claude` 无结果）

**目标**：
- 17:30 推送变成一段 800–1500 字的中文投顾复盘，叙事结构：
  1. 当日大盘总览（沪深主板 / 创业板 / 科创板的涨跌幅、成交额、量比）
  2. 热点行业板块今天怎么走的、资金有没有跟
  3. 概念板块轮动（哪些今天发动、哪些今天歇）
  4. **科技板块资金走向**重点段落（"十五五"六大主题各自表现）
  5. **明确加减仓建议**（一句话级别：板块名 + 动作 + 触发理由）
- 数据缺失时降级为"今日大盘 / 行业 / 概念某维度数据未到位，按可用数据快评"，仍可推送

**非目标**（明确不做）：
- 不做实盘下单（仍是信息推送，KSS 整体定位不变）
- 不引入策略级 LLM agent / 工具调用循环（单次 chat completion 调用即可）
- 不改 `kcb_overlay.py` 的反向索引逻辑（科创板池子标注作为 prompt 上下文继续用，不重构）
- 不重写早盘 9:05 选股推送（两条管线物理隔离）

---

## Scope Boundaries

### 本计划包含
- 配置驱动的"十五五"六大主题映射（板块名 → 主题桶）
- LLM 客户端薄包装（OpenAI SDK，可指向 OpenAI / DeepSeek / oneAPI 网关）
- 大盘指数日线数据 fetch（沪深主板 / 创业板 / 科创板 4 个指数）
- Commentary 生成模块（snapshot 数据 → prompt → LLM → HTML 文本）
- `sector_review.py` 替换为新 commentary 输出
- Telegram HTML 模式推送 + 4096 字裁剪兜底
- LLM 失败 fallback：结构化要点（保留指数涨跌幅 + Top 行业/概念名）

### Deferred to Follow-Up Work
- 多轮对话 / 工具调用风格的 agent 化复盘（先 single-shot 验证有效再说）
- 复盘文字 + 早盘选股推送的联动（如：当日推荐持仓的板块归属本日热点？）
- 历史复盘归档可检索（写入 `storage/sector_review/YYYY-MM-DD.md` 留底）

### 非目标（明确不做）
- 复盘里给出具体股票代码买卖建议（仅板块/概念粒度）
- 引入新数据源（北向继续依赖 Tushare `moneyflow_hsgt`，缺失就降级不补救）
- 替换 `formatter.py`（保留代码，但 17:30 推送链路不再调用 —— 周报 / 其他场景仍可用）

---

## Key Technical Decisions

### KD1. LLM 提供商 = OpenAI SDK + Hermes `.env` 多 key
- 复用 `agentos-stack/hermes_agent/.env` 已有的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY`，wrapper 脚本按 `update_data_daily.sh` 现成模式 `grep -E '^OPENAI_API_KEY=' "$HERMES_ENV"` 注入到环境变量
- 优先 OpenAI（`OPENAI_BASE_URL` 若是 oneAPI 网关可顺带支持 Claude / Gemini 路由），fallback DeepSeek（国产，价格 1/10，质量足够中文投顾文字）
- 依赖：仅加 `openai` 一个 pip 包（不直接装 anthropic SDK，避免双 SDK 维护成本）
- 不做 streaming（一次性拿完整文本，简化错误处理）

### KD2. Telegram parse_mode = "HTML"
- LLM 提示词显式要求只输出 `<b>` `<i>` `<u>` `<code>` 四个标签 + 纯文本
- escape 只需处理 `< > &`（数据值注入位置），LLM 自由输出的散文部分由 LLM 保证不含未配对标签 —— prompt 里要求不要在散文中夹任何 `<` `>` 字符
- 与现有 `telegram_bot.send(msg, parse_mode="HTML")` 签名兼容（`kss/notifications/telegram_bot.py:51` 已支持）

### KD3. 主题清单走 YAML 配置，**不**硬编码在 Python
- 文件位置：`storage/themes_15th_5y.yaml`（与 `storage/sector_review_config.json` 同目录）
- 结构：`themes: {半导体: [板块名1, 板块名2], AI: [...], ...}`
- 板块名匹配 Tushare `ths_index_list` / `concept_detail` 返回的中文名，不用代码
- 用户可热改，commentary 模块每次加载

### KD4. 大盘指数 fetch 新增独立函数，不进 `SectorSnapshot`
- `kss/sector/data_fetcher.py` 现有 `fetch_sw_daily / fetch_ths_daily / fetch_moneyflow_hsgt` 函数粒度，**新增** `fetch_market_indices(yyyymmdd)` 返回 dict[str, dict]，键为指数名（"沪深主板"/"创业板"/"科创板"/"北证 50"），值含 `close / pct_chg / volume / amount`
- Tushare API：`index_daily` 取 `000001.SH / 399006.SZ / 000688.SH`（+ 可选 `899050.BJ`）
- 不进 `SectorSnapshot` 是因为 snapshot 当前语义是"行业+概念维度"，指数是另一维度，硬塞会污染 dataclass

### KD5. 失败 fallback 走"结构化文本模板"
- LLM 失败（超时、key 缺、429）→ commentary 模块返回一段简短结构化文本：
  ```
  📊 板块复盘 YYYY-MM-DD（LLM 失败降级）
  指数：沪深 -0.5% / 创业 +1.2% / 科创 +2.1%
  Top 行业：A +5.2%, B +4.1%, C +3.8%
  Top 概念：X +3.5%, Y +2.9%, Z +2.4%
  ⚠️ 自动复盘失败，可手动查看完整数据
  ```
- 仍然走 Telegram，不静默失败；告警通过文末 `⚠️` 字样让用户肉眼识别

### KD6. 不引入 LangChain / agent framework
- 单次 chat completion 用 `openai.OpenAI().chat.completions.create(...)`，~20 行代码
- 复杂度边界明确：当未来要做多轮对话或工具调用时再考虑框架，YAGNI

---

## System-Wide Impact

| 区域 | 影响 |
|---|---|
| `scripts/sector_review.py` | 调用入口从 `format_sector_review_markdown()` 改为 `generate_commentary()`；CLI args 不变 |
| `kss/sector/formatter.py` | 不删除，但 17:30 链路不再调用（保留给周报或其他场景，避免破坏） |
| `kss/sector/data_fetcher.py` | 新增 `fetch_market_indices()` 函数 |
| `kss/notifications/telegram_bot.py` | 无改动（`parse_mode="HTML"` 已支持） |
| `scripts/run_sector_review_daily.sh` | 加载 Hermes `.env` 时把 `OPENAI_*` / `DEEPSEEK_*` 也注入到 env |
| `deploy/launchd/com.zcdeng.kss.sector_review_daily.plist` | 无改动（wrapper 接管 env 注入） |
| 依赖 | 新增 `openai` Python 包，需 `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pip install openai` |
| Cron 时延 | LLM 调用 +3-10s，17:30 总耗时仍 < 30s，无影响 |
| 推送格式 | MarkdownV1 → HTML，订阅者无感 |

---

## High-Level Technical Design

> *以下是为审阅 plan 方向用的示意，不是实现规范。实现者参考即可。*

```
┌─────────────────────────────────────────────────────────────┐
│  scripts/sector_review.py (入口, --date / --dry-run / ...)  │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌────────────────┐
│ data_fetcher │ │  scorer +    │ │ NEW:           │
│ + 大盘指数   │ │  kcb_overlay │ │ themes_loader  │
│ (新增 fn)    │ │ (现有)       │ │ YAML config    │
└──────┬───────┘ └──────┬───────┘ └────────┬───────┘
       │                │                  │
       └────────┬───────┴──────────────────┘
                ▼
       ┌─────────────────────┐
       │ NEW: commentary.py  │
       │  - build_context()  │  ←── 把上面三路数据序列化成 prompt 上下文（紧凑 JSON）
       │  - render_prompt()  │  ←── system + user prompt（投顾人设 + 输出格式 + 主题）
       │  - call_llm()       │  ←── openai SDK, 失败 raise
       │  - fallback_text()  │  ←── 失败兜底
       │  - clip_to_4096()   │  ←── 超长后置裁剪
       └──────────┬──────────┘
                  ▼
       ┌─────────────────────┐
       │ telegram_bot.send   │
       │ (parse_mode="HTML") │
       └─────────────────────┘
```

LLM prompt 骨架（实现时按需调整）：
- **System**: "你是一位资深 A 股投资顾问。请基于下方结构化数据写一段当日复盘。语气专业但口语化，给出明确的加减仓建议。不要使用 Markdown 语法，只能用 HTML 标签 `<b><i><u><code>`。总长 < 1500 中文字。"
- **User**: JSON 化的指数 / 行业 / 概念 / 北向 / KCB 池子 / 主题映射 + 当日日期
- **输出格式约束**: 6 段结构（大盘 → 行业 → 概念 → 科技重点 → 十五五主题 → 加减仓建议）

---

## Implementation Units

### U1. 起草「十五五」六大主题候选清单 → YAML 配置 + 加载器

**Goal**: 把用户私域定义的"十五五六大热点科技"主题落到可调配置；commentary 模块按主题汇总板块表现。

**Requirements**: 用户原话「十五五六大热点科技的资金轮动」+「我先列建议清单等你审核」

**Dependencies**: 无

**Files**:
- `storage/themes_15th_5y.yaml` (新建)
- `kss/sector/themes_loader.py` (新建)
- `tests/sector/test_themes_loader.py` (新建)

**Approach**:
- YAML 结构：`themes: {<主题名>: {industries: [...], concepts: [...]}}`，按 Tushare 中文名匹配
- Loader 函数 `load_themes(path) -> dict[str, ThemeBucket]`，`ThemeBucket` 是 dataclass，含 `industries` / `concepts` 两个 list
- 候选 6 大主题（**等用户审核，可改**）：
  1. **半导体 / 集成电路**（先进制程、设备、材料、国产替代）
  2. **AI / 算力**（GPU、AI 服务器、液冷、IDC、CPO、光模块）
  3. **新能源 / 储能**（光伏、风电、固态电池、钠电、氢能）
  4. **生物医药 / 创新药械**（创新药、CXO、医疗 AI、合成生物学）
  5. **高端制造 / 工业母机**（机床、机器人、低空经济、商业航天）
  6. **数字经济 / 数据要素**（信创、数据中心、密码安全、卫星互联网、6G）
- 实际板块名匹配后产生候选清单 PR description / commit message 提及，让用户在 PR review 时一并审核
- Loader 容错：YAML 不存在 → 返回空主题（commentary 仍能跑，只是失去主题维度）

**Patterns to follow**:
- 现有 `storage/sector_review_config.json` 是 JSON；本计划用 YAML 是因为主题映射有嵌套数组，YAML 更易读。如需保持一致也可改 JSON —— 实现者按 `kss/sector/__init__.py` 现有约定取舍

**Test scenarios**:
- happy path：完整 YAML 加载 → 6 个主题，每个主题至少 1 industry + 1 concept
- 缺失 YAML 文件 → 返回 `{}`，不抛
- YAML 语法错误 → 抛 `ConfigError`（明确异常，commentary 调用方降级）
- 主题名空字符串 / 板块名为 None → 跳过该项并 log warning，不污染其他主题
- 重复板块名出现在多主题（如"算力"可能既在 AI 又在数字经济）→ 允许，loader 不去重，commentary 决定是否计两次

**Verification**: `pytest tests/sector/test_themes_loader.py` 全绿；YAML 内 6 主题被解析到。

---

### U2. 大盘指数 fetch（沪深主板 / 创业板 / 科创板 / 北证 50）

**Goal**: 在 `data_fetcher.py` 增加一个独立函数，拿到当日 4 个指数的收盘价、涨跌幅、成交额、量比。

**Requirements**: 用户原话「区分沪深、科创和创业板的量价指标」

**Dependencies**: 无

**Files**:
- `kss/sector/data_fetcher.py` (修改：新增 `fetch_market_indices` 函数 + 常量 `MARKET_INDEX_TS_CODES`)
- `tests/sector/test_data_fetcher.py` (修改：补 1 个测试用例)

**Approach**:
- 指数代码：
  - `000001.SH` 上证综指（沪深主板代表）
  - `399001.SZ` 深证成指（深市主板代表）
  - `399006.SZ` 创业板指
  - `000688.SH` 科创 50
- Tushare API: `pro.index_daily(ts_code=..., trade_date=yyyymmdd)`，返回 close / pct_chg / vol / amount
- 量比计算：当日 amount / 过去 5 个交易日均 amount
- 返回 dict[str, dict]：键为指数中文名（"上证主板"/"深证主板"/"创业板"/"科创板"），值为 `{close, pct_chg, amount, volume_ratio}`
- 数据缺失（节假日 / API 失败）→ 函数返回 `{}` + log warning，commentary 调用方降级（与现有 `fetch_sw_daily` 缺失返回 `[]` 的模式对齐）

**Patterns to follow**:
- 现有 `kss/sector/data_fetcher.py:fetch_sw_daily` 的容错与 log 风格
- TushareClient 单例从 `kss.data.tushare_client` import（与 `paper_trade_log_mv` 同源）

**Test scenarios**:
- happy path：mock TushareClient.index_daily 返回 4 个指数数据 → 函数返回 4 个 key
- 部分指数缺失（如科创 50 节假日没数据）→ 该 key 缺失，其他 key 完整，不抛
- 全部缺失 → 返回 `{}`
- 量比计算：mock 过去 5 日均 amount = 100，今日 amount = 150 → volume_ratio = 1.5

**Verification**: 跑 `python -c "from kss.sector.data_fetcher import fetch_market_indices; print(fetch_market_indices('20260512'))"` 真实拉数能拿到 4 个指数，pct_chg / amount 非 None。

---

### U3. LLM 客户端薄包装

**Goal**: 一个最小 OpenAI SDK 包装，从环境变量读 key / base_url / model，单次 chat completion 调用。

**Requirements**: 用户决策"LLM 生成（推荐）"

**Dependencies**: 无（不阻塞 U1/U2）

**Files**:
- `kss/llm/__init__.py` (新建)
- `kss/llm/openai_client.py` (新建)
- `tests/llm/test_openai_client.py` (新建)

**Approach**:
- 装依赖：`/opt/homebrew/opt/python@3.11/bin/python3.11 -m pip install openai`（≥ 1.0，已 v1 SDK）
- 类 `LLMClient(model: str, timeout: float, max_retries: int)`：
  - 从环境读 `OPENAI_API_KEY` / `OPENAI_BASE_URL`；缺失 → fallback `DEEPSEEK_API_KEY` + `https://api.deepseek.com/v1`
  - 一个方法 `complete(system: str, user: str) -> str`，内部 `chat.completions.create(model=self.model, messages=[...], temperature=0.6)`
  - 重试：指数退避 max 3 次，超时 default 30s
  - 失败 → 抛 `LLMUnavailable(str)` 自定义异常，调用方捕获走 fallback
- 不做 streaming，不做 function calling
- model 名走环境变量 `KSS_LLM_MODEL`，default `gpt-4o-mini`（便宜稳定）

**Patterns to follow**:
- `kss/data/tushare_client.py` 的"单例 + 指数退避重试"模式可参考
- 异常风格对齐 `kss/sector/data_fetcher.py`（自定义 exception class，不暴露 SDK 异常）

**Test scenarios**:
- happy path：mock openai 返回 message.content → 函数返回 str
- 401 unauthorized → 抛 `LLMUnavailable`，不重试
- 429 / 5xx → 重试 3 次后抛
- 超时 → 抛 `LLMUnavailable`
- 环境变量两个都缺 → 实例化时立即抛 `LLMUnavailable("no api key configured")`
- 切换 DeepSeek 路径：`OPENAI_API_KEY` 未设但 `DEEPSEEK_API_KEY` 在 → 客户端正确指向 `api.deepseek.com`

**Verification**: 单元测试全绿；本地手动 `python -c "from kss.llm.openai_client import LLMClient; print(LLMClient().complete('say hi','hi'))"`，从 Hermes `.env` 注入 key 后能拿到响应。

---

### U4. Commentary 生成模块（数据 → prompt → LLM → HTML 文本）

**Goal**: 把 SectorSnapshot + 大盘指数 + 主题映射打包成 prompt，调 LLM，拿到 HTML 文本，做长度兜底。

**Requirements**: 用户原话「投资顾问语气、段落式叙述」+「沪深/科创/创业 量价」+「热点板块和概念板块轮动」+「科技板块资金走向」+「十五五六大热点科技」+「加减仓建议」

**Dependencies**: U1, U2, U3

**Files**:
- `kss/sector/commentary.py` (新建)
- `tests/sector/test_commentary.py` (新建)

**Approach**:
- 主入口 `generate_commentary(date_yyyymmdd: str, snapshot: SectorSnapshot, indices: dict, themes: dict) -> str`
- 6 个内部步骤：
  1. `build_context(snapshot, indices, themes)` —— 数据序列化成紧凑 dict（控制 token 数；行业取 top 15、概念取 top 15，避免 prompt 爆炸）
  2. `compute_theme_metrics(snapshot, themes)` —— 按主题 bucket 聚合：每个主题的成份板块今日平均涨幅、累计资金净流入、轮动评分均值，作为 prompt 单独段落
  3. `render_prompt(context, themes_metrics)` —— system + user 字符串
  4. `call_llm(prompts)` —— 走 U3 客户端，失败 raise
  5. `fallback_text(snapshot, indices)` —— LLM 失败 / 数据全缺时的兜底
  6. `clip_to_4096(text)` —— UTF-8 字符计数，超 4000（留 96 字给推送 metadata） → 在最近的 `<br>` 或句号处截断 + 加 `... (已截断)`
- 数据缺失策略：
  - 大盘指数缺 → prompt 里说 "今日指数数据未到位"
  - 行业数据缺 → 跳过行业段落
  - 概念数据缺 → 跳过概念段落
  - **同时**：在 prompt 中明确告诉 LLM 哪些维度缺失，让它在文中诚实标注而不是编造
- prompt system 部分约束（关键）：
  - 角色：A 股投资顾问，关注中线（5-20 个交易日）
  - 风险口径：建议谨慎，不做绝对承诺
  - 输出格式：HTML 标签限 `<b><i><u><code>`；分段用空行；不要 markdown
  - 长度：1000-1500 中文字
  - 必须包含的段落（6 段）：大盘 / 行业 / 概念 / 科技重点 / 十五五 / 加减仓建议
  - 加减仓建议必须给出具体板块或概念名 + 动作（加仓 / 减仓 / 观望 / 持有），不能是泛泛之词

**Patterns to follow**:
- 现有 `formatter.py` 的"snapshot → string"映射逻辑可参考数据访问方式
- KcbOverlay 用法（从 `formatter.py` 抄）：每个 top 板块标注 `⭐N` 代表科创池子持仓数 —— 这些信息在 prompt context 中保留

**Test scenarios**:
- happy path：mock snapshot 完整 + indices 完整 + themes 完整 + LLM 返回 1200 字 HTML → commentary 返回该文本
- LLM 失败 → 返回 fallback 文本，含日期 + 指数 + Top 行业/概念名 + ⚠️ 标记
- 大盘指数缺 → prompt context 中 indices=`{}`；mock LLM 在文中标注 "指数数据未到位"（验证 prompt 真的传递缺失信息）
- 推送超 4096 字 → 兜底裁剪，最后含 `(已截断)` 提示
- 主题映射为空 → prompt 不含主题段落，LLM 不写"十五五"段（验证 graceful degradation）
- LLM 返回 markdown 字符（如 `**bold**`）→ 后置 sanitizer 去掉 `**`/`__`（或在 prompt 强约束 + 一个 assertion 检查输出，超过阈值时降级 fallback）

**Verification**: 跑 `python scripts/sector_review.py --date 2026-05-12 --dry-run`，输出符合 6 段结构 + HTML 标签合法 + 长度 < 4096；网络断开（手动 `unset OPENAI_API_KEY`）→ 输出 fallback 文本。

---

### U5. 接入 `sector_review.py`，切换 17:30 推送链路

**Goal**: 让 17:30 cron 推送的内容从 `formatter.format_sector_review_markdown(...)` 切到 `commentary.generate_commentary(...)`。

**Requirements**: 用户原话「完全替换：只保留文字复盘」

**Dependencies**: U1, U2, U3, U4

**Files**:
- `scripts/sector_review.py` (修改 `run_review` 函数)
- `scripts/run_sector_review_daily.sh` (修改：加载 Hermes `.env` 时注入 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DEEPSEEK_API_KEY` / `KSS_LLM_MODEL`)
- `tests/sector/test_sector_review_script.py` (新建或修改)

**Approach**:
- `run_review()` 内部：
  - 装载 snapshot（已有）→ 装载 indices（U2 新增）→ 装载 themes（U1 新增）→ 调 `generate_commentary` → `send_to_channels(text, parse_mode="HTML")`
- 删除 `format_sector_review_markdown` 调用；`formatter.py` 文件保留（其他场景潜在使用，且周报 `weekly_summary.py` 不在本计划范围）
- wrapper `.sh` 注入 env 时复用现有 `grep -E '^X='` 安全加载模式（避开 `.env` 不规则行 source 错误，见 `scripts/run_paper_trade_daily.sh` head）
- `send_to_channels` 已有，但要确认 `parse_mode` 参数能传通到 `TelegramBot.send` —— 如果没传通需要小改

**Patterns to follow**:
- `scripts/run_update_data_daily.sh` 已示范如何从 Hermes `.env` 安全加载 token
- `scripts/sector_review.py:93` 的 `run_review` 现有签名，尽量保持 CLI 不变

**Test scenarios**:
- happy path：`python scripts/sector_review.py --date 2026-05-12 --dry-run` 输出 commentary 文本（不再是 5 表格）
- LLM 失败 → 仍 print fallback 文本，exit 0
- `--channel telegram` 真实发一条到 dev chat（手动验证）—— 验证 HTML escape 不踩 V1 静默丢失坑
- 数据全缺（如周末 cron）→ fallback 文本 + ⚠️ 标记，仍发送
- wrapper 跑：`bash scripts/run_sector_review_daily.sh --date 2026-05-12 --dry-run`，env 注入成功 + 输出符合预期

**Verification**: 手动跑当日真实推送（2026-05-13），到 Telegram 看效果；`launchctl kickstart -k gui/$UID/com.zcdeng.kss.sector_review_daily` 触发 launchd 路径也能跑通。

---

### U6. 部署文档 + LLM 依赖说明 + 主题清单审核

**Goal**: 让未来重装机 / 其他贡献者能照单复现；同时让本次 6 大主题候选清单成为可审核的 doc 而不是埋在 PR description。

**Requirements**: 用户私域决策"我先列建议清单等你审核"

**Dependencies**: U1, U5

**Files**:
- `docs/solutions/sector_review_deployment.md` (修改：补 LLM 部分 —— pip 安装、env key 来源、降级行为)
- `storage/themes_15th_5y.yaml` (U1 已创建，此处补完整候选 + 注释)

**Approach**:
- 在 `sector_review_deployment.md` 加 "LLM commentary" 章节：
  - 依赖安装：`pip install openai`
  - env key 链路：Hermes `.env` → wrapper grep 注入 → Python 读 os.environ
  - 失败行为：fallback 文本，不静默
  - 模型切换：`KSS_LLM_MODEL=gpt-4o-mini` / `deepseek-chat`
- YAML 文件首行写注释："候选清单 v0，待 review；按 Tushare 概念名匹配，调试方法：`python -c \"from kss.sector.themes_loader import load_themes; print(load_themes())\"`"

**Test scenarios**: none —— pure docs / config

**Verification**: doc 在 GitHub 渲染正常，YAML `plutil` / `yamllint` 通过。

---

## Risk Analysis & Mitigation

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM API 不稳定（429 / 超时） | 中 | 17:30 推送降级到 fallback 文本 | U3 重试 + U4 fallback；用户收到带 ⚠️ 的简版仍有价值 |
| LLM 输出含 Markdown 字符导致 HTML 解析失败 | 中 | Telegram 拒收 → 推送丢失 | U4 post-sanitizer 去 `**`/`__`/未配对 `<>`；prompt 强约束 + 单元测试覆盖 |
| LLM 给出激进具体股票建议 | 低-中 | 合规风险 / 错误信号 | U4 prompt 限定只到板块/概念粒度；system 角色强调"建议谨慎，不做绝对承诺" |
| 主题候选清单与 Tushare 板块名对不上 | 高 | 主题段落空 → LLM 说"无数据" | U1 容错；用户审核 YAML 时按 `storage/stock_names.csv` 已知概念名校验 |
| 单次 LLM 调用成本失控 | 极低 | $$$ | 每日 1 次调用、≤ 4000 token；按 gpt-4o-mini ≤ $0.001/天，DeepSeek ≤ $0.0001/天 |
| 超 4096 字截断截掉「加减仓建议」末尾段 | 中 | 用户看不到核心结论 | U4 prompt 把建议放第二段而非最后一段；裁剪算法按段落优先丢弃中间段、保留首尾 |
| LLM 出现"幻觉"（编造未发生的板块行情） | 中 | 误导 | prompt 强约束"只能基于下方 JSON 数据"，给出的数字必须复述 JSON 里的；U4 单测加 1 个 case：故意提供"AI 板块跌 5%"，验证 LLM 不写"AI 板块涨" |

---

## Verification Strategy

- 单元测试：U1-U4 各自单元测；目标行覆盖率 ≥ 80%
- 集成测试：U5 的 `test_sector_review_script.py` mock LLM 客户端跑通端到端
- 手动验证（必须）：
  1. 真实跑 `python scripts/sector_review.py --date 2026-05-12` 看 console 输出
  2. 真实推一条到 Telegram dev chat，肉眼审 6 段结构 + 加减仓建议是否合规
  3. 故意 `unset OPENAI_API_KEY DEEPSEEK_API_KEY` 跑一次，验证 fallback
  4. launchd 接管：`launchctl kickstart -k gui/$UID/com.zcdeng.kss.sector_review_daily` 完整跑通
- 部署后观察：连续 3 个交易日 17:30 推送质量人工 review，调 prompt 参数

---

## Out-of-Plan Notes (Deferred to Implementation)

- LLM model 具体选 `gpt-4o-mini` 还是 `deepseek-chat`：实现时按 Hermes `OPENAI_BASE_URL` 实际指向决定（若是 oneAPI 网关可走 Claude，若是直连 OpenAI 则 gpt-4o-mini）
- prompt 文案细节（语气、用词、结构示例）：实现时迭代 3-5 次手调，不在 plan 里固化
- 主题候选清单的具体板块名映射：实现时按 Tushare `concept_detail` 当日返回的中文名做最终匹配，YAML 里的候选名作为起点
- 是否归档历史复盘到 `storage/sector_review/YYYY-MM-DD.md`：本计划 Deferred；先跑稳推送链路

---

## Origin

Solo invocation（无 upstream brainstorm 文档）。
Phase 0.7 用户决策：LLM 生成 + 我先出主题候选清单 + 完全替换旧表格。
今天 17:30 cron 实际推送内容验证了原 5 表格输出的局限性。
