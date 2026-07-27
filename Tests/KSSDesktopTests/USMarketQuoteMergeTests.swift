import XCTest
@testable import KSSDesktop

final class USMarketQuoteMergeTests: XCTestCase {
    func testFailureRetainsLastCompleteSnapshotAsStale() {
        let previous = USMarketQuote(
            code: "NVDA", name: "NVIDIA", last: 100, prevClose: 98,
            pct: 2.0408, source: "longbridge",
            sourceAsOf: "2026-07-28T10:00:00-04:00",
            receivedAt: "2026-07-28T10:00:01-04:00",
            marketPhase: "regular", status: "live", error: nil
        )
        let failed = USMarketQuote(
            code: "NVDA", name: "NVIDIA", last: nil, prevClose: nil,
            pct: nil, source: "longbridge", sourceAsOf: nil,
            receivedAt: "2026-07-28T10:01:00-04:00",
            marketPhase: "regular", status: "unavailable", error: "network"
        )

        let merged = USMarketQuoteMerge.merge(
            previous: ["NVDA": previous],
            incoming: [failed]
        )["NVDA"]

        XCTAssertEqual(merged?.last, 100)
        XCTAssertEqual(merged?.prevClose, 98)
        XCTAssertEqual(merged?.status, "stale")
        XCTAssertEqual(merged?.error, "network")
    }

    func testCoverageUsesIndependentUSStatuses() {
        let quotes = [
            "MCHI": quote("MCHI", status: "live"),
            "IXIC": quote("IXIC", status: "delayed"),
            "XIN9": quote("XIN9", status: "static"),
        ]
        let coverage = USMarketQuoteMerge.coverage(
            quotes: quotes,
            orderedCodes: ["MCHI", "IXIC", "XIN9"]
        )

        XCTAssertEqual(coverage.live, 1)
        XCTAssertEqual(coverage.delayed, 1)
        XCTAssertEqual(coverage.static, 1)
        XCTAssertEqual(
            USMarketQuoteMerge.summary(coverage),
            "1 实时 · 1 延迟 · 1 静态"
        )
    }

    private func quote(_ code: String, status: String) -> USMarketQuote {
        USMarketQuote(
            code: code, name: code, last: 1, prevClose: 1, pct: 0,
            source: "test", sourceAsOf: nil, receivedAt: nil,
            marketPhase: "regular", status: status, error: nil
        )
    }
}
