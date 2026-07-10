import XCTest
@testable import KSSDesktop

final class DailyBarFreshnessTests: XCTestCase {

    func testNormalizeDate() {
        XCTAssertEqual(DailyBarFreshness.normalizeDate("2026-07-09"), "2026-07-09")
        XCTAssertEqual(DailyBarFreshness.normalizeDate("20260709"), "2026-07-09")
        XCTAssertEqual(DailyBarFreshness.normalizeDate(" 20260709 "), "2026-07-09")
        XCTAssertNil(DailyBarFreshness.normalizeDate(nil))
        XCTAssertNil(DailyBarFreshness.normalizeDate(""))
        XCTAssertNil(DailyBarFreshness.normalizeDate("bad"))
    }

    func testStatusFreshEqual() {
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "2026-07-09", referenceTradeDate: "2026-07-09"),
            .fresh
        )
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "20260709", referenceTradeDate: "2026-07-09"),
            .fresh
        )
    }

    func testStatusStale() {
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "2026-06-26", referenceTradeDate: "2026-07-10"),
            .stale
        )
    }

    func testStatusMissing() {
        XCTAssertEqual(DailyBarFreshness.status(barDate: nil, referenceTradeDate: "2026-07-10"), .missing)
        XCTAssertEqual(DailyBarFreshness.status(barDate: "", referenceTradeDate: "2026-07-10"), .missing)
    }

    func testStatusNoReferenceNotStale() {
        // 无锚点时不误标陈旧
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "2026-06-26", referenceTradeDate: nil),
            .fresh
        )
    }

    func testLabels() {
        XCTAssertEqual(
            DailyBarFreshness.compactLabel(barDate: "2026-07-09", referenceTradeDate: "2026-07-09"),
            "07-09"
        )
        XCTAssertTrue(
            DailyBarFreshness.compactLabel(barDate: "2026-06-26", referenceTradeDate: "2026-07-10")
                .contains("陈旧")
        )
        XCTAssertEqual(
            DailyBarFreshness.compactLabel(barDate: nil, referenceTradeDate: "2026-07-10"),
            "无日线"
        )
        XCTAssertTrue(
            DailyBarFreshness.detailLabel(barDate: "2026-06-26", referenceTradeDate: "2026-07-10")
                .contains("陈旧")
        )
        XCTAssertTrue(
            DailyBarFreshness.detailLabel(barDate: "2026-07-09", referenceTradeDate: "2026-07-09")
                .contains("日线截至")
        )
    }
}
