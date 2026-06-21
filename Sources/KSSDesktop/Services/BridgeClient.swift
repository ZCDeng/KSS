import Foundation

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
        // U5：优先常驻 sidecar（pandas 暖、无 per-call python 启动）；socket 不可用回退 subprocess。
        if let envelope = try sidecarRequest(args) {
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
        var env = ProcessInfo.processInfo.environment
        env["KSS_PROJECT_ROOT"] = projectRoot.path
        env["KSS_STATE_ROOT"] = stateRoot.path
        env["KSS_PYTHON"] = python.path
        // U3：注入 Keychain 凭据（优先于 .env/network.env，见 bridge setdefault）。
        for (key, value) in KeychainStore.injectedEnvironment() { env[key] = value }
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

    private struct SidecarResponse: Decodable { let code: Int; let stdout: String?; let stderr: String? }

    /// socket 缺失则 spawn sidecar daemon（detached，best-effort），等其就绪 ≤3s。
    private func ensureSidecarRunning() {
        if FileManager.default.fileExists(atPath: socketPath) { return }
        let sidecar = projectRoot.appending(path: "scripts/kss_sidecar.py")
        guard FileManager.default.fileExists(atPath: sidecar.path) else { return }
        let p = Process()
        p.executableURL = python
        p.arguments = [sidecar.path]
        var env = ProcessInfo.processInfo.environment
        env["KSS_PROJECT_ROOT"] = projectRoot.path
        env["KSS_STATE_ROOT"] = stateRoot.path
        env["KSS_PYTHON"] = python.path
        for (key, value) in KeychainStore.injectedEnvironment() { env[key] = value }
        p.environment = env
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()   // detached daemon，不 wait
        for _ in 0..<30 {
            if FileManager.default.fileExists(atPath: socketPath) { return }
            Thread.sleep(forTimeInterval: 0.1)
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
        guard let respData = Self.unixSocketRoundtrip(path: socketPath, request: request, timeout: 3.0),
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

    /// 版本化信封两段解码（KTD3，可测 seam）：先探 schemaVersion，不匹配 throw
    /// `.schemaMismatch`（可读横幅）；缺字段视为 v0（旧/未包裹）；再解 `data` 为 T。
    static func decodeEnvelope<T: Decodable>(_ data: Data) throws -> T {
        let probe = try? JSONDecoder().decode(SchemaProbe.self, from: data)
        let version = probe?.schemaVersion ?? 0
        guard version == supportedSchemaVersion else {
            throw BridgeError.schemaMismatch(bridge: version, app: supportedSchemaVersion)
        }
        do {
            return try JSONDecoder().decode(Envelope<T>.self, from: data).data
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

    // MARK: - U2 运行时解析 + 首启 bootstrap

    /// bridge 脚本解释器：KSS_PYTHON env → state-root bootstrap venv → 系统 python3。
    static func resolvePython(stateRoot: URL) -> URL {
        let fm = FileManager.default
        if let envPy = ProcessInfo.processInfo.environment["KSS_PYTHON"],
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
        let fm = FileManager.default
        let venvPy = stateRoot.appending(path: "venv/bin/python3")
        if fm.isExecutableFile(atPath: venvPy.path) { return }   // 已 provision

        guard let uv = findUV() else {
            throw BridgeError.runtimeBootstrapFailed(
                "未找到 uv，请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh")
        }
        try? fm.createDirectory(at: stateRoot, withIntermediateDirectories: true)
        let p = Process()
        p.executableURL = uv
        p.arguments = ["sync", "--frozen", "--project", projectRoot.path]
        var env = ProcessInfo.processInfo.environment
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
}
