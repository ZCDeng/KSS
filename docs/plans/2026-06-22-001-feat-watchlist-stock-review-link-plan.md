---
type: feat
date: 2026-06-22
status: planned
reviewed: 2026-06-22 (ce-doc-review 4 persona + 郭嘉补强；产物粒度/U1/情形段三决定已采纳)
related: docs/brainstorms/2026-06-21-fix-daily-review-forecast-requirements.md
target: 自用多机；保住「改 Python 不重编 Swift」DX
---

# 打通「股票池加自选 → 个股复盘覆盖所有自选股」链路

把"加自选"接通到个股复盘：加自选即时生成**那只股票**的复盘，按股归档，
个股复盘列表逐股累积、不互相覆盖；新股先补历史，次新股跳过不可靠的情形段，
个股复盘不再推 Telegram。

**Review round 1 三处转向**（ce-doc-review 4 persona 一致指向，已采纳）：
① 产物**按股归档** `{date}_{symbol}.md`（原按日单文件会让即时单只生成互相覆盖、特性反掉）；
② **砍掉 watchlist.json 落盘**（即时路径 Swift 直传 symbol，本期无 cron 读者，落盘是投机基建）；
③ 次新股 **n<阈值跳过情形段**（补历史救不了 n<20，渲染权威实则垃圾的分布是产品不诚信）。

## Problem Frame

实测链路断在二、三环（诊断见会话）：

- **环 1（通）** `StockBrowserView`「加自选」按钮（`:194`）→ `ContentView.toggleWatchlist`（`:262`）→ `@AppStorage("watchlistSymbols")`，默认 `"688017.SH,688322.SH"`。
- **环 2/3（断）** ① `watchlistSymbols` 在 `ContentView` / `StockBrowserView` / `KSSModels` 之外零引用，从不进任何桥命令。② 个股复盘列表 = `snapshot.reviews` = bridge `_reviews()`（`kss_app_bridge.py:278`，读 `STATE_ROOT/storage/daily_review/*.md`），这些 .md 由 `scripts/daily_review_322_017.py` 生成，该脚本 `:46-48` 硬编码 `STOCKS = [('688322','奥比中光','alpha'),('688017','绿的谐波','alpha')]`；`focusSymbols`（`:292`）从 .md 用正则反解。
- **假象根因** watchlist 默认值恰好等于脚本写死的两只，撞一块显得像联动；加第三只票个股复盘无变化。

**已勘的真实约束（reviewer 实证，构成本计划的硬骨头）**：

- **归档按日单文件**：`daily_review_322_017.py:886` `archive_path = .../f'{today_str}.md'`，`:892` `write_text` 整文件覆盖，`:888` body = 当次传入全部 STOCKS。`_reviews():288` 按 `path.stem`=日期 解析。→ 即时单只生成会覆盖当日产物、丢其他自选股（致命，决定①解决）。
- **按日归档是 `validate_predictions.py` 的底稿**（`daily_review_322_017.py:882-883` 注释）。改命名须保其发现逻辑不断（U3）。
- **`.SH` 写死 3 处**：`daily_review:124/125/181` `pro.daily/daily_basic/moneyflow(ts_code=f'{sym}.SH')`。任意 SZ/BJ 自选股会抓错标的（U1）。
- **`cs_data_<sym>.csv` 缺则 `FileNotFoundError`**（`daily_review:115` 附近）；且 `update_cs_data.update_one:64` **先读已存在 csv**、按 `max(trade_date)` 增量，**建不了新股**，无"上市日→今日"全量路径 —— 回填是净新增代码（U2）。
- `name` 不在 cs_data 列（`update_cs_data:42-46` 无 name），渲染标题需 `name`（`daily_review:769`）→ 须 `pro.stock_basic` 取（U1）。
- `KSSTask.arguments`（`KSSModels.swift:624-631`）是静态数组，传动态 symbol 须改 enum 或绕过（U5）。
- 同脚本的**情形分布预测段**正被单独修校准（见 related brainstorm，其 Scope 明确「STOCKS 列表不变」排除本工作）。

## Scope Boundaries

**在范围内**
- `daily_review` 去写死、接 `--symbols`、默认 category、交易所后缀贯通、`name` 经 stock_basic、`--channel console` 静音、n<阈值跳情形段
- 产物按股归档 `{date}_{symbol}.md` + `_reviews()` 按股解析 + `validate_predictions.py` 发现逻辑兼容
- 新股 cs_data 全量回填（净新增）
- bridge 即时单只 run-task + Swift 加自选直传触发

