import Darwin
import Foundation
import XCTest
@testable import KSSDesktop

final class CredentialBrokerTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appending(path: "kss-credential-broker-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testBrokerSocketIsPrivateAndNonceIsConsumedOnce() throws {
        let root = try temporaryDirectory()
        let broker = try CredentialBroker(stateRoot: root)
        defer { broker.stop() }

        let attrs = try FileManager.default.attributesOfItem(atPath: broker.socketPath)
        let permissions = (attrs[.posixPermissions] as? NSNumber)?.intValue ?? 0
        XCTAssertEqual(permissions & 0o077, 0)

        let firstNonce = broker.nonce
        let first = try requestCredentials(socketPath: broker.socketPath, nonce: firstNonce)
        XCTAssertEqual(first["nonce"] as? String, firstNonce)
        let nextNonce = try XCTUnwrap(first["next_nonce"] as? String)
        XCTAssertFalse(nextNonce.isEmpty)

        let second = try requestCredentials(socketPath: broker.socketPath, nonce: firstNonce)
        XCTAssertNotEqual(second["nonce"] as? String, firstNonce)

        let chained = try requestCredentials(socketPath: broker.socketPath, nonce: nextNonce)
        XCTAssertEqual(chained["nonce"] as? String, nextNonce)
        XCTAssertNotEqual(chained["next_nonce"] as? String, nextNonce)
    }

    private func requestCredentials(socketPath: String, nonce: String) throws -> [String: Any] {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        XCTAssertGreaterThanOrEqual(fd, 0)
        defer { close(fd) }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8)
        withUnsafeMutableBytes(of: &address.sun_path) { rawBuffer in
            rawBuffer.initializeMemory(as: UInt8.self, repeating: 0)
            rawBuffer.copyBytes(from: pathBytes)
        }
        let length = socklen_t(MemoryLayout<sockaddr_un>.offset(of: \.sun_path)! + pathBytes.count + 1)
        let status = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, length)
            }
        }
        XCTAssertEqual(status, 0)

        let request: [String: Any] = [
            "protocol_version": 1,
            "action": "credentials",
            "nonce": nonce,
        ]
        let data = try JSONSerialization.data(withJSONObject: request) + Data([10])
        data.withUnsafeBytes { buffer in
            if let base = buffer.baseAddress {
                _ = Darwin.write(fd, base, data.count)
            }
        }

        var bytes: [UInt8] = []
        var byte = UInt8(0)
        while bytes.count < 262144 {
            let count = Darwin.read(fd, &byte, 1)
            if count <= 0 { break }
            if byte == 10 { break }
            bytes.append(byte)
        }
        let responseData = Data(bytes)
        let object = try JSONSerialization.jsonObject(with: responseData)
        guard let dict = object as? [String: Any] else {
            XCTFail("credential response is not an object")
            return [:]
        }
        return dict
    }
}
