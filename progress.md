# KSS 进度日志

> 倒序记录。最新在上。详细技术复盘见 `docs/solutions/`，计划见 `docs/plans/`。

---

## 2026-06-19 — macOS App 第十阶段：UI/UX 重构（中文优先 / 列表排序筛选 / Markdown 渲染）

**起因**：界面还是英文为主、列表不能排序、复盘只能看 raw markdown 文本、留白偏大、状态用纯色点不带文字。按用户 7 条要求整体重构。

**已交付**
- **中文优先**：侧栏 `WorkspaceSection.displayName`（总览/每日推荐/自选/任务/复盘/回测/股票池）、`KSSTask.title`、各页文案、按钮（加自选/取消自选）、空态、搜索 placeholder 全部中文。
- **所有列表可排序 + 日期排序筛选**：新增 `SortControl` 组件。股票池/自选按 代码·名称·涨跌幅·收盘价；每日推荐按 排名·权重·跟踪收益；回测按 更新时间·标题；复盘按日期 最新/最早 + 日期筛选（全部/近7天/近30天，相对最新复盘日）。
- **状态统一图标+文字**：新增 `StatusBadge`。跟踪状态 上涨↗/下跌↘/待T+2⏱（红涨绿跌）；任务状态 成功✓/跳过/失败（语义色，不蹭价格红绿）。
- **Markdown 渲染**：bundle `marked.min.js` + `markdown.html`（Discord 暗色 CSS：标题/表格/代码/引用），新增 `MarkdownWebView.swift`（复用 WKWebView 离线注入模式）。复盘和回测报告全文从等宽 raw 文本换成排版 HTML，表格/标题/列表正常渲染。
- **字体加大、标题加粗**：`SectionHeader` 改 18pt 粗体 + blurple 竖条；个股标题 30pt heavy；列表行标题 15pt 粗；KPI tile 数值加粗等宽。
- **收紧留白**：页面 padding 24→16/18，栅格 spacing 18→10/14，卡片与行内边距收紧。

**验证**
- `swift build`、`./script/build_and_run.sh --verify` 通过；`markdown.html` + `marked.min.js` 已进 `.app` bundle。
- 实机：侧栏全中文；复盘详情 markdown 渲染出粗体标题 + 情形分布表格（情形/原始/修正后/备注）+ 关注代码 + blurple 日期徽标；每日推荐顶部 排名 排序 + 升序 + “5 只”计数，每行 待T+2 图标徽标。

**剩余风险 / 下一步**
- 复盘列表“近 N 天”以最新复盘日为基准（非系统今天），符合离线数据语义；如需绝对日期可再加日期选择器。
- markedjs 默认不开启 raw HTML sanitize；复盘/报告都是项目自产内容，暂不引入 DOMPurify。

## 2026-06-19 — macOS App 第九阶段：TradingView 图表 + 暗色交易台 UI

**起因**：价格图此前只是 SwiftUI Canvas 画一条 close 折线，看不出 K 线、量能和均线结构；整体 UI 还是系统默认浅色卡片。目标要求把图表按 TradingView lightweight-charts 刷新，UI 参照暗色交易台风格。

**已交付**
- **真·lightweight-charts 接入**（库本体随包离线，不依赖运行时联网）：
  - `Sources/KSSDesktop/Resources/` 打包 `lightweight-charts.standalone.production.js`（v4.2.0）+ `chart.html`。
  - `chart.html` 渲染蜡烛图 + 量能副图 + MA5/MA20 叠加线，A 股红涨绿跌；crosshair 联动 legend（开/高/低/收 + MA 值），右轴现价标签。
  - 新增 `Views/ChartWebView.swift`：`NSViewRepresentable` 包 `WKWebView`，Swift 持有数据、Web 只负责渲染，OHLC/量能以 JSON 注入。
  - `Package.swift` 声明 resources；`script/build_and_run.sh` 把 `KSSDesktop_KSSDesktop.bundle` 拷进 `.app`，保证 `Bundle.module` 运行时可解析。
