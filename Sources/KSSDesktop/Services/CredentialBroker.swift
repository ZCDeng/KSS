import Darwin
import Foundation

/// Non-network credential broker for the signed pi-ai helper.
///
/// The Python sidecar receives only the socket path and a one-time nonce. The
/// helper must connect over this 0600 Unix socket and echo the nonce before it
/// gets the in-memory provider-scoped credential snapshot. API keys never enter
/// the Python environment, argv, JSONL, or sidecar logs.
final class CredentialBroker {
    let socketPath: String
    private(set) var nonce: String

    private let fd: Int32
    private let runDirectory: URL
    private let queue: DispatchQueue
    private let stopLock = NSLock()
    private var stopped = false

    init(stateRoot: URL) throws {
        // AF_UNIX paths on macOS are capped at sun_path (~104 bytes). The app
        // state root can live under long sandbox/test paths, so keep the socket
        // under a short private temp directory and key registry lifetime by the
        // original stateRoot.
        let directoryName = "kss-cred-\(UUID().uuidString.prefix(8))"
        let runDirectory = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appending(path: directoryName, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: runDirectory, withIntermediateDirectories: true)
        _ = chmod(runDirectory.path, S_IRWXU)

        let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let socketURL = runDirectory.appending(path: "pi-ai-\(nonce).sock")
        let path = socketURL.path
        guard path.utf8.count < MemoryLayout.size(ofValue: sockaddr_un().sun_path) else {
            throw BrokerError.pathTooLong
        }

        _ = unlink(path)
        let socketFD = socket(AF_UNIX, SOCK_STREAM, 0)
        guard socketFD >= 0 else { throw BrokerError.socket(errno) }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(path.utf8)
        withUnsafeMutableBytes(of: &address.sun_path) { rawBuffer in
            rawBuffer.initializeMemory(as: UInt8.self, repeating: 0)
            rawBuffer.copyBytes(from: pathBytes)
        }
        let length = socklen_t(MemoryLayout<sockaddr_un>.offset(of: \.sun_path)! + pathBytes.count + 1)
        let bindStatus = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(socketFD, $0, length)
            }
        }
        guard bindStatus == 0 else {
            let err = errno
            close(socketFD)
            _ = unlink(path)
            throw BrokerError.bind(err)
        }
        _ = chmod(path, S_IRUSR | S_IWUSR)
        guard listen(socketFD, 8) == 0 else {
            let err = errno
            close(socketFD)
            _ = unlink(path)
            throw BrokerError.listen(err)
        }

        self.fd = socketFD
        self.runDirectory = runDirectory
        self.socketPath = path
        self.nonce = nonce
        self.queue = DispatchQueue(label: "kss.pi-ai.credential-broker.\(nonce)")
        self.queue.async { [weak self] in self?.acceptLoop() }
    }

    deinit {
        stop()
    }

    func stop() {
        stopLock.lock()
        defer { stopLock.unlock() }
        guard !stopped else { return }
        stopped = true
        _ = shutdown(fd, SHUT_RDWR)
        _ = close(fd)
        _ = unlink(socketPath)
        try? FileManager.default.removeItem(at: runDirectory)
    }

    @discardableResult
    func refreshNonce() -> String {
        stopLock.lock()
        defer { stopLock.unlock() }
        nonce = Self.makeNonce()
        return nonce
    }

    private func acceptLoop() {
        while true {
            stopLock.lock()
            let shouldStop = stopped
            stopLock.unlock()
            if shouldStop { return }

            let client = accept(fd, nil, nil)
            if client < 0 {
                if errno == EINTR { continue }
                return
            }
            handle(client: client)
            close(client)
        }
    }

    private func handle(client: Int32) {
        guard let request = readLine(from: client),
              request.count <= 4096,
              let data = request.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["protocol_version"] as? Int == 1,
              object["action"] as? String == "credentials",
              let requestNonce = object["nonce"] as? String
        else {
            writeJSON(["protocol_version": 1, "nonce": "", "credentials": [:]], to: client)
            return
        }
        guard let nextNonce = consumeNonce(requestNonce) else {
            // Never disclose the currently valid nonce to an unauthenticated
            // client. The helper rejects the empty nonce and keeps its existing
            // in-memory credentials unchanged.
            writeJSON(["protocol_version": 1, "nonce": "", "credentials": [:]], to: client)
            return
        }
        writeJSON([
            "protocol_version": 1,
            "nonce": requestNonce,
            "next_nonce": nextNonce,
            "credentials": KeychainStore.piAICredentialSnapshot(),
        ], to: client)
    }

    private static func makeNonce() -> String {
        UUID().uuidString.replacingOccurrences(of: "-", with: "")
    }

    private func consumeNonce(_ candidate: String) -> String? {
        stopLock.lock()
        defer { stopLock.unlock() }
        guard candidate == nonce else { return nil }
        nonce = Self.makeNonce()
        return nonce
    }

    private func readLine(from client: Int32) -> String? {
        var bytes: [UInt8] = []
        var byte = UInt8(0)
        while bytes.count < 4096 {
            let count = Darwin.read(client, &byte, 1)
            if count <= 0 { break }
            if byte == 10 { break }
            bytes.append(byte)
        }
        guard !bytes.isEmpty else { return nil }
        return String(bytes: bytes, encoding: .utf8)
    }

    private func writeJSON(_ object: [String: Any], to client: Int32) {
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object),
              let newline = "\n".data(using: .utf8)
        else { return }
        var payload = Data()
        payload.append(data)
        payload.append(newline)
        payload.withUnsafeBytes { buffer in
            guard let base = buffer.baseAddress else { return }
            _ = Darwin.write(client, base, payload.count)
        }
    }

    enum BrokerError: Error {
        case pathTooLong
        case socket(Int32)
        case bind(Int32)
        case listen(Int32)
    }
}

enum CredentialBrokerRegistry {
    private static let lock = NSLock()
    private static var brokers: [String: CredentialBroker] = [:]

    static func broker(for stateRoot: URL) -> CredentialBroker? {
        broker(for: stateRoot, refreshNonce: false)
    }

    static func broker(for stateRoot: URL, refreshNonce: Bool) -> CredentialBroker? {
        let key = stateRoot.standardizedFileURL.path
        lock.lock()
        defer { lock.unlock() }
        if let broker = brokers[key] {
            if refreshNonce { broker.refreshNonce() }
            return broker
        }
        guard let broker = try? CredentialBroker(stateRoot: stateRoot) else { return nil }
        if refreshNonce { broker.refreshNonce() }
        brokers[key] = broker
        return broker
    }
}
