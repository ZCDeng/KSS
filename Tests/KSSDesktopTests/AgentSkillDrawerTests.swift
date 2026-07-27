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

    func testUtilityWorkspacesReuseTasksTypographyAndGrouping() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/AIChatView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        let skillsStart = try XCTUnwrap(source.range(of: "private var focusSkillPalette"))
        let contextStart = try XCTUnwrap(
            source.range(of: "private var focusContextPopover", range: skillsStart.upperBound..<source.endIndex)
        )
        let palette = String(source[skillsStart.lowerBound..<contextStart.lowerBound])
        let contextEnd = try XCTUnwrap(
            source.range(of: "private func focusPanelHeader", range: contextStart.upperBound..<source.endIndex)
        )
        let context = String(source[contextStart.lowerBound..<contextEnd.lowerBound])

        XCTAssertTrue(palette.contains("SettingsFormStyle.itemTitle"))
        XCTAssertTrue(palette.contains("SettingsFormStyle.bodyHint"))
        XCTAssertTrue(palette.contains("SettingsFormStyle.meta"))
        XCTAssertTrue(palette.contains("seesawPanelGroupHeader"))
        XCTAssertTrue(context.contains("SettingsFormStyle.itemTitle"))
        XCTAssertTrue(context.contains("SettingsFormStyle.bodyHint"))
        XCTAssertTrue(context.contains("seesawPanelGroupHeader"))
        XCTAssertTrue(context.contains("seesawPanelRow"))
    }

    func testSkillWorkspaceCountsUseSwiftInterpolation() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/AIChatView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        let skillsStart = try XCTUnwrap(source.range(of: "private var focusSkillPalette"))
        let contextStart = try XCTUnwrap(
            source.range(of: "private var focusContextPopover", range: skillsStart.upperBound..<source.endIndex)
        )
        let palette = String(source[skillsStart.lowerBound..<contextStart.lowerBound])

        XCTAssertTrue(palette.contains("\\(enabledSkillCount) 个启用"))
        XCTAssertTrue(palette.contains("\\(pinnedSkills.count) 个置顶"))
        XCTAssertTrue(palette.contains("\\(filteredSkills.count) 项可见"))
        XCTAssertFalse(palette.contains("text: \"(enabledSkillCount) 个启用\""))
        XCTAssertFalse(palette.contains("SettingsStatusCapsule(text: \"(pinnedSkills.count) 个置顶\")"))
        XCTAssertFalse(palette.contains("status: \"(filteredSkills.count) 项可见\""))
    }
}