- **数据层补全 OHLC**：bridge `stock_detail` 的 history 增加 `open/high/low/volume`；`PricePoint` 同步加字段（不动选股逻辑与本地 CSV）。
- **暗色设计系统对齐 Discord token**：`Support/Theme.swift` 采用 Discord design system（`Discord-showcase.html` 的 DESIGN.md token）——画布 #1E1F22(--bg) / 卡片 #2B2D31(--surface) / 描边 #3F4147(--border) / blurple 强调 #5865F2(--accent) / 正文 #DBDEE1(--fg) / 次要 #949BA4(--muted)，圆角 14。组件采用 Discord 习语：KPI tile 大写 tracked 标签 + 等宽数值，SectionHeader 改 mono 大写 blurple eyebrow，badge 用 accent 染色。`kssCard` 修饰符 + `signColor` 红涨绿跌。Dashboard / Stocks / Backtests / Reviews / Runbook / Recommendations 全量改用 Discord 卡片与画布；强制 `.preferredColorScheme(.dark)`。图表同步换 Discord token（surface 底、blurple MA20/crosshair、muted 轴）。
- **修掉一个会卡死的 bridge bug**（阻塞全 App，非本次新引入）：`BridgeClient.run` 原先 `waitUntilExit()` 之后才读 stdout；snapshot JSON 已涨到 ~83KB，超过 ~64KB 管道缓冲，bridge 卡在 `print` 写、App 卡在等退出，死锁。改为后台并发抽干 stderr、主线程读 stdout、再 wait。snapshot 子进程从“分钟级不返回”变为正常完成。

**验证**
- `python3 scripts/kss_app_bridge.py stock 688017.SH`：history 末条含 `open/high/low/close/volume`（381.6/413.82/375.64/408.01/192016.16）。
- `/usr/bin/python3 ... snapshot`：2.4s、退出 0、83584 bytes；定位死锁靠 `sample` 抓到 `PyFile_WriteObject` 阻塞栈。
- `swift build`、`./script/build_and_run.sh --verify`：通过；确认 `chart.html` 与库都在 `dist/KSSDesktop.app/.../KSSDesktop_KSSDesktop.bundle`。
- 实机截图：Dashboard 暗色加载完整；Stocks→华大智造 详情页 lightweight-charts 蜡烛图 + MA5(橙)/MA20(蓝) + 量能 + 现价标签 + TradingView logo 正常渲染。

**剩余风险 / 下一步**
- 图表周期固定日 K；后续可加周期切换（周/月）或复权选项。
- 暗色为强制；如需跟随系统明暗，需要再补一套浅色 token。
- ETF 正式 parquet 回测仍缺 `pyarrow`/`fastparquet`，本阶段未碰依赖。

## 2026-06-19 — macOS App 第八阶段：自选股分析指标增强

**起因**：Stocks / Watchlist 已能展示单股行情、价格曲线和基础估值，但“自选股票分析”还需要更直接的趋势与风险读数，减少用户从原始价格序列中手算。

**已交付**
- `StockDetailView` 新增 Analysis 指标区：
  - 20 日收益。
  - 60 日收益。
  - 20 日年化波动。
  - 60 日最大回撤。
  - 距 20 日高点。
  - MA20 偏离。
- 指标全部在 Swift 侧由 `StockDetail.history` 和 `latest` 派生，不改变 Python bridge、选股逻辑或本地 CSV。
- Watchlist 与 Stocks 共用同一套分析视图，自选股和全量股票池都能查看。

**验证**
- `python3 scripts/kss_app_bridge.py stock 688017.SH`：确认单股详情返回近 160 日价格序列、`high20`、`ma20` 等计算输入。
- `swift build`：通过。
- `./script/build_and_run.sh --verify`：通过。

**剩余风险 / 下一步**
- 指标是静态本地派生分析，不含行业相对强弱或因子归因；后续可在不新增依赖的前提下加入同池分位数。
- 当前价格图仍只画 close 曲线，尚未叠加 MA5/MA20 或成交量副图。

## 2026-06-19 — macOS App 第七阶段：复盘 / 回测报告全文详情

**起因**：Reviews 和 Backtests 页面已有摘要和指标卡片，但复盘与数据回测分析需要能在桌面端直接阅读完整报告、表格和审计说明，不能只依赖 excerpt。