**不在范围内**
- 情形分布段的校准逻辑（区间宽化 / regime / 撤段）—— 属 related brainstorm
- watchlist 落盘 + cron `formal-daily-review` 切读 watchlist（本期即时路径不需要；要做时另起 PR）
- `kss/prediction/daily_forecast.py` ML 流水线
- watchlist 板块/概念分组、批量导入 UI、per-symbol category 持久化

**有意非目标**
- 投资决策建议（红线不变）

## Key Technical Decisions

**KTD1 · 即时路径不落盘 watchlist，Swift 直传 symbol（review 决定②）。** 原计划的 `watchlist.json` 跨进程契约本期无 in-scope 读者（即时路径有 symbol 在手、cron 切换 deferred）→ 砍掉。`toggleWatchlist` 的"加入"分支直接 `bridge.run(["run","daily-review-symbol","--symbols",code])`。消掉了双状态漂移与多机同步风险。watchlist 仍只活在 `@AppStorage`（UI 态，app 重启自存）。

**KTD2 · 杀掉「假象」靠去静默默认，不靠 fallback（review 决定，回应"完全替代"质疑）。** `daily_review` 删 `STOCKS` 常量；**缺 `--symbols` → 大声报错，无静默回退 322/017**。cron `formal-daily-review` 改为**显式传** `--symbols 688322.SH,688017.SH`（那两只的唯一存活处，是 cron task 定义、非脚本隐藏默认）。即时路径永远显式传 symbol → watchlist 联动不再有"空了就退回写死两只"的假象。任意自选股**默认 category = `alpha`**（保守：大单流出当反指，不激进判出货）。

**KTD3 · 产物按股归档 `{date}_{symbol}.md`（review 决定①）。** `daily_review` 归档名从 `{date}.md` 改 `{date}_{symbol}.md`（单只 run 写单股文件，不碰其他股当日产物）。`_reviews()` 改：按股一条，`symbol` 从文件名解析（`date` 不再等于 `path.stem`，需从文件名前段取日期）。**`validate_predictions.py` 兼容**：确认其底稿发现逻辑（glob 模式 / 文件名解析）在新命名下仍工作 —— 要么它也按 `{date}_{symbol}.md` 发现，要么 cron 批保留并行的按日产物供它读（U3 抉择）。

**KTD4 · 加自选即时单只 + 静音 Telegram（用户定）。** "加入"分支触发 `daily-review-symbol`（单只）；取消自选不触发。run-task 固定 `--channel console`（`daily_review:906-907` 仅 telegram/all 才发，console 已 no-op）。复用 `_run_process_task` + `run` 白名单（已含 `WRITE_COMMANDS:3036`）。

**KTD5 · 新股补历史解崩溃，次新股跳情形段保诚实（用户定 + review 决定③）。** `ensure_history(code)`：cs_data 缺失 → `pro.stock_basic` 查上市日 → 全量 range-fetch 写 `cs_data_<code>.csv`（净新增，非复用 `update_one`）；存在则 no-op。**情形段渲染加 `n>=阈值` 门**：次新股 n<阈值时不渲染情形段，只出关键位+3 口径均值+操作建议（避免给次新股看权威实则垃圾的分布）。阈值复用脚本现有 n=20 口径。补历史只解 `FileNotFoundError`，不声称修好情形段。

**KTD6 · 交易所后缀全程贯通。** `--symbols` 携带后缀（`688x.SH`/`300x.SZ`/`920x.BJ`）；`daily_review` 内 `:124/125/181` 三处 `f'{sym}.SH'` 改用解析出的完整 `ts_code`；`ensure_history` 同样按后缀拉取与命名。

## Implementation Units

### U1. daily_review 脚本参数化 + 改名

