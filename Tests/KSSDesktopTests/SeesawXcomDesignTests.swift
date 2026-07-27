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

    private var contentSource: String {
        get throws {
            let sourceURL = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appending(path: "Sources/KSSDesktop/Views/ContentView.swift")
            return try String(contentsOf: sourceURL, encoding: .utf8)
        }
    }

    func testAllThemesUseSharedFocusShell() throws {
        let source = try source
        XCTAssertTrue(source.contains("focusSeesawShell(size: geo.size)"))
        XCTAssertTrue(source.contains("private func focusSeesawShell"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.feedColumnWidth"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.headerHeight"))
        XCTAssertFalse(source.contains("if isXcom"))
    }

    func testFocusShellKeepsAuxiliarySurfacesOutOfConversationColumn() throws {
        let source = try source
        let focusStart = try XCTUnwrap(source.range(of: "private func focusSeesawShell"))
        let legacyStart = try XCTUnwrap(
            source.range(of: "private func xcomSeesawShell", range: focusStart.upperBound..<source.endIndex)
        )
        let focus = String(source[focusStart.lowerBound..<legacyStart.lowerBound])
        XCTAssertTrue(focus.contains("focusSessionPalette"))
        XCTAssertTrue(focus.contains("focusSkillPalette"))
        XCTAssertTrue(focus.contains("focusContextPopover"))
        XCTAssertFalse(focus.contains("xcomAgentSidebar"))
        XCTAssertFalse(focus.contains("xcomUtilityPanel"))
    }

    func testFocusComposerIsSharedAndSkillsRemainExplicit() throws {
        let source = try source
        XCTAssertTrue(source.contains("private var focusComposer"))
        XCTAssertTrue(source.contains("focusPinnedSkillChips"))
        XCTAssertTrue(source.contains("availableSkillStarters"))
        XCTAssertTrue(source.contains("private var focusSkillPalette"))
        XCTAssertTrue(source.contains("Toggle(\"启用\""))
        XCTAssertTrue(source.contains("Toggle(\"置顶\""))
    }

    func testSeesawNavigationCollapseIsTransient() throws {
        let source = try contentSource
        XCTAssertTrue(source.contains("@State private var seesawNavigationExpanded"))
        XCTAssertTrue(source.contains("private var effectiveSidebarCollapsed"))
        XCTAssertTrue(source.contains("if store.selectedSection == .aiChat"))
        XCTAssertTrue(source.contains("return !seesawNavigationExpanded"))
        XCTAssertTrue(source.contains("AIChatView(globalNavigationExpanded: $seesawNavigationExpanded)"))
    }
}