**已交付**
- bridge 新增只读 `report PATH` 命令：
  - 读取项目内 markdown 报告并返回 `title`、`path`、`updatedAt`、全文 `text`。
  - 校验路径必须是项目内相对路径，且后缀为 `.md`，避免详情接口读取项目外文件。
- Swift 模型和 Store 新增 `ReportDetail` / `loadReport(path:)`。
- Backtests 页面改为 macOS 原生 sidebar-detail：
  - 左侧列出回测/分析报告。
  - 右侧保留纸交易跟踪指标，并展示所选报告的完整 markdown 原文。
  - 使用等宽文本保留报告表格结构，支持文本选择。
- Reviews 页面同样接入全文详情：
  - 左侧保留每日复盘列表。
  - 右侧展示所选复盘 markdown 全文，并保留关注股票代码。

**验证**
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `python3 scripts/kss_app_bridge.py report storage/reports/kss_desktop_radar_archive_analysis.md`：成功返回完整报告正文，包含 16 个归档日、94 个主题信号和 grade 分布表。
- `python3 scripts/kss_app_bridge.py report storage/daily_review/2026-06-18.md`：成功返回完整每日复盘正文。
- `swift build`：通过。
- `./script/build_and_run.sh --verify`：通过。

**剩余风险 / 下一步**
- 报告详情当前是 raw markdown 文本视图，优先保证表格和审计文本可读；后续如需要更强排版，可再加 markdown 渲染或外部打开按钮。
- ETF 正式 parquet 回测仍缺 `pyarrow` 或 `fastparquet`，未在本阶段新增依赖。

## 2026-06-19 — macOS App 第六阶段：本地股票元数据补全 + 雷达归档分析

**起因**：Stocks / Watchlist 中创业板样本只有代码没有名称和产业链标签；同时 ETF 正式回测仍缺 parquet engine，需要先提供一个不加新依赖、基于现有归档的雷达分析入口。

**已交付**
- 股票元数据 fallback：
  - `scripts/kss_app_bridge.py` 读取 `storage/stock_names.csv` 后，再用 `kss/config/supply_chain.yaml` 补全缺失名称。
  - 对 300/301/302 等创业板样本补充 `name`、首个 `demand_chains` 作为 `industry`、完整需求链作为 `concept`。
  - 不改写 `storage/stock_names.csv`，只在 App bridge 的 snapshot/stock detail 层增强展示。
- Runbook Quick 新增 `Radar Archive Analysis`：
  - 仅读取 `storage/etf_radar/*.json`。
  - 生成 `storage/reports/kss_desktop_radar_archive_analysis.md`。
  - 汇总归档天数、主题信号数、grade 分布、最新雷达、最近 20 个主题信号。
  - 报告进入 Backtests 页面候选列表，作为 parquet 正式回测未可用前的本地分析视图。

**验证**
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `python3 scripts/kss_app_bridge.py stock 300857.SZ`：成功显示 `协创数据`、`AI算力` 元数据和价格历史。
- `python3 scripts/kss_app_bridge.py run radar-archive-analysis`：成功生成 16 个归档日、94 个主题信号的分析报告；artifact 为 `storage/reports/kss_desktop_radar_archive_analysis.md`。
- `python3 -c '... snapshot ...'`：确认 snapshot 中 `300857.SZ` 元数据已补全，最新 task 为 `radar-archive-analysis`。
- `swift build`：通过。
- `./script/build_and_run.sh --verify`：通过。

**剩余风险 / 下一步**
- `supply_chain.yaml` 的产业链标签是项目研究视角，不等同于交易所/申万行业分类；App 当前用它解决可读性，不替代正式行业源。
- Radar Archive Analysis 是归档汇总，不替代 `backtest_etf_radar.py` 的一年正式回测；正式回测仍需 `pyarrow` 或 `fastparquet`。
- 后续可在 Backtests 页面加入报告全文打开/复制能力，减少只看摘要的限制。

## 2026-06-19 — macOS App 第五阶段：Runbook 持久日志 + 板块复盘入口

