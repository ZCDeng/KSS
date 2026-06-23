---
title: "feat: MCP 编排剧本(orchestration recipes)—— 确定性复盘 DAG 注册表"
type: feat
date: 2026-06-22
status: planned
depth: standard
deepened: 2026-06-22
origin: docs/brainstorms/2026-06-22-kssdeck-agent-panel-requirements.md
related:
  - docs/ideation/2026-06-22-agent-reasoning-framework-ideation.html
  - docs/plans/2026-06-22-002-feat-mcp-data-catalog-orientation-plan.md
  - docs/solutions/ai_native_surface_assessment.md
---

# feat: MCP 编排剧本(orchestration recipes)—— 确定性复盘 DAG 注册表

## Summary

把高频多步复盘任务固化成 Python 函数剧本注册表(recipe registry),经 `bridge.dispatch`
暴露为公开**只读** MCP 工具。agent 用一句话选一条剧本(如「解释这只今天为什么上榜」),代码顺序串接
现有 read 命令、打包真值返回;agent 只做「选哪条 + 在真值上叙事」。ideation #3 / brainstorm 依赖 #3,
站在 #1+#2 地基(PR #31)上。

「框架」= 剧本注册表,**不是** LangGraph/自主 loop。本轮**只发 read 侧**;write 执行路径 defer 到 #4
(其人在环内 UI confirm 契约落地时按实际设计,见 Scope Boundaries)。安全由**能力式门控**保证:
read 剧本拿到的是「碰写命令即 raise」的受限 `call`,写操作物理不可达(KTD-3)。

---

## Problem Frame

当前 agent 复盘要自己想多步:调 get_stock、再 sector-rotation、再 discovery……每次自由发挥 →
步骤不稳、耗 token、难复现、难测。把最高频几条链固化成剧本后,agent 一次调用拿一整束相关真值,
稳定性与可测性都上来。复用已落地的 #1+#2 seam(dispatch 路由 + `COMMANDS`/`RUN_TASKS` registry +
`_orientation()` + MCP `@mcp.tool→_call` + U7 数字纪律)。

---

## Requirements

- **R1** 剧本 = `scripts/kss_recipes.py` 里的 Python 函数,登记进 `RECIPES`(`name → {desc, write, args, fn}`),
  每条顺序调注入的 `call` 串接、纯 Python 取值传递(KTD-1)。
- **R2** 剧本只返**结构化真值数据**,**recipe 层不调 LLM**;叙事交调用方 agent。透传字段中既有的 LLM
  自由文本(如 commentary)须标记 `provenance:"llm_prior"` 或排除——不在 U7 保证范围(KTD-2)。
- **R3** 经 dispatch 暴露**只读**:`recipe-list`(剧本目录)+ `run-recipe`(跑 read 剧本)。read 路径给剧本
  注入**能力式受限 call**:碰 `WRITE_COMMANDS` 即 raise;自声明 `write=True` 的剧本经 read 路径直接拒(KTD-3)。
- **R4** MCP 暴露只读公开:`list_recipes` + `run_recipe`。**本轮不发 write MCP 工具**(write 路径 defer)。
- **R5** 剧本登记进 `get_orientation` 能力面(agent 能发现)。
- **R6** 种子 ≥2 条只读复盘剧本,**必含**「解释这只今天为什么上榜」(`explain_stock_today`)。
- **R7**(成功标准,任务相关版)`run_recipe("explain_stock_today",{symbol})` 一次返回足以回答「为什么上榜」
  的束:该股(含当日 move)+ 板块上下文(含该股板块归属)+ 主题龙头 + 发现候选命中(**含 reason/score**),
  agent 无需追加工具调用即可叙事。dogfood:对一个真实 symbol 实跑一次确认够用(见 KTD-2)。

---

## Key Technical Decisions

**KTD-1 剧本格式 = Python 函数注册表(已确认,非声明式 DSL)。** 每条 recipe 是 `kss_recipes.py` 里
`fn(call, **args) -> dict`,`call` 为注入的受限/原始 dispatch(KTD-3/4);登记进 `RECIPES`。镜像现有
`run_task` 的「字符串→Python 函数」路由。比 DSL 简单、可测、纯 stdlib、合约定;不引编排引擎。