- **Goal**：删写死 STOCKS、接 `--symbols`、默认 category、后缀贯通、`name` 经 stock_basic、`--channel console`、n<阈值跳情形段、按股归档。
- **Dependencies**：无。
- **Files**：`scripts/daily_review_322_017.py` → `git mv` `scripts/daily_review.py`；更新引用 `scripts/kss_app_bridge.py:1205`（`_run_formal_daily_review` 脚本路径 + 改为显式传 `--symbols 688322.SH,688017.SH`）、cron/launchd plist、docs；测试 `kss/tests/test_daily_review_symbols.py`。
- **Approach**：① `STOCKS` 删除，`--symbols a.SH,b.SZ` 解析为 `[(code, suffix, name, category)]`；缺 `--symbols` 大声报错（KTD2）。② `name` 经 `pro.stock_basic` 取，失败回退 `(code)` 标题不阻断。③ category 缺省 `alpha`。④ `:124/125/181` 三处 `.SH` 改完整 ts_code（KTD6）。⑤ 归档名 `{date}_{symbol}.md`（KTD3）。⑥ 情形段渲染加 `if n>=阈值` 门（KTD5）。⑦ 确认 `--channel` 默认 `console`。保留 `--date`/`--dry-run`。
- **Execution note**：characterization-first —— 改名前用旧脚本以 322/017 跑基线；改名后新脚本同入参重跑（除归档名与情形段门控的预期差异外）对比零漂移。
- **Patterns to follow**：脚本 `argparse:831`、`CATEGORY_LABEL`、`send_to_channels:906`。
- **Test scenarios**：`--symbols 688322.SH` 单只产物落 `{date}_688322.SH.md`；`--symbols 300x.SZ` 走 `.SZ` 抓取（非 `.SH`）；`920x.BJ` 后缀正确；缺 `--symbols` 报错非静默；未知 category→alpha；n<阈值股不渲染情形段、关键位仍在；n>=阈值股情形段照常；`--channel console` 零 Telegram 发送；stock_basic 失败 → `(code)` 标题不崩。
- **Verification**：`python scripts/daily_review.py --symbols 688322.SH --channel console --dry-run` 产出关键位/均值与基线一致；旧文件名 grep 无残留引用。

### U2. 新股历史回填 ensure_history（净新增）

- **Goal**：自选股 cs_data 缺失时按上市日全量拉取，避免 `daily_review` 崩。
- **Dependencies**：无。
- **Files**：`scripts/update_cs_data.py`（新增可对单 code 全量回填的入口，**不复用** `update_one` 的增量假设）或 `daily_review.py` 内 `ensure_history`；测试 `kss/tests/test_ensure_history.py`。
- **Approach**：`ensure_history(code, suffix)`：`cs_data_<code>.csv` 存在 → no-op；缺失 → `pro.stock_basic(ts_code)` 取 `list_date` → range-fetch `pro.daily`+`pro.daily_basic`（上市日→今日，按 `--throttle` 限流）→ 合并去重 → **原子写**（`.tmp` 后 rename，防半截 csv 被后续误判"存在"）。后缀（SH/SZ/BJ）按入参，不沿用 `update_one:60-62` 只认 300/301 的判定。
- **Test scenarios**：不存在 code → 建 csv、行数>0、列齐（`update_cs_data:42-46` EXPECTED_COLS）；已存在 → no-op 不破坏增量；BJ/SZ 后缀建名正确；range-fetch 跨多页拉全；写中断 → 无半截 csv（原子）；非法 code → 命名错误非静默。
- **Verification**：删一个 cs_data 后 `ensure_history` 重建，`daily_review --symbols` 对该股不再崩；次新股回填行数 = 上市以来交易日数。

### U3. _reviews() 按股解析 + validate_predictions 兼容

- **Goal**：`_reviews()` 适配 `{date}_{symbol}.md`、按股一条；保 `validate_predictions.py` 底稿发现不断。
- **Dependencies**：U1（产物命名）。
- **Files**：`scripts/kss_app_bridge.py:278-294`（`_reviews()` 解析）；`scripts/validate_predictions.py`（核对/调整底稿发现逻辑）；测试 `kss/tests/test_reviews_per_symbol.py`。
- **Approach**：`_reviews()` 从文件名 `{date}_{symbol}.md` 解出 `date` 与 `symbol`（不再 `path.stem`=date）；每文件一条，`focusSymbols` 取文件名 symbol（确定性，替代正则反解）。**先读 `validate_predictions.py` 的底稿发现**：若它 glob `{date}.md`，二选一 —— (a) 它改按 `{date}_{symbol}.md` 发现；(b) cron 批仍写并行 `{date}.md` 供它读，即时单股只写 `{date}_{symbol}.md`。倾向 (a)，避免双写。
- **Execution note**：characterization-first —— 先抓 `validate_predictions` 当前对历史 `{date}.md` 的发现行为做基线，改后对齐。
- **Test scenarios**：目录含 `20260622_688322.SH.md`+`20260622_688017.SH.md` → `_reviews()` 返两条、symbol 准、date 准；旧 `{date}.md` 历史文件仍可读不崩（过渡兼容）；`validate_predictions` 在新命名下仍发现底稿、打分不空。
- **Verification**：`snapshot.reviews` 对多股各一条；`python scripts/validate_predictions.py` 在新产物上跑通退出 0。

### U4. bridge 即时单只复盘 run-task

