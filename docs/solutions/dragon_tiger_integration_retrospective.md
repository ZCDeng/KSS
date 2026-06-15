---
title: 龙虎榜接入板块复盘 —— LLM 编造数字的发现与确定性渲染修复
tags: [sector-review, llm, data-integrity, hallucination, data-source, git-worktree]
problem_type: llm-output-correctness
module: kss/sector
created: 2026-06-15
---

# 龙虎榜接入板块复盘 —— LLM 编造数字的发现与确定性渲染修复

## TL;DR

- 起点是数据源评估：对照 `simonlin1212/a-stock-data`，结论是**不替换 Tushare 回测骨架**（PIT 不可动），只在实时解读层补强。优先级最高的 P0 是东财龙虎榜。
- 接入分两层：龙虎榜进 LLM 复盘 prompt（席位级资金动因）+ 进 fallback 降级表。PR #8 合并。
- **dry-run 实测撞出一个数据完整性 bug**：LLM 把龙虎榜真值 上榜 101 / 净买 59 / 净卖 42 / +53.65 亿 **编造**成 45 只 / 87 亿，每次运行编的数还不一样。即使 prompt 明令「必须复述」「严禁编造」也压不住。
- 根因不是 prompt 不够强，是**把确定性变换误派给了 LLM**。修法：数字由代码 `render_dragon_tiger_line` 确定性渲染，喂给 LLM 的 payload 剥光裸数字、只留定性 `bias`。PR #9。
- 过程中还踩了一个**并发 git 事故**：同目录另一个 kronos 会话移动了共享 HEAD，我的提交落错分支。用 `git worktree` 隔离恢复，零打扰对方。

## 一、数据源评估：补强还是替换

对照仓库 `simonlin1212/a-stock-data`（27 个端点，mootdx/腾讯/东财/同花顺等多源直连，全免费无 Key）。

判断的关键轴是 **PIT（point-in-time）可复现性**：

- KSS 立身之本是反 look-ahead bias，回测靠 Tushare 的时点历史（`daily` / `daily_basic` 按日可复现，779+ 测试压在上面）。
- a-stock-data 是**实时抓取**，给的是"当下快照"，不是"历史某天的时点值"。拿快照回填历史 = 直接引入 look-ahead / 幸存者偏差。

所以结论分层：

| 用途 | 裁决 |
|------|------|
| 回测骨架（daily/daily_basic/moneyflow 历史） | **保留 Tushare，一行不改** |
| 实时解读层（龙虎榜/题材/解禁/两融/北向明细） | a-stock-data **补强**，免费无 Key 是净赚 |
| 红线 | 解读层数据**严禁回流回测**（非 PIT） |

P0 选东财龙虎榜：Tushare 概念资金流只有板块聚合、没有席位级动向，龙虎榜补"今天游资/机构抢哪些"的因果。

## 二、接入实现（PR #8）

镜像既有 `ths_client`（同花顺热点）的无鉴权 HTTP 范式：

- `kss/data/dragon_tiger_client.py`：东财 `RPT_DAILYBILLBOARD_DETAILS`，失败 / 网络错 / 非交易日空响应 / 必需列缺失 → 返回 `None` 不外抛；2 次尝试 + 指数退避；字段重命名为项目蛇形口径。东财字段名用真实响应（20260612，101 行）逐字核对。
- `SectorSnapshot` 加 `dragon_tiger` 字段，`load_sector_snapshot` 紧挨 `ths_hot` 串行 fetch（东财不并发防限流）+ 失败计入 `missing`。
- commentary 聚合 + prompt 注入；formatter + fallback 各加一行。

三条纪律：不进回测红线、东财串行不并发、`reason` 进 LLM payload 前过 `sanitize_llm_input`。

## 三、核心教训：LLM 不可托管渲染金融数字（PR #9）

### 现象

走包装脚本（从 Hermes `.env` 注入 LLM key）跑真实 dry-run，第一版 prompt 让 LLM「复述」龙虎榜数字。结果：

