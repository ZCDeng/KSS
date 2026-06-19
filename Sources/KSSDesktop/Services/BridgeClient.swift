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
    let projectRoot: URL

    init() throws {
        guard let root = Self.findProjectRoot() else {
            throw BridgeError.projectRootNotFound
        }
        self.projectRoot = root
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

    private func run<T: Decodable>(_ args: [String], as type: T.Type) throws -> T {
        let bridge = projectRoot.appending(path: "scripts/kss_app_bridge.py")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [bridge.path] + args
        process.currentDirectoryURL = projectRoot

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

    private static func findProjectRoot() -> URL? {
        let fm = FileManager.default
        var candidates: [URL] = []
        if let env = ProcessInfo.processInfo.environment["KSS_PROJECT_ROOT"] {
            candidates.append(URL(fileURLWithPath: env))
        }
        candidates.append(URL(fileURLWithPath: fm.currentDirectoryPath))
        candidates.append(Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent())

        if let executable = Bundle.main.executableURL {
            candidates.append(executable.deletingLastPathComponent())
        }

        for candidate in candidates {
            var url = candidate
            for _ in 0..<8 {
                if fm.fileExists(atPath: url.appending(path: "scripts/kss_app_bridge.py").path) {
                    return url
                }
                let parent = url.deletingLastPathComponent()
                if parent.path == url.path { break }
                url = parent
            }
        }
        return nil
    }
}
