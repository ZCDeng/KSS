import Foundation

enum BridgeError: LocalizedError {
    case projectRootNotFound
    case processFailed(String)
    case invalidOutput

    var errorDescription: String? {
        switch self {
        case .projectRootNotFound:
            return "Cannot locate scripts/kss_app_bridge.py"
        case .processFailed(let message):
            return message
        case .invalidOutput:
            return "Bridge returned invalid JSON"
        }
    }
}

struct BridgeClient {
    /// 不可变代码根（scripts/ 与 kss/config 所在）。
    let projectRoot: URL
    /// 可变状态根（storage/.cache）。dev-mode 默认 = projectRoot（in-repo，行为不变）；
    /// bundle-mode 默认 = ~/Library/Application Support/KSS。
    let stateRoot: URL

    init() throws {
        guard let roots = Self.resolveRoots() else {
            throw BridgeError.projectRootNotFound
        }
        self.projectRoot = roots.project
        self.stateRoot = roots.state
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

    func paperSummary() throws -> TrackingSummary {
        try run(["paper-summary"], as: TrackingSummary.self)
    }

    func runTask(_ task: KSSTask) throws -> TaskRunResult {
        try run(["run", task.rawValue] + task.arguments, as: TaskRunResult.self)
    }

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

    func themeLeaders() throws -> [ThemeLeaders] {
        try run(["theme-leaders"], as: [ThemeLeaders].self)
    }

    // MARK: 定时任务（launchd）

    func scheduledJobs() throws -> [ScheduledJob] {
        try run(["cron-list"], as: [ScheduledJob].self)
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

    /// 批量重跑指定 label（空 = 全部启用项）；每个 label 仍走 bridge 白名单校验。
    func rerunJobs(_ labels: [String]) throws -> CronBatchResult {
        try run(["cron-rerun-many", labels.joined(separator: ",")], as: CronBatchResult.self)
    }

    /// 趋势页：某月月度格子。
    func trendsMonth(_ month: String) throws -> TrendMonth {
        try run(["trends-month", month], as: TrendMonth.self)
    }

    /// 趋势页：某日完整明细。
    func trendsDay(_ date: String) throws -> TrendDayDetail {
        try run(["trends-day", date], as: TrendDayDetail.self)
    }

    private func run<T: Decodable>(_ args: [String], as type: T.Type) throws -> T {
        let bridge = projectRoot.appending(path: "scripts/kss_app_bridge.py")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [bridge.path] + args
        process.currentDirectoryURL = projectRoot
        // 显式注入双根，使 bridge 及其派生子脚本（U1 惰性 env 解析）一致定位代码/状态。
        var env = ProcessInfo.processInfo.environment
        env["KSS_PROJECT_ROOT"] = projectRoot.path
        env["KSS_STATE_ROOT"] = stateRoot.path
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

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw BridgeError.invalidOutput
        }
    }

    /// dev-mode 判定：`KSS_PROJECT_ROOT` env 存在即 dev（build_and_run.sh 注入）。
    private static var isDevMode: Bool {
        ProcessInfo.processInfo.environment["KSS_PROJECT_ROOT"] != nil
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

    /// 解析 (代码根, 状态根)。优先级：
    /// projectRoot = KSS_PROJECT_ROOT(dev) → breadcrumb → bundle Resources → 历史爬升兜底。
    /// stateRoot   = KSS_STATE_ROOT → breadcrumb → (dev? projectRoot : ~/Library/Application Support/KSS)。
    private static func resolveRoots() -> (project: URL, state: URL)? {
        let fm = FileManager.default
        let envProject = ProcessInfo.processInfo.environment["KSS_PROJECT_ROOT"]
        let envState = ProcessInfo.processInfo.environment["KSS_STATE_ROOT"]
        let crumb = readBreadcrumb()

        func hasBridge(_ url: URL) -> Bool {
            fm.fileExists(atPath: url.appending(path: "scripts/kss_app_bridge.py").path)
        }

        // ---- projectRoot ----
        var project: URL?
        if let envProject { project = URL(fileURLWithPath: envProject) }            // dev 硬分支
        else if let crumb { project = URL(fileURLWithPath: crumb.projectRoot) }     // bundle 面包屑
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
        let state: URL
        if let envState { state = URL(fileURLWithPath: envState) }
        else if let crumb { state = URL(fileURLWithPath: crumb.stateRoot) }
        else if isDevMode { state = resolvedProject }          // dev：in-repo，行为不变
        else { state = appSupportDefault }                      // bundle 默认

        return (resolvedProject, state)
    }
}
