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
        XCTAssertEqual(
            DailyBarFreshness.compactLabel(barDate: "2026-06-26", referenceTradeDate: "2026-07-10"),
            "陈旧·06-26"
        )
        XCTAssertEqual(
            DailyBarFreshness.compactLabel(barDate: nil, referenceTradeDate: "2026-07-10"),
            "无日线"
        )
        XCTAssertEqual(
            DailyBarFreshness.detailLabel(barDate: "2026-06-26", referenceTradeDate: "2026-07-10"),
            "日线陈旧 · 截至 2026-06-26"
        )
        XCTAssertEqual(
            DailyBarFreshness.detailLabel(barDate: "2026-07-09", referenceTradeDate: "2026-07-09"),
            "日线截至 2026-07-09"
        )
    }

    func testStatusBarAfterReferenceIsFresh() {
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "2026-07-10", referenceTradeDate: "2026-07-09"),
            .fresh
        )
    }

    func testStatusUnparseableReferenceNotStale() {
        XCTAssertEqual(
            DailyBarFreshness.status(barDate: "2026-06-26", referenceTradeDate: "not-a-date"),
            .fresh
        )
    }

    func testTradingHoursDecodesReferenceTradeDate() throws {
        let withRef = """
        {"is_trade_day":true,"is_trading_session":false,"session_end":"15:05","now":"x","reference_trade_date":"2026-07-09"}
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(TradingHours.self, from: withRef)
        XCTAssertEqual(decoded.referenceTradeDate, "2026-07-09")

        let withoutRef = """
        {"is_trade_day":true,"is_trading_session":true,"session_end":"15:05"}
        """.data(using: .utf8)!
        let decoded2 = try JSONDecoder().decode(TradingHours.self, from: withoutRef)
        XCTAssertNil(decoded2.referenceTradeDate)
    }
}
