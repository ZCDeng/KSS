import Foundation
import CryptoKit

enum BridgeError: LocalizedError {
    case projectRootNotFound
    case processFailed(String)
    case invalidOutput
    /// 桥协议版本与 app 支持版本不一致（KTD3）。
    case schemaMismatch(bridge: Int, app: Int)
    /// 首启 uv bootstrap 失败（U2）。
    case runtimeBootstrapFailed(String)

    var errorDescription: String? {
        switch self {
        case .projectRootNotFound:
            return "Cannot locate scripts/kss_app_bridge.py"
        case .processFailed(let message):
            return message
        case .invalidOutput:
            return "Bridge returned invalid JSON"
        case .schemaMismatch(let bridge, let app):
            if bridge > app {
                return "桥协议版本过新（脚本 v\(bridge) > App v\(app)）——请重新编译 KSSDesktop。"
            } else {
                return "桥协议版本过旧（脚本 v\(bridge) < App v\(app)）——请更新 scripts/（git pull）。"
            }
        case .runtimeBootstrapFailed(let message):
            return "首次配置 Python 运行时失败：\(message)"
        }
    }
}

struct BridgeClient {
    /// Child processes inherit only non-secret ambient settings. Credentials
    /// required by KSS are appended explicitly from KeychainStore below.
    ///
    /// This is deliberately pattern based: an installed app launched from a
    /// developer shell can otherwise inherit unrelated API keys and forward
    /// them to the long-lived Python sidecar.
    static func sanitizedChildEnvironment(
        _ source: [String: String] = ProcessInfo.processInfo.environment
    ) -> [String: String] {
        let secretFragments = [
            "API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
            "AUTHORIZATION", "PRIVATE_KEY",
        ]
        return source.filter { key, _ in
            let upper = key.uppercased()
            return !secretFragments.contains(where: upper.contains)
        }
    }

    /// 不可变代码根（scripts/ 与 kss/config 所在）。
    let projectRoot: URL
    /// 可变状态根（storage/.cache）。dev-mode 默认 = projectRoot（in-repo，行为不变）；
    /// bundle-mode 默认 = ~/Library/Application Support/KSS。
    let stateRoot: URL

    /// bridge 脚本运行用的 Python 解释器。
    let python: URL

    init() throws {
        guard let roots = Self.resolveRoots() else {
            throw BridgeError.projectRootNotFound
        }
        self.projectRoot = roots.project
        self.stateRoot = roots.state
        // U2：bundle-mode 首启若 state-root venv 缺失，bootstrap provision（uv sync）。
        try Self.provisionRuntimeIfNeeded(projectRoot: roots.project, stateRoot: roots.state)
        self.python = Self.resolvePython(stateRoot: roots.state)
        // 已安装 .app 只刷新状态根。projectRoot 必须留给 launchd 用的真实仓库——
        // 写成 Resources 会让下次 plist 渲染 fail-loud（07-14 趋势归档实锤）。
        if Self.isBundledApp {
            if let project = Self.packagedBreadcrumbProjectRoot(
                codeRoot: roots.project,
                existingProjectRoot: Self.readBreadcrumb()?.projectRoot,
                fileManager: .default
            ) {
                Self.writeBreadcrumb(project: project, state: roots.state)
            }
        }
        // U9：lock 变化则后台非阻塞 uv sync（不卡启动）。
        Self.refreshRuntimeIfLockChanged(projectRoot: roots.project, stateRoot: roots.state)
    }