**KTD-2 剧本只返真值,叙事交 agent;U7 声明收敛(doc-review P1)。** recipe 顺序调 read 命令打包返回,
**绝不调 LLM**——这排除「recipe 引入*新*数字幻觉」。但 recipe 透传子命令**所有字段**,其中
`sector-rotation`/`etf_radar` 的 `commentary` 是既有 LLM 文本(可能夹模型写的数字),**不在 U7 保证内**。
故:打包时对自由文本字段(commentary 及类似)标 `provenance:"llm_prior"`,让下游 agent / #4 provenance
守卫当未验证叙事处理,不当可引真值。**不声明「整束 U7 安全」**,只声明「不引入新数字幻觉面」。

**KTD-3 能力式只读门控 + write 执行路径 defer(已确认 defer,doc-review Critical)。**
本轮只发 read 侧。read 路径 `_run_recipe` 给剧本注入**受限 call**:
`if command in WRITE_COMMANDS: raise PermissionError`,否则转 dispatch。这样「读剧本内部串到写命令」
**物理不可达**,门控基于能力(`WRITE_COMMANDS` 这个既有权威集)而非作者诚实标的 flag。
`write:bool` 字段保留(前向兼容 + orientation 路由提示);自声明 `write=True` 的剧本经 `run-recipe` 直接拒。
真实 write 执行路径(run-write-recipe / run_write_recipe MCP 工具 / 整束 confirm 门控)**defer 到 #4**——
其「人在环内逐次 UI confirm」契约定了再按实际形状设计,避免现在对着猜的契约建管道(见 Scope Boundaries)。

**KTD-4 惰性 import 解循环 + orientation 降级(doc-review P2)。** `kss_recipes` 不 import bridge(收注入 call);
bridge 的 `_recipe_list`/`_run_recipe` **函数内**惰性 import `kss_recipes`,无模块级循环、无 init 顺序坑。
`_orientation()` 调 `_recipe_list()` 须 try/except 降级为 `{"error":...}`(仿 catalog 区),防 recipes 模块
import 错拖垮 orientation 这个核心 read 工具。

**KTD-5 args 经 dispatch 传 JSON + 校验(doc-review P2)。** `run-recipe` args=`[name, json_args]`;
`_run_recipe`:空/缺 json_args → `{}`(`json.loads("")` 会抛,显式空→{});解析后**校验 keys ⊆
`RECIPES[name].args` 且值为 str**(防类型混淆 / 多余键流入 dispatch);坏 JSON / 非法键 fail-loud
返回 error+hint,不崩 sidecar。无既有命令传 JSON-in-args,本约定为新引入(已核实)。

**KTD-6 漂移守卫 + 正向接线测试(doc-review P1)。** 既有漂移守卫只断言 dispatch 字面 ⊆ COMMANDS(单向),
**不能证明新 dispatch 分支真接上了**。故 U3 必加正向接线测试:`dispatch("recipe-list",[])` 返回 list、
`dispatch("run-recipe",[seed,"{}"])` 返回 payload。两命令登记进 `COMMANDS`(均标 read)。

**KTD-7 部分失败语义(doc-review P1)。** 多步串接某步报错时:该区标 `{error,hint}` + 顶层置
`partial:true` & `failedSteps:[区名]`(否则 agent 被告知「信任这束」却拿到静默半束)。受限 call 须把
下游 `SystemExit`(如 `report` 路径护栏抛的,`except Exception` 捕不到)归一化为区级 error,不崩 sidecar。
空结果区分:未命中 `{hit:null, queried:true}` vs 出错 `{error:...}`。

---

## High-Level Technical Design

```mermaid
flowchart LR
  agent["agent / 未来 #4 面板 loop"] -->|"run_recipe(name,args)"| T[MCP run_recipe 只读公开]
  T -->|_call| D["dispatch 'run-recipe'"]
  D --> RR["_run_recipe(name,json_args)"]
  RR -->|"校验 args / 拒 write=True 剧本"| GATE{ }
  RR -->|惰性 import| REG["RECIPES 注册表"]
  GATE -->|注入受限 call| FN["recipe fn(restricted_call, **args)"]
  FN -->|"受限 call:碰 WRITE_COMMANDS 即 raise"| RO["只读 dispatch 子集"]
  RO --> C1[stock] & C2[sector-rotation] & C3[discovery/theme]
  FN -->|"打包真值 + commentary 标 llm_prior<br/>+ partial/failedSteps"| OUT["payload → agent 叙事"]
  LR2["list_recipes / recipe-list"] -.->|目录:name/desc/write/args| REG
  D -.->|orientation 露出 recipes(降级保护)| ORI[get_orientation]
  WRITE["write 执行路径"]:::deferred -.->|defer 到 #4| X[ ]
  classDef deferred stroke-dasharray: 5 5,opacity:0.5
```

