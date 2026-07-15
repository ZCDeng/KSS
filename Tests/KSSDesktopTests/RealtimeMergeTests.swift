import XCTest
@testable import KSSDesktop

final class RealtimeMergeTests: XCTestCase {

    // MARK: isLiveableSymbol / toLongbridgeSymbol

    func testLiveableSHAndSZ() {
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("000001.SH"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("159361.sz"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("563360.SH"))
        XCTAssertEqual(RealtimeMerge.toLongbridgeSymbol("159361.sz"), "159361.SZ")
    }

    func testLiveableHKAndAliases() {
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("HSI"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("HSTECH"))
        XCTAssertTrue(RealtimeMerge.isLiveableSymbol("02828.HK"))
        XCTAssertEqual(RealtimeMerge.toLongbridgeSymbol("HSI"), "HSI.HK")
        XCTAssertEqual(RealtimeMerge.toLongbridgeSymbol("HSTECH"), "HSTECH.HK")
        XCTAssertEqual(RealtimeMerge.toLongbridgeSymbol("02828.HK"), "02828.HK")
    }

    func testRejectsBJGlobalBare() {
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("830799.BJ"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("IXIC"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol("AAPL"))
        XCTAssertFalse(RealtimeMerge.isLiveableSymbol(""))
        XCTAssertNil(RealtimeMerge.toLongbridgeSymbol("IXIC"))
        XCTAssertNil(RealtimeMerge.toLongbridgeSymbol("899050.BJ"))
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
        let symbols = RealtimeMerge.harvestSymbols(strip: strip, priority: ["688017.SH", "830799.BJ"])
        // priority 页内标的排最前
        XCTAssertEqual(symbols.first, "688017.SH")
        XCTAssertTrue(symbols.contains("563360.SH"))
        XCTAssertTrue(symbols.contains("000001.SH"))
        XCTAssertTrue(symbols.contains("399006.SZ"))
        XCTAssertTrue(symbols.contains("HSI"))  // 港股别名可实时
        XCTAssertFalse(symbols.contains("IXIC"))
        XCTAssertFalse(symbols.contains("830799.BJ"))
        // 去重：上证只一次
        XCTAssertEqual(symbols.filter { $0 == "000001.SH" }.count, 1)
    }

    func testHarvestIncludesIndexStacksEarly() {
        let stacks = [
            IndexStackColumn(id: "main", items: [
                IndexStackItem(code: "000001.SH", name: "上证", close: 1, pct: 0),
                IndexStackItem(code: "899050.BJ", name: "北证", close: 1, pct: 0),
            ]),
            IndexStackColumn(id: "hk", items: [
                IndexStackItem(code: "HSI", name: "恒生", close: 1, pct: 0),
                IndexStackItem(code: "HSTECH", name: "恒科", close: 1, pct: 0),
            ]),
        ]
        let strip = MarketStrip(
            date: nil, northMoney: nil, northDate: nil,
            etfs: [ETFQuote(code: "563360.SH", name: "A500", close: 1, pct: 0)],
            indices: nil, indexBoard: nil,
            limitBoard: nil, turnoverTop: nil, globalIndices: nil,
            overnightUS: nil, indexStacks: stacks
        )
        let symbols = RealtimeMerge.harvestSymbols(strip: strip, priority: [], maxCount: 10)
        XCTAssertTrue(symbols.contains("000001.SH"))
        XCTAssertTrue(symbols.contains("HSI"))
        XCTAssertTrue(symbols.contains("HSTECH"))
        XCTAssertTrue(symbols.contains("563360.SH"))
        XCTAssertFalse(symbols.contains("899050.BJ"))  // 北交所无 Longbridge
        // stacks 在 ETF 之前：000001 应早于或等于 ETF（priority 空时 stacks 先）
        let iSH = symbols.firstIndex(of: "000001.SH")!
        let iETF = symbols.firstIndex(of: "563360.SH")!
        XCTAssertLessThan(iSH, iETF)
    }

    func testPriorityBeatsIndexBoardCap() {
        var board: [IndexQuote] = []
        for i in 0..<25 {
            board.append(IndexQuote(code: String(format: "%06d.SH", i + 1), name: "x", close: 1, pct: 0))
        }
        let strip = MarketStrip(
            date: nil, northMoney: nil, northDate: nil,
            etfs: [], indices: nil, indexBoard: board,
            limitBoard: nil, turnoverTop: nil, globalIndices: nil
        )
        let symbols = RealtimeMerge.harvestSymbols(
            strip: strip,
            priority: ["688017.SH", "688322.SH"],
            maxCount: 5
        )
        XCTAssertEqual(symbols.prefix(2).map { $0 }, ["688017.SH", "688322.SH"])
        XCTAssertEqual(symbols.count, 5)
    }

    func testDisplayPriceLiveAndSnapshot() {
        var q = LongbridgeQuote()
        q.lastDone = 12.5
        q.prevClose = 10
        let live = RealtimeMerge.displayPrice(snapshotClose: 11, quote: q)
        XCTAssertEqual(live?.close, 12.5)
        XCTAssertEqual(live?.pct ?? 0, 25, accuracy: 1e-9)
        XCTAssertEqual(live?.isLive, true)

        let snap = RealtimeMerge.displayPrice(snapshotClose: 11, quote: nil)
        XCTAssertEqual(snap?.close, 11)
        XCTAssertEqual(snap?.isLive, false)

        XCTAssertNil(RealtimeMerge.displayPrice(snapshotClose: nil, quote: nil))
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
        let map = ["000001.SH": live, "IXIC": dead, "HSI": live]
        XCTAssertTrue(RealtimeMerge.hasAnyLive(symbols: ["000001.SH", "IXIC"], quotes: map))
        XCTAssertTrue(RealtimeMerge.hasAnyLive(symbols: ["HSI"], quotes: map))
        XCTAssertFalse(RealtimeMerge.hasAnyLive(symbols: ["IXIC"], quotes: map))
        XCTAssertFalse(RealtimeMerge.hasAnyLive(symbols: ["399006.SZ"], quotes: map))
    }

    // MARK: freshness / worstFreshness

    func testFreshnessUsesFallbackWhenSourceAsofTsMissing() {
        var q = LongbridgeQuote()
        q.lastDone = 1
        let now = Date()
        let quotes = ["000001.SH": q]
        let receivedAt = ["000001.SH": now.addingTimeInterval(-120)]
        XCTAssertEqual(
            RealtimeMerge.freshness(for: "000001.SH", quotes: quotes, receivedAtBySymbol: receivedAt, now: now),
            .fresh
        )
    }

    func testFreshnessMissingWhenNoQuoteAtAll() {
        XCTAssertEqual(
            RealtimeMerge.freshness(for: "000001.SH", quotes: [:], receivedAtBySymbol: [:], now: Date()),
            .missing
        )
    }

    func testWorstFreshnessDowngradesWhenAnyStale() {
        let now = Date()
        var q = LongbridgeQuote()
        q.lastDone = 1
        let quotes = ["A": q, "B": q]
        // A 新鲜（2 分钟前接收），B 过期（10 分钟前接收）——两者都没有 sourceAsofTs，走回退路径。
        let receivedAt = ["A": now.addingTimeInterval(-120), "B": now.addingTimeInterval(-600)]
        XCTAssertEqual(
            RealtimeMerge.worstFreshness(symbols: ["A", "B"], quotes: quotes, receivedAtBySymbol: receivedAt, now: now),
            .stale
        )
    }

    func testWorstFreshnessFreshWhenAllFreshAndNoneStale() {
        let now = Date()
        var q = LongbridgeQuote()
        q.lastDone = 1
        let quotes = ["A": q, "B": q]
        let receivedAt = ["A": now.addingTimeInterval(-60), "B": now.addingTimeInterval(-90)]
        XCTAssertEqual(
            RealtimeMerge.worstFreshness(symbols: ["A", "B"], quotes: quotes, receivedAtBySymbol: receivedAt, now: now),
            .fresh
        )
    }

    func testWorstFreshnessMissingWhenNoSymbolInMap() {
        XCTAssertEqual(
            RealtimeMerge.worstFreshness(symbols: ["A", "B"], quotes: [:], receivedAtBySymbol: [:], now: Date()),
            .missing
        )
    }

    func testWorstFreshnessIsolatesPerSymbolFallback() {
        // 一个标的自己的软失败/过期不会因为另一个标的仍在成功刷新而被掩盖（R2 回归）。
        let now = Date()
        var live = LongbridgeQuote()
        live.lastDone = 1
        var stale = LongbridgeQuote()
        stale.lastDone = 2
        let quotes = ["FRESH": live, "STALE": stale]
        let receivedAt = ["FRESH": now.addingTimeInterval(-30), "STALE": now.addingTimeInterval(-900)]
        XCTAssertEqual(
            RealtimeMerge.freshness(for: "STALE", quotes: quotes, receivedAtBySymbol: receivedAt, now: now),
            .stale
        )
        XCTAssertEqual(
            RealtimeMerge.freshness(for: "FRESH", quotes: quotes, receivedAtBySymbol: receivedAt, now: now),
            .fresh
        )
    }

    // MARK: sparklineCloses

    func testSparklineClosesDownsample() {
        let bars = (0..<300).map { i -> OHLCBar in
            var b = OHLCBar()
            b.close = Double(i)
            return b
        }
        let pts = RealtimeMerge.sparklineCloses(from: bars, maxPoints: 120)
        XCTAssertLessThanOrEqual(pts.count, 120)
        XCTAssertFalse(pts.isEmpty)
        XCTAssertEqual(pts.first, 0)
    }

    // MARK: - R6 U8：页头核心集合口径 + 最陈旧标的诊断

    private func coreStrip() -> MarketStrip {
        MarketStrip(
            date: "20260715", northMoney: 1, northDate: nil,
            etfs: [ETFQuote(code: "563360.SH", name: "A500ETF", close: 1, pct: 0.1)],
            indices: [IndexQuote(code: "000905.SH", name: "中证500", close: 1, pct: 0)],
            indexBoard: [IndexQuote(code: "399006.SZ", name: "创业板", close: 1, pct: 0)],
            limitBoard: nil, turnoverTop: nil, globalIndices: nil,
            overnightUS: nil,
            indexStacks: [IndexStackColumn(id: "main", items: [
                IndexStackItem(code: "000001.SH", name: "上证", close: 1, pct: 0),
            ])]
        )
    }

    private func liveQuote(asof: String) -> LongbridgeQuote {
        LongbridgeQuote(symbol: nil, lastDone: 1, prevClose: 1, open: 1, high: 1, low: 1,
                        volume: 1, turnover: 1, tradeStatus: "Normal", sourceAsofTs: asof,
                        eligibility: nil, routedProvider: nil, manifestStale: nil,
                        error: nil, hint: nil)
    }

    func testCoreDisplaySymbolsIsStacksPlusEtfsOnly() {
        let core = RealtimeMerge.coreDisplaySymbols(strip: coreStrip())
        XCTAssertEqual(Set(core), ["000001.SH", "563360.SH"])  // 指数一览/主指数行不入核心集合
    }

    func testHeaderFreshWhenCoreFreshDespiteStalePeripheral() {
        let now = Date()
        let freshTs = ISO8601DateFormatter().string(from: now.addingTimeInterval(-10))
        let staleTs = ISO8601DateFormatter().string(from: now.addingTimeInterval(-3600))
        let quotes = [
            "000001.SH": liveQuote(asof: freshTs),
            "563360.SH": liveQuote(asof: freshTs),
            "399006.SZ": liveQuote(asof: staleTs),  // 外围陈旧，不该拖垮页头
        ]
        let core = RealtimeMerge.coreDisplaySymbols(strip: coreStrip())
        XCTAssertEqual(RealtimeMerge.worstFreshness(
            symbols: core, quotes: quotes, receivedAtBySymbol: [:], now: now), .fresh)
    }

    func testHeaderStaleWhenAnyCoreSymbolStale() {
        let now = Date()
        let freshTs = ISO8601DateFormatter().string(from: now.addingTimeInterval(-10))
        let staleTs = ISO8601DateFormatter().string(from: now.addingTimeInterval(-3600))
        let quotes = [
            "000001.SH": liveQuote(asof: staleTs),
            "563360.SH": liveQuote(asof: freshTs),
        ]
        let core = RealtimeMerge.coreDisplaySymbols(strip: coreStrip())
        XCTAssertEqual(RealtimeMerge.worstFreshness(
            symbols: core, quotes: quotes, receivedAtBySymbol: [:], now: now), .stale)
        let worst = RealtimeMerge.stalestSymbol(
            symbols: core, quotes: quotes, receivedAtBySymbol: [:], now: now)
        XCTAssertEqual(worst?.symbol, "000001.SH")
        XCTAssertEqual(worst!.ageSeconds, 3600, accuracy: 5)
    }
}
