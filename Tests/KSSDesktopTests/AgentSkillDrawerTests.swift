import XCTest

final class AgentSkillDrawerTests: XCTestCase {

    func testSkillControlsKeepVisibleLabels() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/AIChatView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        let drawerStart = try XCTUnwrap(source.range(of: "private func focusSkillRow"))
        let drawerEnd = try XCTUnwrap(
            source.range(of: "private var focusContextPopover", range: drawerStart.upperBound..<source.endIndex)
        )
        let drawer = String(source[drawerStart.lowerBound..<drawerEnd.lowerBound])

        XCTAssertTrue(drawer.contains("Toggle(\"启用\""), "全局启用控件必须显示文字标签")
        XCTAssertTrue(drawer.contains("Toggle(\"置顶\""), "会话置顶控件必须显示文字标签")
        XCTAssertFalse(drawer.contains(".labelsHidden()"), "相邻技能控件不得再次隐藏标签")
    }
}
