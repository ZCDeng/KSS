---
title: 收盘后板块复盘部署指南
tags: [deployment, sector-review, cron, monitoring, tushare, ths-hot]
problem_type: operations
module: scripts/sector_review
created: 2026-05-13
updated: 2026-05-14
---

# 收盘后板块复盘部署指南

> 每个交易日 17:30 收盘后，自动拉取全市场行业 / 概念资金流 + 大盘指数 + 北向，
> 经 LLM 生成投资顾问语气的复盘短文（6 段结构含加减仓建议），推送到 Telegram.
> 与早盘 9:05 选股推送形成「盘前选股 + 盘后复盘」闭环.
>
> LLM 失败时自动降级为结构化纯文本，不静默失败.

## TL;DR

```bash
# 1. 配置文件
cat storage/themes_15th_5y.yaml        # 十五五六大主题候选清单（可热改）
cat storage/sector_review_config.json  # 评分权重（v2 仅 scorer 保留，commentary 不用）

# 2. 确认 LLM 凭据（wrapper 自动从 Hermes .env 注入）
grep -E "OPENAI_API_KEY|DEEPSEEK_API_KEY" /Users/zcdeng/projects/agentos-stack/hermes_agent/.env

# 3. 部署（launchd，每个交易日 17:30 跑）
launchctl list | grep kss.sector_review

# 4. 手动测试
bash scripts/run_sector_review_daily.sh --dry-run                           # 仅 print
bash scripts/run_sector_review_daily.sh --date 2026-05-12 --channel telegram  # 真实推送
```

## 1. 数据流与依赖

```
17:30 launchd
  ↓
run_sector_review_daily.sh        # .env 加载 (Telegram + Tushare + LLM keys)
  ↓
scripts/sector_review.py
  ↓
数据装载
  ├── Tushare API (kss.data.tushare_client)
  │   ├── moneyflow_ind_dc       # 东财行业资金流
  │   ├── moneyflow_cnt_ths      # 同花顺概念资金流
  │   ├── sw_daily               # 申万指数日线
  │   ├── moneyflow_hsgt         # 北向汇总
  │   └── index_daily            # 大盘指数（沪深/创业/科创）
  ├── 同花顺热点 (kss.data.ths_client)  # 当日强势股 + 人工题材归因 reason 标签
  ├── kss.sector.kcb_overlay     # 科创板池子持仓数
  └── kss.sector.themes          # 十五五主题映射 (YAML)
  ↓
LLM Commentary (kss.sector.commentary)
  ├── build_context              # 行业/概念/指数 序列化
  ├── compute_theme_metrics      # 六大主题聚合
  ├── render_prompt              # system + user prompt
  └── call_llm (kss.llm.LLMClient)  # OpenAI SDK
  ↓
HTML sanitize + clip_to_max_len
  ↓
kss.notifications.manager.send_to_channels(parse_mode="HTML")
  ↓
Telegram bot（自建 server，与 paper_trade 共用 .env 凭据）
```

## 2. 配置文件

### 2.1 主题映射

`storage/themes_15th_5y.yaml`：六大科技主题 → 行业/概念名候选清单.

```yaml
themes:
  半导体:
    industries: [半导体]
    concepts: [半导体概念, 集成电路概念, 国产芯片, 存储芯片]
  AI算力:
    industries: [软件开发]
    concepts: [算力概念, AI服务器, 数据中心, 东数西算]
  ...
```

- 名字按 Tushare `moneyflow_ind_dc`（东财行业）和 `moneyflow_cnt_ths`（同花顺概念）匹配
- 不存在不报错，loader 容错跳过
- 用户可热改，commentary 每次加载

### 2.2 评分权重（遗留，scorer 仍可用）

`storage/sector_review_config.json`：formatter / scorer 子系统的权重配置.
commentary 链路不依赖它，但保留兼容.

### 2.3 同花顺热点题材归因（无需配置）

`kss/data/ths_client.py` 调 `zx.10jqka.com.cn/event/api/getharden`（无鉴权 HTTP）
抓当日 ~125 只强势股，每只附带编辑部人工标注的 `reason` 题材标签
（形如 `"算力租赁+Token工厂+AI政务"`）.

commentary 在 `build_context` 阶段做两层派生：

- **`hot_reason_tags`**：把全部 `reason` 按 `+/、·` 切分计数 →
  当日高频题材关键词 Top-10，喂给 LLM 写「概念轮动」段时作为「今天为什么涨」的因果.
- **`hot_kcb_stocks`**：与 KCB 活跃池（51 只科创板）做代码交集 →
  「我们关注的票今天上了强势榜」信号，仅供 LLM 推理用，
  prompt 明确禁止输出股票代码 / 简称.

特性：

- 无 API Key、无 cookie / Referer 校验，仅 UA 即可
- 调用时点：盘后 **15:30 起 reason 字段才完整**（17:30 cron 时序天然满足）
- 非交易日返回空 → 数据层契约视为 None，进入 `SectorSnapshot.missing`
- 失败不影响其他段叙述（与 Tushare 任一字段失败的降级路径一致）

## 3. LLM 部署

### 3.1 凭据来源

wrapper 脚本 `run_sector_review_daily.sh` 从 **两处** 加载环境变量：

