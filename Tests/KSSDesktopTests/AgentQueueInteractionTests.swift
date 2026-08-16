import XCTest

final class AgentQueueInteractionTests: XCTestCase {
    func testGeneratingComposerKeepsQueueShortcutsAndStopControl() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/Seesaw/AIChatView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        // Focus Layout 是唯一 composer：Return 统一经 onSubmit（IME 安全），
        // 流式中由 submitInput 转入队列语义，不再有四套 onKeyPress(.return)。
        XCTAssertFalse(source.contains("onKeyPress(.return"))
        XCTAssertTrue(source.contains("submitInput(mode: \"steering\")"))
        XCTAssertTrue(source.contains("if store.isChatStreaming {"))
        XCTAssertTrue(source.contains("store.enqueueAgentInput("))
        XCTAssertTrue(source.contains(#"? "follow_up""#))

        // 队列可视化与停止控制保持在位;快捷键提示已删,排队语义由
        // 流式占位文案表达(实测反馈:常驻提示毫无意义)。
        XCTAssertFalse(source.contains("排队追问 · ⌥↩"))
        XCTAssertTrue(source.contains("追问会排队，本轮生成结束后处理…"))
        XCTAssertTrue(source.contains("queuedInputPanel"))
        XCTAssertTrue(source.contains("恢复的输入不会自动执行"))
        XCTAssertTrue(source.contains("store.stopChatGeneration()"))
        XCTAssertTrue(source.contains(".onChange(of: store.isChatStreaming)"))
        XCTAssertTrue(source.contains("pendingQueueClientMessageId = nil"))

        // 旧四 composer 时代的按钮不允许回潮。
        XCTAssertFalse(source.contains("xcomQueueButtons"))
        XCTAssertFalse(source.contains("queueButtons"))
        XCTAssertFalse(source.contains("submitFromKeyboard"))
    }
}