- **Goal**：新增 `daily-review-symbol`：补历史 → 生成单股复盘（静音、按股归档）。
- **Dependencies**：U1、U2、U3。
- **Files**：`scripts/kss_app_bridge.py`（`_run_daily_review_symbol(args)` + `run_task:1369` 分发表注册；`run` 已在 `WRITE_COMMANDS:3036`）；测试 `kss/tests/test_bridge_daily_review_symbol.py`。
- **Approach**：handler 从 `_parse_args` dict 取 `args["symbols"]`（确认 `_parse_args:712` 保留该 token），自建子进程 `command` 列表（仿 `_run_formal_daily_review:1203`）：先 `ensure_history` → `daily_review.py --symbols <one> --channel console`。**timeout 显式调大**（回填可能超 `_run_process_task:606` 默认 300s）或把 `ensure_history` 拆成前置独立 step。**写竞争**：快速连加多股 → 串行化该 task（队列/锁），避免并发回填+生成互踩。
- **Patterns to follow**：`_run_formal_daily_review:1183`、`_run_process_task:600`、`run_task:1369` 分发链。
- **Test scenarios**：`daily-review-symbol --symbols 688114.SH` → 新股先回填、生成 `{date}_688114.SH.md`、`_reviews()` 含该股；无 Telegram 副作用；缺参/非白名单 → 命名错误零副作用；连调两不同 symbol → 两文件并存不互覆（验证①已解）；回填耗时不触发 timeout。
- **Verification**：bridge CLI 跑该 task 退出 0，`storage/daily_review/` 出现按股产物，`snapshot.reviews` 含之。

### U5. Swift 加自选直传触发

- **Goal**：`toggleWatchlist` 的"加入"分支直接触发 `daily-review-symbol`（不落 json），个股复盘随后刷新。
- **Dependencies**：U4。
- **Files**：`Sources/KSSDesktop/Views/ContentView.swift:262`（加入分支调 run）；`Sources/KSSDesktop/Services/*`（`KSSStore`/`BridgeClient` 暴露带 `--symbols` 的 run；解决 `KSSModels.swift:624-631` `KSSTask.arguments` 静态签名 —— 扩 enum 带关联值 **或** 绕过 enum 直接 `bridge.run(["run","daily-review-symbol","--symbols",code])`）；测试 `Tests/KSSDesktopTests/WatchlistTriggerTests.swift`。
- **Approach**：仅"加入"分支触发，单只 symbol 直传（KTD1，不写 json）。run 完刷新 `snapshot.reviews` 使列表即时出现新条目；生成期给轻量进行中态（复用既有 run UI 反馈）。
- **Execution note**：test-first —— 先写「加入触发一次 run、取消不触发」store 单测。
- **Test scenarios**：加自选 → 恰一次 `daily-review-symbol`、symbol 正确；取消 → 零触发；run 完个股复盘含新股；run 失败 → 横幅不崩、`@AppStorage` watchlist 仍在；快速连加 → 各自触发不丢（配合 U4 串行化）。
- **Verification**：app 内股票池加一只新票 → 个股复盘几秒后出现该股；`swift build` + 相关 `swift test` 通过。

## Risks & Dependencies

- **R1 · validate_predictions 兼容（新增首要）**：按股改名可能断其底稿发现（`daily_review:882-883`）。缓解：U3 先抓基线再改，二选一（它改发现 / cron 并行按日产物）。**这是 U3 的实现期必答，非事后**。
- **R2 · 回填耗时 + tushare 限流**：次新股全量回填 + 复盘生成可能超 300s。缓解：U4 显式大 timeout 或拆前置 step；`--throttle` 限流；UI 进行中态。
- **R3 · 并发加股写竞争**：快速连加 → 并发回填/生成互踩同目录。缓解：U4 串行化该 task。
- **R4 · category 默认 alpha 判读偏差**：任意自选股按 alpha 解读大单流出，真博弈股可能判反。缓解：默认保守可接受；per-symbol category 持久化留 follow-on。
- **R5 · 次新股情形段缺失体验**：跳过情形段后版面靠关键位+均值维持（与 related brainstorm 撤段后版面同构）。已知可接受。

## Open Questions（实现期决议）

- `validate_predictions` 兼容走「它改按股发现」还是「cron 并行按日产物」—— U3 读其代码后定，倾向前者免双写。
- `ensure_history` 落 `update_cs_data.py`（公共入口）还是 `daily_review.py` 内 —— U2 定，倾向前者集中数据层。
- `KSSTask` 扩关联值 vs `toggleWatchlist` 直接绕过 enum 调 `bridge.run` —— U5 定，倾向后者最小改动。
- 情形段 n 阈值沿用 20 还是另设 —— U1 定，倾向沿用脚本现有口径。
