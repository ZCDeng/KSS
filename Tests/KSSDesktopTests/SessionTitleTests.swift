import XCTest
@testable import KSSDesktop

/// 会话自动标题:首条输入 → 可辨识标题(实测反馈:标题永远是"新会话")。
final class SessionTitleTests: XCTestCase {
    func testDerivedTitleUsesFirstNonEmptyLine() {
        XCTAssertEqual(
            KSSStore.derivedSessionTitle(from: "\n  \n688008 今天为什么动\n继续说"),
            "688008 今天为什么动"
        )
    }

    func testDerivedTitleTruncatesLongInputWithEllipsis() {
        let input = String(repeating: "北", count: 30)
        let title = KSSStore.derivedSessionTitle(from: input)
        XCTAssertEqual(title, String(repeating: "北", count: 18) + "…")
    }

    func testDerivedTitleKeepsExactly18CharsWithoutEllipsis() {
        let input = String(repeating: "a", count: 18)
        XCTAssertEqual(KSSStore.derivedSessionTitle(from: input), input)
    }

    func testDerivedTitleNilForWhitespaceOnlyInput() {
        XCTAssertNil(KSSStore.derivedSessionTitle(from: "  \n\t\n  "))
    }
}
