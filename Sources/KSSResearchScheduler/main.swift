import Darwin
import Foundation
import Security

private enum SchedulerError: LocalizedError {
    case invalidArguments
    case unavailablePython
    case broker

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "invalid scheduler arguments"
        case .unavailablePython: return "no KSS Python runtime available"
        case .broker: return "unable to create credential broker"
        }
    }
}

/// This target intentionally shares the exact Keychain service and broker wire
/// format with KSSDesktop, but remains a minimal executable so launchd never
/// has to start the GUI application merely to run a scheduled report.
private enum SchedulerKeychain {
    static let service = "com.zcdeng.KSSDesktop.credentials"
    private static let providerPrefix = "KSS_PROVIDER_API_KEY."
    private static let providerIndex = "kss.llm.providerCredentialIds.v1"

    static func read(_ account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else { return nil }
        return value
    }

    static func snapshot() -> [String: Any] {
        let defaults = UserDefaults(suiteName: "com.zcdeng.KSSDesktop")
        let ids = defaults?.stringArray(forKey: providerIndex) ?? []
        var credentials: [String: Any] = [:]
        for id in ids where id.range(of: #"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"#, options: .regularExpression) != nil {
            if let key = read(providerPrefix + id) {
                credentials[id] = ["type": "api_key", "key": key]
            }
        }
        let legacyKeys = [
            "KSS_LLM_PRIMARY_KEY", "KSS_LLM_FALLBACK_KEY",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
            "KSS_LLM_PRIMARY_BASE_URL", "KSS_LLM_FALLBACK_BASE_URL",
        ]
        var legacy: [String: String] = [:]
        for key in legacyKeys {
            if let value = read(key) { legacy[key] = value }
        }
        func insert(_ id: String, _ key: String?) {
            guard credentials[id] == nil,
                  let key,
                  !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            credentials[id] = ["type": "api_key", "key": key]
        }
        insert("kss-primary", legacy["KSS_LLM_PRIMARY_KEY"])
        insert("kss-fallback", legacy["KSS_LLM_FALLBACK_KEY"])
        insert("deepseek", legacy["DEEPSEEK_API_KEY"])
        insert("openai", legacy["OPENAI_API_KEY"])
        insert("openrouter", legacy["OPENROUTER_API_KEY"])
        let primaryBase = legacy["KSS_LLM_PRIMARY_BASE_URL"]?.lowercased() ?? ""
        let fallbackBase = legacy["KSS_LLM_FALLBACK_BASE_URL"]?.lowercased() ?? ""
        insert("kss-primary", primaryBase.contains("deepseek") ? legacy["DEEPSEEK_API_KEY"] : (legacy["OPENAI_API_KEY"] ?? legacy["DEEPSEEK_API_KEY"]))
        insert("kss-fallback", fallbackBase.contains("deepseek") ? legacy["DEEPSEEK_API_KEY"] : (legacy["OPENAI_API_KEY"] ?? legacy["DEEPSEEK_API_KEY"]))
        return credentials
    }
}

private final class CredentialBroker {
    let socketPath: String
    private(set) var nonce: String
    private let fd: Int32
    private let directory: URL
    private let queue: DispatchQueue
    private let lock = NSLock()
    private var stopped = false