| 来源 | 变量 | 说明 |
|------|------|------|
| KSS `.env` | `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` `TUSHARE_TOKEN` | 推送 + 数据 |
| Hermes `.env` | `OPENAI_API_KEY` `OPENAI_BASE_URL` `DEEPSEEK_API_KEY` `KSS_LLM_MODEL` | LLM 调用 |

Hermes `.env` 路径：
`/Users/zcdeng/projects/agentos-stack/hermes_agent/.env`

### 3.2 模型选择优先级

1. `KSS_LLM_MODEL` 环境变量（如 `gpt-4o` / `deepseek-chat`）
2. 无环境变量时：
   - `OPENAI_API_KEY` 存在 → `gpt-4o-mini`（OpenAI 默认）
   - 仅 `DEEPSEEK_API_KEY` → `deepseek-chat`（DeepSeek 默认）
3. `OPENAI_BASE_URL` 可指向 oneAPI 网关（支持 Claude / Gemini 路由）

### 3.3 成本估算

- 每日 1 次调用
- prompt ~3000 tokens（含 top 15 行业 + top 15 概念 + 4 个指数 + 主题聚合）
- completion ~800-1200 tokens
- gpt-4o-mini：~$0.001/天
- deepseek-chat：~$0.0001/天

## 4. 已知限制

### 4.1 KCB 池行业维度命中率低

**现象**：`moneyflow_ind_dc` 用东财细分行业命名（如「半导体设备」），
而 `stock_names.csv` 的 `industry` 是申万一级（如「半导体」），两套命名空间不一致.

**commentary 处理方式**：prompt 中同时传入两个维度的 KCB 持仓数，
LLM 自由叙述「半导体相关板块」时不精确要求 string-equal 命中.

### 4.2 LLM 幻觉风险

**现象**：LLM 偶尔编造不存在的数据或板块表现.

**缓解**：
- prompt 明确约束「所有数字必须复述 JSON 里的数值」
- `_sanitize_html()` 过滤未授权标签，防止 HTML 解析失败
- fallback 机制：LLM 失败 → 结构化文本兜底，不静默丢失

### 4.3 Telegram HTML 模式

从 MarkdownV1 切换为 HTML，escape 面只剩 `&lt; &gt; &amp;`
（参考 `docs/solutions/telegram_markdown_v1_silent_drop.md` 的踩坑记录）.
_fallback 和 commentary 输出都经过 `_sanitize_html()` 处理._

### 4.4 同花顺端点单点依赖

`zx.10jqka.com.cn` 是无鉴权第三方端点，无 SLA 保证；若上游下线 / 改字段：

- `ths_client.fetch_ths_hot` 已做格式漂移防御（缺 `reason` 字段 → None）
- 进入 `missing` 列表后，commentary prompt 不会编造题材归因
- 不影响 Tushare / 北向 / 大盘指数 等其他段叙述

注：`errocode` 字段拼写非标准（不是 `errorcode`），来自上游原样.
排查脚本里 grep 时不要纠错.

## 5. launchd 部署

launchd agent：`com.zcdeng.kss.sector_review_daily`

```bash
# 查看
launchctl list | grep kss.sector_review

# 手动触发
launchctl kickstart -k gui/$UID/com.zcdeng.kss.sector_review_daily
```

日志：`storage/logs/cron/sector_review_daily.log`

## 6. 故障排查

| 现象 | 排查路径 |
|------|---------|
| 整份报告「LLM 复盘失败」| `storage/logs/cron/sector_review_daily.log` 看 OPENAI/DEEPSEEK key 是否注入成功；Hermes `.env` 是否存在 |
| Telegram 没收到 | `send_to_channels` 返回 `{telegram: False}` → 看 telegram bot 容器状态 |
| 仅个别板块缺数据 | 报告底部「⚠️ 缺失数据源」列出具体字段；单 API 失败不影响其他段 |
| 「概念轮动」段没有题材关键词 | log 搜 `[ths_hot]`：业务错（`errocode != 0`）/ 网络错 / 非交易日空响应 → `ths_hot` 进入 `missing` |
| LLM 输出 markdown 字符 | `_sanitize_html` 会自动转换，但如遇到漏网之鱼可手动调 prompt 约束 |
| 报告被截断 | LLM 返回超长（>4000 字）→ 兜底裁剪；调低 prompt 里的字数约束 |

## 7. 验证步骤

新部署后第一天观察：

1. `launchctl list | grep kss.sector_review` 确认 agent 已加载
2. 等 17:30 后看 `storage/logs/cron/sector_review_daily.log`
3. Telegram 是否收到 HTML 格式复盘（6 段结构，含加减仓建议）
4. 跑一次手动回放：`bash scripts/run_sector_review_daily.sh --date 上一交易日 --dry-run`
5. 故意 `unset OPENAI_API_KEY DEEPSEEK_API_KEY` 跑一次，验证 fallback
6. 测试套件：`pytest kss/tests/test_sector_*.py kss/tests/test_llm_*.py -v`

## 8. 相关文档

- `docs/plans/2026-05-13-002-feat-llm-sector-review-commentary-plan.md` —— LLM commentary 实施计划
- `docs/solutions/paper_trade_deployment.md` —— 早盘选股部署
- `docs/solutions/telegram_deployment.md` —— Telegram bot 自建 server
- `docs/solutions/telegram_markdown_v1_silent_drop.md` —— MarkdownV1 静默丢失踩坑记录
