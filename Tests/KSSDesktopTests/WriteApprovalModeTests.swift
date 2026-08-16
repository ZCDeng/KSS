import XCTest
@testable import KSSDesktop

/// 执行模式:默认逐次确认;自动模式只改 UI 应答,不绕过 grant 链路。
final class WriteApprovalModeTests: XCTestCase {
    func testDefaultModeIsAskWhenUnset() {
        UserDefaults.standard.removeObject(forKey: "kss.seesaw.writeApprovalMode.v1")
        XCTAssertEqual(KSSStore.restoredWriteApprovalMode(), .ask)
    }

    func testModeRoundTripsThroughDefaults() {
        UserDefaults.standard.set("auto", forKey: "kss.seesaw.writeApprovalMode.v1")
        XCTAssertEqual(KSSStore.restoredWriteApprovalMode(), .auto)
        UserDefaults.standard.set("garbage", forKey: "kss.seesaw.writeApprovalMode.v1")
        XCTAssertEqual(KSSStore.restoredWriteApprovalMode(), .ask)
        UserDefaults.standard.removeObject(forKey: "kss.seesaw.writeApprovalMode.v1")
    }

    func testAutoApprovalStillRidesControlChannelInSource() throws {
        let storeURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Services/KSSStore.swift")
        let source = try String(contentsOf: storeURL, encoding: .utf8)
        // 自动分支必须复用 control.confirm(同一 grant/审计链路),
        // 且通道缺失时回落弹窗,不得静默吞确认。
        XCTAssertTrue(source.contains("self.writeApprovalMode == .auto"))
        XCTAssertTrue(source.contains("approved: true"))
        XCTAssertTrue(source.contains("let control = self.activeAgentControl"))
        XCTAssertTrue(source.contains("recordAutoApprovedWrite"))
    }
}
