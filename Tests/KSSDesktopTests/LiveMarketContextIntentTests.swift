import XCTest
@testable import KSSDesktop

final class LiveMarketContextIntentTests: XCTestCase {
    func testHistoricalQuestionDoesNotCreateLiveContextScope() {
        XCTAssertNil(
            KSSStore.liveContextScope(
                for: "复盘 688008 上周为什么上涨？",
                watchlistSymbols: ["688008.SH"]
            )
        )
    }

    func testRealtimeMarketQuestionCreatesVisibleMarketScope() {
        let scope = KSSStore.liveContextScope(
            for: "今天大盘总体怎么样？",
            watchlistSymbols: []
        )

        XCTAssertEqual(scope?["scope"], "market")
        XCTAssertEqual(scope?["intent"], "explain")
        XCTAssertEqual(scope?["symbols"], "000001.SH,399001.SZ,399006.SZ,000300.SH")
    }

    func testRealtimeWatchlistQuestionUsesWatchlistScope() {
        let scope = KSSStore.liveContextScope(
            for: "请看当前自选报价",
            watchlistSymbols: ["688008.SH", "00700.HK"]
        )

        XCTAssertEqual(scope?["scope"], "watchlist")
        XCTAssertEqual(scope?["symbols"], "688008.SH,00700.HK")
    }

    func testRealtimeNamedSymbolsUseTheSmallestExplicitScope() {
        let scope = KSSStore.liveContextScope(
            for: "688008 和 300750 当前报价怎么样？",
            watchlistSymbols: ["000001.SH"]
        )

        XCTAssertEqual(scope?["scope"], "symbols")
        XCTAssertEqual(scope?["symbols"], "688008,300750")
    }
}
