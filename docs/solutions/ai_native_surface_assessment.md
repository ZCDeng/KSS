---
module: desktop, mcp, llm
tags: [ai-native, mcp, agent-parity, hallucination-defense, prompt-injection]
problem_type: architecture-assessment
date: 2026-06-22
related_plan: docs/plans/2026-06-21-005-feat-kssdeck-standalone-packaging-plan.md
---

# KSSDeck 的 AI-Native 落在哪里——一次诚实盘点

plan 005 把 "AI-native" 列为三支柱之一。打包链收尾后,这份文档记下它到底兑现了多少,
哪些是真落地、哪些还薄、哪些是有意不做。判断基于代码实证,不基于计划承诺。

## 一、真正落地的三条

### 1. Agent parity —— UI 能看的,agent 都能查

`scripts/kss_mcp.py`(U6a/U6b)把桌面 app 的整个数据面经同一个 `kss_app_bridge.dispatch`
暴露成 MCP tool,与 SwiftUI 客户端零逻辑 fork。

- 11 个读 tool:snapshot / 个股 / 板块轮动 / 板块轮动历史 / 主题龙头 / 潜力候选 /
  模拟盘汇总 / 报告 / 趋势月历 / 趋势日历 / cron 列表。
- 写操作 `_LIVE=False` 默认锁死(paper-only):须 `KSS_MCP_LIVE=1`(启动读一次,防 agent
  中途翻转)+ 每调用 `confirm=True` 才放行。
- 注册在 `~/.claude.json`,指向 state-root bootstrap venv 的 python。2026-06-22 从旧
  datasette pilot 重指到本 server,撞名解除。

实证(2026-06-22 重启 Claude Code 后实跑 `mcp__kss-mcp__get_snapshot`):返回 175KB
真实快照,与 app 同源。覆盖 165 只个股、5 条推荐、北向资金 429343.56(20260618)。
指数行情该日为 null —— app 端渲染成「—」,缺数据大声降级不崩(印证 U3)。

这条是 AI-native 与「加了个 AI 按钮」的本质分界:app 不是人用鼠标点的工具,是人和
agent 同一套接口都能驱动的工具。

### 2. LLM 在数据管线里有实职

`kss/llm/openai_client.py` + `kss/sector/commentary.py`:板块复盘文字由 LLM 真写
(`数据 → prompt → LLM → HTML`,OpenAI / DeepSeek 网关,gpt-4o-mini / deepseek-chat,
`KSS_LLM_MODEL` 可换)。失败兜底走结构化纯文本。不是模板拼接,是 LLM 读盘面后产出定性叙事。

### 3. 配套 AI 纪律 —— 两道防线

- **输入侧** `kss/llm/sanitizer.py`:爬来的同花顺数据进 prompt 前过 prompt-injection
  过滤(`ignore previous/prior/all` 这类整段 `[REDACTED]` + warn log)。承认外部源是攻击面。
- **输出侧** U7 `_neutralize_fabricated_percentages` + 代码渲染真值:LLM 只给定性标签,
  所有金融数字由 Python 从 parquet/CSV 代码追加;LLM 正文里冒出的涨跌幅 `%` 检出即中和为
  「相关幅度」+ fail-loud 记日志。73 个 commentary 测试覆盖。

这是「LLM 复述金融数字会幻觉」从经验认知到工程兑现的那一步。

## 二、还薄 / 有意不做

诚实说,以下不算已体现:

- **没有自主 agent loop**:commentary 是 cron 批处理,不是交互式 agent 推理。MCP 让 agent
  能查,但没有 agent 自主决策的回路。
- **eval 回路没上线**:`kss/prediction/ledger.py`(决策账本)在,但 plan 把
  「决策账本 eval 回路」明确 deferred,要 U7 跑稳 2-3 周后才接。
- **U7 取了收敛路线**:计划里的 Pydantic `value:null` + `<kss-number>` 前端槽未建,
  改走 server-side 中和(product-lens 建议最省)。防的是同一个幻觉,机制更轻,但「类型强约束」
  这层没上。

## 三、有意的红线

不提供个性化投资决策。AI 写复盘叙事 + 渲染真值,但不替用户做买卖判断。这是合规边界,
不是缺口。

## 一句话

AI-native 在 KSSDeck = **agent 能像人一样驱动它(MCP)** + **LLM 在管线里有实职 +
数字/注入纪律**,而非 **自主 AI 帮你炒股**。前者已落地并实测,后者既没做、也不打算做。
