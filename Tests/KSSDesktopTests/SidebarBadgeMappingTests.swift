import XCTest
@testable import KSSDesktop

final class SidebarBadgeMappingTests: XCTestCase {
    func testNoSignalsYieldsEmptyMap() {
        let map = SidebarBadgeMapping.badges(selfCheckFailCount: 0, recommendationCount: 0)
        XCTAssertTrue(map.isEmpty)
    }

    func testSelfCheckFailsAddsDashboardDot() {
        let map = SidebarBadgeMapping.badges(selfCheckFailCount: 3, recommendationCount: 0)
        XCTAssertEqual(map[.dashboard], .dot)
        XCTAssertNil(map[.recommendations])
    }

    func testZeroFailDoesNotBadgeDashboard() {
        let map = SidebarBadgeMapping.badges(selfCheckFailCount: 0, recommendationCount: 5)
        XCTAssertNil(map[.dashboard])
        XCTAssertEqual(map[.recommendations], .count(5))
    }

    func testBothSignals() {
        let map = SidebarBadgeMapping.badges(selfCheckFailCount: 1, recommendationCount: 2)
        XCTAssertEqual(map[.dashboard], .dot)
        XCTAssertEqual(map[.recommendations], .count(2))
    }

    func testCountDisplayClampsNegativeViaMappingPositiveOnly() {
        // mapping only emits when count > 0
        let map = SidebarBadgeMapping.badges(selfCheckFailCount: 0, recommendationCount: -1)
        XCTAssertTrue(map.isEmpty)
    }

    func testBadgeDisplayCount() {
        XCTAssertNil(SidebarNavBadge.dot.displayCount)
        XCTAssertEqual(SidebarNavBadge.count(2).displayCount, 2)
        XCTAssertEqual(SidebarNavBadge.count(-3).displayCount, 0)
    }
}