| 字段 | payload 真值 | LLM 第一次 | LLM 第二次 |
|------|------------|-----------|-----------|
| 上榜数 | **101** | 45 | 64 |
| 净买 | **59** | 38 | 32 |
| 净卖 | **42** | 7 | 32 |
| 净买入合计 | **53.65 亿** | 87 亿 | 16.32 亿 |

同一次运行内抓 payload + 输出对账确认：payload 稳定在 101/59/42/53.65，LLM 输出每次不同且全错，还凭空捏了「游资情绪高涨」「军工资产注入预期」这种数据里没有的定性判断。

加强 prompt（「必须复述 listed_count」+ 固定句式）后，句子出现了，但数字照样编 —— **prompt 调措辞是在跟幻觉打地鼠**。

### 根因

让 LLM「复述」数字，本质是把**确定性变换**误派给模型，违反全局规则「模型只做判断类任务；确定性变换让代码答」。对照组：同一 prompt 里 ETF 雷达段被忠实采纳——但那也只是这次没翻车，不能依赖。

### 修法（职责切分）

1. **代码出数字**：`render_dragon_tiger_line(summary)` 确定性渲染真值行，是数字唯一来源。LLM 成功分支末尾追加，fallback 共用同一渲染 → 两路径数字必然一致。
2. **payload 剥裸数字**：`_dragon_tiger_prompt_payload` 喂给 LLM 的只有定性 `bias`（偏多/偏空/均衡，由 `_dragon_tiger_bias` 按净买卖只数判定）+ 原因 tag，**没有任何裸数字**，从源头断掉幻觉诱因。
3. **prompt 改判断**：「用 `bias` 做一句定性点评，严禁输出任何龙虎榜数字（由系统按真值追加）」。

### 验证

真实 dry-run 输出结尾真值行「今日 101 只上榜，净买 59 / 净卖 42，净买入合计 +53.65 亿元」与 payload 探针**逐字一致**；LLM 正文无编造数字、无个股代码泄漏。

回归测试锁死：mock LLM 故意吐编造数字（「45 只 / 87 亿」），断言最终输出仍以代码真值行收尾。

### 可复用范围

同源风险点，接入时套同一模式：

- 紫苏叶分析师覆盖数
- **ETF 雷达措辞**（当前是预格式化数字让 LLM 复述，应警惕同类幻觉，优先改成代码追加真值行）
- 后续 P1 两融 / 北向明细

一句话原则：**LLM 只做判断（定性方向），代码做渲染（精确数字）。任何进投顾消息的数字都要有代码兜底。**

## 四、附带教训：并发会话下的 git 卫生

修复期间，同一个工作目录里另开了一个 kronos 会话。它移动了**共享 HEAD**，导致：

- 我以为在 `fix/sector-dragon-tiger-prompt`，提交时 HEAD 已被挪到 `feat/kronos-shadow-synthetic-stress`，我的 commit 落到了 kronos 分支顶上。
- 我的 feature 分支被外部重指向了 kronos commit；误推出一个名字误导的垃圾远程分支。

恢复原则：**两个 agent 写同一 git 工作树时，不做盲目的 checkout / reset（会移动共享 HEAD、可能冲掉对方未提交改动）**。正确做法用 `git worktree`：

```
git worktree add -b <new-branch> <隔离路径> main   # 独立目录 + 独立 HEAD
git -C <隔离路径> cherry-pick <我的 commit SHA>      # 在隔离区操作
```

worktree 全程不碰主工作树 HEAD，对方会话零打扰。cherry-pick 前先验 `merge-base` + 触及文件的 blob 是否与目标分支一致，确认能干净应用。

## 相关

- [`sector_review_deployment.md`](sector_review_deployment.md) — 板块复盘部署链路（含 Hermes `.env` 注入 LLM key 的机制）
- [`etf_flow_signal_lessons.md`](etf_flow_signal_lessons.md) — ETF 雷达（同样有 LLM 渲染数字的潜在风险）
- 全局规则「模型只做判断类任务；确定性变换让代码答」的项目实证
