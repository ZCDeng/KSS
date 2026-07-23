---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
date: 2026-06-29
type: feat
status: implementation-ready
---

# 记忆召回排序核 (temporal-decay + MMR) - Plan

> **Product Contract preservation**: Product Contract 未改（WHAT 来自 ce-brainstorm，4 个决策原样保留）。本次 enrich 仅追加 Planning Contract / Implementation Units / Verification / DoD。

## Goal Capsule

**目标**：把 dexter 检索栈的两段纯函数（`temporal_decay` 时间衰减 + `MMR` 多样性去重）移植成零依赖 Python 排序核，先驱动**个股日复盘**的「近期复盘演变」历史召回——同股过去 N 天复盘的「论据片段」按时间衰减 + 多样性重排，拼成一段确定性 markdown，框定为「待验证的先验」，插在今日操作建议之前。

**产品权威**：用户逐项拍板 4 个决策（见 Decisions 表）。

**Open blockers**：无。

---

## 背景：原始前提被探查推翻两次（必读）

ce-brainstorm 的 Explore 探查戳破两个隐含假设：

1. **不存在「历史→LLM」链路可 retrofit**。KSS 所有 LLM 链路（`kss/sector/commentary.py:190`、`kss/news/digest.py:62`）只喂当天。`storage/daily_review/` 归档只被 `scripts/validate_predictions.py:126` 拿去事后验证，从不回灌。这是从 0 到 1 建一条历史召回链路。
2. **daily_review 是纯确定性模板，不调 LLM**。`render()`（`scripts/daily_review.py:809`）从统计算的情形表 / 关键位 / `_advice_block()` 规则建议拼 markdown，全程无 `client.complete`。故历史注入是**确定性 markdown 段**，绕开「LLM 复述数字必幻觉」老坑。

用户原话的 `MEMORY.md + YYYY-MM-DD.md` 同构，实为 Claude Code agent 记忆目录，非 KSS runtime。

**现状基线**（Explore 确认）：零 embedding、零 token 计数、零 ranking、零 dedup、零历史注入。pytest，setuptools，src 布局 `kss/`，779 passing。

---

## Product Contract

### 核心需求

- **R1** 排序核 = 纯函数库 `kss/memory/`：`temporal_decay`（指数衰减，半衰期默认 30 天可配；非 dated 条目 evergreen 豁免；日期从 `{YYYY-MM-DD}_{tscode}.md` 解析）、`mmr`（`λ·相关性 − (1−λ)·与已选最大相似度`，λ 默认 0.7；相似度用**字符 bigram 的 Jaccard**）、`rank` 编排（候选 → 关键词打分 → 衰减 → MMR → top-K）。
- **R2** 首个消费方 = `scripts/daily_review.py`：生成今日复盘时，对该 ts_code 召回 `storage/daily_review/` 下历史同股复盘，排序核重排后取 top-K，渲染成确定性 markdown。
- **R3** 注入形态 = 确定性 markdown 段，标题「近期复盘演变（待验证先验）」，插在今日 `_advice_block` 产物之前。零 LLM 经手。
- **R4** 历史框定 = 待验证先验：措辞「这是你之前的判断——拿今日数据重新验证，变了就明说变在哪」，非「接着写的连续论据」。
- **R5** 召回 query = 今日复盘关键特征（形态 / category / 情形标签），用于同股历史内部排序。

### 成功标准

- **AE1** 排序核 100% 纯函数可单测，移植 dexter `temporal-decay` / `mmr` 等价断言 + 补中文 bigram 用例。
- **AE2** 对 ≥10 天历史的股，top-K 不是简单按近期平铺——可观察到 MMR 把连续重复日（「仍横盘」×N）压成 1 条，腾位给真正变化的日子。
- **AE3** 首次出现、无历史的股：段落优雅缺省（不报错、不空标题）。
- **AE4** 不引入任何新第三方依赖（`pyproject.toml` 不变）；现有 779 passing 不破。

### 范围边界

**本期交付**：纯函数排序核 + daily_review 一个消费方的确定性 markdown 段。

**Deferred for later（明确不做）**：向量/embedding 段；`sector_commentary` 的 LLM prompt 注入（第二消费方，复用同核）；news digest（已停用）；token 计数集成。

**Deferred to Follow-Up Work**：旧合并档（`{date}.md` 多股）解析拆股——v1 只 glob 按股 `*_{tscode}.md`，旧档不参与召回。

**Outside this product's identity**：不建持久向量库、不做跨 session 结构化 memory store、不动现有 LLM 链路的当日上下文装配。

### Decisions（用户已拍板）

