import XCTest
@testable import KSSDesktop

final class SidebarNavIconTests: XCTestCase {
    func testNavSectionsHaveCustomIconBases() {
        let expected: [WorkspaceSection: String] = [
            .dashboard: "dashboard",
            .recommendations: "recommendations",
            .watchlist: "watchlist",
            .themes: "themes",
            .investabilityMap: "investability",
            .heatmap: "heatmap",
            .trends: "trends",
            .reviews: "reviews",
            .newsDigest: "newsDigest",
            .backtests: "backtests",
            .stocks: "stocks",
            .investmentAnalysis: "investmentAnalysis",
        ]
        for (section, base) in expected {
            XCTAssertEqual(SidebarNavIconCatalog.resourceBase(for: section), base, section.rawValue)
        }
    }

    func testNonNavSectionsFallBackToSFSymbol() {
        XCTAssertNil(SidebarNavIconCatalog.resourceBase(for: .settings))
        XCTAssertNil(SidebarNavIconCatalog.resourceBase(for: .architecture))
        XCTAssertNil(SidebarNavIconCatalog.resourceBase(for: .runbook))
    }

    func testSeesawHasCustomIconBase() {
        XCTAssertEqual(SidebarNavIconCatalog.resourceBase(for: .aiChat), "seesaw")
    }

    func testBundledPDFLoadsForOutlineAndFilled() {
        let bases = ["dashboard", "recommendations", "watchlist", "themes", "trends",
                     "reviews", "newsDigest", "backtests", "stocks", "investmentAnalysis",
                     "investability", "heatmap", "seesaw"]
        for base in bases {
            XCTAssertNotNil(SidebarNavIconCatalog.image(base: base, filled: false), "\(base)-outline")
            XCTAssertNotNil(SidebarNavIconCatalog.image(base: base, filled: true), "\(base)-filled")
        }
    }
}
