import XCTest
@testable import KSSDesktop

final class RealtimeMergeTests: XCTestCase {

    // MARK: isLiveableSymbol

    func testLiveableSHAndSZ() {
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("000001.SH"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("159361.sz"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("563360.SH"))
    }

    func testRejectsBJGlobalBare() {
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("830799.BJ"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("IXIC"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("HSI"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("AAPL"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol(""))
    }

    // MARK: harvestSymbols

    func testHarvestPrioritizesEtfsIndicesFiltersGlobals() {
        let strip = MarketStrip(
            date: "20260710",
            northMoney: 1,
            northDate: nil,
            etfs: [
                ETFQuote(code: "563360.SH", name: "A500ETF", close: 1, pct: 0.1),
                ETFQuote(code: "159361.SZ", name: "A500ETF", close: 1, pct: 0.1)
            ],
            indices: [
                IndexQuote(code: "000001.SH", name: "上证", close: 3000, pct: 0.5),
                IndexQuote(code: "IXIC", name: "纳指", close: 1, pct: 0),
                IndexQuote(code: "HSI", name: "恒生", close: 1, pct: 0)
            ],
            indexBoard: [
                IndexQuote(code: "399006.SZ", name: "创业板", close: 2000, pct: 1),
                IndexQuote(code: "000001.SH", name: "上证", close: 3000, pct: 0.5) // dupe
            ],
            limitBoard: nil,
            turnoverTop: nil,
            globalIndices: nil
        )
        let symbols = RealtimeMerge.harvestSymbols(strip: strip, extra: ["688017.SH", "830799.BJ"])
        XCTAssertEqual(symbols.first, "563360.SH")
        XCTAssertTrue(symbols.contains("000001.SH"))
        XCTAssertTrue(symbols.contains("399006.SZ"))
        XCTAssertTrue(symbols.contains("688017.SH"))
        XCTAssertFalse(symbols.contains("IXIC"))
        XCTAssertFalse(symbols.contains("HSI"))
        XCTAssertFalse(symbols.contains("830799.BJ"))
        // 去重：上证只一次
        XCTAssertEqual(symbols.filter { $0 == "000001.SH" }.count, 1)
    }

    func testHarvestCap() {
        var board: [IndexQuote] = []
        for i in 0..<30 {
            board.append(IndexQuote(code: String(format: "%06d.SH", i), name: "x", close: 1, pct: 0))
        }
        let strip = MarketStrip(
            date: nil, northMoney: nil, northDate: nil,
            etfs: [], indices: nil, indexBoard: board,
            limitBoard: nil, turnoverTop: nil, globalIndices: nil
        )
        let symbols = RealtimeMerge.harvestSymbols(strip: strip, maxCount: 20)
        XCTAssertEqual(symbols.count, 20)
    }

    // MARK: applyLive

    func testApplyLiveWithPrevClose() {
        var q = LongbridgeQuote()
        q.lastDone = 110
        q.prevClose = 100
        let r = RealtimeMerge.applyLive(close: 99, pct: -1, quote: q)
        XCTAssertEqual(r.close, 110, accuracy: 1e-9)
        XCTAssertEqual(r.pct, 10, accuracy: 1e-9)
        XCTAssertTrue(r.isLive)
    }

    func testApplyLivePriceOnlyKeepsSnapshotPct() {
        var q = LongbridgeQuote()
        q.lastDone = 50
        q.prevClose = nil
        let r = RealtimeMerge.applyLive(close: 48, pct: 2.5, quote: q)
        XCTAssertEqual(r.close, 50, accuracy: 1e-9)
        XCTAssertEqual(r.pct, 2.5, accuracy: 1e-9)
        XCTAssertTrue(r.isLive)
    }

    func testApplyLiveNilOrErrorKeepsSnapshot() {
        var bad = LongbridgeQuote()
        bad.error = "not_covered"
        bad.lastDone = 1
        let r1 = RealtimeMerge.applyLive(close: 10, pct: 1, quote: bad)
        XCTAssertEqual(r1.close, 10)
        XCTAssertFalse(r1.isLive)

        let r2 = RealtimeMerge.applyLive(close: 10, pct: 1, quote: nil)
        XCTAssertEqual(r2.close, 10)
        XCTAssertFalse(r2.isLive)
    }

    // MARK: hasAnyLive

    func testHasAnyLiveFieldLevel() {
        var live = LongbridgeQuote()
        live.lastDone = 1
        var dead = LongbridgeQuote()
        dead.error = "x"
        let map = ["000001.SH": live, "IXIC": dead]
        XCTAssertTrue(RealtimeMerge.hasAnyLive(symbols: ["000001.SH", "IXIC"], quotes: map))
        XCTAssertFalse(RealtimeMerge.hasAnyLive(symbols: ["IXIC", "HSI"], quotes: map))
        XCTAssertFalse(RealtimeMerge.hasAnyLive(symbols: ["399006.SZ"], quotes: map))
    }
}