| # | 决策 | 选定 | 理由 |
|---|------|------|------|
| 1 | 检索栈范围 | 纯关键词 + 衰减 + MMR，零新依赖 | 衰减/MMR 本是纯函数；向量是唯一需基建段，先拿 80% 价值 |
| 2 | 首个消费方 | 个股日复盘 daily_review | ts_code 相关性键最干净、按股归档、连续性价值最高 |
| 3 | 历史框定 | 待验证的先验（防锚定） | 合 dexter fresh-eyes + 证据优先纪律 |
| 4 | 注入形态 | 确定性 markdown 段（非 LLM） | daily_review 本就不调 LLM；零 LLM 经手命中 llm-numbers-deterministic 纪律 |

---

## Planning Contract

### Key Technical Decisions

- **KTD1 中文相似度用字符 bigram**。dexter `mmr.ts:29` 的 `tokenize` 用 `[a-z0-9_]+`，对中文返回空集 → Jaccard 退化。改为：对字符串取相邻字符二元组集合（`"横盘震荡"→{横盘,盘震,震荡}`），中英混排时英文 token 与 bigram 并入同一集合。零依赖，不引 jieba。
- **KTD2 排序核领域无关**。`rank()` 只认 `Candidate{id, text, timestamp_ms|None, base_score}`，不认 daily_review 概念。daily_review 专属的 glob/抽取/渲染全在 U2，使 U1 可被第二消费方（sector_commentary）原样复用。
- **KTD3 衰减豁免参数保留但 daily_review 不触发**。`timestamp_ms=None` → evergreen 不衰减。daily_review 档全是 dated，实际不触发；保留是为第二消费方（可能含 evergreen 主题档）复用。
- **KTD4 抽取粒度 = 建议块 + 形态行**（call-out 默认）。每个历史档抽 `形态:` 行 + `*建议*` 块（📈/⚠️/actions）作当日「论据片段」，作为 MMR 的 `text`。比抽整段噪声小，是跨天真正变化的承载点。
- **KTD5 召回在 main() 算、render() 只渲染**。`main()` 的股循环里有 ts_code + archive_dir，计算 `s['history_recap']`（markdown 行列表）；`render()` 仅 `s.get('history_recap')` 插入。保持 render() 渲染职责单一。
- **KTD6 query 无命中时优雅退化**。`rank()` 的 `query=None` 时跳过关键词打分，只走衰减 + MMR（纯按时间衰减 + 多样性）。daily_review 始终给 query，但核须支持 None 以备复用。

### High-Level Technical Design

```
scripts/daily_review.py  main() 股循环
        │  ts_code, today_features(形态/category/情形), archive_dir, now
        ▼
kss/memory/review_recall.py  build_history_recap()
   1. glob_symbol_reviews(ts_code, archive_dir)   # *_{tscode}.md, 排除旧合并档
   2. 每档 → parse_date(前缀) + extract_thesis_snippet(建议块+形态行)
   3. Candidate[]  (id=date, text=snippet, timestamp_ms=date)
        ▼
kss/memory/rank.py  rank(candidates, query=today_features, now_ms, k=TOP_K)
   ├─ score.py     关键词打分(query vs snippet, bigram 重叠)  → base_score
   ├─ temporal_decay.py  半衰期 30d 指数衰减   → score *= multiplier
   ├─ (sort by score)
   └─ mmr.py       λ=0.7 多样性重排(similarity.py: bigram Jaccard) → top-K
        ▼
   render 「近期复盘演变(待验证先验)」 markdown 行
        ▼
scripts/daily_review.py  render()  插在 _advice_block 之前 (行 ~902)
```

### Output Structure

```
kss/memory/
├─ __init__.py
├─ types.py            # Candidate dataclass
├─ temporal_decay.py   # 衰减 + 文件名日期解析 + evergreen 判定
├─ similarity.py       # 字符 bigram tokenize + jaccard
├─ mmr.py              # mmr_rerank
├─ score.py            # 关键词打分 (query vs text)
├─ rank.py             # rank() 编排
└─ review_recall.py    # daily_review 专属: glob/抽取/渲染 (依赖上面)
tests/memory/
├─ test_temporal_decay.py
├─ test_similarity.py
├─ test_mmr.py
├─ test_rank.py
└─ test_review_recall.py
```

---

## Implementation Units

### U1. 排序核纯函数包 `kss/memory/`

**Goal**：领域无关的 `rank()` 及其四块纯函数子件，可单测、零依赖。

**Requirements**：R1、AE1、AE4、KTD1/2/3/6。

**Dependencies**：无。

**Files**：
- `kss/memory/__init__.py`、`kss/memory/types.py`、`kss/memory/temporal_decay.py`、`kss/memory/similarity.py`、`kss/memory/mmr.py`、`kss/memory/score.py`、`kss/memory/rank.py`
- `tests/memory/test_temporal_decay.py`、`tests/memory/test_similarity.py`、`tests/memory/test_mmr.py`、`tests/memory/test_rank.py`

