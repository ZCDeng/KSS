---
type: feat
origin: docs/ideation/2026-06-21-kssdeck-standalone-packaging-ideation.html
date: 2026-06-21
reviewed: 2026-06-21 (ce-doc-review round 1：郭嘉补强 contradiction-finder focus；6 persona；首启 bootstrap + U6/U7 后置 已采纳)
status: shipped (11 单元全合 main；U6a kss-mcp 注册 2026-06-22 重指新 server + 旧 datasette pilot 弃用)
target: 仅作者自己 2-3 台 Mac 同步使用；Apple Developer ID 已有；三台机器均已装 uv
---

# KSSDeck 独立化打包：可分发 + 可进化 + AI-native

把 KSSDesktop 从「必须活在 clone 仓库里的瘦前端」改造成一个可放进 `/Applications`、能在作者自己 2-3 台 Mac 间同步运行、且可被 agent 驱动的独立 app —— **不牺牲「改 Python 不用重编译」的快迭代 DX**。

源自 ce-ideate 的 7 条 survivor，经 7 份并行 brainstorm + ce-doc-review（6 persona）收敛。

**Review round 1 关键转向**：Python 运行时由「vendor 进签名 bundle + 逐 dylib 签名」改为 **首启 uv bootstrap 到 state root**。四个 reviewer 独立指向同一处 —— 此改一刀溶掉：U8↔U9「uv sync 往哪写」的破签名矛盾、R1 的 lightgbm/pyarrow dylib 重定位风险、U8 逐 dylib 签名循环、`disable-library-validation` 熵。.app 从数百 MB 降回 ~8MB。代价：首启需联网 + uv（三台机器均具备）。

## Problem Frame

当前架构（grounding + repo 实证）：

- `BridgeClient.swift:108` 硬编码 `/usr/bin/python3`；`findProjectRoot()`（`:143-168`，注意 `KSS_PROJECT_ROOT` 已是 candidate[0]，但仍向上爬 8 层）；`kss_app_bridge.py` 用 `Path(__file__).resolve().parents[1]` 当 `PROJECT_ROOT`。
- **全仓 60 个 Python 文件**各自 `Path(__file__).resolve().parents[1]` 算 root，仅 6 个 import 集中式 `kss/config/paths`；`_run_process_task`（`kss_app_bridge.py:567`）派生 ~10 个子脚本（sector_review / update_cs_data / refresh_hotspot_rotation / backtest_etf_radar …），每个各算 root + 写 `storage/`；bridge 模块级绑了 25+ 个 `PROJECT_ROOT / "storage" / ...` 常量（import 时冻结）；`_run_process_task` 还把 `.cache/MPLCONFIGDIR/XDG_CACHE_HOME/HOME` 指向 `PROJECT_ROOT`。**这些全是 state-root 切分的实际面，远超「改 paths.py」**。
- 每次桥调用 fork 一个 python3、重 import pandas，~80KB snapshot 逼近 64KB pipe buffer（`:119-128` 双管道绕行）。
- bridge JSON 无版本号；`HotspotLeaderStock`（`KSSModels.swift:25-62`）手缝双 schema 兜底解码器，**两个 emit site**：摘要卡 `topLeaders`=symbol/appearances、归档全快照 `leaderBoards[].leaderStocks`=raw code/count。
- `build_and_run.sh` 用 `swift build`（SwiftPM/CLT），出 ~8MB 无签名 .app；Python 运行时假定本机存在。
- **无 `pyproject.toml`/`uv.lock`**；依赖现散在 `.venv-desktop`（pyarrow 手装）。
- commentary 是 LLM 写死的静态 .md；`<kss-number>` hydration 槽**实测不存在**（grep Sources/ 零命中）。
- **已存在未追踪的 `datasette/plugins/kss_mcp/`**（fastmcp pilot，console-script `kss-mcp`，`fastmcp==3.3.1`）—— U6 不能当无主新建。

**核心张力**：天真打包会反转「改 Python 不重编」。本计划用 dev-mode / bundle-mode 双模式 + state-root venv 同时拿到「可分发」与「可进化」。

## Scope Boundaries

