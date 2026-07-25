import XCTest

final class SeesawXcomDesignTests: XCTestCase {
    private var source: String {
        get throws {
            let sourceURL = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appending(path: "Sources/KSSDesktop/Views/AIChatView.swift")
            return try String(contentsOf: sourceURL, encoding: .utf8)
        }
    }

    func testXcomUsesDedicatedTimelineShell() throws {
        let source = try source
        XCTAssertTrue(source.contains("private func xcomSeesawShell"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.feedColumnWidth"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.headerHeight"))
    }

    func testXcomEmptyStateDoesNotUseCenteredHero() throws {
        let source = try source
        let shellStart = try XCTUnwrap(source.range(of: "private func xcomSeesawShell"))
        let classicStart = try XCTUnwrap(
            source.range(of: "private func classicSeesawShell", range: shellStart.upperBound..<source.endIndex)
        )
        let shell = String(source[shellStart.lowerBound..<classicStart.lowerBound])
        XCTAssertFalse(shell.contains("heroEmptyState"))
        XCTAssertTrue(shell.contains("xcomEmptyTimeline"))

        let timelineStart = try XCTUnwrap(source.range(of: "private var xcomEmptyTimeline"))
        let composerStart = try XCTUnwrap(
            source.range(of: "private var xcomComposer", range: timelineStart.upperBound..<source.endIndex)
        )
        let timeline = String(source[timelineStart.lowerBound..<composerStart.lowerBound])
        XCTAssertTrue(timeline.contains("xcomComposer"))
    }

    func testXcomSkillsUseFlatUtilityPanelInsteadOfPopover() throws {
        let source = try source
        XCTAssertTrue(source.contains("private var xcomSkillPanel"))
        XCTAssertTrue(source.contains("xcomUtilityPanel"))
        XCTAssertTrue(source.contains("Toggle(\"启用\""))
        XCTAssertTrue(source.contains("Toggle(\"置顶\""))
    }
}
