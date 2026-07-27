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

    func testHeaderStatusMovesFreshnessOutOfIndividualCards() {
        let regular = USMarketQuoteMerge.headerStatus(
            USMarketCoverage(live: 2, delayed: 1, stale: 0, static: 9, unavailable: 0),
            phase: "regular"
        )
        XCTAssertEqual(regular.text, "盘中 · 2 实时 · 1 延迟")
        XCTAssertEqual(regular.systemImage, "dot.radiowaves.left.and.right")
        XCTAssertTrue(regular.isActive)
        XCTAssertFalse(regular.text.contains("静态"))

        let post = USMarketQuoteMerge.headerStatus(
            USMarketCoverage(live: 0, delayed: 0, stale: 0, static: 12, unavailable: 0),
            phase: "post"
        )
        XCTAssertEqual(post.text, "盘后 · 收盘数据")
        XCTAssertEqual(post.systemImage, "moon.stars")
        XCTAssertFalse(post.isActive)
        XCTAssertFalse(post.text.contains("yFinance"))
    }

    func testOvernightMarqueeUsesContinuousTimelineAndNoPerCardSourceLabel() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Views/DashboardView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        let start = try XCTUnwrap(source.range(of: "struct OvernightUSMarquee"))
        let end = try XCTUnwrap(
            source.range(of: "struct MarketIndexRow", range: start.upperBound..<source.endIndex)
        )
        let marquee = String(source[start.lowerBound..<end.lowerBound])

        XCTAssertTrue(marquee.contains("TimelineView(.animation"))
        XCTAssertTrue(marquee.contains("row(measured: true)"))
        XCTAssertTrue(marquee.contains("row(measured: false)"))
        XCTAssertFalse(marquee.contains("statusLabel("))
        XCTAssertFalse(marquee.contains("sourceLabel("))
    }

    private func quote(_ code: String, status: String) -> USMarketQuote {
        USMarketQuote(
            code: code, name: code, last: 1, prevClose: 1, pct: 0,
            source: "test", sourceAsOf: nil, receivedAt: nil,
            marketPhase: "regular", status: status, error: nil
        )
    }
}