**起因**：桌面 App 已能触发推荐、个股复盘、纸交易跟踪和回测任务，但 Runbook 执行记录只存在于本次 App 进程内；同时项目已有板块复盘 `scripts/sector_review.py`，还没有纳入桌面入口。

**已交付**
- Runbook 任务历史持久化：
  - bridge 对每次 `run TASK` 结果追加写入 `storage/app_runs/kss_desktop_tasks.jsonl`。
  - `snapshot` 新增 `recentTaskRuns`，App 启动/刷新后自动恢复最近 25 条任务记录。
  - Swift store 会把当前内存结果和持久结果去重合并，避免刷新后丢失刚运行的任务。
- Runbook Full 分组新增 `Formal Sector Review`：
  - 调用项目既有 `scripts/sector_review.py`。
  - 默认使用本地科创 CSV 最新日期，显式 `--channel console --dry-run`，不从 App 推送 Telegram。
  - 保留原脚本 first-write-wins 雷达存档语义；如果 live radar 成功，会继续写 `storage/etf_radar/YYYYMMDD.json` 校验底稿。
- 日期与 artifact 修正：
  - sector review 默认日期统一归一化为 `YYYYMMDD`。
  - Runbook artifact 指向 `storage/etf_radar/YYYYMMDD.json` 和 `storage/app_runs/kss_desktop_tasks.jsonl`。

**验证**
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `python3 scripts/kss_app_bridge.py run daily-picks-preview`：成功，并写入 `storage/app_runs/kss_desktop_tasks.jsonl`。
- `python3 scripts/kss_app_bridge.py run formal-sector-review --date 20260618`：成功返回结构化任务结果；sandbox 无外网时按项目逻辑降级为简表，stderr 保留数据源缺失/网络失败证据，不推送 Telegram。
- `python3 -c '... snapshot ...'`：确认 `recentTaskRuns` 可从 snapshot 读出，最新任务为 `formal-sector-review`，Python 环境可用。
- `swift build`：通过，说明 Swift `AppSnapshot.recentTaskRuns` / `KSSTask.formalSectorReview` 解码与 UI wiring 编译无误。
- `./script/build_and_run.sh --verify`：通过。

**剩余风险 / 下一步**
- `formal-sector-review` 在当前 sandbox 中只能验证降级路径；真实完整复盘需要可联网 Tushare/东财/同花顺环境和 LLM key。
- `storage/app_runs/kss_desktop_tasks.jsonl` 会随 App 使用增长，后续可增加“清理日志”或分页查看。
- 创业板名称/行业补全和 ETF parquet engine 仍是下一批完成项。

## 2026-06-19 — macOS App 第四阶段：正式复盘入口 + ETF 任务路径修正

**起因**：原始目标要求桌面端支持“每日推荐、复盘、跟踪、数据回测分析”；前三阶段已有推荐、跟踪和回测入口，但复盘生成还只能在命令行执行，且 ETF 正式回测入口需要校正到真实脚本路径。

**已交付**
- Runbook Full 分组新增 `Formal Daily Review`：
  - 调用项目既有 `scripts/daily_review_322_017.py`。
  - 默认使用 688322 / 688017 本地 CSV 的最新共同交易日，避免在非交易日或数据未更新时生成未来日期复盘。
  - 固定使用 `--channel console --dry-run`，不从桌面 App 触发 Telegram 推送。
  - 仍保留原脚本的审计语义：`storage/daily_review/YYYY-MM-DD.md` 已存在时 dry-run 不覆盖。
- bridge 子进程环境加固：
  - `MPLCONFIGDIR` / `XDG_CACHE_HOME` / `HOME` 都指向项目内 `.cache`，避免 matplotlib / Tushare 缓存写到用户 home。
  - 安全读取项目 `.env` 中的 `TUSHARE_TOKEN` 和 Telegram 变量供既有脚本使用，但 App 默认 review task 不推送。
- ETF 正式回测入口修正：
  - 脚本路径从不存在的 `scripts/backtest_etf_radar.py` 改为项目根目录 `backtest_etf_radar.py`。
  - parquet precheck 改为接受 `pyarrow` 或 `fastparquet` 任一可用，而不是只认 `pyarrow`。