**In scope（本期，scoped to 自用多机 + Developer ID 已有）**
- Phase 0 地基：依赖闭包捕获（U0）、路径与双模式解析（U1）、网络凭据（U3）
- Phase 1 运行时+契约+进程：首启 bootstrap（U2）、版本化桥协议（U4）、常驻 sidecar（U5）
- Phase 2 AI-native（**不阻塞打包核心，可后置**）：kss-mcp（U6a/U6b）、结构化 commentary（U7）
- Phase 3 分发+进化：.app 签名（U8）、Python 层独立更新（U9）

**Deferred（记录，本期不做）**
- 完整公证（`notarytool`+staple）/ GHA full-Xcode CI / 完整 Sparkle appcast：自用机器 Developer ID 签名 + iCloud 同步够。
- commentary 的 (b) sqlite 追问线程 / (c) 决策账本 eval 回路：U7 (a) 跑稳 2-3 周后 follow-on。
- 迁移向导 UI、多用户隔离、后台静默 uv sync。

**Out of scope（明确非目标）**
- App Sandbox 沙箱化（破坏 `Process()` 拉子进程，Developer ID 分发不需要）。
- Windows / Linux。server/SaaS/多用户。

## Key Technical Decisions

**KTD1 · dev/bundle 是双轴，不是干净二元。** 解析 = (解释器: 系统/`.venv-desktop` · state-root bootstrap venv) ×(脚本目录: `KSS_PROJECT_ROOT/scripts`(dev) · `KSS_SCRIPTS_ROOT`(可选同步目录) · `Bundle/Resources/scripts`(签名 baseline 兜底))。**bundle-mode + override 是多机 target 的主稳态，不是边缘情况**（与 KTD7 合读，别被「一个分支」措辞掩盖 R5 的优先级复杂度）。