**Approach**：
- `types.Candidate`：`@dataclass`，字段 `id: str`、`text: str`、`timestamp_ms: int | None`、`base_score: float = 0.0`、`score: float = 0.0`。
- `temporal_decay`：`decay_lambda(half_life_days)=ln2/h`；`decay_multiplier(age_days, half_life_days)`（age≤0 或 λ≤0 → 1.0）；`parse_date_from_filename(name)` 匹配 `^(\d{4})-(\d{2})-(\d{2})_.+\.md$`（注意 KSS 是 `{date}_{tscode}` 前缀，**非** dexter 的纯 `{date}.md`）；`is_evergreen` = 非 dated；`apply_decay(cands, now_ms, half_life_days)` 对 `timestamp_ms is None` 跳过。移植自 `scratchpad/dexter/src/memory/temporal-decay.ts`。
- `similarity`：`tokenize(text) -> set[str]`（字符 bigram + 英文 token，KTD1）；`jaccard(a,b)`（双空→1，单空→0）。
- `mmr`：`mmr_rerank(items, lambda_=0.7) -> list`，分数归一化到 [0,1] 后迭代选 `λ·rel − (1−λ)·max_sim`；λ=1 退化为纯按分排序。移植自 `scratchpad/dexter/src/memory/mmr.ts`。
- `score`：`keyword_score(query, text)` = query bigram 与 text bigram 的重叠占比（query 为 None 时调用方跳过）。
- `rank`：`rank(candidates, *, query, now_ms, half_life_days=30, mmr_lambda=0.7, top_k=5)`：query 非空→填 base_score；apply_decay；按 score 排序；mmr_rerank 取前 `top_k*2` 输入；截 top_k。对齐 `scratchpad/dexter/src/memory/search.ts` 的 stage 3-5。

**Patterns to follow**：现有 `kss/` 子包的 dataclass + 纯函数风格（如 `kss/sector/scorer.py`）；pytest 平铺断言。

**Test scenarios**：
- `test_temporal_decay`：age=half_life → multiplier≈0.5；age=0 → 1.0；age<0（未来）→ clamp 到 1.0；λ≤0 → 1.0。`parse_date_from_filename("2026-06-18_688017.SH.md")` → 2026-06-18；`"2026-06-18.md"`（旧合并档）→ None（无 `_` 前缀不匹配）；`"MEMORY.md"` → None；非法日期 `"2026-13-40_x.md"` → None。`apply_decay` 对 `timestamp_ms=None` 条目 score 不变。
- `test_similarity`：`tokenize("横盘震荡")` → `{横盘,盘震,震荡}`；`tokenize("MACD缩柱")` 含英文 token `macd` 与中文 bigram；`jaccard` 两空集→1，一空→0，"横盘震荡" vs "横盘整理" 部分重叠 0<j<1，相同→1。
- `test_mmr`：λ=1 → 纯按 score 降序；3 个候选其中 2 个文本近同 → 重排后近同的不相邻（多样性优先）；空列表→空；单元素→原样。
- `test_rank`：query 命中 → 高 base_score 条目靠前；10 个候选含 6 个「仍横盘」近同 + 4 个变化日 → top_k=5 里近同只剩 1，变化日全入（**Covers AE2**）；`query=None` → 仅衰减+MMR 不报错（KTD6）；候选数 < top_k → 全返回不报错。

**Verification**：`pytest tests/memory/ -q` 全绿；`rank()` 在无 query、空候选、超量候选三种边界不抛异常。

---

### U2. daily_review 历史召回抽取 + 渲染接线

**Goal**：把 U1 的核接到 daily_review，产出「近期复盘演变（待验证先验）」markdown 段并插入正文。

**Requirements**：R2、R3、R4、R5、AE2、AE3、KTD4/5。

**Dependencies**：U1。

**Files**：
- `kss/memory/review_recall.py`（新）
- `scripts/daily_review.py`（改 `main()` 股循环 + `render()` 插入点）
- `tests/memory/test_review_recall.py`

**Approach**：
- `glob_symbol_reviews(ts_code, archive_dir) -> list[Path]`：匹配 `*_{ts_code}.md`，**排除**无 `_` 前缀的旧合并档（Deferred to Follow-Up）。
- `extract_thesis_snippet(md_text) -> str`：抽 `形态:` 行 + `*建议*` 块（📈/⚠️/`• actions` 直到段落结束）。锚点取自真实档结构（`storage/daily_review/2026-06-18.md` 样本）。抽不到则回退取该股段落前 N 行。
- `build_history_recap(ts_code, today_features, archive_dir, *, now_ms, k=TOP_K, exclude_date) -> list[str]`：glob → 解析日期+抽片段 → `Candidate(id=date_str, text=snippet, timestamp_ms=date_ms)` → `rank(query=today_features_str, now_ms=now_ms, top_k=k)` → 渲染 markdown 行：标题 `*近期复盘演变* (待验证先验)` + 每条 `· {date}: {snippet 首行}`，末尾固定一句框定语「↑ 以上为过去判断，请用今日数据重新验证，变化点优先」。无候选 → 返回 `[]`（AE3）。
- `today_features` 串：从 `s` 取 `形态` flags（p1/p2/p3）、`category`、情形分布主峰标签拼成短串作 query（R5）。
- `scripts/daily_review.py` 接线：`main()` 股循环内（行 ~965-969 之后，已知 `ts_codes[i]`）调 `build_history_recap`，存 `s['history_recap']`；`render()` 个股循环情形分布之后、`_advice_block` 之前（行 ~901-902）插入 `lines.extend(s.get('history_recap') or [])`。