    /// U9：Python 层版本（scripts/VERSION），与 Swift 二进制版本独立。
    var scriptsVersion: String {
        let v = try? String(contentsOf: projectRoot.appending(path: "scripts/VERSION"), encoding: .utf8)
        return (v?.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap { $0.isEmpty ? nil : $0 } ?? "—"
    }

    /// Swift 二进制版本（CFBundleShortVersionString）。
    static var appVersion: String {
        (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "dev"
    }

    /// 无需实例即可读 Python 层版本（解析根 + scripts/VERSION）。
    static func scriptsVersionOnDisk() -> String {
        guard let roots = resolveRoots() else { return "—" }
        let v = try? String(contentsOf: roots.project.appending(path: "scripts/VERSION"),
                            encoding: .utf8)
        let trimmed = v?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (trimmed?.isEmpty == false) ? trimmed! : "—"
    }

    func snapshot() throws -> AppSnapshot {
        try run(["snapshot"], as: AppSnapshot.self)
    }

    func stock(symbol: String) throws -> StockDetail {
        try run(["stock", symbol], as: StockDetail.self)
    }

    func report(path: String) throws -> ReportDetail {
        try run(["report", path], as: ReportDetail.self)
    }

    /// Seesaw @file 引用：工作区文件模糊搜索（只读白名单）。
    func workspaceFiles(query: String, limit: Int = 30) throws -> WorkspaceFilesResponse {
        try run(["workspace-files", query, String(limit)], as: WorkspaceFilesResponse.self)
    }

    func perillaEnrichment(symbol: String) throws -> PerillaEnrichment {
        try run(["perilla-enrichment", symbol], as: PerillaEnrichment.self)
    }

    // MARK: Longbridge 实时（U1）—— 只读命令走 sidecar 热路径（不在 subprocessOnlyCommands）。

    /// 实时快照（ChinaConnect LV1，仅陆股通标的）。R1/R11。
    func longbridgeQuote(symbol: String) throws -> LongbridgeQuote {
        try run(["longbridge-quote", symbol], as: LongbridgeQuote.self)
    }

    /// 批量实时快照（R5）：单次 bridge/SDK 往返覆盖整个 tick 的符号预算。
    func longbridgeQuotes(symbols: [String]) throws -> [LongbridgeQuote] {
        guard !symbols.isEmpty else { return [] }
        return try run(
            ["longbridge-quotes", symbols.joined(separator: ",")],
            as: LongbridgeQuotesResponse.self
        ).quotes
    }

    /// 隔夜美股独立行情服务：Longbridge 优先、yFinance 逐项回退。
    /// 返回统一的新鲜度状态，不进入 ChinaConnect `RealtimeMerge`。
    func usMarketQuotes(symbols: [String] = []) throws -> USMarketQuotesResponse {
        var command = ["us-market-quotes"]
        if !symbols.isEmpty {
            command.append(symbols.joined(separator: ","))
        }
        return try run(command, as: USMarketQuotesResponse.self)
    }

    /// 最新分钟 bar 快照（按覆盖路由，前向-only）。R2。
    func intradaySnapshot(symbol: String, interval: Int = 1) throws -> IntradaySnapshot {
        try run(["intraday-snapshot", symbol, String(interval)], as: IntradaySnapshot.self)
    }

    /// 完整日内 bar 序列（K 线图渲染需全序列，F006）。R2/R6。
    func intradayBars(symbol: String, interval: Int = 1) throws -> IntradayBars {
        try run(["intraday-bars", symbol, String(interval)], as: IntradayBars.self)
    }

    /// 交易时段查询（门控实时拉取 / 定时器，F007）。R13。
    func tradingHours() throws -> TradingHours {
        try run(["trading-hours"], as: TradingHours.self)
    }

    func paperSummary() throws -> TrackingSummary {
        try run(["paper-summary"], as: TrackingSummary.self)
    }

    /// 日志分区（设置页，plan 2026-07-12-005 / U7）：枚举 storage/logs 下全部文件（含轮转代）。
    func logList() throws -> LogListResponse {
        try run(["log-list"], as: LogListResponse.self)
    }

    /// 日志尾部读取 + 可选关键词过滤。name 为 log-list 返回的相对路径（路径白名单锁 bridge 侧校验）。
    func logTail(name: String, lines: Int = 500, grep: String = "") throws -> LogTailResponse {
        try run(["log-tail", name, String(lines), grep], as: LogTailResponse.self)
    }

    /// 启动/手动自检（plan 2026-07-12-005 / U8）：运行时、数据目录、各凭证。
    func selfCheck() throws -> SelfCheckResponse {
        try run(["self-check"], as: SelfCheckResponse.self)
    }

    /// 数据源连通性测试（设置页数据源分区，plan 2026-07-12-005 / U4）。只读，不需写确认。
    func datasourceTest(source: String) throws -> DataSourceTestResult {
        try run(["datasource-test", source], as: DataSourceTestResult.self)
    }

    /// 会话开场确定性候选建议（plan 2026-07-12-004 U9）：代码规则选一个，不调 LLM。
    func suggestIndicator() throws -> IndicatorSuggestion {
        try run(["indicator-suggest"], as: IndicatorSuggestion.self)
    }

    func runTask(_ task: KSSTask) throws -> TaskRunResult {
        try run(["run", task.rawValue] + task.arguments, as: TaskRunResult.self)
    }

    /// U5: 加自选即时复盘 —— 动态 symbol 不入 KSSTask 静态枚举, 直接走 run 白名单。
    func runDailyReviewSymbol(_ symbol: String) throws -> TaskRunResult {
        try run(["run", "daily-review-symbol", "--symbols", symbol], as: TaskRunResult.self)
    }

    /// 风格对照整池写入影子纸交易轨（不写 formal）。
    func runStyleContrastShadowWrite(styleId: String, date: String? = nil) throws -> TaskRunResult {
        var args = ["run", "style-contrast-shadow-write", "--style-id", styleId]
        if let date, !date.isEmpty {
            args += ["--date", date]
        }
        return try run(args, as: TaskRunResult.self)
    }

    // MARK: U2 资讯雷达（IntelView 复用既有 news-digest bridge 命令）

    func resolveStocks(_ text: String) throws -> [ResolvedStock] {
        try run(["resolve", text], as: [ResolvedStock].self)
    }

    func importStocks(_ codes: [String]) throws -> TaskRunResult {
        try run(["import", codes.joined(separator: ",")], as: TaskRunResult.self)
    }
    func sectorRotation(date: String? = nil) throws -> HotspotRotationSnapshot {
        var args = ["sector-rotation"]
        if let date { args.append(date) }
        return try run(args, as: HotspotRotationSnapshot.self)
    }

    func sectorRotationHistory(limit: Int = 30) throws -> [HotspotRotationHistoryItem] {
        try run(["sector-rotation-history", String(limit)], as: [HotspotRotationHistoryItem].self)
    }

    /// 舆情热点 digest：无参 = 最新；可指定 date / scene 拉某档。
    func newsDigest(date: String? = nil, scene: String? = nil) throws -> NewsDigestResponse {
        var args = ["news-digest"]
        if let date, !date.isEmpty { args.append(date) }
        if let scene, !scene.isEmpty { args.append(scene) }
        return try run(args, as: NewsDigestResponse.self)
    }

    /// 资讯雷达 12 赛道 RSS（Investment News）。``force: true`` 实时抓取（≈20-40s），默认读缓存。
    func intelRadar(force: Bool = false) throws -> NewsDigestResponse {
        var args = ["intel-radar"]
        if force { args.append("force") }
        return try run(args, as: NewsDigestResponse.self)
    }

    /// yupi 旁路灌入合并缓存。
    func intelYupiIngest(force: Bool = false) throws -> NewsDigestResponse {
        var args = ["intel-yupi-ingest"]
        if force { args.append("force") }
        return try run(args, as: NewsDigestResponse.self)
    }

    /// 读 12 赛道 yupi 监控词。
    func intelKeywordsGet() throws -> IntelKeywordsResponse {
        try run(["intel-keywords-get"], as: IntelKeywordsResponse.self)
    }

    /// 写用户词表覆盖（整表 tracks 或单赛道）。
    func intelKeywordsSet(tracks: [String: [String]]) throws -> IntelKeywordsSetResponse {
        struct Payload: Encodable { let tracks: [String: [String]] }
        let data = try JSONEncoder().encode(Payload(tracks: tracks))
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-keywords-set", json], as: IntelKeywordsSetResponse.self)
    }

    func yupiStatus() throws -> YupiRuntimeStatus {
        try run(["yupi-status"], as: YupiRuntimeStatus.self)
    }

    func yupiEnsure(force: Bool = false) throws -> YupiEnsureResponse {
        var args = ["yupi-ensure"]
        if force { args.append("force") }
        return try run(args, as: YupiEnsureResponse.self)
    }

    /// 资讯雷达单赛道 AI 要点提炼（plan 2026-07-09-001）。
    /// ``force: true`` 跳过缓存直接重新调 LLM；``items`` 已由调用方截断到 25 条。
    func intelDigest(
        trackKey: String,
        trackName: String,
        items: [IntelItem],
        force: Bool = false
    ) throws -> IntelDigestResponse {
        struct Payload: Encodable {
            let track_key: String
            let track_name: String
            let items: [IntelItem]
            let force: Bool
        }
        let payload = Payload(track_key: trackKey, track_name: trackName, items: items, force: force)
        let data = try JSONEncoder().encode(payload)
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-digest", json], as: IntelDigestResponse.self)
    }

    /// 12 赛道全景热点（独立 LLM）。
    func intelPanorama(tracks: [IntelPanoramaTrackInput]) throws -> IntelPanoramaResponse {
        struct Payload: Encodable {
            let tracks: [IntelPanoramaTrackInput]
        }
        let data = try JSONEncoder().encode(Payload(tracks: tracks))
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-panorama", json], as: IntelPanoramaResponse.self)
    }

    /// 把已生成的 AI digest 写入沉淀库（md+json）。
    func intelDigestSave(
        trackKey: String,
        trackName: String,
        prompt: String,
        response: String,
        model: String,
        items: [IntelItem]
    ) throws -> IntelDigestSaveResponse {
        struct Payload: Encodable {
            let track_key: String
            let track_name: String
            let prompt: String
            let response: String
            let model: String
            let items: [IntelItem]
        }
        let payload = Payload(
            track_key: trackKey, track_name: trackName,
            prompt: prompt, response: response, model: model,
            items: items,
        )
        let data = try JSONEncoder().encode(payload)
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-digest-save", json], as: IntelDigestSaveResponse.self)
    }

    /// 正文 best-effort 抓取。
    func intelArticle(url: String, summary: String = "") throws -> IntelArticleResponse {
        struct Payload: Encodable {
            let url: String
            let summary: String
        }
        let data = try JSONEncoder().encode(Payload(url: url, summary: summary))
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-article", json], as: IntelArticleResponse.self)
    }

    /// 单篇改写（on-demand）。kind: investment | translation（chinese 保留后端，无 UI 入口）
    func intelRewrite(
        trackKey: String,
        trackName: String,
        item: IntelItem,
        force: Bool = false,
        kind: String = "investment"
    ) throws -> IntelRewriteResponse {
        struct Payload: Encodable {
            let track_key: String
            let track_name: String
            let item: IntelItem
            let force: Bool
            let kind: String
        }
        let payload = Payload(
            track_key: trackKey, track_name: trackName, item: item, force: force, kind: kind
        )
        let data = try JSONEncoder().encode(payload)
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-rewrite", json], as: IntelRewriteResponse.self)
    }

    /// 踢 Top-K rewrite worker（不阻塞雷达列表语义，调用方 fire-and-forget）。
    /// trackKey 给定时只预热该赛道（plan 2026-07-22-001 U4/U5）。
    func intelRewriteRun(
        k: Int? = nil, force: Bool = false, trackKey: String? = nil
    ) throws -> IntelRewriteRunResponse {
        struct Payload: Encodable {
            let k: Int?
            let force: Bool
            let track_key: String?
        }
        let data = try JSONEncoder().encode(Payload(k: k, force: force, track_key: trackKey))
        let json = String(data: data, encoding: .utf8) ?? "{}"
        return try run(["intel-rewrite-run", json], as: IntelRewriteRunResponse.self)
    }

    func themeLeaders() throws -> [ThemeLeaders] {
        try run(["theme-leaders"], as: [ThemeLeaders].self)
    }

    // MARK: 定时任务（launchd）

    func scheduledJobs() throws -> [ScheduledJob] {
        try run(["cron-list"], as: CronListResponse.self).jobs
    }

    /// cron-list 含分类排序的完整响应（U5 任务页读 categoryOrder 替代 Swift 硬编码）。
    func cronList() throws -> CronListResponse {
        try run(["cron-list"], as: CronListResponse.self)
    }

    func rerunJob(_ label: String) throws -> CronActionResult {
        try run(["cron-rerun", label], as: CronActionResult.self)
    }

    func setJobEnabled(_ label: String, enabled: Bool) throws -> CronActionResult {
        try run([enabled ? "cron-enable" : "cron-disable", label], as: CronActionResult.self)
    }

    /// 补跑所有「应跑未跑」的启用任务（关机漏跑自检）。
    func catchUpJobs() throws -> CronBatchResult {
        try run(["cron-catchup"], as: CronBatchResult.self)
    }

    func syncCronJobs() throws -> CronSyncResponse {
        try run(["cron-sync"], as: CronSyncResponse.self)
    }

    /// 批量重跑指定 label（空 = 全部启用项）；每个 label 仍走 bridge 白名单校验。
    func rerunJobs(_ labels: [String]) throws -> CronBatchResult {
        try run(["cron-rerun-many", labels.joined(separator: ",")], as: CronBatchResult.self)
    }

    /// 应用内排期编辑（设置页任务分区，plan 2026-07-12-005 / U6）。写命令，需人在环内确认。
    /// scheduleJSON 形如 `{"hour":18,"minute":30,"weekdays":[1,2,3,4,5]}` 或
    /// `{"weekly":{"weekday":5,"hour":20,"minute":0}}`。
    func editCronSchedule(suffix: String, scheduleJSON: String) throws -> CronActionResult {
        try run(["cron-edit-schedule", suffix, scheduleJSON], as: CronActionResult.self)
    }

    /// 自选列表整表替换（plan 2026-07-12-005 / U15）：写 kss.db，取代原先直接写
    /// storage/watchlist_symbols.txt 的 syncWatchlistFile。
    /// 盯盘 surface 配置读取（候选表 + 当前 append + 指标）。
    func surfaceGet() throws -> SurfaceGetResponse {
        try run(["surface-get"], as: SurfaceGetResponse.self)
    }

    /// 应用 surface patch（ops JSON 数组字符串）。
    func surfaceApply(opsJSON: String) throws -> SurfaceApplyResponse {
        try run(["surface-apply", opsJSON], as: SurfaceApplyResponse.self)
    }

    /// 档 A/B 自然语言解析 surface draft（不落盘；可能探针外网）。
    /// `slotId` 可选：strip 四槽已选槽时传入，NL 不必再写「第 N 张」。
    func surfaceNlInterpret(
        region: String,
        text: String,
        slotId: String? = nil
    ) throws -> SurfaceNlInterpretResponse {
        var args = ["surface-nl-interpret", region, text]
        if let slotId, !slotId.isEmpty {
            args.append(slotId)
        }
        return try run(args, as: SurfaceNlInterpretResponse.self)
    }

    /// Bind Catalog 只读搜索（slot + 可选 q）。
    func surfaceCatalog(slot: String, q: String = "") throws -> SurfaceCatalogResponse {
        if q.isEmpty {
            return try run(["surface-catalog", slot], as: SurfaceCatalogResponse.self)
        }
        return try run(["surface-catalog", slot, q], as: SurfaceCatalogResponse.self)
    }

    func setWatchlist(_ symbols: [String]) throws -> WatchlistSetResult {
        try run(["watchlist-set", symbols.joined(separator: ",")], as: WatchlistSetResult.self)
    }

    // MARK: 可投资地图（plan 2026-08-09-001 U5/U6/U7）
    //
    // 区位串、配额占比、陈旧标记都由 Python 算完返回（KTD2）；这里只做传输。
    // 配额分母由调用方传显式代码列表，不让 Python 去查自选表（KTD3）。

    /// 节点树 + 色板 + 主轴 + 每个节点的挂载个股与覆盖态。
    /// `codes` 是要挂到节点上的代码全集；传空则节点展开区一只票都没有。
    func investabilityMap(codes: [String] = []) throws -> ExposureMap {
        try run(["investability-map", codes.joined(separator: ",")], as: ExposureMap.self)
    }

    /// 批量取暴露信息；传单个代码即单票查询。空列表不打桥，直接返回空。
    func investabilityStocks(codes: [String]) throws -> [String: ExposureStock] {
        guard !codes.isEmpty else { return [:] }
        return try run(
            ["investability-stocks", codes.joined(separator: ",")],
            as: ExposureStocksResponse.self
        ).stocks
    }

    /// 组合暴露配额。`capPct` 为橙+紫合计上限，空串即不判定越线。
    func investabilitySummary(codes: [String], capPct: String = "") throws -> ExposureQuota {
        try run(
            ["investability-summary", codes.joined(separator: ","), capPct],
            as: ExposureQuota.self
        )
    }

    /// 整体替换一只票的节点标注；`primary` 传空串等同于清空该票全部标注。
    func investabilitySetLabel(
        symbol: String, primary: String, secondaries: [String] = []
    ) throws -> ExposureLabelResult {
        try run(
            ["investability-label", symbol, primary, secondaries.joined(separator: ",")],
            as: ExposureLabelResult.self
        )
    }

    /// 写一只票的单题 8 问答案。`value` 取 yes / no / unknown。
    func investabilitySetAnswer(
        symbol: String, question: Int, value: String
    ) throws -> ExposureAnswerResult {
        try run(
            ["investability-answer", symbol, String(question), value],
            as: ExposureAnswerResult.self
        )
    }

    /// 让助手给 8 问出草稿。只读命令——草稿不落库，界面上逐题人工确认才写。
    func investabilityAnswerDraft(symbol: String) throws -> ExposureAnswerDrafts {
        try run(["investability-answer-draft", symbol], as: ExposureAnswerDrafts.self)
    }

    /// 把节点标成已人工确认无标的，或撤销该确认（R9 的「空心描边」态）。
    func investabilitySetNodeCoverage(
        nodeId: String, confirmed: Bool
    ) throws -> ExposureNodeCoverageResult {
        try run(
            ["investability-node-coverage", nodeId, confirmed ? "true" : "false"],
            as: ExposureNodeCoverageResult.self
        )
    }

    /// 趋势页：某月月度格子。
    func trendsMonth(_ month: String) throws -> TrendMonth {
        try run(["trends-month", month], as: TrendMonth.self)
    }

    /// 趋势页：某日完整明细。
    func trendsDay(_ date: String) throws -> TrendDayDetail {
        try run(["trends-day", date], as: TrendDayDetail.self)
    }

    func heatmapSnapshot(market: String = "all", period: String = "day") throws -> HeatmapSnapshot {
        try run(["heatmap-snapshot", market, period], as: HeatmapSnapshot.self)
    }

    /// 长跑任务（run/import 拉数据、回测、回填）直接走 subprocess：sidecar 的 3s
    /// socket 超时会误判不可用并回退，而 daemon 仍在跑同一任务 → 双跑（重复 Tushare
    /// 调用 + 争抢同一归档）。这些命令不属于热路径读，无需暖 pandas。
    // perilla-enrichment 走外网(Tushare+yFinance)耗时常 >3s，跳过 sidecar 避免超时双跑。
    private static let subprocessOnlyCommands: Set<String> = [
        "run", "import", "perilla-enrichment",
        // 8 问草拟走外网 LLM，耗时常 >3s：跟 perilla-enrichment 同理跳过 sidecar，避免超时双跑
        "investability-answer-draft",
        "intel-radar", "intel-yupi-ingest", "intel-keywords-get", "intel-keywords-set",
        "yupi-status", "yupi-ensure",
        "intel-digest", "intel-panorama", "intel-digest-save",
        "intel-article", "intel-rewrite", "intel-rewrite-run",
        "cron-catchup", "cron-rerun", "cron-rerun-many", "cron-enable", "cron-disable",
        // surface-apply / propose / nl-interpret 可能探针外网（yfinance），避免 sidecar 3s 超时双跑
        "surface-apply", "surface-propose", "surface-get", "surface-metrics",
        "surface-nl-interpret", "surface-catalog",
    ]

    private func run<T: Decodable>(_ args: [String], as type: T.Type) throws -> T {
        // U5：热路径读优先常驻 sidecar（pandas 暖、无 per-call python 启动）；
        // socket 不可用回退 subprocess。长跑任务跳过 sidecar 避免 3s 超时双跑。
        if let cmd = args.first, !Self.subprocessOnlyCommands.contains(cmd),
           let envelope = try sidecarRequest(args) {
            return try Self.decodeEnvelope(envelope)
        }
        return try runSubprocess(args)
    }

    private func runSubprocess<T: Decodable>(_ args: [String]) throws -> T {
        let bridge = projectRoot.appending(path: "scripts/kss_app_bridge.py")
        let process = Process()
        process.executableURL = python
        process.arguments = [bridge.path] + args
        process.currentDirectoryURL = projectRoot
        // 显式注入双根 + 解释器，使 bridge 及其派生子脚本（U1 惰性 env 解析）一致定位代码/状态/运行时。
        var env = Self.sanitizedChildEnvironment()
        env["KSS_PROJECT_ROOT"] = projectRoot.path
        env["KSS_STATE_ROOT"] = stateRoot.path
        env["KSS_PYTHON"] = python.path
        // 禁止在 .app/Resources 写 __pycache__（会破坏 codesign sealed resources → 无法打开）
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = stateRoot.appending(path: ".cache/pycache").path
        // 一次性 bridge：完整 LLM + provider 映射（见 KeychainStore.bridgeEnvironment）。
        // intel-rewrite / panorama / digest 走 Python LLMClient，不能只用 sidecarEnvironment。
        for (key, value) in KeychainStore.bridgeEnvironment() {
            env[key] = value
        }
        // 诊断：不落密钥，只记是否注入成功（便于排「改写仍无凭据」）
        let hasLLM = env["KSS_LLM_PRIMARY_KEY"] != nil
            || env["DEEPSEEK_API_KEY"] != nil
            || env["OPENAI_API_KEY"] != nil
        if !hasLLM {
            NSLog("[KSS bridge] WARNING: no LLM key injected for subprocess cmd=%@", args.first ?? "?")
        }
        if let broker = CredentialBrokerRegistry.broker(for: stateRoot) {
            env["KSS_PI_AI_CREDENTIAL_SOCKET"] = broker.socketPath
            env["KSS_PI_AI_CREDENTIAL_NONCE"] = broker.nonce
        }
        process.environment = env

        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error

        try process.run()

        // Drain both pipes concurrently BEFORE waiting. The snapshot payload is
        // ~80KB, larger than the OS pipe buffer (~64KB); reading only after
        // waitUntilExit() deadlocks (bridge blocks on write, app blocks on exit).
        var errorData = Data()
        let errorQueue = DispatchQueue(label: "kss.bridge.stderr")
        errorQueue.async {
            errorData = error.fileHandleForReading.readDataToEndOfFile()
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        errorQueue.sync {}
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let message = String(data: errorData, encoding: .utf8) ?? "Bridge failed"
            throw BridgeError.processFailed(message.trimmingCharacters(in: .whitespacesAndNewlines))
        }

        return try Self.decodeEnvelope(data)
    }

    /// 桥协议版本（KTD3）；Python BRIDGE_SCHEMA_VERSION 必须同 commit 同步。
    static let supportedSchemaVersion = 1
    private struct SchemaProbe: Decodable { let schemaVersion: Int }
    private struct Envelope<T: Decodable>: Decodable { let schemaVersion: Int; let data: T }

    // MARK: - U5 常驻 sidecar（Unix socket）

    private var socketPath: String {
        stateRoot.appending(path: "run/kss-sidecar.sock").path
    }

    private var versionPath: String {
        stateRoot.appending(path: "run/kss-sidecar.version").path
    }

    private struct SidecarResponse: Decodable { let code: Int; let stdout: String?; let stderr: String? }
    private static let sidecarStartLock = NSLock()

    /// 计算本地 sidecar 代码指纹，与 Python 侧 `_sidecar_version_fingerprint()` 保持同步。
    /// 策略：dev 优先 git describe；bundle 或 git 失败 fallback 到 VERSION + 关键文件 hash。
    private static func sidecarVersionFingerprint(projectRoot: URL) -> String {
        if let git = runGitDescribe(in: projectRoot), !git.isEmpty {
            return git
        }

        var data = Data()
        let versionFile = projectRoot.appending(path: "scripts/VERSION")
        guard let versionData = try? Data(contentsOf: versionFile) else { return "unknown" }
        data.append(versionData)
        let bridgeFile = projectRoot.appending(path: "scripts/kss_app_bridge.py")
        if let d = try? Data(contentsOf: bridgeFile) { data.append(d) }
        let sidecarFile = projectRoot.appending(path: "scripts/kss_sidecar.py")
        if let d = try? Data(contentsOf: sidecarFile) { data.append(d) }
        let kernelFile = projectRoot.appending(path: "kss/agent/harness_kernel.py")
        if let d = try? Data(contentsOf: kernelFile) { data.append(d) }
        let liveFile = projectRoot.appending(path: "scripts/kss_harness_live.mjs")
        if let d = try? Data(contentsOf: liveFile) { data.append(d) }
        let hostFile = projectRoot.appending(path: "scripts/kss_harness_host.mjs")
        if let d = try? Data(contentsOf: hostFile) { data.append(d) }

        let digest = SHA256.hash(data: data)
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return "bundle:\(String(hex.prefix(16)))"
    }

    private static func runGitDescribe(in repo: URL) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = ["-C", repo.path, "describe", "--always", "--dirty"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        try? p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// socket 缺失、pid 失效、版本不匹配或 socket 无法 connect 则 spawn sidecar daemon
    ///（detached，best-effort），等其真正可接 ≤5s。U10：版本握手防止陈旧 sidecar 继续服务。
    /// 只看 sock 文件 + pid 存活不够：App 退出后 SIGHUP re-exec / SIGPIPE 可能留下
    /// 「文件还在、listen 已死」的残留，Agent 路径没有 subprocess 回退，会直接报无法连接。
    private func ensureSidecarRunning() {
        Self.sidecarStartLock.lock()
        defer { Self.sidecarStartLock.unlock() }
        let fm = FileManager.default
        if fm.fileExists(atPath: socketPath) {
            let pidPath = (stateRoot.appending(path: "run/kss-sidecar.pid")).path
            if let pidStr = try? String(contentsOfFile: pidPath, encoding: .utf8),
               let pid = pid_t(pidStr.trimmingCharacters(in: .whitespacesAndNewlines)),
               kill(pid, 0) == 0 {
                // 版本握手：version 文件缺失 / 空 / 不匹配 → 陈旧，杀旧拉新
                let expected = Self.sidecarVersionFingerprint(projectRoot: projectRoot)
                let current = (try? String(contentsOfFile: versionPath, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
                if let current, !current.isEmpty, current == expected,
                   Self.sidecarAcceptsConnection(path: socketPath) {
                    return  // socket + alive pid + version 一致 + 可 connect → 复用
                }
                // 旧 sidecar 不认识 version 命令/没写文件，或指纹不同，或 listen 已死
                kill(pid, SIGTERM)
                // 给旧进程留 0.2s 收尾；socket 随后会被新 daemon 替换
                Thread.sleep(forTimeInterval: 0.2)
            }
            // Stale：清理 pid/socket/version 后 respawn
            cleanupSidecarFiles()
        }
        let sidecar = projectRoot.appending(path: "scripts/kss_sidecar.py")
        guard fm.fileExists(atPath: sidecar.path) else { return }
        let p = Process()
        p.executableURL = python
        p.arguments = [sidecar.path]
        var env = Self.sanitizedChildEnvironment()
        env["KSS_PROJECT_ROOT"] = projectRoot.path
        env["KSS_STATE_ROOT"] = stateRoot.path
        env["KSS_PYTHON"] = python.path
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPYCACHEPREFIX"] = stateRoot.appending(path: ".cache/pycache").path
        for (key, value) in KeychainStore.sidecarEnvironment() { env[key] = value }
        if let broker = CredentialBrokerRegistry.broker(for: stateRoot) {
            env["KSS_PI_AI_CREDENTIAL_SOCKET"] = broker.socketPath
            env["KSS_PI_AI_CREDENTIAL_NONCE"] = broker.nonce
        }
        p.environment = env
        p.standardInput = FileHandle.nullDevice
        let logHandle = Self.sidecarLogHandle(stateRoot: stateRoot)
        p.standardOutput = logHandle
        p.standardError = logHandle
        do {
            try p.run()   // detached daemon，不 wait
        } catch {
            NSLog("[KSS] sidecar spawn failed python=%@ script=%@ error=%@",
                  python.path, sidecar.path, String(describing: error))
            return
        }
        for _ in 0..<50 {
            if Self.sidecarAcceptsConnection(path: socketPath) { return }
            Thread.sleep(forTimeInterval: 0.1)
        }
    }

    /// sock 文件存在不等于 listen 还活着。Agent 命令必须真能 connect。
    private static func sidecarAcceptsConnection(path: String) -> Bool {
        guard let fd = connectToSidecar(path: path) else { return false }
        close(fd)
        return true
    }

    /// sidecar 是常驻 daemon，此前 stdout/stderr 一律丢 /dev/null——排障时拿不到任何
    /// Python logging 输出。改落 storage/logs/sidecar.log（与既有 storage/logs/cron/*.log
    /// 同族约定），文件不存在则建；打开失败兜底回 /dev/null（不让日志问题拖垮 spawn）。
    /// 轮转（plan 2026-07-12-005 / U7 KTD10）：打开前检查大小，>10MB 轮转保留 3 代
    /// （sidecar.log.1/.2/.3），旧到 .3 的直接丢弃——只在 spawn 时机检查一次，足够。
    private static func sidecarLogHandle(stateRoot: URL) -> FileHandle {
        let logURL = stateRoot.appending(path: "storage/logs/sidecar.log")
        try? FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        rotateSidecarLogIfNeeded(logURL: logURL)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: logURL) else { return .nullDevice }
        handle.seekToEndOfFile()
        return handle
    }

    static let sidecarLogRotateThresholdBytes: UInt64 = 10 * 1024 * 1024
    static let sidecarLogRotateKeepGenerations = 3

    /// internal（非 private）以便 @testable 单测覆盖轮转纪律，不经真实 spawn 路径。
    static func rotateSidecarLogIfNeeded(logURL: URL) {
        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: logURL.path),
              let size = attrs[.size] as? UInt64,
              size > sidecarLogRotateThresholdBytes else { return }
        // .3 → 丢弃；.2 → .3；.1 → .2；current → .1（从最老代开始挪，避免覆盖冲突）。
        let oldestGen = logURL.appendingPathExtension(String(sidecarLogRotateKeepGenerations))
        try? fm.removeItem(at: oldestGen)
        for gen in stride(from: sidecarLogRotateKeepGenerations - 1, through: 1, by: -1) {
            let src = logURL.appendingPathExtension(String(gen))
            let dst = logURL.appendingPathExtension(String(gen + 1))
            if fm.fileExists(atPath: src.path) {
                try? fm.moveItem(at: src, to: dst)
            }
        }
        try? fm.moveItem(at: logURL, to: logURL.appendingPathExtension("1"))
    }

    private func cleanupSidecarFiles() {
        let runDir = stateRoot.appending(path: "run")
        for name in ["kss-sidecar.pid", "kss-sidecar.sock", "kss-sidecar.version"] {
            try? FileManager.default.removeItem(atPath: runDir.appending(path: name).path)
        }
    }

    /// 经 sidecar 调度。返回 envelope Data（成功）；nil = socket 不可用（回退 subprocess）；
    /// throw = 命令业务失败（code 1，不回退，与 subprocess 语义一致）。
    private func sidecarRequest(_ args: [String]) throws -> Data? {
        ensureSidecarRunning()
        let cmd = args.first ?? ""
        let rest = Array(args.dropFirst())
        guard var request = try? JSONSerialization.data(
            withJSONObject: ["cmd": cmd, "args": rest]) else { return nil }
        request.append(0x0A)
        // Longbridge 首连/行情常 >3s；过短会超时回退 subprocess（冷启 SDK 更慢）。
        let timeout: TimeInterval
        switch cmd {
        case "longbridge-quote", "intraday-snapshot", "intraday-bars", "trading-hours",
             "heatmap-snapshot":
            timeout = 20.0
        default:
            timeout = 3.0
        }
        guard let respData = Self.unixSocketRoundtrip(path: socketPath, request: request, timeout: timeout),
              let resp = try? JSONDecoder().decode(SidecarResponse.self, from: respData)
        else { return nil }   // 连不上/超时/响应不可解 → 回退 subprocess
        if resp.code == 0, let out = resp.stdout {
            return Data(out.utf8)
        }
        throw BridgeError.processFailed(
            (resp.stderr ?? "sidecar failed").trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// 一次 Unix domain socket 往返（连接→发请求→读到换行）。失败/超时返回 nil。
    static func unixSocketRoundtrip(path: String, request: Data, timeout: TimeInterval) -> Data? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        if fd < 0 { return nil }
        defer { close(fd) }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(path.utf8)
        let cap = MemoryLayout.size(ofValue: addr.sun_path) - 1
        let n = min(pathBytes.count, cap)
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            pathBytes.withUnsafeBytes { src in
                raw.baseAddress!.copyMemory(from: src.baseAddress!, byteCount: n)
            }
        }
        var tv = timeval(tv_sec: Int(timeout), tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        let connected = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                connect(fd, sa, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connected != 0 { return nil }

        let reqBytes = [UInt8](request)
        var sent = 0
        while sent < reqBytes.count {
            let w = reqBytes.withUnsafeBytes { raw in
                send(fd, raw.baseAddress!.advanced(by: sent), reqBytes.count - sent, 0)
            }
            if w <= 0 { return nil }
            sent += w
        }

        var out = Data()
        var buf = [UInt8](repeating: 0, count: 65536)
        while true {
            let r = read(fd, &buf, buf.count)
            if r <= 0 { break }
            out.append(contentsOf: buf[0..<r])
            if out.last == 0x0A { break }
        }
        return out.isEmpty ? nil : out
    }

    // MARK: - 流式聊天（#4 U3/U4）—— 独立于 unixSocketRoundtrip，无 3s 硬超时、逐帧投递

    /// 流式跑一轮聊天。**阻塞**调用（store 在 Task.detached 里跑）。
    /// onFrame：每收一帧调一次（后台线程，store 内自行 hop MainActor）。
    /// onConfirmRequired：收到 confirm_required 时同步调用，返回本人是否批准；据此在**同连接**写回确认。
    /// onEnd：流结束时调一次，error 非 nil 表示异常收尾（断连/超时/编码失败）。
    func chatTurn(messages: [[String: String]],
                  onFrame: @escaping (ChatFrame) -> Void,
                  onConfirmRequired: @escaping (ChatFrame) -> Bool,
                  onEnd: @escaping (String?) -> Void) {
        ensureSidecarRunning()
        guard var request = try? JSONSerialization.data(
            withJSONObject: ["cmd": "chat-turn", "messages": messages]) else {
            onEnd("无法编码聊天请求"); return
        }
        request.append(0x0A)
        Self.chatTurnStream(path: socketPath, request: request,
                            onFrame: onFrame, onConfirmRequired: onConfirmRequired, onEnd: onEnd)
    }

    func agentSessions(
        action: String = "list",
        sessionId: String? = nil,
        title: String? = nil,
        providerRoute: AgentProviderRoute? = nil
    ) throws -> AgentSessionListResponse {
        var payload: [String: Any] = ["action": action]
        if let sessionId { payload["session_id"] = sessionId }
        if let title { payload["title"] = title }
        if let providerRoute,
           let data = try? JSONEncoder().encode(providerRoute),
           let object = try? JSONSerialization.jsonObject(with: data) {
            payload["provider_route"] = object
        }
        return try agentCommand("agent-session", payload: payload, as: AgentSessionListResponse.self)
    }

    func agentSkills(action: String = "list", sessionId: String? = nil, skillId: String? = nil,
                     pinned: Bool? = nil, enabled: Bool? = nil) throws -> AgentSkillsResponse {
        var payload: [String: Any] = ["action": action]
        if let sessionId { payload["session_id"] = sessionId }
        if let skillId { payload["skill_id"] = skillId }
        if let pinned { payload["pinned"] = pinned }
        if let enabled { payload["enabled"] = enabled }
        return try agentCommand("agent-skills", payload: payload, as: AgentSkillsResponse.self)
    }

    func agentMemories(action: String = "list", query: String? = nil, memoryId: String? = nil,
                       candidateId: String? = nil, approved: Bool? = nil, text: String? = nil,
                       kind: String? = nil, sourceSession: String? = nil) throws -> AgentMemoriesResponse {
        var payload: [String: Any] = ["action": action]
        if let query { payload["query"] = query }
        if let memoryId { payload["memory_id"] = memoryId }
        if let candidateId { payload["candidate_id"] = candidateId }
        if let approved { payload["approved"] = approved }
        if let text { payload["text"] = text }
        if let kind { payload["kind"] = kind }
        if let sourceSession { payload["source_session"] = sourceSession }
        return try agentCommand("agent-memories", payload: payload, as: AgentMemoriesResponse.self)
    }

    func agentQueue(action: String = "list", sessionId: String, queueId: String? = nil) throws -> AgentQueueResponse {
        var payload: [String: Any] = [
            "action": action,
            "session_id": sessionId,
        ]
        if let queueId { payload["queue_id"] = queueId }
        return try agentCommand("agent-queue", payload: payload, as: AgentQueueResponse.self)
    }

    func agentProviders(
        action: String = "list",
        primary: AgentProviderRoute? = nil,
        fallback: AgentProviderRoute? = nil,
        vision: AgentProviderRoute? = nil,
        clearVision: Bool = false,
        customProvider: [String: Any]? = nil,
        customProviderId: String? = nil
    ) throws -> AgentProvidersResponse {
        var payload: [String: Any] = ["action": action]
        if action == "reload_credentials",
           let broker = CredentialBrokerRegistry.broker(for: stateRoot, refreshNonce: true) {
            payload["socket_path"] = broker.socketPath
            payload["nonce"] = broker.nonce
        }
        let encoder = JSONEncoder()
        if let primary,
           let data = try? encoder.encode(primary),
           let object = try? JSONSerialization.jsonObject(with: data) {
            payload["primary"] = object
        }
        if let fallback,
           let data = try? encoder.encode(fallback),
           let object = try? JSONSerialization.jsonObject(with: data) {
            payload["fallback"] = object
        }
        // vision 键的存在性即语义：带 dict 覆盖，带 null 清除，缺省保持。
        if let vision,
           let data = try? encoder.encode(vision),
           let object = try? JSONSerialization.jsonObject(with: data) {
            payload["vision"] = object
        } else if clearVision {
            payload["vision"] = NSNull()
        }
        if let customProvider {
            payload["provider"] = customProvider
        }
        if let customProviderId {
            payload["provider_id"] = customProviderId
        }
        return try agentCommand("agent-providers", payload: payload, as: AgentProvidersResponse.self)
    }

    /// Seesaw slash command:catalog 列只读工具;run 直连执行并落会话。
    func agentSlash(
        action: String,
        sessionId: String? = nil,
        name: String? = nil,
        args: [String: String]? = nil
    ) throws -> AgentSlashResponse {
        var payload: [String: Any] = ["action": action]
        if let sessionId { payload["session_id"] = sessionId }
        if let name { payload["name"] = name }
        if let args { payload["args"] = args }
        return try agentCommand("agent-slash", payload: payload, as: AgentSlashResponse.self)
    }

    func agentAttachments(
        action: String,
        sessionId: String,
        path: String? = nil,
        attachmentId: String? = nil,
        extractedText: String? = nil
    ) throws -> AgentAttachmentsResponse {
        var payload: [String: Any] = [
            "action": action,
            "session_id": sessionId,
        ]
        if let path { payload["path"] = path }
        if let attachmentId { payload["attachment_id"] = attachmentId }
        if let extractedText { payload["extracted_text"] = extractedText }
        return try agentCommand("agent-attachments", payload: payload, as: AgentAttachmentsResponse.self)
    }

    func agentResearch(
        action: String = "list",
        clientRequestId: String? = nil,
        sessionId: String? = nil,
        goalId: String? = nil,
        taskId: String? = nil,
        profileId: String? = nil,
        executionMode: String? = nil,
        objective: String? = nil,
        inputs: [String: String]? = nil,
        budgetOverrides: [String: Int]? = nil,
        origin: String? = nil,
        cadence: String? = nil,
        profileIds: [String]? = nil,
        limit: Int? = nil,
        cursor: String? = nil,
        path: String? = nil
    ) throws -> ResearchResponse {
        var payload: [String: Any] = ["action": action]
        if let clientRequestId { payload["client_request_id"] = clientRequestId }
        if let sessionId { payload["session_id"] = sessionId }
        if let goalId { payload["goal_id"] = goalId }
        if let taskId { payload["task_id"] = taskId }
        if let profileId { payload["profile_id"] = profileId }
        if let executionMode { payload["execution_mode"] = executionMode }
        if let objective { payload["objective"] = objective }
        if let inputs { payload["inputs"] = inputs }
        if let budgetOverrides { payload["budget_overrides"] = budgetOverrides }
        if let origin { payload["origin"] = origin }
        if let cadence { payload["cadence"] = cadence }
        if let profileIds { payload["profile_ids"] = profileIds }
        if let limit { payload["limit"] = limit }
        if let cursor { payload["cursor"] = cursor }
        if let path { payload["path"] = path }
        return try agentCommand("agent-research", payload: payload, as: ResearchResponse.self)
    }

    func agentArtifacts(
        action: String = "list",
        goalId: String,
        artifactId: String? = nil,
        destination: String? = nil,
        overwrite: Bool? = nil
    ) throws -> ResearchArtifactResponse {
        var payload: [String: Any] = [
            "action": action,
            "goal_id": goalId,
        ]
        if let artifactId { payload["artifact_id"] = artifactId }
        if let destination { payload["destination"] = destination }
        if let overwrite { payload["overwrite"] = overwrite }
        return try agentCommand("agent-artifacts", payload: payload, as: ResearchArtifactResponse.self)
    }

    /// Replays research events after a durable sequence. The server may return a
    /// finite replay or keep the socket open for live events; callers therefore run
    /// this method off the main actor.
    func agentResearchEvents(
        goalId: String,
        afterSequence: Int = 0,
        onEvent: @escaping (ResearchEvent) -> Void,
        onEnd: @escaping (String?) -> Void
    ) {
        ensureSidecarRunning()
        guard var request = try? JSONSerialization.data(withJSONObject: [
            "cmd": "agent-research-events",
            "goal_id": goalId,
            "after_sequence": afterSequence,
        ]) else {
            onEnd("无法编码研究事件请求")
            return
        }
        request.append(0x0A)
        guard let fd = Self.connectToSidecar(path: socketPath) else {
            onEnd("无法连接 Agent sidecar")
            return
        }
        Self.runResearchEventStream(fd: fd, request: request, onEvent: onEvent, onEnd: onEnd)
    }

    func agentTurn(sessionId: String, clientTurnId: String, input: String,
                   sourceQueueId: String? = nil,
                   attachmentIds: [String] = [],
                   fileRefs: [String] = [],
                   liveContextScope: [String: String]? = nil,
                   onControlReady: @escaping (AgentControlChannel) -> Void,
                   onFrame: @escaping (AgentFrame) -> Void,
                   onConfirmRequired: @escaping (AgentFrame) -> Bool,
                   onEnd: @escaping (String?) -> Void) {
        ensureSidecarRunning()
        var payload: [String: Any] = [
            "cmd": "agent-turn",
            "session_id": sessionId,
            "client_turn_id": clientTurnId,
            "input": input,
        ]
        if let sourceQueueId { payload["source_queue_id"] = sourceQueueId }
        if !attachmentIds.isEmpty { payload["attachment_ids"] = attachmentIds }
        if !fileRefs.isEmpty { payload["file_refs"] = fileRefs }
        if let liveContextScope { payload["live_context_scope"] = liveContextScope }
        guard var request = try? JSONSerialization.data(withJSONObject: payload) else {
            onEnd("无法编码 Agent 请求"); return
        }
        request.append(0x0A)
        Self.agentTurnStream(
            path: socketPath,
            request: request,
            respawn: { [self] in
                cleanupSidecarFiles()
                ensureSidecarRunning()
            },
            onControlReady: onControlReady, onFrame: onFrame,
            onConfirmRequired: onConfirmRequired, onEnd: onEnd)
    }

    private func agentCommand<T: Decodable>(_ cmd: String, payload: [String: Any], as type: T.Type) throws -> T {
        ensureSidecarRunning()
        var requestPayload = payload
        requestPayload["cmd"] = cmd
        guard var request = try? JSONSerialization.data(withJSONObject: requestPayload) else {
            throw BridgeError.invalidOutput
        }
        request.append(0x0A)

        func roundtrip() -> SidecarResponse? {
            guard let respData = Self.unixSocketRoundtrip(path: socketPath, request: request, timeout: 20) else {
                return nil
            }
            return try? JSONDecoder().decode(SidecarResponse.self, from: respData)
        }

        func decodeOrThrow(_ resp: SidecarResponse) throws -> T {
            if resp.code == 0, let out = resp.stdout {
                return try Self.decodeEnvelope(Data(out.utf8))
            }
            throw BridgeError.processFailed(
                (resp.stderr ?? "Agent sidecar failed").trimmingCharacters(in: .whitespacesAndNewlines))
        }

        if let resp = roundtrip() {
            return try decodeOrThrow(resp)
        }
        cleanupSidecarFiles()
        ensureSidecarRunning()
        guard let retry = roundtrip() else {
            throw BridgeError.processFailed("无法连接 Agent sidecar")
        }
        return try decodeOrThrow(retry)
    }

    /// 连接 → 发请求 → 逐 newline 帧读到 done/error/EOF。SO_RCVTIMEO 作 **idle 间隔**而非硬超时。
    /// 连接失败时清理 stale pid/socket 并自动 respawn + 重试一次（处理 sidecar 突然崩溃的情况）。
    private static func chatTurnStream(path: String, request: Data,
                                       onFrame: @escaping (ChatFrame) -> Void,
                                       onConfirmRequired: @escaping (ChatFrame) -> Bool,
                                       onEnd: @escaping (String?) -> Void) {
        // 第一次连接尝试
        if let fd = connectToSidecar(path: path) {
            runStreamLoop(fd: fd, request: request,
                          onFrame: onFrame, onConfirmRequired: onConfirmRequired, onEnd: onEnd)
            return
        }
        // 连接失败：清理 stale 文件 + 触发 respawn（通过清理 socket 强制 ensureSidecarRunning 走 respawn 分支）
        let socketURL = URL(fileURLWithPath: path)
        let pidURL = socketURL.deletingLastPathComponent().appendingPathComponent("kss-sidecar.pid")
        try? FileManager.default.removeItem(at: socketURL)
        try? FileManager.default.removeItem(at: pidURL)
        // 等 sidecar 重新 spawn（最多 3s，匹配 ensureSidecarRunning）
        Thread.sleep(forTimeInterval: 0.5)
        // 第二次连接尝试
        if let fd = connectToSidecar(path: path) {
            runStreamLoop(fd: fd, request: request,
                          onFrame: onFrame, onConfirmRequired: onConfirmRequired, onEnd: onEnd)
        } else {
            onEnd("无法连接 sidecar")
        }
    }

    private static func agentTurnStream(path: String, request: Data,
                                        respawn: (() -> Void)? = nil,
                                        onControlReady: @escaping (AgentControlChannel) -> Void,
                                        onFrame: @escaping (AgentFrame) -> Void,
                                        onConfirmRequired: @escaping (AgentFrame) -> Bool,
                                        onEnd: @escaping (String?) -> Void) {
        if let fd = connectToSidecar(path: path) {
            runAgentStreamLoop(fd: fd, request: request,
                               onControlReady: onControlReady, onFrame: onFrame,
                               onConfirmRequired: onConfirmRequired, onEnd: onEnd)
            return
        }
        respawn?()
        if let fd = connectToSidecar(path: path) {
            runAgentStreamLoop(fd: fd, request: request,
                               onControlReady: onControlReady, onFrame: onFrame,
                               onConfirmRequired: onConfirmRequired, onEnd: onEnd)
        } else {
            onEnd("无法连接 Agent sidecar")
        }
    }

    /// Unix domain socket 连接。成功返回 fd，失败返回 nil。
    private static func connectToSidecar(path: String) -> Int32? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        if fd < 0 { return nil }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(path.utf8)
        let cap = MemoryLayout.size(ofValue: addr.sun_path) - 1
        let n = min(pathBytes.count, cap)
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            pathBytes.withUnsafeBytes { src in
                raw.baseAddress!.copyMemory(from: src.baseAddress!, byteCount: n)
            }
        }
        var tv = timeval(tv_sec: 1, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        let connected = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                connect(fd, sa, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connected != 0 {
            close(fd)
            return nil
        }
        return fd
    }

    /// 已有 fd 的 stream 循环：发请求 → 读帧 → done/error/EOF 退出。
    private static func runStreamLoop(fd: Int32, request: Data,
                                     onFrame: @escaping (ChatFrame) -> Void,
                                     onConfirmRequired: @escaping (ChatFrame) -> Bool,
                                     onEnd: @escaping (String?) -> Void) {
        defer { close(fd) }
        let reqBytes = [UInt8](request)
        var sent = 0
        while sent < reqBytes.count {
            let w = reqBytes.withUnsafeBytes { raw in
                send(fd, raw.baseAddress!.advanced(by: sent), reqBytes.count - sent, 0)
            }
            if w <= 0 { onEnd("发送请求失败"); return }
            sent += w
        }

        var acc = Data()
        var buf = [UInt8](repeating: 0, count: 65536)
        var idleTicks = 0
        let maxIdleTicks = 300        // 1s × 300 = 5min 无任何数据 → 放弃（idle 总上限）
        while true {
            let r = read(fd, &buf, buf.count)
            if r > 0 {
                idleTicks = 0
                acc.append(contentsOf: buf[0..<r])
                // 逐 newline 切帧 —— 不在首个 0x0A break（流式可能一次收多帧）
                while let nl = acc.firstIndex(of: 0x0A) {
                    let lineData = acc.subdata(in: acc.startIndex..<nl)
                    acc.removeSubrange(acc.startIndex...nl)
                    if lineData.isEmpty { continue }
                    guard let frame = try? JSONDecoder().decode(ChatFrame.self, from: lineData)
                    else { continue }
                    onFrame(frame)
                    if frame.type == "confirm_required" {
                        let approved = onConfirmRequired(frame)
                        sendConfirm(fd: fd, callId: frame.callId ?? "", approved: approved)
                    }
                    if frame.type == "done" || frame.type == "error" {
                        onEnd(nil); return
                    }
                }
            } else if r == 0 {
                onEnd("连接中断"); return          // EOF
            } else {
                let e = errno
                if e == EAGAIN || e == EWOULDBLOCK {   // idle：本秒无数据，继续等
                    idleTicks += 1
                    if idleTicks >= maxIdleTicks { onEnd("响应超时"); return }
                    continue
                }
                onEnd("读取错误 errno=\(e)"); return    // 真错
            }
        }
    }

    private static func runAgentStreamLoop(fd: Int32, request: Data,
                                           onControlReady: @escaping (AgentControlChannel) -> Void,
                                           onFrame: @escaping (AgentFrame) -> Void,
                                           onConfirmRequired: @escaping (AgentFrame) -> Bool,
                                           onEnd: @escaping (String?) -> Void) {
        defer { close(fd) }
        let control = AgentControlChannel(fd: fd)
        onControlReady(control)
        let reqBytes = [UInt8](request)
        var sent = 0
        while sent < reqBytes.count {
            let w = reqBytes.withUnsafeBytes { raw in
                send(fd, raw.baseAddress!.advanced(by: sent), reqBytes.count - sent, 0)
            }
            if w <= 0 { onEnd("发送 Agent 请求失败"); return }
            sent += w
        }

        var acc = Data()
        var buf = [UInt8](repeating: 0, count: 65536)
        var idleTicks = 0
        var streamError: String?
        var confirmSeen = false
        while true {
            let r = read(fd, &buf, buf.count)
            if r > 0 {
                idleTicks = 0
                acc.append(contentsOf: buf[0..<r])
                while let nl = acc.firstIndex(of: 0x0A) {
                    let lineData = acc.subdata(in: acc.startIndex..<nl)
                    acc.removeSubrange(acc.startIndex...nl)
                    if lineData.isEmpty { continue }
                    guard let frame = try? JSONDecoder().decode(AgentFrame.self, from: lineData)
                    else { continue }
                    onFrame(frame)
                    if frame.type == "confirm_required" {
                        // Chrome owns the answerer. Sending confirm here would auto-deny
                        // because onConfirmRequired no longer blocks the reader.
                        confirmSeen = true
                        _ = onConfirmRequired(frame)
                    }
                    if Self.isAgentStreamTerminal(frame) {
                        onEnd(nil)
                        return
                    }
                    if frame.type == "error" {
                        streamError = frame.error ?? "Agent error"
                    }
                    if frame.type == "agent_end" {
                        onEnd(streamError)
                        return
                    }
                }
            } else if r == 0 {
                onEnd(streamError ?? "Agent 连接中断"); return
            } else {
                let e = errno
                if e == EAGAIN || e == EWOULDBLOCK {
                    idleTicks += 1
                    if idleTicks >= Self.agentStreamIdleBudget(confirmSeen: confirmSeen) {
                        onEnd("Agent 响应超时"); return
                    }
                    continue
                }
                onEnd("Agent 读取错误 errno=\(e)"); return
            }
        }
    }

    /// 审批等待期 sidecar 无帧可发（人未 tap 时最长 300s 才自动拒绝恢复出帧）。
    /// 空闲预算必须盖过这段静默，否则读循环先断连，确认条一挂就杀回合。
    static func agentStreamIdleBudget(confirmSeen: Bool) -> Int {
        confirmSeen ? 360 : 300
    }

    private static func runResearchEventStream(
        fd: Int32,
        request: Data,
        onEvent: @escaping (ResearchEvent) -> Void,
        onEnd: @escaping (String?) -> Void
    ) {
        defer { close(fd) }
        let requestBytes = [UInt8](request)
        var sent = 0
        while sent < requestBytes.count {
            let written = requestBytes.withUnsafeBytes { raw in
                send(fd, raw.baseAddress!.advanced(by: sent), requestBytes.count - sent, 0)
            }
            if written <= 0 {
                onEnd("发送研究事件请求失败")
                return
            }
            sent += written
        }

        var accumulator = Data()
        var buffer = [UInt8](repeating: 0, count: 65_536)
        var idleTicks = 0
        while true {
            let readCount = read(fd, &buffer, buffer.count)
            if readCount > 0 {
                idleTicks = 0
                accumulator.append(contentsOf: buffer[0..<readCount])
                while let newline = accumulator.firstIndex(of: 0x0A) {
                    let line = accumulator.subdata(in: accumulator.startIndex..<newline)
                    accumulator.removeSubrange(accumulator.startIndex...newline)
                    guard !line.isEmpty else { continue }
                    if let event = try? JSONDecoder().decode(ResearchEvent.self, from: line) {
                        onEvent(event)
                    }
                }
            } else if readCount == 0 {
                onEnd(nil)
                return
            } else if errno == EAGAIN || errno == EWOULDBLOCK {
                idleTicks += 1
                if idleTicks >= 300 {
                    onEnd("研究事件响应超时")
                    return
                }
            } else {
                onEnd("研究事件读取错误 errno=\(errno)")
                return
            }
        }
    }

    static func isAgentDuplicateTerminal(_ frame: AgentFrame) -> Bool {
        frame.duplicateReason != nil
    }

    static func isAgentStreamTerminal(_ frame: AgentFrame) -> Bool {
        isAgentDuplicateTerminal(frame) || frame.type == "agent_end"
    }

    /// Legacy chat-turn adapter onto the same grant path as `AgentControlChannel.confirm`.
    private static func sendConfirm(fd: Int32, callId: String, approved: Bool) {
        AgentControlChannel(fd: fd).confirm(runId: nil, callId: callId, approved: approved)
    }

    final class AgentControlChannel {
        private let fd: Int32
        private let lock = NSLock()

        init(fd: Int32) {
            self.fd = fd
        }

        func confirm(runId: String?, callId: String, approved: Bool) {
            send([
                "cmd": "agent-control",
                "action": "confirm",
                "run_id": runId ?? "",
                "call_id": callId,
                "approved": approved,
            ])
        }

        func abort(runId: String?) {
            send([
                "cmd": "agent-control",
                "action": "abort",
                "run_id": runId ?? "",
            ])
        }

        func steer(
            runId: String?,
            clientMessageId: String,
            input: String,
            sourceQueueId: String? = nil
        ) {
            enqueue(
                action: "steer",
                runId: runId,
                clientMessageId: clientMessageId,
                input: input,
                sourceQueueId: sourceQueueId)
        }

        func followUp(
            runId: String?,
            clientMessageId: String,
            input: String,
            sourceQueueId: String? = nil
        ) {
            enqueue(
                action: "follow_up",
                runId: runId,
                clientMessageId: clientMessageId,
                input: input,
                sourceQueueId: sourceQueueId)
        }

        private func enqueue(
            action: String,
            runId: String?,
            clientMessageId: String,
            input: String,
            sourceQueueId: String?
        ) {
            var payload: [String: Any] = [
                "cmd": "agent-control",
                "action": action,
                "run_id": runId ?? "",
                "client_message_id": clientMessageId,
                "input": input,
            ]
            if let sourceQueueId { payload["source_queue_id"] = sourceQueueId }
            send(payload)
        }

        private func send(_ object: [String: Any]) {
            guard var line = try? JSONSerialization.data(withJSONObject: object) else { return }
            line.append(0x0A)
            let bytes = [UInt8](line)
            lock.lock()
            defer { lock.unlock() }
            var sent = 0
            while sent < bytes.count {
                let w = bytes.withUnsafeBytes { raw in
                    Darwin.send(fd, raw.baseAddress!.advanced(by: sent), bytes.count - sent, 0)
                }
                if w <= 0 { return }
                sent += w
            }
        }
    }

    /// 版本化信封两段解码（KTD3，可测 seam）：先探 schemaVersion，不匹配 throw
    /// `.schemaMismatch`（可读横幅）；缺字段视为 v0（旧/未包裹）；再解 `data` 为 T。
    static func decodeEnvelope<T: Decodable>(_ data: Data) throws -> T {
        // 兼容 SDK/第三方往 stdout 夹带杂讯：取首个完整 JSON 对象再解码。
        let jsonData = Self.extractJSONObject(from: data) ?? data
        let probe = try? JSONDecoder().decode(SchemaProbe.self, from: jsonData)
        let version = probe?.schemaVersion ?? 0
        guard version == supportedSchemaVersion else {
            throw BridgeError.schemaMismatch(bridge: version, app: supportedSchemaVersion)
        }
        do {
            return try JSONDecoder().decode(Envelope<T>.self, from: jsonData).data
        } catch {
            throw BridgeError.invalidOutput
        }
    }

    /// 从可能含前缀/后缀杂讯的 stdout 中切出第一个顶层 `{...}` JSON 对象。
    static func extractJSONObject(from data: Data) -> Data? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        guard let start = text.firstIndex(of: "{") else { return nil }
        var depth = 0
        var inString = false
        var escape = false
        var i = start
        while i < text.endIndex {
            let ch = text[i]
            if inString {
                if escape {
                    escape = false
                } else if ch == "\\" {
                    escape = true
                } else if ch == "\"" {
                    inString = false
                }
            } else {
                switch ch {
                case "\"": inString = true
                case "{": depth += 1
                case "}":
                    depth -= 1
                    if depth == 0 {
                        let slice = text[start...i]
                        return Data(slice.utf8)
                    }
                default: break
                }
            }
            i = text.index(after: i)
        }
        return nil
    }

    /// A signed app always uses its embedded code and Application Support
    /// state. Ambient development overrides are only honored by non-bundled
    /// test/dev executables.
    private static var isBundledApp: Bool {
        Bundle.main.bundleURL.pathExtension.lowercased() == "app"
    }

    /// dev-mode 判定：非 app bundle 且 `KSS_PROJECT_ROOT` env 存在。
    private static var isDevMode: Bool {
        !isBundledApp && ProcessInfo.processInfo.environment["KSS_PROJECT_ROOT"] != nil
    }

    /// bundle-mode 状态根默认：`~/Library/Application Support/KSS`。
    private static var appSupportDefault: URL {
        FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "KSS")
    }

    /// 安装/首启写下的面包屑：`~/Library/Application Support/KSS/breadcrumb.json`，
    /// 记 `{projectRoot, stateRoot}`，bundle-mode 据此定位（取代 8 层文件系统爬升）。
    private static var breadcrumbURL: URL {
        appSupportDefault.appending(path: "breadcrumb.json")
    }

    private struct Breadcrumb: Codable { var projectRoot: String; var stateRoot: String }

    private static func readBreadcrumb() -> Breadcrumb? {
        guard let data = try? Data(contentsOf: breadcrumbURL) else { return nil }
        return try? JSONDecoder().decode(Breadcrumb.self, from: data)
    }

    /// 持久化解析结果（bundle-mode 首启），供后续启动免再探测。
    static func writeBreadcrumb(project: URL, state: URL) {
        let dir = appSupportDefault
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let crumb = Breadcrumb(projectRoot: project.path, stateRoot: state.path)
        if let data = try? JSONEncoder().encode(crumb) {
            try? data.write(to: breadcrumbURL)
        }
    }

    /// launchd 的 projectRoot 必须是可写仓库，不能是 `.app/Contents/Resources`。
    /// 已装 App 的代码根仍是 Resources；面包屑只保存「真实仓库 + Application Support」。
    static func packagedBreadcrumbProjectRoot(
        codeRoot: URL,
        existingProjectRoot: String?,
        fileManager: FileManager = .default
    ) -> URL? {
        func isBundlePath(_ path: String) -> Bool {
            path.contains(".app/Contents")
        }
        func isLaunchdProject(_ url: URL) -> Bool {
            !isBundlePath(url.path)
                && fileManager.fileExists(atPath: url.appending(path: "scripts/kss_app_bridge.py").path)
        }
        if isLaunchdProject(codeRoot) {
            return codeRoot.standardizedFileURL
        }
        if let existingProjectRoot, existingProjectRoot.hasPrefix("/") {
            let candidate = URL(fileURLWithPath: existingProjectRoot).standardizedFileURL
            if isLaunchdProject(candidate) {
                return candidate
            }
        }
        return nil
    }

    /// 解析 (代码根, 状态根)。优先级：
    /// projectRoot = KSS_PROJECT_ROOT(dev) → breadcrumb → bundle Resources → 历史爬升兜底。
    /// stateRoot   = KSS_STATE_ROOT → 有效 breadcrumb → (dev? projectRoot : ~/Library/Application Support/KSS)。
    /// bundle 的代码仍来自 Resources，但可变数据继续使用安装期记录的状态根；二者不能混为一处。
    private static func resolveRoots() -> (project: URL, state: URL)? {
        let fm = FileManager.default
        let envProject = isBundledApp ? nil : ProcessInfo.processInfo.environment["KSS_PROJECT_ROOT"]
        let envState = isBundledApp ? nil : ProcessInfo.processInfo.environment["KSS_STATE_ROOT"]
        let crumb = readBreadcrumb()

        func hasBridge(_ url: URL) -> Bool {
            fm.fileExists(atPath: url.appending(path: "scripts/kss_app_bridge.py").path)
        }

        // ---- projectRoot（KTD7 三层：dev env > 同步代码 override > bundle Resources > 面包屑 > 兜底）----
        // bundle 模式下：只要 Resources 内嵌 scripts/ 存在，就优先用 bundle 代码，避免 breadcrumb
        // 残留指向旧主仓库导致 app 升级后仍读旧代码。
        let envScripts = isBundledApp ? nil : ProcessInfo.processInfo.environment["KSS_SCRIPTS_ROOT"]
        var project: URL?
        if let envProject { project = URL(fileURLWithPath: envProject) }            // dev 硬分支
        else if let envScripts,                                                      // 同步代码 override（iCloud/共享）
                hasBridge(URL(fileURLWithPath: envScripts)) {
            project = URL(fileURLWithPath: envScripts)
        }
        else if !isDevMode,
                let resources = Bundle.main.resourceURL,
                hasBridge(resources) {
            // bundle 模式：优先使用 .app/Resources 内嵌脚本，与 app 版本严格对齐。
            project = resources
        }
        else if let crumb { project = URL(fileURLWithPath: crumb.projectRoot) }     // bundle 面包屑（fallback）
        else {
            // bundle baseline：scripts 随 .app 进 Resources。
            let resources = Bundle.main.resourceURL
            if let resources, hasBridge(resources) { project = resources }
        }
        // 兜底：历史向上爬升（仅当上面全落空，dev 无 env 启动等罕见场景）。
        if project == nil || !(project.map(hasBridge) ?? false) {
            for start in [URL(fileURLWithPath: fm.currentDirectoryPath),
                          Bundle.main.executableURL?.deletingLastPathComponent()].compactMap({ $0 }) {
                var url = start
                for _ in 0..<8 {
                    if hasBridge(url) { project = url; break }
                    let parent = url.deletingLastPathComponent()
                    if parent.path == url.path { break }
                    url = parent
                }
                if project.map(hasBridge) ?? false { break }
            }
        }
        guard let resolvedProject = project, hasBridge(resolvedProject) else { return nil }

        // ---- stateRoot ----
        let state = selectStateRoot(
            envState: envState,
            breadcrumbState: crumb?.stateRoot,
            isDevMode: isDevMode,
            projectRoot: resolvedProject,
            appSupportRoot: appSupportDefault,
            fileManager: fm
        )

        return (resolvedProject, state)
    }

    /// 只负责状态根优先级，保留为可单测的启动契约。
    /// 环境变量是显式覆盖，允许指向尚未创建的目录；面包屑来自旧安装，仅接受仍存在的绝对目录。
    /// 已安装 App（非 dev）不得把 git 工作副本当状态根：开发期 `swift run` 留下的
    /// breadcrumb 会让公证包去打仓库 venv/socket，退出后再开就复用已死的 sidecar。
    static func selectStateRoot(
        envState: String?,
        breadcrumbState: String?,
        isDevMode: Bool,
        projectRoot: URL,
        appSupportRoot: URL,
        fileManager: FileManager = .default
    ) -> URL {
        if let envState, !envState.isEmpty {
            return URL(fileURLWithPath: envState).standardizedFileURL
        }
        if let breadcrumbState, breadcrumbState.hasPrefix("/") {
            let candidate = URL(fileURLWithPath: breadcrumbState).standardizedFileURL
            var isDirectory: ObjCBool = false
            if fileManager.fileExists(atPath: candidate.path, isDirectory: &isDirectory),
               isDirectory.boolValue {
                if !isDevMode, isSourceCheckout(candidate, fileManager: fileManager) {
                    return appSupportRoot
                }
                return candidate
            }
        }
        return isDevMode ? projectRoot : appSupportRoot
    }

    /// git 工作副本（含 `.git` 文件或目录）不能作为已安装 App 的可变状态根。
    static func isSourceCheckout(_ url: URL, fileManager: FileManager = .default) -> Bool {
        fileManager.fileExists(atPath: url.appending(path: ".git").path)
    }

    // MARK: - U2 运行时解析 + 首启 bootstrap

    /// bridge 脚本解释器：KSS_PYTHON env → state-root bootstrap venv → 系统 python3。
    static func resolvePython(stateRoot: URL) -> URL {
        let fm = FileManager.default
        if !isBundledApp,
           let envPy = ProcessInfo.processInfo.environment["KSS_PYTHON"],
           fm.isExecutableFile(atPath: envPy) {
            return URL(fileURLWithPath: envPy)
        }
        let venvPy = stateRoot.appending(path: "venv/bin/python3")
        if fm.isExecutableFile(atPath: venvPy.path) { return venvPy }
        return URL(fileURLWithPath: "/usr/bin/python3")
    }

    /// 定位 uv 可执行（PATH + 常见安装位置）。
    private static func findUV() -> URL? {
        let fm = FileManager.default
        var candidates = ["\(NSHomeDirectory())/.local/bin/uv",
                          "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
        if let path = ProcessInfo.processInfo.environment["PATH"] {
            candidates += path.split(separator: ":").map { "\($0)/uv" }
        }
        for c in candidates where fm.isExecutableFile(atPath: c) {
            return URL(fileURLWithPath: c)
        }
        return nil
    }

    /// bundle-mode 首启：state-root venv 缺失则 `uv sync --frozen` provision（dev-mode 跳过）。
    static func provisionRuntimeIfNeeded(projectRoot: URL, stateRoot: URL) throws {
        guard !isDevMode else { return }                 // dev 用 .venv-desktop，不 bootstrap
        let venvPy = stateRoot.appending(path: "venv/bin/python3")
        if FileManager.default.isExecutableFile(atPath: venvPy.path) { return }   // 已 provision
        try runUVSync(projectRoot: projectRoot, stateRoot: stateRoot)
    }

    /// 自检"重新初始化运行时"（plan 2026-07-12-005 / U8 KTD4）：解释器文件存在但已损坏
    /// （import 失败）时，`provisionRuntimeIfNeeded` 的存在性检查会误判"已 provision"而跳过——
    /// 这里强制先删旧 venv 再重跑同一套 uv sync，修复损坏但文件仍在的场景。
    static func reinitializeRuntime(projectRoot: URL, stateRoot: URL) throws {
        guard !isDevMode else { return }
        try? FileManager.default.removeItem(at: stateRoot.appending(path: "venv"))
        try runUVSync(projectRoot: projectRoot, stateRoot: stateRoot)
    }

    private static func runUVSync(projectRoot: URL, stateRoot: URL) throws {
        let fm = FileManager.default
        let venvPy = stateRoot.appending(path: "venv/bin/python3")
        guard let uv = findUV() else {
            throw BridgeError.runtimeBootstrapFailed(
                "未找到 uv，请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh")
        }
        try? fm.createDirectory(at: stateRoot, withIntermediateDirectories: true)
        let p = Process()
        p.executableURL = uv
        // --no-dev（plan 2026-07-12-005 / U11 R16）：生产 venv 不带 pytest 等 dev 依赖组。
        p.arguments = ["sync", "--frozen", "--no-dev", "--project", projectRoot.path]
        var env = sanitizedChildEnvironment()
        env["UV_PROJECT_ENVIRONMENT"] = stateRoot.appending(path: "venv").path
        p.environment = env
        let errPipe = Pipe()
        p.standardError = errPipe
        p.standardOutput = Pipe()
        do { try p.run() } catch {
            throw BridgeError.runtimeBootstrapFailed("uv 启动失败：\(error.localizedDescription)")
        }
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            let msg = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(),
                             encoding: .utf8) ?? ""
            throw BridgeError.runtimeBootstrapFailed("uv sync 失败：\(msg.suffix(300))")
        }
        guard fm.isExecutableFile(atPath: venvPy.path) else {
            throw BridgeError.runtimeBootstrapFailed("uv sync 完成但缺解释器：\(venvPy.path)")
        }
    }

    /// 凭据/开关变更后重启 sidecar：**SIGTERM 全杀**（SIGHUP re-exec 会保留旧 env，拿不到新 key），
    /// 并移除 socket，使下次调用以最新 `injectedEnvironment()` 重启常驻进程（#4 key 管理）。
    static func restartSidecarForEnvChange() {
        guard let roots = resolveRoots() else { return }
        let runDir = roots.state.appending(path: "run")
        let pidFile = runDir.appending(path: "kss-sidecar.pid")
        if let pidStr = try? String(contentsOf: pidFile, encoding: .utf8),
           let pid = Int32(pidStr.trimmingCharacters(in: .whitespacesAndNewlines)) {
            kill(pid, SIGTERM)
        }
        // 兜底移除 socket：SIGTERM 处理器也会自清，但移除可确保 ensureSidecarRunning 认定需重启。
        try? FileManager.default.removeItem(at: runDir.appending(path: "kss-sidecar.sock"))
    }

    /// U9：venv 已存在但 uv.lock 变化 → 后台非阻塞 uv sync，再 SIGHUP 重载 sidecar。
    /// 永不阻塞启动；失败保留现有 venv（read-only 看数据不该被同步卡死）。
    static func refreshRuntimeIfLockChanged(projectRoot: URL, stateRoot: URL) {
        guard !isDevMode else { return }                 // dev 用 .venv-desktop
        let fm = FileManager.default
        let venvDir = stateRoot.appending(path: "venv")
        guard fm.isExecutableFile(atPath: venvDir.appending(path: "bin/python3").path) else { return }
        let lock = projectRoot.appending(path: "uv.lock")
        let synced = venvDir.appending(path: ".synced-uv.lock")
        guard let current = try? Data(contentsOf: lock) else { return }
        if let prev = try? Data(contentsOf: synced), prev == current { return }   // 未变
        guard let uv = findUV() else { return }
        DispatchQueue.global(qos: .utility).async {
            let p = Process()
            p.executableURL = uv
            // --no-dev（plan 2026-07-12-005 / U11 R16）：生产 venv 不带 pytest 等 dev 依赖组。
            p.arguments = ["sync", "--frozen", "--no-dev", "--project", projectRoot.path]
            var env = sanitizedChildEnvironment()
            env["UV_PROJECT_ENVIRONMENT"] = venvDir.path
            p.environment = env
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            guard (try? p.run()) != nil else { return }
            p.waitUntilExit()
            guard p.terminationStatus == 0 else { return }
            try? current.write(to: synced)
            // 依赖变了：SIGHUP 让 sidecar re-exec 重载（PID 文件由 daemon 写）。
            let pidFile = stateRoot.appending(path: "run/kss-sidecar.pid")
            if let pidStr = try? String(contentsOf: pidFile, encoding: .utf8),
               let pid = Int32(pidStr.trimmingCharacters(in: .whitespacesAndNewlines)) {
                kill(pid, SIGHUP)
            }
        }
    }
}