**KTD2 · `KSS_STATE_ROOT = ~/Library/Application Support/KSS/`。** 持有：sqlite/parquet/.md/logs/`.cache`/**bootstrap venv**。bundle = 不可变签名代码；state root = 全部可变数据 + 可变 Python 运行时。

**KTD3 · 版本化信封 `{"schemaVersion": N, "data": <payload>}`，单 int，同 commit 同改。** 保留信封 + Swift 两段解码（真 bug fix：退役双 decoder、给可读错误）。**简化 negotiator**：不做 grace 期 / schemaTooOld 迁移分支（单开发者两侧同 commit，desync 不存在）—— 不匹配即 log + 横幅、不尝试解码。enforcer 是同 commit 纪律，非运行时协商。

**KTD4 · sidecar = 薄 asyncio wrapper，import 现有 handler。** 前置硬要求（见 U1）：bridge 模块级路径常量先改成 **env 解析的惰性 accessor**，否则 in-process import（sidecar/MCP）会冻结到 bundle 路径、与 subprocess 回退解析不一致。Unix socket（state root，`0700`），newline-JSON，`id` 关联。SMAppService 优先 / LaunchAgent 兜底（**注意：SMAppService 大概率需签名 bundle → U8 前只有 LaunchAgent 路径可跑**）/ socket 3s 回退 subprocess。

**KTD5 · MCP 与 Swift socket 共驻同一 sidecar；威胁模型显式记录。** 读命令直接成 tool；写/run/cron-mutation paper-only。**`KSS_MCP_LIVE` 仅 daemon 启动时读一次（不 per-call 重读，防 agent 中途翻转）**；`confirm:true` 单独不够 → 文档须明记「同用户的被注入 agent 经 stdio 可触发写命令」这一边界，或加 Swift app 生成的一次性 token（确认意图来自人不来自 agent）。本地 stdio，限定 caller（作者自启的 Claude Code）。

**KTD6 · 签名 = 只签 .app（Developer ID + hardened runtime）。** bootstrap 后 Python 解释器 + 依赖在 **state root 之外、作为子进程运行**，不进签名 bundle —— 故**无逐 dylib 签名循环、无 `disable-library-validation`**（Gatekeeper 只查 .app 启动，不查它派生的子进程）。`notarytool`+staple = deferred-but-ready commented slot。

**KTD7 · 脚本三层解析 + 诚实威胁模型。** 优先级 = `$KSS_PROJECT_ROOT/scripts`(dev) > `$KSS_SCRIPTS_ROOT`(可选同步目录) > `Bundle/Resources/scripts`(签名 baseline)。改签名内 baseline 破签名 → **实时编辑永远走 override**。**签名只买到 Gatekeeper 启动 + 签名 Swift 二进制，不保证被执行 Python 的完整性**（override 目录是无 OS 完整性边界的代码执行面，且「Python 层完整性校验」本期 deferred）。缓解二选一：(a) `$KSS_SCRIPTS_ROOT` 用**非 iCloud 本地目录** + 手动 `git pull`/rsync；(b) 维护 Developer-ID 私钥签的 SHA-256 manifest，启动校验 override 目录后再执行（~50 行）。**默认取 (a)**，避免 iCloud 远程可写面默认开启。三个版本号互不相同：`schemaVersion`(桥协议,KTD3) · `CFBundleShortVersionString`(Swift) · `scripts/VERSION`(Python 层)；后两个并列在 About。

**KTD8 · 幻觉防线结构化。** commentary signals `value: null` 由 Pydantic `Literal[None]` 强约束；真值 `true_value` 由 LLM 之后的 Python 代码步从 parquet/CSV 填。

## Implementation Units

### U0 · 依赖闭包捕获（bootstrap 前置）
- **Goal**：把现 `.venv-desktop` 的依赖闭包落成可复现 uv 项目。
- **Files**：新增根 `pyproject.toml`（pin pandas/lightgbm/tushare/akshare/pyarrow + 传递依赖，Python 3.12）；生成 `uv.lock`。
- **Approach**：以 bridge `_python_candidates()` 当前实际命中的解释器/包集为基准建 lock；后续 U2/U9 全部 `uv sync --frozen` 依赖它。
- **Verification**：`uv sync --frozen` 在干净临时目录建出能 `import pandas,lightgbm,tushare,pyarrow` 的 env。
- **Dependencies**：无（U2/U9 的硬前置）。

### U1 · 路径与双模式解析重构（地基，已扩 scope）
- **Goal**：消除「app 必须活在 clone 里」；把**全部**可变写入引到 state root；解释器/脚本路径参数化。
- **Files**：`BridgeClient.swift`（`:108` 解释器、`:143-168` 把 `KSS_PROJECT_ROOT` 从 candidate[0] 提为硬分支 + 删 8 层爬升 + cwd/bundle fallback）；`kss_app_bridge.py`（① 模块级 25+ 路径常量 → **env 解析惰性 accessor**；② `_run_process_task` 把 `KSS_STATE_ROOT`/`KSS_PROJECT_ROOT` 注入子进程 `env`（已有 `env=os.environ.copy()`，一行）+ `.cache/MPLCONFIGDIR/XDG_CACHE_HOME/HOME` 指向 state root）；**~10 个派生子脚本** + `kss/config/paths` 的 `storage/` 写入统一走 `state_root()` helper；首启写 `~/Library/Application Support/KSS/breadcrumb.json` 的 Swift helper。
- **Approach**：先**全量 grep 盘点**（`PROJECT_ROOT`/`parents[`/`storage/`/`.cache`/`MPLCONFIGDIR`），产出「写 state vs 只读」清单（feasibility 要求显式枚举，不留到实现期）。env 有 `KSS_PROJECT_ROOT` 走 dev，否则读 breadcrumb/bundle。
- **Execution note**：characterization-first —— 先抓 snapshot/stock/sector-rotation + 一个 `run` 任务的实际输出/落盘位置做基线，再改，确保零漂移。
- **Test scenarios**：dev-mode 改 .py 立即生效；bundle-mode（breadcrumb 指临时 state root）snapshot + 一个 `run` 任务的所有写入都落 state root、零写进只读 bundle；惰性 accessor 在 in-process import 与 subprocess 两路解析一致。
- **Verification**：`swift build`；dev-mode snapshot 逐字对基线；模拟 bundle-mode 跑一个 `run` 任务确认 `.cache/MPLCONFIGDIR` 落 state root。
- **Dependencies**：无（脊柱根；U2/U5/U6/U8/U9 全依赖惰性 accessor + state 切分）。

### U2 · 首启 uv bootstrap（取代 vendoring）
- **Goal**：`/usr/bin/python3` 依赖清零；运行时可复现；**不进签名 bundle**。
- **Files**：BridgeClient 首启检查 `KSS_STATE_ROOT/venv/bin/python3`，缺则跑 `uv sync --frozen --project <scripts-root> --python 3.12` provision 进 state root + 进度 sheet；`KSS_PYTHON` 指向该 venv（bundle-mode）。
- **Approach**：`uv` 从 PATH 找，缺则 bundle 一个 `uv` 静态二进制兜底（决策见开放问题）。lock 来自 U0。**无 dylib 重定位 / install_name_tool / 逐 dylib 签名**（env 在 state root，非签名 bundle 内）。
- **Test scenarios**：干净第二台机首启 → provision 出可用 venv，snapshot 退出 0；venv 已存在且 lock 指纹一致 → 跳过 provision 秒起；断网且无 venv → 命名错误 + 引导（非静默）。
- **Verification**：第二台实机首启走查；`KSS_STATE_ROOT/venv/bin/python3 -c "import pandas,lightgbm,tushare,pyarrow"` 退出 0。
- **Dependencies**：U0（lock）+ U1（路径/解释器参数化）。

### U3 · 网络凭据 + 配置表面化
- **Goal**：Tushare key / Telegram token / 代理可配；缺凭据大声降级；**秘密走 Keychain**。
- **Files**：**Tushare token + Telegram token/secret 存 macOS Keychain**（SecItem）；`network.env` 只留非敏感（proxy URL/超时），且**置于 state root（非 iCloud Mobile Documents 路径）**；Settings/Network 面板（SwiftUI）；`kss/notifications/*`、Tushare 客户端读 Keychain override。
- **Approach**：缺凭据返回命名错误 + app 退回 last-known snapshot + 「数据 N 天前」横幅。
- **Test scenarios**：删 token → 命名错误 + 横幅不崩；Keychain 取数；第二台无透明代理 → 面板改 `TELEGRAM_API_URL`。
- **Dependencies**：U1（state root + 配置位置）。

### U4 · 版本化桥协议（+ leaderStocks 统一）
- **Goal**：schema drift 可路由；退役双 decoder。
- **Files**：`kss_app_bridge.py`（`_versioned_dump` 包裹所有 `_json_dump` 站点，**含 cron-catchup/rerun-many 顶层数组**；**统一归档全快照 `leaderBoards[].leaderStocks` emitter 为 `symbol/name/appearances`**）；`KSSModels.swift:25-62`（删双 decoder）；`KSSStore`（`IncompatibleBridgeView` 横幅）；**已归档 `storage/etf_radar/*.json` 的 code/count → normalize-on-read shim 或一次性迁移**。
- **Approach**：`{"schemaVersion":1,"data":...}`；Swift 先解 envelope 再解 `data as T`；不匹配 → 横幅，不解码（KTD3 简化）。
- **Execution note**：test-first —— 先写 envelope 解码 + 不匹配横幅的单测；**删兜底前先确认两个 emit site 都已归一 + 历史 JSON 有读侧兜底**。
- **Test scenarios**：matched 解码；不匹配 → 横幅不崩；双 decoder 删除后摘要卡与归档全快照 leaderStocks 均正常、历史 JSON 不 keyNotFound。
- **Dependencies**：可与 U1 并行起步（纯契约，不依赖打包）；须早于 U5/U6/U9。

### U5 · 常驻 Python sidecar（Unix socket）
- **Goal**：subprocess-per-call → 常驻 daemon；杀延迟 + 管道死锁；为 MCP 备基座。
- **Files**：新增 `scripts/kss_sidecar.py`（asyncio，import U1 惰性化后的 handler）；`BridgeClient.swift`（socket 路由 + subprocess 回退）；`com.zcdeng.kss.sidecar.plist`。
- **Approach**：承载 U4 信封；SMAppService 优先 / LaunchAgent 兜底（U8 签名前实际只跑 LaunchAgent）；退出发 `shutdown`；改 Python 经 `SIGHUP` 显式重载（不引 watchdog 依赖）；socket 3s 回退 subprocess。
- **Test scenarios**：暖 daemon round-trip ≤80ms；80KB snapshot 无管道限制；`kill -9` 后经 LaunchAgent 重启成功；socket 缺失回退 subprocess。
- **Dependencies**：U1（惰性 accessor 是 in-process import 前提）+ U4（信封即 wire format）。

### U6a · kss-mcp（dev-mode，系统 Python 上线）
- **Goal**：尽早把 agent 可驱动能力上线，不等打包链。
- **Files**：**先审 `datasette/plugins/kss_mcp/` 现有 fastmcp pilot**（server.py + test_server.py + `fastmcp==3.3.1`）→ 决定 supersede/extend、**解 `kss-mcp` console-script 撞名**、commit 或显式弃用该未追踪 pilot；在 sidecar 内挂 MCP（系统/`.venv-desktop` Python）。
- **Approach**：读命令成 tool（byte-equal UI 同款 JSON）；写命令 live-gated（KTD5：启动读一次 flag + confirm + 复用 run 白名单 + 威胁模型文档）。本地 stdio。
- **Test scenarios**：Claude Code 挂 kss-mcp 读一条 sector-rotation = UI 同款；无 flag 调 `run` → 拦截零副作用；现有 pilot 撞名已解。
- **Dependencies**：U5（共驻 daemon）。**不依赖 U2** —— 这是 product-lens 的早上线路径。

### U6b · MCP 进 bootstrap venv
- **Goal**：MCP SDK 纳入可复现运行时。
- **Files**：MCP SDK（沿用 pilot 的 `fastmcp` 版本或显式换）进 `pyproject.toml`/`uv.lock`；bundle-mode 经 bootstrap venv 跑 MCP。
- **Dependencies**：U6a + U2 + U0。

### U7 · 结构化 commentary（Phase 2，非阻塞）
- **Goal**：幻觉防线从 prompt 自律变类型约束。
- **Files**：`kss/sector/commentary.py`/`scripts/sector_review.py`（出 `YYYYMMDD.commentary.json`）；Pydantic `CommentarySignal(value: Literal[None])`；代码填 `true_value`；bridge 反序列化。**渲染**：`<kss-number>` 槽**实测不存在** → 二选一：(i) 本单元内建该 hydration 槽（定义 `<kss-number key>` markup + JS 从 true_value map 填 + null→「—」）；或 (ii) **Python 侧直接把真值填进 `narrative_html`**（server-side），免新前端机制、最省（product-lens 建议）。
- **Test scenarios**：LLM 输出无非空数值（Pydantic 校验）；UI 显示 ETF 净流入 % 与 parquet 源值 2 位小数一致；null → 「—」。
- **Dependencies**：复用 U4 schemaVersion 模式，否则独立，**可与 U1-U6 并行；不阻塞打包核心**。

### U8 · .app Developer ID 签名（最小）
- **Goal**：嵌入子进程的 .app 在第二/三台自用机无 Gatekeeper 硬阻。
- **Files**：新增 `script/sign_and_build.sh`、`script/KSSDesktop.entitlements`（hardened runtime；**无 `disable-library-validation`**）。
- **Approach**：`swift build` → `codesign --options runtime --sign 'Developer ID Application: …'` 签 .app（**无 python-env 逐 dylib 循环** —— 运行时在 state root 外，作为子进程跑）。`security find-identity` 缺证书大声失败。`notarytool`+staple 留 commented slot。
- **Test scenarios**：签名 .app AirDrop 第二台 → 至多一次「无法验证」可点 Open 无硬阻；`codesign --verify --strict` 退出 0。
- **Dependencies**：U1（bundle 布局）。**不再依赖 U2**（运行时已不在 bundle 内）。

### U9 · Python 层独立更新（双版本号，非阻塞）
- **Goal**：保住「改 Python 不重编」于打包后；两层版本独立；跨机同步无服务器。
- **Files**：BridgeClient 脚本三层解析（KTD7）；启动 `uv sync` **指向 state-root venv**（非 bundle，矛盾已解）；`scripts/VERSION`；About 并列 Swift + scripts 两版本号。
- **Approach**：**非阻塞**：lock 指纹一致跳过 sync；不一致则带 10s 超时尝试，超时/断网 → 用现有 venv + 「依赖未同步·上次 N 天前」横幅；**仅当无可用 venv 才阻断**（read-only 看数据不该被 sync 卡死）。依赖变化后 `SIGHUP` 重载 daemon（U5）。
- **Test scenarios**：bundle-mode 改 override 目录一条桥命令 → 无 Swift 重编下次命中；断网仍能起（横幅）；lock 经同步到第二台 → 下次启动对齐；Swift 与 scripts 版本号 About 独立显示。
- **Dependencies**：U0/U1/U2（state-root venv）+ U4（版本语义）+ U5（重载）。

## Sequencing

```
Phase 0 地基   U0 依赖闭包 ── U1 路径/state(扩) ──┬── U3 凭据
                                                  │
Phase 1 运行时 U2 首启bootstrap(需U0,U1) ─────────┤
        +契约  U4 版本协议(可与U1并行) ───────────┼── U5 sidecar(需U1,U4)
                                                  │
Phase 2 AI     U6a MCP系统Python(需U5,早上线) ── U6b(需U6a,U2) · U7(并行,独立)
        非阻塞                                    │
Phase 3 分发   U8 签.app(需U1) ───────────────────┴── U9 独立更新(需U0,U1,U2,U4,U5)
```
- U0 → U1 最先。U4 可与 U1 并行。
- **U6a 早上线**（只需 U5，不等 bootstrap）—— 把最高杠杆的 AI-native 价值提前验证（product-lens）。
- U6b/U7 后置、不阻塞 U8/U9 打包核心。

## Risks & Dependencies

- **R1（中，已大幅降级）· 首启 uv/网络可用性**：bootstrap 改造后，原 lightgbm/pyarrow dylib 重定位风险消失；新风险 = 干净机首启需联网 + uv。缓解：U2 缺 venv/断网 → 命名错误 + 引导（非静默）；可选 bundle 一个 `uv` 静态二进制兜底 PATH 缺失。
- **R2 · SMAppService 需签名 bundle**：U8 前只有 LaunchAgent 路径可跑 → U5 的 daemon 生命周期在签名落地前只在兜底路径验证。
- **R3 · CLT vs full Xcode**：U8 签名需完整 Xcode + Developer ID 在 Keychain；本机当前 CLT 跑不了 codesign。
- **R4 · paths/写入面盘点完整性**：U1 须显式枚举 ~10 子脚本 + 60 文件 + cache/HOME 重定向的「写 state vs 只读」清单，不留到实现期（feasibility P0）。
- **R5 · KTD7 三层解析优先级**：实现错 → 破签名或丢 no-recompile；须显式优先级测试。
- **R6（新）· `$KSS_SCRIPTS_ROOT` 完整性 / iCloud 供应链**：override 目录是无完整性边界的代码执行面。默认取非 iCloud 本地 + 手动同步（KTD7-a）；若改用 iCloud 须上 manifest 校验（KTD7-b）。
- **R7 · MCP 信任边界**：env flag + confirm 非人确认；KTD5 要求启动读一次 + 文档化威胁模型 + 限定 caller。

## Deferred to Implementation

- bootstrap 是否 bundle 一个 `uv` 静态二进制兜底 PATH 缺失（U2）。
- `_versioned_dump` 裹顶层数组的具体形态（U4）。
- sidecar 重载 `SIGHUP` vs 显式 reload 命令（U5）。
- U7 渲染走 `<kss-number>` 新槽 vs server-side 填 `narrative_html`（U7，倾向后者最省）。
- `$KSS_SCRIPTS_ROOT` 默认本地目录的确切约定（U9，默认非 iCloud）。
- MCP 是否加 Swift app 生成的一次性 token 做人确认（U6a，视威胁容忍度）。