**Execution note**：先写 `extract_thesis_snippet` 对真实档样本的 characterization 测试，再改 `render()`——daily_review 是审计底稿生成器，渲染回归须有锚点保护。

**Patterns to follow**：`scripts/daily_review.py` 现有 `_key_levels_block` / `_advice_block`（返回 `list[str]` 拼进 lines）的块渲染风格；`s['_df']` 等中间态挂在 `s` dict 的约定。

**Test scenarios**：
- `glob_symbol_reviews`：目录含 `2026-06-18_688017.SH.md` + `2026-06-19_688017.SH.md` + `2026-06-18_300750.SZ.md` + 旧档 `2026-06-18.md` → 查 688017.SH 只返 2 个该股按股档，**排除旧合并档与他股**。
- `extract_thesis_snippet`：喂真实档文本 → 含「形态:」与「• 止损位」actions，不含大盘背景/footer；缺建议块的次新股档 → 回退前 N 行不抛异常。
- `build_history_recap`：≥10 天同股档、含连续「持仓保留」近同日 → 输出 top-K 去重且含框定语（**Covers AE2/R4**）；该股**零历史** → 返回 `[]`，render 不插空标题（**Covers AE3**）；`exclude_date=今日` → 今日档不进召回。
- `render()` 集成：给 `s['history_recap']=['line']` → 出现在情形分布之后、`*建议*` 之前；`s` 无该键 → 正文与现状逐字一致（回归保护）。

**Verification**：对一只有多日历史的真实 ts_code 跑 `python scripts/daily_review.py --symbols <code> --dry-run`，正文在操作建议前出现「近期复盘演变」段且条目去重；无历史的次新股不出现该段、不报错；现有 daily_review 相关测试不破。

---

## System-Wide Impact

- **仅 daily_review 正文新增一段**；归档文件随之包含该段（`validate_predictions.py` 解析的是「预期区间/情形」锚点，不依赖建议块文本，新增段不破坏其抽取——U2 验证须确认）。
- **无新依赖、无 schema 变更、无 cron 变更**。
- `kss/memory/` 为新包，第二消费方（sector_commentary LLM 注入）后续可复用 U1。

## Risks & Dependencies

- **R-risk1 抽取锚点脆弱**：`extract_thesis_snippet` 依赖建议块的 emoji/`*建议*` 文本标记，若 daily_review 渲染格式日后改动会失配。缓解：characterization 测试锁样本 + 抽不到回退前 N 行。
- **R-risk2 validate_predictions 误食**：新增段若含被 `validate_predictions.py:73-104` 误判为预测锚点的字样会污染审计。缓解：U2 验证须跑一遍 validate 解析确认新增段不被拾取；框定语避开「预期区间/情形分布」关键词。
- **R-risk3 中文 bigram 区分度**：极短片段（<3 字）bigram 稀疏，相似度可能失真。缓解：snippet 取建议块（通常数十字），且 query 为多特征拼串。

## Definition of Done

- U1、U2 全部 test scenarios 通过；`pytest tests/memory/ -q` 全绿。
- 真实多历史 ts_code 的 `--dry-run` 正文在操作建议前出现去重的「近期复盘演变（待验证先验）」段；无历史股不出现该段、不报错。
- `pyproject.toml` 未新增依赖；现有 779 passing 不破；`validate_predictions.py` 解析不被新增段污染。

## Sources & Research

- dexter 源码（MIT，浅克隆 `scratchpad/dexter/src/memory/`）：`temporal-decay.ts`、`mmr.ts`、`search.ts`（五段栈 stage 3-5）。
- KSS 探查锚点：`scripts/daily_review.py:809`(render)/`:910`(main)/`:977`(archive)、`storage/daily_review/2026-06-18.md`(档样本)、`kss/sector/commentary.py:190`(对照：当日上下文)、`scripts/validate_predictions.py:73-144`(归档消费方)。
- 关联记忆：`llm-numbers-deterministic-rendering`、`watchlist-review-link-per-symbol`、`backtest-loop-closure-shipped`。