---

## Implementation Units

### U1. 剧本注册表 + 模块骨架

**Goal:** 建 `kss_recipes.py`:`RECIPES` 结构 + 注入式 `call` 契约 + `write:bool` 自声明字段。
**Requirements:** R1, KTD-1。
**Dependencies:** 无(站在已 merge 的 #1+#2 上)。
**Files:**
- `scripts/kss_recipes.py`(新建:`RECIPES` dict + recipe fn 签名约定 + 注册结构)
**Approach:** `RECIPES: dict[str,dict]`,每条 `{desc, write:bool, args:list[str], fn:callable}`。
fn 签名 `fn(call, **args) -> dict`,`call` 由 bridge 注入(read 路径注入受限 call,KTD-3)。纯 stdlib,
不 import bridge(KTD-4)。`write` 字段本轮仅作前向兼容 + orientation 提示(无 write 执行路径)。
**Patterns to follow:** `kss_app_bridge.py` 的 `COMMANDS`/`RUN_TASKS` 注册表;`run_task` 函数路由。
**Test scenarios:** Test expectation: none —— 纯结构/契约定义,行为在 U2/U3 验证。

### U2. 种子只读复盘剧本 + 测试

**Goal:** ≥2 条只读剧本,必含 `explain_stock_today`;打包真值、标记 LLM 文本、部分失败信号。
**Requirements:** R1, R2, R6, R7, KTD-2/7。
**Dependencies:** U1。
**Files:**
- `scripts/kss_recipes.py`(改:实现种子剧本)
- `kss/tests/test_recipes.py`(新建)
**Approach:** 种子(均 `write=False`,只调 read 命令,不调 LLM):
- `explain_stock_today(call, symbol)` → 串 `stock` + `sector-rotation`(latest)+ `theme-leaders` +
  `get-discovery-candidates`(按 **`ts_code`** 精确匹配过滤命中,带 reason/score),返回
  `{stock, sectorContext, themeLeaders, discoveryHit, partial?, failedSteps?}`。该股板块归属 + 当日 move
  须在束内(R7 任务相关)。
- `sector_context(call, date="")` → 串 `sector-rotation`(date)+ `sector-rotation-history` + `theme-leaders`。
打包时:`commentary` 类自由文本字段标 `provenance:"llm_prior"`(KTD-2);某步抛 → 区级 error + 顶层
`partial/failedSteps`(KTD-7);未命中与出错区分(KTD-7)。
**Patterns to follow:** 现有 dispatch read 命令返回结构(#1+#2 同源);`_discovery_merge` 的 `ts_code` 键(bridge)。
**Test scenarios:**
- happy(explain_stock_today):注入 fake call 返桩 → 四区齐,`discoveryHit` 含 reason/score、`sectorContext`
  含该股板块归属。**Covers R7**(够回答「为什么上榜」,非仅四键存在)。
- discovery 过滤:候选含/不含该 `ts_code` → 命中带 reason/score / `{hit:null,queried:true}`,不与 error 混。
- **不调 LLM 断言(KTD-2)**:monkeypatch 让任何 LLM 调用点抛错,确认 recipe 路径从不触发。
- **commentary 标记(KTD-2)**:子命令返回含 commentary → 打包后该字段带 `provenance:"llm_prior"`。
- **部分失败(KTD-7)**:4 步中 1 步抛 → 该区 error + 顶层 `partial==true` & `failedSteps` 含该区名,
  其余区正常。
- **SystemExit 归一(KTD-7)**:某步触发 `report` 坏路径(抛 SystemExit)→ 区级 error,不 recipe 级 raise。
- sector_context:date 透传;空 date 取 latest。

### U3. bridge 集成:recipe-list / run-recipe(只读)+ 能力门控 + orientation

**Goal:** 两个只读 dispatch 命令 + 能力式受限 call + arg 校验 + COMMANDS 登记 + orientation 露出(降级保护)。
**Requirements:** R3, R5, KTD-3/4/5/6/7。
**Dependencies:** U1, U2。
**Files:**
- `scripts/kss_app_bridge.py`(改:`_recipe_list()`;`_run_recipe(name,json_args)`;
  `_make_read_only_call(dispatch)`(碰 WRITE_COMMANDS raise + SystemExit 归一);dispatch 加两分支;
  `COMMANDS` 加 `recipe-list`/`run-recipe`(均 read);`_orientation()` 加 `recipes` 区 + try/except 降级)
- `kss/tests/test_bridge_recipes.py`(新建)
**Approach:** `_recipe_list()` 惰性 import kss_recipes,返回 `[{name,desc,write,args}]`(无 fn 泄漏)。
`_run_recipe(name, json_args)`:查注册表;**自声明 `write=True` → 拒**(`{"error":"write_recipe_deferred",...}`);
空 json→`{}`,解析 + 校验 keys⊆args 且值 str(KTD-5);注入 `_make_read_only_call(dispatch)`;
`fn(restricted_call, **args)`。dispatch:`recipe-list`→`_recipe_list()`;`run-recipe`→`_run_recipe(args[0], args[1] if len>1 else "")`。
**`WRITE_COMMANDS` 不动**(本轮无写命令)。`_orientation()` 加 `recipes=_recipe_list()`,包 try/except 降级。
**Patterns to follow:** #1+#2 的 `_data_catalog()`/`_orientation()` + dispatch 分支 + COMMANDS 登记 + 降级风格。
**Test scenarios:**
- **正向接线(KTD-6)**:`dispatch("recipe-list",[])` 返回 list;`dispatch("run-recipe",["explain_stock_today",json])`
  返回 payload(证明分支真接上,不靠单向漂移守卫)。
- **能力门控(KTD-3,核心安全)**:注入一条 `write=False` 但 fn 内 `call("run",[...])` 的测试剧本 →
  经 run-recipe 调用时受限 call **raise**,写操作不执行。这是「物理不可达」而非「policy 拒」的验证。
- **自声明 write 拒**:`write=True` 剧本经 run-recipe → 拒 + hint。
- **arg 校验(KTD-5)**:多余键 / 非 str 值 / 坏 JSON → fail-loud error,不崩;空 json → `{}` 正常跑零参剧本。
- **orientation 露出 + 降级(KTD-4)**:`dispatch("orientation")` 的 `recipes` 区含种子;模拟 recipes import 错 →
  `recipes` 区降级 error,orientation 其余区仍返回。
- 漂移守卫:recipe-list/run-recipe 都在 `COMMANDS`、标 read。
- 未知剧本:error+hint,不抛。

### U4. MCP 暴露 list_recipes / run_recipe(只读公开)

**Goal:** 剧本注册为公开**只读** MCP 工具。
**Requirements:** R4, R5。
**Dependencies:** U3。
**Files:**
- `scripts/kss_mcp.py`(改:加 `list_recipes`、`run_recipe`,均在 `if _LIVE` 之外)
**Approach:** 仿现有读工具:`@mcp.tool def list_recipes()->dict: return _call("recipe-list")`;
`def run_recipe(name:str, args:str="")->dict: return _call("run-recipe",[name,args])`(args 为 JSON 串)。
**本轮不加 `run_write_recipe`**(write 路径 defer,KTD-3)。验证 server venv(STATE_ROOT/venv)导入 + 注册。
**Patterns to follow:** `kss_mcp.py` 的 `get_data_catalog`/`get_orientation`(只读 _call)。
**Test scenarios:**
- happy:server venv 导入 kss_mcp,`list_recipes`/`run_recipe` 在 `_LIVE=0` 注册可调。
- 真·stdio(可选 smoke):`fastmcp.Client` 调 `run_recipe("explain_stock_today",'{"symbol":"688114.SH"}')`
  返回非空(**只断言 shape/非空,不断言具体数值**——live 数据每日变,KTD 见下)。

---

## Scope Boundaries

### Deferred to Follow-Up Work
- **write 执行路径整体 defer(已与用户确认)**:`run-write-recipe` dispatch 命令、`run_write_recipe` MCP 工具、
  `WRITE_COMMANDS` 新条目、整束 confirm 门控、fixture 门控测试 —— 全部 defer 到 #4。理由:本轮零真实 write 剧本;
  #4 的「人在环内逐次 UI confirm」契约未定,现在建的整束 confirm 可能对不上,且 agent 自设 confirm 与 brainstorm
  承诺冲突。届时按 #4 真实 write 剧本形状 + 人在环内 UI 设计。本轮已留 `write:bool` 字段 + read 路径拒 write 剧本,
  前向兼容。
- **种子 write 剧本** —— 同上,随 #4。
- **`daily_review_brief`(当日全景束)** —— 与 `snapshot`/`#2 orientation` 重叠,频率未证(doc-review F3);
  待实际反复需要时再加,不让「凑数量」驱动。
- **声明式 recipe DSL** —— 明确不做(KTD-1)。

### 不做
- recipe 层不调 LLM(KTD-2 红线)。
- 不引编排引擎/自主 loop(brainstorm 否决)。
- 不给 bridge 引 pandas(服务层 stdlib 红线)。

---

## Risks & Dependencies

- **R-risk-1 能力门控是核心安全不变式**:read 剧本经受限 call,写命令物理不可达(KTD-3)。U3 显式测试覆盖
  「write=False 剧本内串 run → raise」。这取代了原计划易绕过的声明式门控(doc-review Critical)。
- **R-risk-2 透传 LLM 文本被误当真值**:commentary 等自由文本随 sector-rotation 进束(doc-review High)。
  缓解:KTD-2 标 `provenance:"llm_prior"`;不声明整束 U7 安全。
- **R-risk-3 子命令延迟 + 重命令**:`get-discovery-candidates` 实为 4 管线 live 拉取(最重,非缓存文件读,
  doc-review 实证);`explain_stock_today` 调它 → 单次 recipe 可能 fan-out 四路。缓解:种子剧本步数 ≤4;
  recipe 级缓存 defer;#4 多轮调用频率由 #4 处理。本轮接受(交互式单用户)。
- **R-risk-4 循环 import / orientation 拖垮**:KTD-4 惰性 import + orientation try/except 降级。
- **R-risk-5 autonomous loop 是否真用 recipe(doc-review F4,FYI)**:#4 的自主 loop 可能宁可自己组合 primitive
  也不选 recipe → token 节省只对交互式 Claude Code 成立。缓解:R5 经 orientation 露出是 hook;#4 plan 应把
  「loop 是否偏好 recipe」做成显式 eval。本轮不解。
- **依赖**:#1+#2 已 merge(PR #31)—— dispatch/COMMANDS/orientation/MCP 模式就绪。

---

## Success Criteria

- `run_recipe("explain_stock_today",{symbol})` 一次返回足以回答「为什么上榜」的束(该股+当日move / 板块归属 /
  主题龙头 / 发现候选含 reason/score),agent 无追加调用即可叙事(R7);dogfood 一个真实 symbol 确认够用。
- **能力门控**:read 剧本(含误标 write=False 的)碰任何 WRITE_COMMAND → 受限 call raise,写不执行(R-risk-1)。
- recipe 路径全程不触发 LLM;透传的 commentary 等 LLM 文本带 `provenance:"llm_prior"`(KTD-2)。
- 部分失败时顶层 `partial/failedSteps` 置位,未命中与出错可区分(KTD-7);SystemExit 不崩 sidecar。
- arg 非法/坏 JSON fail-loud;空 JSON → 零参剧本正常(KTD-5)。
- 正向接线测试证明 recipe-list/run-recipe 真接上 dispatch(KTD-6);orientation 露出 recipes 且 recipes 模块
  坏时降级不崩。
- MCP `list_recipes`/`run_recipe` 只读公开;本轮无 write MCP 工具。

---

## Sources & Research

- origin 需求:`docs/brainstorms/2026-06-22-kssdeck-agent-panel-requirements.md`(依赖 #3)
- 选题:`docs/ideation/2026-06-22-agent-reasoning-framework-ideation.html`(#3)
- 地基(已 merge PR #31):`docs/plans/2026-06-22-002-feat-mcp-data-catalog-orientation-plan.md`
- 代码模式(本会话第一手 + doc-review feasibility 复核):dispatch 路由 + `COMMANDS`/`WRITE_COMMANDS`
  (`scripts/kss_app_bridge.py`:dispatch ~3221、WRITE_COMMANDS ~3120、_orientation ~3192);`run_task` 路由(~1449);
  `_discovery_merge` 的 ts_code 键(~2344,4 管线 live 拉取);`report` 路径护栏抛 SystemExit(~375);
  commentary LLM 文本(~1957);MCP `@mcp.tool→_call` 只读模式(`scripts/kss_mcp.py`);sidecar arg 强制 str
  (`scripts/kss_sidecar.py` ~34);漂移守卫现状(`kss/tests/test_bridge_orientation.py` ~50)。
- doc-review(2026-06-22,5 persona):coherence 0;feasibility P1×1(漂移守卫单向)+P2×3;
  security Critical×1(声明式门控可绕)+High×3;product P1×1(write 路径 defer)+P2;
  adversarial Critical×1+High×2(U7 过度声明 / 部分失败半束)+Medium×2。write 路径经用户确认 defer;
  其余 P1/P2 均已并入本版(能力门控、U7 收敛、partial 信号、arg 校验、SystemExit 归一、正向接线、orientation 降级)。
