import XCTest
@testable import KSSDesktop

final class IntradaySparklineTests: XCTestCase {
    // MARK: - Covers AE4

    func testSameSliceExpandedDoesNotChangeRangeWhenNoNewDeviation() {
        // 30 点里已含最大偏离（早盘冲高），后续 240 点未突破 → 范围不变。
        let prevClose = 100.0
        let first30 = [100.0, 106.0] + Array(repeating: 101.0, count: 28)
        let s1 = SparklineYAxis.merge(
            existing: nil, newPoints: first30, newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range1 = SparklineYAxis.range(for: s1)!

        let fuller240 = first30 + Array(repeating: 101.5, count: 210)
        let s2 = SparklineYAxis.merge(
            existing: s1, newPoints: fuller240, newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range2 = SparklineYAxis.range(for: s2)!

        XCTAssertEqual(range1.yMin, range2.yMin, accuracy: 1e-9)
        XCTAssertEqual(range1.yMax, range2.yMax, accuracy: 1e-9)
    }

    func testRangeExpandsMonotonicallyAndDoesNotShrinkOnSmallerSlice() {
        let prevClose = 100.0
        let s1 = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 101.0], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range1 = SparklineYAxis.range(for: s1)!

        // 更大偏离出现（跌 5%）
        let s2 = SparklineYAxis.merge(
            existing: s1, newPoints: [100.0, 101.0, 95.0], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range2 = SparklineYAxis.range(for: s2)!
        XCTAssertLessThan(range2.yMin, range1.yMin)

        // 随后传入偏离更小的切片 → 范围不回缩
        let s3 = SparklineYAxis.merge(
            existing: s2, newPoints: [100.2, 100.4], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range3 = SparklineYAxis.range(for: s3)!
        XCTAssertEqual(range3.yMin, range2.yMin, accuracy: 1e-9)
        XCTAssertEqual(range3.yMax, range2.yMax, accuracy: 1e-9)
    }

    func testFlatDayFallsBackToMinHalfSpan() {
        let prevClose = 100.0
        let s = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 100.01, 99.99], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let range = SparklineYAxis.range(for: s)!
        XCTAssertEqual(range.yMax - range.yMin, 2 * prevClose * SparklineYAxis.minHalfSpanFraction, accuracy: 1e-9)
    }

    func testDayHighLowAnchorsFullRangeOnFirstFrame() {
        let prevClose = 100.0
        // 首帧只有两个点，但 dayHigh/dayLow 已覆盖全日范围。
        let s = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 100.5], newPrevClose: prevClose,
            newDayHigh: 108.0, newDayLow: 96.0, newTradeDate: "2026-07-14")
        let range = SparklineYAxis.range(for: s)!
        XCTAssertEqual(range.yMax, prevClose + 8.0, accuracy: 1e-9)
        XCTAssertEqual(range.yMin, prevClose - 8.0, accuracy: 1e-9)
    }

    func testMissingDayHighLowDegradesToPointsOnlyMonotonicExpansion() {
        let prevClose = 100.0
        let s1 = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 101.0], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        let s2 = SparklineYAxis.merge(
            existing: s1, newPoints: [100.0, 101.0, 103.0], newPrevClose: prevClose,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        XCTAssertEqual(s2.maxDeviation, 3.0, accuracy: 1e-9)
    }

    func testTradeDateChangeResetsDeviationAndPrevClose() {
        let s1 = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 110.0], newPrevClose: 100.0,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        XCTAssertEqual(s1.maxDeviation, 10.0, accuracy: 1e-9)

        // 次日开盘：新 tradeDate，新 prevClose，昨日大偏离不应带入。
        let s2 = SparklineYAxis.merge(
            existing: s1, newPoints: [50.0, 50.2], newPrevClose: 50.0,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-15")
        XCTAssertEqual(s2.tradeDate, "2026-07-15")
        XCTAssertEqual(s2.prevClose, 50.0)
        XCTAssertLessThan(s2.maxDeviation, 10.0)
        let range = SparklineYAxis.range(for: s2)!
        // 半幅落回最小保底（0.5%），不是被昨日 10.0 的偏离撑大。
        XCTAssertEqual(range.yMax - range.yMin, 2 * 50.0 * SparklineYAxis.minHalfSpanFraction, accuracy: 1e-9)
    }

    func testMissingOrZeroPrevCloseReturnsNilRangeWithoutCrashing() {
        let sNil = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 101.0], newPrevClose: nil,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        XCTAssertNil(SparklineYAxis.range(for: sNil))

        let sZero = SparklineYAxis.merge(
            existing: nil, newPoints: [100.0, 101.0], newPrevClose: 0,
            newDayHigh: nil, newDayLow: nil, newTradeDate: "2026-07-14")
        XCTAssertNil(SparklineYAxis.range(for: sZero))
    }
}