    init() throws {
        let nonce = Self.newNonce()
        let directory = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent("kss-scheduled-\(UUID().uuidString.prefix(8))", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        _ = chmod(directory.path, S_IRWXU)
        let path = directory.appendingPathComponent("credentials.sock").path
        guard path.utf8.count < MemoryLayout.size(ofValue: sockaddr_un().sun_path) else {
            try? FileManager.default.removeItem(at: directory)
            throw SchedulerError.broker
        }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw SchedulerError.broker }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        withUnsafeMutableBytes(of: &address.sun_path) { buffer in
            buffer.initializeMemory(as: UInt8.self, repeating: 0)
            buffer.copyBytes(from: bytes)
        }
        let length = socklen_t(MemoryLayout<sockaddr_un>.offset(of: \.sun_path)! + bytes.count + 1)
        let status = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, length) }
        }
        guard status == 0, listen(fd, 4) == 0 else {
            close(fd)
            _ = unlink(path)
            try? FileManager.default.removeItem(at: directory)
            throw SchedulerError.broker
        }
        _ = chmod(path, S_IRUSR | S_IWUSR)
        self.fd = fd
        self.socketPath = path
        self.nonce = nonce
        self.directory = directory
        self.queue = DispatchQueue(label: "com.zcdeng.KSSDesktop.scheduled-credential-broker")
        queue.async { [weak self] in self?.acceptLoop() }
    }

    deinit { stop() }

    func stop() {
        lock.lock()
        defer { lock.unlock() }
        guard !stopped else { return }
        stopped = true
        _ = shutdown(fd, SHUT_RDWR)
        close(fd)
        _ = unlink(socketPath)
        try? FileManager.default.removeItem(at: directory)
    }

    private func acceptLoop() {
        while true {
            lock.lock(); let isStopped = stopped; lock.unlock()
            if isStopped { return }
            let client = accept(fd, nil, nil)
            if client < 0 { return }
            handle(client)
            close(client)
        }
    }

    private func handle(_ client: Int32) {
        guard let line = readLine(client),
              let data = line.data(using: .utf8),
              let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              request["protocol_version"] as? Int == 1,
              request["action"] as? String == "credentials",
              let requestNonce = request["nonce"] as? String,
              let nextNonce = consume(requestNonce) else {
            write(["protocol_version": 1, "nonce": "", "credentials": [:]], to: client)
            return
        }
        write([
            "protocol_version": 1,
            "nonce": requestNonce,
            "next_nonce": nextNonce,
            "credentials": SchedulerKeychain.snapshot(),
        ], to: client)
    }

    private func consume(_ candidate: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        guard candidate == nonce else { return nil }
        nonce = Self.newNonce()
        return nonce
    }

    private static func newNonce() -> String {
        UUID().uuidString.replacingOccurrences(of: "-", with: "")
    }

    private func readLine(_ client: Int32) -> String? {
        var values: [UInt8] = []
        var byte = UInt8(0)
        while values.count < 4096 {
            let count = Darwin.read(client, &byte, 1)
            if count <= 0 || byte == 10 { break }
            values.append(byte)
        }
        return values.isEmpty ? nil : String(bytes: values, encoding: .utf8)
    }

    private func write(_ object: [String: Any], to client: Int32) {
        guard JSONSerialization.isValidJSONObject(object),
              let json = try? JSONSerialization.data(withJSONObject: object) else { return }
        var payload = json
        payload.append(10)
        payload.withUnsafeBytes { buffer in
            guard let base = buffer.baseAddress else { return }
            _ = Darwin.write(client, base, payload.count)
        }
    }
}

private func value(after flag: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: flag), index + 1 < args.count else { return nil }
    return args[index + 1]
}

private func executablePython(projectRoot: URL, stateRoot: URL) -> URL? {
    let explicit = ProcessInfo.processInfo.environment["KSS_PYTHON"]
        .flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) }
    let candidates = [
        explicit,
        stateRoot.appendingPathComponent("venv/bin/python3"),
        projectRoot.appendingPathComponent(".venv-desktop/bin/python"),
        URL(fileURLWithPath: "/usr/bin/python3"),
    ].compactMap { $0 }
    return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }
}

private func run() throws -> Int32 {
    let args = Array(CommandLine.arguments.dropFirst())
    guard let projectRaw = value(after: "--project-root", in: args),
          let stateRaw = value(after: "--state-root", in: args),
          let cadence = value(after: "--cadence", in: args),
          ["daily", "weekly"].contains(cadence) else { throw SchedulerError.invalidArguments }
    let projectRoot = URL(fileURLWithPath: projectRaw).standardizedFileURL
    let stateRoot = URL(fileURLWithPath: stateRaw).standardizedFileURL
    guard FileManager.default.fileExists(atPath: projectRoot.appendingPathComponent("scripts/run_scheduled_research.py").path),
          let python = executablePython(projectRoot: projectRoot, stateRoot: stateRoot) else { throw SchedulerError.unavailablePython }
    let broker = try CredentialBroker()
    defer { broker.stop() }
    let process = Process()
    process.executableURL = python
    process.arguments = [
        projectRoot.appendingPathComponent("scripts/run_scheduled_research.py").path,
        "--project-root", projectRoot.path,
        "--state-root", stateRoot.path,
        "--cadence", cadence,
    ]
    if let maxSeconds = value(after: "--max-seconds", in: args) { process.arguments?.append(contentsOf: ["--max-seconds", maxSeconds]) }
    var environment: [String: String] = [
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": NSHomeDirectory(),
        "KSS_PROJECT_ROOT": projectRoot.path,
        "KSS_STATE_ROOT": stateRoot.path,
        "KSS_PI_AI_CREDENTIAL_SOCKET": broker.socketPath,
        "KSS_PI_AI_CREDENTIAL_NONCE": broker.nonce,
        "PYTHONDONTWRITEBYTECODE": "1",
    ]
    for key in ["LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"] {
        if let value = ProcessInfo.processInfo.environment[key], !value.isEmpty { environment[key] = value }
    }
    process.environment = environment
    process.currentDirectoryURL = projectRoot
    try process.run()
    process.waitUntilExit()
    return process.terminationStatus
}

do {
    exit(try run())
} catch {
    fputs("KSS scheduled research helper: \(error.localizedDescription)\n", stderr)
    exit(2)
}