**验证**
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `python3 scripts/kss_app_bridge.py run formal-daily-review`：成功生成/读取 `storage/daily_review/2026-06-18.md`；sandbox 内 Tushare 网络不可达时按脚本逻辑退回本地缓存，任务仍返回 success。
- `python3 scripts/kss_app_bridge.py run formal-etf-radar-backtest`：路径检查已走到依赖 gate；当前按预期返回缺少 `pyarrow` / `fastparquet` 的结构化失败。
- `python3 scripts/kss_app_bridge.py snapshot`：成功序列化 App snapshot，并把 `2026-06-18` 复盘纳入 Reviews。
- `python3 scripts/kss_app_bridge.py run formal-paper-summary`：成功，stdout 无 stderr。
- `swift build`：通过。
- `./script/build_and_run.sh --verify`：通过。

**剩余风险 / 下一步**
- 当前 sandbox 无法访问 Tushare 网络；桌面 App 在正常用户环境下可用项目 `.env` 尝试实时更新，失败时仍会降级到缓存。
- ETF 正式回测仍需显式引入 parquet engine 后才能跑通；未违反“无明确请求不加新依赖”。
- 还可以继续补：板块复盘 `scripts/sector_review.py` 的 dry-run 入口、任务日志持久化、创业板名称/行业补全。

## 2026-06-19 — macOS App 第三阶段：正式 Python 环境 + Full 任务入口

**起因**：第二阶段 Runbook 只能跑不依赖 pandas 的 quick 任务；要让桌面 App 真正接上项目既有生产脚本，需要补齐可识别的 Python 环境，并把正式选股、跟踪、回测入口暴露到 UI。

**已交付**
- 新增项目本地桌面运行环境 `.venv-desktop`，用 Python 3.11 安装 `kss/requirements.txt`，供 App bridge 调用正式脚本。
- `scripts/kss_app_bridge.py` 新增 `python-env` 能力：
  - 优先选择 `.venv-desktop/bin/python`，回退 `.venv/bin/python`、Homebrew / 系统 Python。
  - 检查正式任务必需模块 `pandas` / `lightgbm` / `tushare` / `akshare`。
  - 单独检查 ETF parquet 缓存所需的可选模块 `pyarrow`。
- Runbook UI 拆成 Quick / Full 两组：
  - Quick：沿用 preview / save picks / summary / 轻量回测。
  - Full：Formal Daily Picks、Formal Paper Summary、Formal ETF Radar Backtest。
- 正式任务通过 bridge 子进程执行项目原脚本：
  - `formal-daily-picks` -> `scripts/paper_trade_log_mv.py`
  - `formal-paper-summary` -> `scripts/paper_trade_log_mv.py --summary`
  - `formal-etf-radar-backtest` -> `scripts/backtest_etf_radar.py`
- bridge 为子进程设置项目内 `.cache` 作为 `MPLCONFIGDIR` / `XDG_CACHE_HOME`，避免 matplotlib 写用户 cache。
- Full ETF Radar Backtest 已接线，但当前缺少 parquet engine；bridge 会返回结构化失败和明确依赖说明，不再把 Python traceback 暴露给 App。

**验证**
- `.venv-desktop/bin/python -m pip install -r kss/requirements.txt`：成功安装项目声明依赖。
- `python3 scripts/kss_app_bridge.py python-env`：成功选择 `.venv-desktop/bin/python`，必需模块可用；`pyarrow` 显示为缺失的可选模块。
- `.venv-desktop/bin/python scripts/paper_trade_log_mv.py --summary`：成功输出 `n_days_logged: 4`、`n_days_with_returns: 2`、`sharpe: -2.3580561892724514`。
- `.venv-desktop/bin/python scripts/paper_trade_log_mv.py --date 2026-06-18 --no-execution`：成功生成 2026-06-18 纸交易推荐；已有日志时按脚本逻辑跳过覆盖。
- `python3 scripts/kss_app_bridge.py run formal-paper-summary`：成功。
- `python3 scripts/kss_app_bridge.py run formal-daily-picks`：成功；已有 2026-06-18 日志时输出 skip warning，但脚本退出码为 0。
- `python3 scripts/kss_app_bridge.py run formal-etf-radar-backtest`：按预期返回失败，原因是缺少 `pyarrow` 或 `fastparquet`，未新增项目未声明依赖。
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `swift build`：通过。
- `./script/build_and_run.sh --verify`：通过，说明 `.app` 打包和启动路径仍可用。

