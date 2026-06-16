# KSS 进度日志

> 倒序记录。最新在上。详细技术复盘见 `docs/solutions/`，计划见 `docs/plans/`。

---

## 2026-06-15 — 板块复盘解读层数据源补强（龙虎榜 + 科创两融）

**起因**：对照 `simonlin1212/a-stock-data` 评估 KSS 数据源，决定是否补强/替换。

**结论与决策**
- **不替换 Tushare 回测骨架**（PIT 可复现是 KSS 反 look-ahead 立身之本），只用 a-stock-data 免费源补**实时解读层**。
- 解读层数据**严禁回流回测**（非 PIT，会重新引入幸存者/look-ahead 偏差）。

**已交付（main，PR #8/#9/#10/#11 全合并）**
- **P0 龙虎榜**（PR #8）：东财全市场龙虎榜接入 `sector_commentary`（席位资金动向）+ fallback。`kss/data/dragon_tiger_client.py`。
- **P0 LLM 编数字修复**（PR #9）：dry-run 撞出数据完整性 bug——LLM 把龙虎榜真值（101/59/42/+53.65亿）编成 45/87 亿且每次不同。改为**数字归代码**：`render_*_line` 确定性渲染真值行，喂 LLM 的 payload 只含定性 `bias`、剥光裸数字。含幻觉防护回归测试。
- **P1-a 科创两融**（PR #10）：东财 `RPTA_WEB_RZRQ_GGMX` 按 `(DATE)(KCB=1)` 拉全科创板（597 只/2 页）聚合"科创板杠杆情绪"。`kss/data/margin_client.py`。沿用数字归代码范式。
- **P1-b 北向板块明细**：❌ BLOCKED。验证 gate 实测 a-stock-data 北向只有全市场总量、无板块拆分；且 eastmoney 北向数据 2024-08 起上游断供。直接否决，不写代码。
- **文档**（PR #11）：plan + 复盘归档。

**核心教训（已写入项目记忆 + docs/solutions）**
- **LLM 只做判断，代码做渲染**：任何进投顾消息的金融数字必须代码兜底，LLM 复述会幻觉。同源风险点：紫苏叶分析师覆盖数、ETF 雷达措辞（当前仍是预格式化让 LLM 复述，待改）。
- **外部数据源先验真实响应再写代码**：两次验证 gate 各省一轮无效工作（P1-a 改聚合口径、P1-b 否决）。
- **并发会话用 git worktree 隔离**：本会话与 kronos 会话共享工作树，HEAD 被移动导致提交落错分支；用 worktree 隔离开发，不碰共享 HEAD。

**纪律**：数字归代码、不进回测、东财串行不并发、外部文本过 `sanitize_llm_input`、并发用 worktree。

**待跟进（非阻塞）**
- [ ] PR #10 两融的 live-LLM dry-run 终确认散文措辞（需主工作树 + Hermes .env，`bash scripts/run_sector_review_daily.sh --date 2026-06-12 --dry-run`）。
- [ ] P2 题材归因接入（plan `docs/plans/2026-06-15-002-*` §8 留存；注意与 `ths_hot` 概念口径打架风险）。
