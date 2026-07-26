import XCTest

final class AgentQueueInteractionTests: XCTestCase {
    func testGeneratingComposerKeepsQueueShortcutsAndStopControl() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/AIChatView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertGreaterThanOrEqual(
            source.components(separatedBy: ".onKeyPress(.return").count - 1,
            4,
            "四种 composer 都必须把 Return 交给队列语义")
        XCTAssertTrue(source.contains("keyPress.modifiers.contains(.option)"))
        XCTAssertTrue(source.contains(#"? "follow_up""#))
        XCTAssertFalse(source.contains("submitFromKeyboard"))
        XCTAssertFalse(source.contains("xcomQueueButtons"))
        XCTAssertFalse(source.contains("queueButtons"))
        XCTAssertTrue(source.contains("↩ 引导 · ⌥↩ 后续"))
        XCTAssertTrue(source.contains("queuedInputPanel"))
        XCTAssertTrue(source.contains("恢复的输入不会自动执行"))
        XCTAssertTrue(source.contains("store.stopChatGeneration()"))
        XCTAssertTrue(source.contains(".onChange(of: store.isChatStreaming)"))
        XCTAssertTrue(source.contains("pendingQueueClientMessageId = nil"))
    }
}