**设计约束**
- 遵守“无明确请求不加新依赖”，因此没有安装 `pyarrow` / `fastparquet`；ETF 正式回测入口先保留为可诊断的 blocked 状态。
- `.venv-desktop`、`.cache`、`.build`、`dist` 均加入 `.gitignore`，不把本机环境和构建产物纳入版本控制。
- 正式脚本仍保持原有写盘/跳过覆盖语义，App 不绕过审计底稿。

**下一步**
- 如需正式 ETF radar backtest，在项目依赖中明确增加 parquet engine 后再启用该按钮的成功路径。
- 增加复盘生成任务，并把任务 stdout/stderr 归档到 `storage/reports` 或专用 run logs。
- 补齐创业板名称/行业映射，提升自选股和搜索结果的可读性。

## 2026-06-19 — macOS App 第二阶段：Runbook 任务面板 + 轻量回测

**起因**：第一阶段 App 只是读取本地数据；目标要求还需要支持选股、复盘跟踪、数据回测分析，因此补上可触发的本地任务面板。

**已交付**
- `scripts/kss_app_bridge.py` 从只读 bridge 扩展为 JSON bridge：
  - `run daily-picks-preview`：按 KSS 纸交易核心逻辑 `log_mv = ln(total_mv)`、低市值 Top 5 生成预览，不写盘。
  - `run daily-picks`：同样规则生成并保存 `storage/paper_trade/YYYY-MM-DD.json`；已存在时默认 `skipped`，避免改写审计底稿。
  - `run paper-summary`：刷新纸交易跟踪汇总。
  - `run logmv-backtest --lookback 160`：用本地 `cs_data_688*.csv` 做轻量回测，规则为每日 total_mv 最小 5 只、T+1 open 到 T+2 open 等权收益。
- 新增 `Sources/KSSDesktop/Views/RunbookView.swift`：
  - Runbook 分栏按钮：Preview Picks / Save Daily Picks / Paper Tracking / log_mv Backtest。
  - 任务执行结果结构化显示：状态、摘要、stdout、stderr、artifact 路径。
  - 任务成功或跳过后自动刷新 snapshot，让新推荐或新回测报告进入 Daily Picks / Backtests。
- 新增 Swift model/store/client 支撑：
  - `TaskRunResult`
  - `KSSTask`
  - `BridgeClient.runTask`
  - `KSSStore.runTask`
- Backtests 现在优先读取 `storage/reports/kss_desktop_logmv_backtest.md`。
- 生成了新的轻量回测报告：`storage/reports/kss_desktop_logmv_backtest.md`。

**验证**
- `python3 scripts/kss_app_bridge.py run daily-picks-preview`：成功输出 2026-06-18 Top 5，和现有纸交易日志一致。
- `python3 scripts/kss_app_bridge.py run paper-summary`：成功输出 `2 / 4 days evaluated`。
- `python3 scripts/kss_app_bridge.py run logmv-backtest --lookback 160`：成功生成 160 个信号日轻量回测；Sharpe -0.18，最大回撤 -23.60%，胜率 53.8%。
- `python3 -m py_compile scripts/kss_app_bridge.py`：通过。
- `python3 scripts/kss_app_bridge.py snapshot`：成功把新回测报告纳入 Backtests 数据。
- `swift build`：通过（仍需允许 Swift/Clang 写用户 cache；sandbox 内会因 `~/.cache/clang` 权限失败）。
- `./script/build_and_run.sh --verify`：通过，说明 Codex Run / `.app` 打包启动路径有效。

