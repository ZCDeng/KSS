import XCTest
@testable import KSSDesktop

final class WorkspaceSectionTests: XCTestCase {
    func testSeesawIsThePostLaunchDefaultAndReportArchiveIsVisible() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let storeSource = try String(
            contentsOf: root.appending(path: "Sources/KSSDesktop/Services/KSSStore.swift"),
            encoding: .utf8)

        XCTAssertTrue(storeSource.contains("selectedSection: WorkspaceSection = .aiChat"))
        XCTAssertTrue(WorkspaceSection.ordered(from: "").contains(.investmentAnalysis))
        XCTAssertFalse(WorkspaceSection.hidden.contains(.investmentAnalysis))
    }

    func testSettingsIsHidden() {
        XCTAssertTrue(WorkspaceSection.hidden.contains(.settings))
        XCTAssertTrue(WorkspaceSection.hidden.contains(.themes))
        XCTAssertTrue(WorkspaceSection.hidden.contains(.reviews))
    }

    func testOrderedNeverIncludesSettings() {
        XCTAssertFalse(WorkspaceSection.ordered(from: "").contains(.settings))
        XCTAssertFalse(WorkspaceSection.ordered(from: "Settings,Dashboard").contains(.settings))
        XCTAssertFalse(WorkspaceSection.ordered(from: "Themes,Dashboard").contains(.themes))
        XCTAssertFalse(WorkspaceSection.ordered(from: "").contains(.reviews))
        XCTAssertFalse(WorkspaceSection.ordered(from: "Reviews,Dashboard").contains(.reviews))
    }

    func testRoundTripThroughOrderedNeverReintroducesSettings() {
        let ordered = WorkspaceSection.ordered(from: "")
        let encoded = WorkspaceSection.encode(ordered)
        XCTAssertFalse(encoded.contains("Settings"))
        XCTAssertFalse(encoded.contains("Reviews"))
    }

    func testHeatmapIsVisibleAndNotHidden() {
        XCTAssertEqual(WorkspaceSection.heatmap.displayName, "热力图")
        XCTAssertTrue(WorkspaceSection.ordered(from: "").contains(.heatmap))
        XCTAssertFalse(WorkspaceSection.hidden.contains(.heatmap))
        XCTAssertFalse(WorkspaceSection.pinned.contains(.heatmap))
        XCTAssertNotEqual(
            SidebarNavIconCatalog.resourceBase(for: .heatmap),
            SidebarNavIconCatalog.resourceBase(for: .investabilityMap)
        )
    }

    func testOrderedAppendsHeatmapWhenSavedOrderOmitsIt() {
        let saved = WorkspaceSection.encode(
            WorkspaceSection.ordered(from: "").filter { $0 != .heatmap }
        )
        XCTAssertFalse(saved.contains(WorkspaceSection.heatmap.rawValue))
        let ordered = WorkspaceSection.ordered(from: saved)
        XCTAssertEqual(ordered.last, .heatmap)
    }

    func testHeatmapRoutesBeforeDashboardSnapshotWait() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: root.appending(path: "Sources/KSSDesktop/Views/ContentView.swift"),
            encoding: .utf8)

        let heatmapRoute = source.range(of: "store.selectedSection == .heatmap")
        let snapshotWait = source.range(of: "else if let snapshot = store.snapshot")
        XCTAssertNotNil(heatmapRoute)
        XCTAssertNotNil(snapshotWait)
        XCTAssertLessThan(heatmapRoute!.lowerBound, snapshotWait!.lowerBound)
        XCTAssertTrue(source.contains("case .investmentAnalysis, .investabilityMap, .heatmap:"))
    }

    func testInvestmentAnalysisKeepsCadenceTabsAtTheTopOfTheArchiveColumn() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: root.appending(path: "Sources/KSSDesktop/Views/InvestmentAnalysisView.swift"),
            encoding: .utf8)

        XCTAssertTrue(source.contains("HStack(alignment: .top, spacing: 0)"))
        XCTAssertTrue(source.contains("alignment: .topLeading"))
        XCTAssertTrue(source.contains(".frame(maxHeight: .infinity, alignment: .top)"))
    }
}
