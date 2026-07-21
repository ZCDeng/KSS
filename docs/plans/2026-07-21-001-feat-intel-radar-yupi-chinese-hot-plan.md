---
title: Intel Radar Yupi Chinese Hot Sources - Plan
type: feat
date: 2026-07-21
topic: intel-radar-yupi-chinese-hot
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# Intel Radar Yupi Chinese Hot Sources - Plan

## Goal Capsule

- **Objective:** 用旁路 [yupi-hot-monitor](https://github.com/liyupi/yupi-hot-monitor) 在盘前/盘后两窗抓热点，按 12 赛道词表灌入资讯雷达，与 RSS 同列表混排，并进入 AI 要点/全景，缓解「偏海外媒体」缺口。
- **Product authority:** 下方 Product Contract；既有资讯雷达（12 赛道 RSS + digest/panorama）为基线，不替换。
- **Open blockers:** 无。
- **Out of scope (identity):** 不迁入 yupi 前端/邮件/WebSocket；不做 Agent Skills；不替换 108 RSS；不做盘中实时推送。
- **Product Contract preservation:** Product Contract unchanged（planning 仅补充 HOW；planning 确认：常驻 yupi + **全源混入**）。

---

## Product Contract

### Summary

旁路运行 yupi-hot-monitor，在 **盘前/盘后** 两窗按 12 赛道关键词抓取热点，映射为与现有雷达条目同形的 item，与 RSS **同列表混排**（来源标签可辨）。仓库内置默认词表，设置页可改并落本地（KSS 为词表权威）。混入条目参与单赛道 AI 要点与 12 赛道全景。yupi 不可用时，雷达仍只依赖 RSS 正常可用。

### Problem Frame

当前资讯雷达靠 12 赛道、约 108 个公开 RSS（偏全球科技媒体与官方博客），合规过滤后按赛道分组。对 A 股投研场景，中文产业动态与政策叙事覆盖不足，列表与 AI 摘要都容易「海外媒体味」重。用户已选定用 yupi 多源能力补这一侧，而不是只加几条中文 RSS。

### Key Decisions

- **KD1. 旁路 yupi，而非只扩 RSS / 整站迁入 KSS。** 保留 yupi 多源抓取与 AI 相关性；KSS 只消费热点结果并合并展示。
- **KD2. 节奏 = 盘前 + 盘后两窗。** 不对齐全天轮询或打开即实时抓。
- **KD3. 展示 = 同列表混排 + 来源可辨。** 不新建独立 tab。
- **KD4. 词表权威 = KSS。** 默认词内置；设置可改落本地；不以 yupi 前端为主配置。
- **KD5. 首版同时做列表混排与 digest/全景摄入。**

### Requirements

**Ingest & merge**

- R1. 盘前与盘后各应有一次（可手动补跑）热点灌入，覆盖全部 12 赛道词表所绑定的监控查询。
- R2. 灌入结果与既有 RSS 条目合并为同一赛道列表，按时间倒序（或既有雷达排序规则），每条带来源类型可辨标签（至少区分 RSS vs yupi 热议源）。
- R3. 合并结果进入资讯雷达既有缓存读取路径：打开雷达默认读合并后缓存。
- R4. yupi 旁路失败、超时或未配置时，RSS 基线仍完整可用；失败可观测，不得空白整页。

**Keywords**

- R5. 仓库提供 12 赛道默认中文关键词表，与 `ai/semi/robot/...` 赛道 key 对齐。
- R6. 用户可在设置增删改某赛道关键词；修改持久化到本地，并成为后续灌入的权威输入。
- R7. 合规红线与现有雷达一致：赌博/预测市场/加密/色情等不得进入合并列表。

**AI digest / panorama**

- R8. 单赛道 AI 要点输入池包含该赛道已合并的 yupi 条目（截断规则内默认包含）。
- R9. 12 赛道全景能看到各赛道 yupi 信号，摘要可出现产业/政策叙事且来源可辨。
- R10. 进入 digest/全景的 prompt 侧带来源标记，避免误述为海外媒体 RSS。

**UX surface**

- R11. 赛道列表 UI 无需新主导航；混排条目用徽章/来源名区分。
- R12. 设置中词表编辑：改词 → 保存 → 下次盘前/盘后窗口生效（立即补跑为加分项）。

### Actors

- A1. 研究员（桌面端主用户）
- A2. 旁路热点服务（yupi-hot-monitor）
- A3. KSS 资讯雷达管线

### Key Flows

- F1. 盘前/盘后灌入 — 读词表 → 同步 yupi → check → 映射合并 → 写缓存（R1–R4, R7）
- F2. 打开资讯雷达 — 读合并缓存同列表展示（R2, R3, R11）
- F3. 改词表 — 本地权威配置，下次 F1 使用（R5, R6, R12）
- F4. AI 要点/全景 — 条目池含 yupi 且带来源标记（R8–R10）

### Acceptance Examples

- AE1. 同列表混排 + 来源标记（R2, R11）
- AE2. yupi 失败时 RSS 仍可用（R4）
- AE3. 改词后下次灌入反映新词（R6, R1）
- AE4. digest 含 yupi 条目且来源不混淆（R8, R10）
- AE5. 红线过滤生效（R7）

### Success Criteria

- S1. 盘前或盘后成功灌入后，抽查 3 个赛道合并列表出现 yupi 来源条目（有命中时）。
- S2. 改一词表 → 下一窗口灌入 → 列表变化可感知或明确无新命中。
- S3. yupi 断开时 RSS 与仅 RSS 的 digest 仍可完成。
- S4. 全景或单赛道要点至少一次能引用到 yupi 侧信息（有命中时）。

### Scope Boundaries

**In scope:** 旁路 yupi → 合并缓存 → 混排 UI → 词表默认+可编辑 → digest/panorama 摄入；盘前/盘后两窗；12 赛道。

**Deferred for later:** 打开雷达强制实时抓；盘中通知；中文源进 digest 总开关；仅 3–5 赛道试点；Agent Skills。

**Outside this product's identity:** 重做成 yupi Web 站；替换 RSS 体系；个股字段进雷达。

### Dependencies / Assumptions

- D1. 本机可常驻 yupi-hot-monitor（Node ≥ 18）及 OpenRouter（或 yupi 配置）Key。
- D2. 既有 `intel-radar` / `intel-digest` / `intel-panorama` 与 12 industries 契约保留。
- D3. 盘前/盘后可挂接既有 launchd 清单（`kss/config/cron_jobs.yaml`）。
- A1. 个别赛道召回可能偏少，空结果可接受。
- A2. 去重以 URL/标题近似为主；允许少量跨源重复。

### Outstanding Questions (product)

无 Resolve Before Planning。实现细节见 Open Questions（planning）。

### Sources / Research (product)

- https://github.com/liyupi/yupi-hot-monitor
- `kss/news/radar.py`, `kss/news/news_sources.json`, `scripts/kss_app_bridge.py` intel-* 命令
- 前序：`docs/plans/2026-07-09-001-feat-news-radar-ai-digest-plan.md` 等

---

## Planning Contract

### Summary

在既有 `fetch_radar` → `intel_radar_cache` → IntelView 链路上，增加 **yupi HTTP 旁路适配层**：本机常驻 yupi（默认 `http://127.0.0.1:3001`），盘前/盘后任务同步 KSS 权威词表到 yupi keywords（`category=track_key`）、触发 `POST /api/check-hotspots`、拉取全源 hotspots、映射为雷达 item 后与 RSS **合并写回同一缓存**。`source` 字段前缀可辨（如 `热议·{platform}`），使现有 favicon/列表与 digest `_format_items` 无需大改即可满足来源可辨与 prompt 标记。设置页增加 12 赛道词表编辑；cron 将资讯雷达刷新对齐盘前+盘后两窗。

### Problem Frame (implementation)

- RSS 路径：`kss/news/radar.py` `fetch_radar` 写 `intel_radar_cache`；`scripts/run_intel_radar.sh` 现仅工作日 09:00。
- 盘前/盘后已有 `news_digest_premarket`（08:40）/ `news_digest_postclose`（17:35），与雷达刷新未合并。
- yupi 暴露：`/api/health`、`/api/keywords` CRUD、`POST /api/check-hotspots`、`GET /api/hotspots?keywordId=&…`；关键词含 `category`。
- digest 已把 `source` 写进 prompt（`kss/news/digest_ai.py` `_format_items`），混排后自动进 AI 层，但需保证 yupi 条目 `source` 不可与 RSS 名混淆。
- Swift `IntelItem` 仅 `title/url/time/source/summary`——优先用 `source` 字符串表达类型，避免首版破 Codable。

### Key Technical Decisions

- **KTD1. 常驻 HTTP 旁路，不启停 Node 进程。** `YUPI_BASE_URL`（默认 `http://127.0.0.1:3001`）；健康检查失败则跳过合并并记录 `yupi_status`，RSS 照常。
- **KTD2. 全源混入。** 不按中文平台过滤；凡 yupi 返回且过红线/时间窗的热点均可入列表（用户确认）。
- **KTD3. KSS 词表权威 → 灌入前 reconcile 到 yupi。** 本地 JSON（默认+用户覆盖）为真源；灌入前按 `category=track_key` 同步 active keywords（创建缺失、停用多余或文案不一致项——策略：以 KSS 文本集合为准 upsert，yupi 侧多余同 category 词 deactivate）。
- **KTD4. 合并点在 Python 缓存层。** `fetch_radar` 之后或独立 `merge_yupi_into_radar_cache()`：读当前 cache（或刚抓的 RSS）→ 合并 yupi items → 按 `ts` 倒序 → 写回 `intel_radar_cache`。不新增 UI 数据通道。
- **KTD5. 条目映射。** yupi hotspot → `{title, url, time, ts, summary, source}`；`source = "热议·" + platform_or_source`；可选附 `origin: "yupi"` 于 JSON（Swift 可忽略未知键若 Codable 严格则仅用 source）。
- **KTD6. 盘前/盘后调度。** 扩展 `run_intel_radar.sh`：RSS fetch → yupi ingest/merge →（既有）rewrite worker。cron 改为两窗（对齐舆情热点盘前/盘后，如 08:45 与 17:40），替换或拆分现仅 09:00 的 `intel_radar_refresh`。
- **KTD7. 设置面。** 词表：`STATE_ROOT` 下用户覆盖 + 仓库默认；bridge 读写命令；Settings 任务/数据源区或独立「资讯雷达词表」小节。yupi URL 可用环境变量 + 可选设置字段。
- **KTD8. 超时与降级。** check-hotspots 与分页拉取设硬超时；失败不抛垮 `run_intel_radar.sh` 的 RSS 成功路径（与 rewrite worker 失败隔离风格一致）。

### High-Level Technical Design

```mermaid
sequenceDiagram
  participant Cron as run_intel_radar.sh
  participant Radar as kss.news.radar
  participant YIn as kss.news.yupi_ingest
  participant Yupi as yupi :3001
  participant DB as intel_radar_cache
  participant UI as IntelView / digest

  Cron->>Radar: fetch_radar (RSS)
  Radar->>DB: write RSS payload
  Cron->>YIn: ingest_and_merge()
  YIn->>YIn: load KSS keyword config
  YIn->>Yupi: reconcile keywords (category=track_key)
  YIn->>Yupi: POST /api/check-hotspots
  YIn->>Yupi: GET /api/hotspots per keyword
  YIn->>YIn: map + redline + dedupe
  YIn->>DB: merge into industries[].items
  UI->>DB: intel-radar load_cache
  UI->>UI: mixed list; digest uses items.source
```

### Alternative Approaches Considered

| Approach | Why not |
|----------|---------|
| 只扩中文 RSS | 产品否决；缺搜索/B 站等 |
| 窗口内启停 Node | 用户选常驻 |
| 中文源过滤 | 用户选全源混入 |
| 重写 yupi 逻辑进 Python | 丢 AI 相关性与维护面；产品要旁路 |

### Scope Boundaries (implementation)

**Deferred to Follow-Up Work**

- digest「排除 yupi」总开关
- Swift 专用 `sourceKind` 枚举与独立徽章组件（首版字符串即可）
- yupi 未安装时的一键安装向导
- Twitter API Key 专项设置（沿用 yupi `.env`）

### Dependencies / Prerequisites

- 本机已按 yupi README 安装 server 依赖、配置 OpenRouter、可 `npm run dev` 常驻 3001。
- 现有 launchd 渲染链路：`kss/config/cron_jobs.yaml` + `scripts/render_launchd_plists.py` + `run_intel_radar.sh`。

### Risk Analysis & Mitigation

| Risk | Mitigation |
|------|------------|
| yupi check 慢/全量 12×N 词超时 | 词数上限/每赛道 cap；超时跳过并记 stats |
| 全源混入再偏海外 | source 可辨；后续可加过滤（deferred） |
| 词表双源漂移 | 每次灌入前 KSS→yupi reconcile |
| Codable 遇未知字段 | 映射层只写 IntelItem 已知键 |
| OpenRouter 费用 | 文档说明；失败降级 RSS |

### Open Questions (planning)

- OQ-P1. 默认每赛道词数与具体默认词文案（实现时给保守 3–5 个/赛道产业词，可后续调）。
- OQ-P2. check-hotspots 是否对全部 active 词串行（yupi 内置行为）— 接受其耗时，仅设 KSS 侧总超时。
- OQ-P3. 合并去重键：优先 `url` 规范化，其次 title 折叠空白。

### Assumptions (planning)

- yupi `category` 字段可存 track_key 字符串。
- 用户接受全源混入后列表仍可能含 HN/Twitter 等；价值靠词表中文产业词召回。
- `intel-digest` 仍由前端传 items；合并缓存加载后前端列表即含 yupi，故 digest 自然纳入（R8）。

---

## Implementation Units

### U1. Yupi HTTP client + keyword reconcile + item map

- **Goal:** 可对常驻 yupi 做 health、关键词同步、触发检查、拉取 hotspot，并映射为雷达 item；红线过滤。
- **Requirements:** R1, R5, R7, R10（source 标记）
- **Dependencies:** none
- **Files:**
  - create: `kss/news/yupi_client.py`
  - create: `kss/news/yupi_ingest.py`（map + redline 复用 radar 红线列表）
  - create: `kss/news/track_keywords.default.json`（12 赛道默认词）
  - create: `kss/tests/test_yupi_ingest.py`
- **Approach:** stdlib `urllib` 调 yupi REST；base URL 来自 `YUPI_BASE_URL`；reconcile 以 KSS 词集合为准；map 时 `source` 前缀 `热议·`。不启动 Node 进程。
- **Patterns to follow:** `kss/news/radar.py` 纯 stdlib 风格；超时与 fail-soft。
- **Test scenarios:**
  - Happy: mock HTTP — reconcile 创建缺失词；hotspot 映射含 `热议·` source 与 ts。
  - Edge: health 失败 → ingest 返回 skipped + reason，不抛。
  - Error: check-hotspots 500 → merge 跳过 yupi。
  - Covers AE5: 红线标题被丢弃。
- **Verification:** 单测全绿；无真实 yupi 依赖。

### U2. Merge into intel_radar_cache + extend fetch/refresh path

- **Goal:** 将 yupi items 并入 12 赛道缓存，与 RSS 同列表排序；暴露可调用入口。
- **Requirements:** R2, R3, R4
- **Dependencies:** U1
- **Files:**
  - modify: `kss/news/radar.py` 或 `kss/news/yupi_ingest.py`（`merge_into_cache` / `fetch_radar_with_yupi`）
  - modify: `scripts/run_intel_radar.sh`
  - modify: `kss/tests/test_yupi_ingest.py`（或新 test 文件）
- **Approach:** RSS `fetch_radar` 成功后调用 merge；yupi 失败时保留纯 RSS 缓存并在 stats 记 `yupi_ok/false`。去重 URL/title。可选 `force` 路径与 bridge 对齐。
- **Patterns to follow:** `fetch_radar` 写 `intel_radar_cache`；shell 中 rewrite 失败不拖垮主退出码的隔离方式用于 yupi。
- **Test scenarios:**
  - Covers AE1: 合并后 industries[i].items 同时含 RSS 形与 `热议·` 条，按 ts 降序。
  - Covers AE2: yupi 不可达时 cache 仍有 RSS items。
  - Edge: 同 URL RSS 与 yupi → 保留一条（策略写清：优先保留先出现或带 summary 更长者）。
- **Verification:** 单测；本地有 yupi 时可手工跑 shell。

### U3. Keyword config load/save + bridge + Settings UI

- **Goal:** 默认词 + 用户覆盖；设置页可编辑；bridge 读写。
- **Requirements:** R5, R6, R12
- **Dependencies:** U1（词表 schema）
- **Files:**
  - create/modify: `kss/news/track_keywords.py`（load merge default+user）
  - modify: `scripts/kss_app_bridge.py`（如 `intel-keywords-get` / `intel-keywords-set`）
  - modify: `Sources/KSSDesktop/Services/BridgeClient.swift`
  - modify: `Sources/KSSDesktop/Views/SettingsView.swift`（或轻量子视图）
  - create: `kss/tests/test_track_keywords.py`
- **Approach:** 用户文件在 `STATE_ROOT/storage/`（或既有 settings 惯例）；set 校验 track_key ∈ 12 industries；保存后不强制即时 yupi sync（下次 ingest reconcile）。可选「立即灌入」按钮调 U4 命令。
- **Patterns to follow:** 既有 Settings 分区与 bridge JSON 命令；写命令进 WRITE_COMMANDS。
- **Test scenarios:**
  - Covers AE3: load 默认；set 覆盖后 load 见新词；非法 track_key 拒绝。
  - Edge: 空词列表某赛道 → 该赛道 yupi 侧无查询，RSS 不受影响。
- **Verification:** Python 单测；UI 手工改一词保存再读回。

### U4. Bridge ingest command + 盘前/盘后 cron

- **Goal:** 盘前/盘后自动灌入；可手动 bridge/cron-rerun。
- **Requirements:** R1, R3, R4
- **Dependencies:** U2
- **Files:**
  - modify: `scripts/kss_app_bridge.py`（`intel-yupi-ingest` 或扩展 `intel-radar` 文档化 force 路径）
  - modify: `scripts/run_intel_radar.sh`
  - modify: `kss/config/cron_jobs.yaml`（盘前+盘后两窗；标题/分类清晰）
  - modify: `Sources/KSSDesktop/Services/BridgeClient.swift`（若新命令）
  - docs: `docs/RELEASE_GUIDE.md` 一行依赖说明（yupi 常驻）
- **Approach:** 两窗 schedule 对齐舆情热点（建议 08:45 / 17:40）；同一 wrapper。文档写明 yupi 需常驻与 OpenRouter。
- **Execution note:** Prefer install/runtime smoke：无 yupi 时任务仍 0 退出且 RSS 刷新成功。
- **Test scenarios:**
  - Integration: mock 下 bridge 命令返回 envelope 含 `yupi_status` 与 industries。
  - Cron yaml 含两个启用窗口或等价双 suffix。
- **Verification:** `cron-list` 可见任务；无 yupi 时 shell 仍刷新 RSS。

### U5. Digest/panorama source marking + list affordance

- **Goal:** 确认 AI 层与列表对 yupi 条目来源可辨；必要时加强 prompt 一行说明。
- **Requirements:** R8, R9, R10, R11
- **Dependencies:** U2
- **Files:**
  - modify: `kss/news/digest_ai.py`（`_format_items` 或 system 提示：含「热议·」为多源热议非 RSS）
  - modify: `Sources/KSSDesktop/Views/IntelView.swift`（可选：source 含「热议」时次要样式）
  - modify: `kss/tests/test_intel_digest.py`
- **Approach:** 最小改动——若 `source` 已前缀 `热议·`，digest 格式化已带 source，则仅补 system 一句防误述；UI 徽章可选。
- **Test scenarios:**
  - Covers AE4: `_format_items` 输出含 `热议·`；build_prompt 用户块含该行。
  - Happy: run_digest mock LLM 收到的 user prompt 含 yupi 标题。
- **Verification:** 单测；有数据时手工跑一赛道 digest 目视。

---

## Verification Contract

- 单元：`kss/tests/test_yupi_ingest.py`、`test_track_keywords.py`、扩展 `test_intel_digest.py`。
- 集成（可选有 yupi）：`run_intel_radar.sh` 后 `intel-radar` 缓存含 `热议·`；改词 → 再 ingest。
- 降级：停 yupi → 脚本与 UI 仍显示 RSS。
- 对照 Success Criteria S1–S4 与 AE1–AE5。

## Definition of Done

- U1–U5 行为满足 Product Contract R1–R12。
- 盘前/盘后 cron 已配置并可在设置→任务中看到/重跑。
- RELEASE_GUIDE 或等价处注明 yupi 常驻依赖。
- 无 yupi 时不破坏既有资讯雷达。
- 测试覆盖 map/merge/关键词/digest 标记与 yupi 失败路径。

## System-Wide Impact

- **End user:** 列表更杂（全源），需依赖 `热议·` 与词表质量。
- **Ops:** 多一个常驻 Node 服务与 OpenRouter 成本。
- **Data:** `intel_radar_cache` payload 变大；无新表强依赖（词表文件 + 可选 stats 字段）。

## Sources & Research

- yupi API：`/api/keywords`、`/api/hotspots`、`POST /api/check-hotspots`、`/api/health`（repo `server/src`）。
- KSS：`kss/news/radar.py`、`digest_ai.py`、`scripts/run_intel_radar.sh`、`kss/config/cron_jobs.yaml`、`IntelItem` in `Sources/KSSDesktop/Models/KSSModels.swift`。
- Grounding: `/tmp/compound-engineering/ce-brainstorm/yupi-hot-radar/grounding.md`