**设计约束**
- 当前 `.venv/bin/python` 指向系统 Python，缺少 pandas；因此 Runbook 第二阶段没有直接调用 `scripts/paper_trade_log_mv.py` / `backtest_etf_radar.py`，而是提供不依赖 pandas 的本地轻量实现。
- `daily-picks` 保存格式保持与 `paper_trade_log_mv.py` 的 JSON schema 对齐，但 `use_execution=false`，尚未建模涨停、滑点、部分成交。
- 轻量回测是桌面分析用快速版本，不替代 KSS 正式 backtest / DSR 报告。

**下一步**
- 修复/重建项目 Python 环境后，把 Runbook 增加“正式纸交易脚本”和“正式 ETF radar backtest”入口，并在 UI 中明确区分 quick / full。
- 增加复盘生成任务（读取 `scripts/daily_review_322_017.py` 或 sector review wrapper）和运行日志归档。
- 补齐创业板样本名称/行业映射，否则 Watchlist / Stocks 中创业板元数据为空。

## 2026-06-19 — macOS 原生桌面 App 第一阶段骨架

**起因**：把 KSS 现有选股、复盘、跟踪、回测产物做成 macOS 原生桌面入口，便于自选股分析和日常复盘。

**已交付**
- 新增 SwiftPM 原生 macOS App：`KSSDesktop`，入口在 `Package.swift` + `Sources/KSSDesktop/`。
- 新增只读 bridge：`scripts/kss_app_bridge.py`，提供 `snapshot` / `stock <symbol>` / `paper-summary` 三个 JSON 命令。
- UI 第一版覆盖：
  - Dashboard：最新数据日期、股票池数量、最新推荐、纸交易跟踪指标。
  - Daily Picks：读取最新 `storage/paper_trade/*.json` 推荐，补充名称、行业、T+2 跟踪状态。
  - Watchlist / Stocks：支持本地自选列表、搜索、单股近 160 日价格曲线、MA5/MA20、成交额、PE/PB。
  - Reviews：读取 `storage/daily_review/*.md`。
  - Backtests：读取核心 `storage/reports/*.md` 回测/研究报告摘要，并显示纸交易累计表现。
- 新增 `script/build_and_run.sh`，按 SwiftPM GUI App 方式构建 `dist/KSSDesktop.app` 并用 `open -n` 启动。
- 新增 `.codex/environments/environment.toml`，Codex Run action 指向 `./script/build_and_run.sh`。

**验证**
- `python3 scripts/kss_app_bridge.py snapshot`：成功识别最新行情日期 `2026-06-18`、最新推荐日期 `2026-06-18`、股票数 102。
- `python3 scripts/kss_app_bridge.py stock 688017.SH`：成功返回绿的谐波单股元数据与近 160 日价格序列。
- `python3 scripts/kss_app_bridge.py paper-summary`：成功返回纸交易日志汇总；当前可评估样本 2 天。
- `swift build`：通过（需允许 Swift/Clang 写用户 cache；sandbox 内会因 `~/.cache/clang` 权限失败）。
- `./script/build_and_run.sh --verify`：构建并生成 `dist/KSSDesktop.app`；进程校验命令在 sandbox 中 `pgrep` 不可用，但脚本在提权运行下返回 0。

**剩余风险 / 下一步**
- 第一版只读消费本地数据产物，尚未在 UI 内触发重跑重计算任务（如全量 backtest / paper_trade 生成）；后续可把重任务做成显式按钮和进度日志。
- `stock_names.csv` 目前主要覆盖科创板，创业板样本名称/行业会为空；需要补齐 `storage/stock_names.csv` 或增加名称源。
- 纸交易跟踪样本仍少，App 展示的是当前日志可证明的结果，不代表长期策略结论。

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
- [x] ~~P2 题材归因接入~~ → **SKIP**（验证 gate：a-stock-data 题材端点 = KSS 已接的同花顺 `getharden`，无增量。详见 plan §8）。

**收尾状态**：数据源补强主线全部裁决完毕——能接的（龙虎榜、科创两融）已接，重复的（题材归因）SKIP，无数据的（北向明细）BLOCKED。三次验证 gate 各拦下一类无效工作（端点不对 / 无数据 / 重复造轮子）。
