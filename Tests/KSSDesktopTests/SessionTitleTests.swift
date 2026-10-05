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

    // MARK: - 列表展示标题与分组

    private var shanghai: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        return calendar
    }

    /// 2026-10-05 03:00 (UTC+8)
    private let now = Date(timeIntervalSince1970: 1_791_140_400)

    private func session(_ id: String, title: String, updatedAt: Date?, messages: [AgentHydratedMessage]? = nil) -> AgentSession {
        AgentSession(
            sessionId: id,
            title: title,
            updatedAt: updatedAt.map { String($0.timeIntervalSince1970) },
            messages: messages)
    }

    func testDisplayTitleNeverShowsRawSessionId() {
        let raw = session("local-92197B6B", title: "local-92197B6B", updatedAt: nil,
                          messages: [AgentHydratedMessage(id: "1", role: "user", text: "688008 今天为什么动")])
        XCTAssertEqual(raw.displayTitle(calendar: shanghai), "688008 今天为什么动")
    }

    func testDisplayTitleFallsBackToActivityTime() {
        let empty = session("local-1", title: "新会话", updatedAt: now, messages: [])
        XCTAssertEqual(empty.displayTitle(calendar: shanghai), "新会话 · 10-05 03:00")
        XCTAssertEqual(session("local-2", title: "新会话", updatedAt: nil).displayTitle(), "新会话")
    }

    func testCustomTitleIsKept() {
        XCTAssertEqual(session("x", title: "RSI 阈值", updatedAt: now).displayTitle(), "RSI 阈值")
    }

    func testListedHidesEmptyPlaceholderSessionsExceptSelected() {
        let sessions = [
            session("a", title: "新会话", updatedAt: now, messages: []),
            session("b", title: "新会话", updatedAt: now, messages: []),
            session("c", title: "板块强势", updatedAt: now, messages: []),
        ]
        let listed = AgentSession.listed(sessions, selectedId: "b", search: "")
        XCTAssertEqual(listed.map(\.sessionId), ["b", "c"])
    }

    func testRecencyGroupsSortNewestFirst() {
        let day: TimeInterval = 86_400
        let sessions = [
            session("old", title: "old", updatedAt: now.addingTimeInterval(-30 * day)),
            session("today", title: "today", updatedAt: now.addingTimeInterval(-60)),
            session("week", title: "week", updatedAt: now.addingTimeInterval(-3 * day)),
            session("undated", title: "undated", updatedAt: nil),
        ]
        let groups = AgentSession.recencyGroups(sessions, now: now, calendar: shanghai)
        XCTAssertEqual(groups.map(\.label), ["今天", "最近 7 天", "更早"])
        XCTAssertEqual(groups.map { $0.sessions.map(\.sessionId) }, [["today"], ["week"], ["old", "undated"]])
    }
}
