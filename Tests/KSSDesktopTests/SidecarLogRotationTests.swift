import XCTest
@testable import KSSDesktop

/// U7（plan 2026-07-12-005）：sidecar 日志轮转纪律——>10MB 触发，保留 3 代，最老丢弃。
final class SidecarLogRotationTests: XCTestCase {
    private var tmpDir: URL!

    override func setUp() {
        super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("kss-sidecar-log-rotation-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tmpDir)
        super.tearDown()
    }

    private func write(_ url: URL, bytes: Int, content: String = "x") {
        let data = Data(repeating: UInt8(content.utf8.first ?? 0x78), count: bytes)
        FileManager.default.createFile(atPath: url.path, contents: data)
    }

    func testSmallFileDoesNotRotate() {
        let logURL = tmpDir.appendingPathComponent("sidecar.log")
        write(logURL, bytes: 1024)
        BridgeClient.rotateSidecarLogIfNeeded(logURL: logURL)
        XCTAssertTrue(FileManager.default.fileExists(atPath: logURL.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: logURL.appendingPathExtension("1").path))
    }

    func testOversizedFileRotatesToGen1() {
        let logURL = tmpDir.appendingPathComponent("sidecar.log")
        write(logURL, bytes: Int(BridgeClient.sidecarLogRotateThresholdBytes) + 1)
        BridgeClient.rotateSidecarLogIfNeeded(logURL: logURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: logURL.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: logURL.appendingPathExtension("1").path))
    }

    func testExistingGenerationsShiftUp() {
        let logURL = tmpDir.appendingPathComponent("sidecar.log")
        write(logURL, bytes: Int(BridgeClient.sidecarLogRotateThresholdBytes) + 1, content: "current")
        write(logURL.appendingPathExtension("1"), bytes: 10, content: "gen1")
        write(logURL.appendingPathExtension("2"), bytes: 10, content: "gen2")

        BridgeClient.rotateSidecarLogIfNeeded(logURL: logURL)

        let gen1Content = try? String(contentsOf: logURL.appendingPathExtension("1"), encoding: .utf8)
        let gen2Content = try? String(contentsOf: logURL.appendingPathExtension("2"), encoding: .utf8)
        let gen3Content = try? String(contentsOf: logURL.appendingPathExtension("3"), encoding: .utf8)
        XCTAssertEqual(gen1Content?.first, "c")  // 新 .1 = 原 current
        XCTAssertEqual(gen2Content?.first, "g")  // 新 .2 = 原 .1 (gen1)
        XCTAssertEqual(gen3Content?.first, "g")  // 新 .3 = 原 .2 (gen2)
    }

    func testOldestGenerationDiscarded() {
        let logURL = tmpDir.appendingPathComponent("sidecar.log")
        write(logURL, bytes: Int(BridgeClient.sidecarLogRotateThresholdBytes) + 1)
        write(logURL.appendingPathExtension("1"), bytes: 10, content: "a")
        write(logURL.appendingPathExtension("2"), bytes: 10, content: "b")
        write(logURL.appendingPathExtension("3"), bytes: 10, content: "c-should-be-discarded")

        BridgeClient.rotateSidecarLogIfNeeded(logURL: logURL)

        // .3 之前内容("c-should-be-discarded")应被丢弃，新 .3 来自旧 .2("b")。
        let gen3Content = try? String(contentsOf: logURL.appendingPathExtension("3"), encoding: .utf8)
        XCTAssertEqual(gen3Content?.first, "b")
    }
}
