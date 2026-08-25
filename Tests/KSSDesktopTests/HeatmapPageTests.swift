import XCTest
@testable import KSSDesktop

final class HeatmapPageTests: XCTestCase {
    private func liveSnapshot(source: String = "direct") -> HeatmapSnapshot {
        HeatmapSnapshot(
            market: "all",
            period: "day",
            updatedAt: "2024-08-25T16:00:00+08:00",
            tradeDate: "20240825",
            source: source,
            tiles: [
                HeatmapTile(
                    code: "000001",
                    symbol: "000001.SZ",
                    name: "平安银行",
                    industry: "银行",
                    circMv: 8e10,
                    changePct: 1.2,
                    turnover: 1e9,
                    price: 10
                )
            ],
            summary: HeatmapSummary(
                advanceCount: 1,
                flatCount: 0,
                declineCount: 0,
                turnoverAmount: 1e9
            )
        )
    }

    private var repoRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    func testLivePayloadCanInjectAndSampleCannot() {
        XCTAssertTrue(HeatmapTape.canShow(liveSnapshot()))
        XCTAssertFalse(HeatmapTape.canShow(liveSnapshot(source: "fallback")))
        XCTAssertFalse(HeatmapTape.canShow(liveSnapshot(source: "sample")))
        var empty = liveSnapshot()
        empty.tiles = []
        XCTAssertFalse(HeatmapTape.canShow(empty))
        var undated = liveSnapshot()
        undated.tradeDate = ""
        XCTAssertFalse(HeatmapTape.canShow(undated))
    }

    @MainActor
    func testFailedFetchNeverSetsACloud() async {
        let store = KSSStore(testBridge: nil)
        await store.loadHeatmapSnapshot()
        let snapshot = store.heatmapSnapshot
        let error = store.heatmapError
        XCTAssertNil(snapshot)
        XCTAssertNotNil(error)
    }

    func testSelectStockMessageDoesNotCarryAURL() {
        XCTAssertEqual(
            HeatmapMessage.parse(["action": "selectStock", "symbol": "000001.SZ"]),
            .selectStock("000001.SZ")
        )
        XCTAssertEqual(
            HeatmapMessage.parse(["action": "selectStock", "symbol": "000001"]),
            .selectStock("000001")
        )
        XCTAssertNil(HeatmapMessage.parse(["action": "selectStock", "symbol": "https://xueqiu.com/S/SZ000001"]))
        if case .selectStock(let symbol) = HeatmapMessage.parse(["action": "selectStock", "symbol": "000001.SZ"]) {
            XCTAssertFalse(symbol.contains("://"))
        } else {
            XCTFail("expected selectStock")
        }
    }

    func testRangePeriodRefetchMessage() {
        XCTAssertEqual(
            HeatmapMessage.parse(["action": "refetch", "market": "cyb", "period": "week"]),
            .refetch(market: "cyb", period: "week")
        )
    }

    func testOffPoolSelectStockSetsImportingSymbol() throws {
        let source = try String(
            contentsOf: repoRoot.appending(path: "Sources/KSSDesktop/Services/KSSStore.swift"),
            encoding: .utf8
        )
        XCTAssertTrue(source.contains("importingSymbol = symbol"))
        let view = try String(
            contentsOf: repoRoot.appending(path: "Sources/KSSDesktop/Views/ContentView.swift"),
            encoding: .utf8
        )
        XCTAssertTrue(view.contains("正在导入"))
        XCTAssertTrue(view.contains("onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } }"))
    }

    func testShippedAssetsHaveNoWatchlistOrXueqiuPath() throws {
        let js = try String(
            contentsOf: repoRoot.appending(path: "Sources/KSSDesktop/Resources/Heatmap/heatmap.js"),
            encoding: .utf8
        )
        let html = try String(
            contentsOf: repoRoot.appending(path: "Sources/KSSDesktop/Resources/Heatmap/heatmap.html"),
            encoding: .utf8
        )
        let shipped = js + html
        XCTAssertTrue(shipped.contains("kssHeatmap"))
        XCTAssertTrue(shipped.contains("kssSetHeatmapB64"))
        XCTAssertFalse(shipped.lowercased().contains("watchlist"))
        XCTAssertFalse(shipped.lowercased().contains("xueqiu"))
        XCTAssertFalse(shipped.lowercased().contains("自选"))
    }

    func testWebViewBlocksNonFileNavigation() throws {
        let source = try String(
            contentsOf: repoRoot.appending(path: "Sources/KSSDesktop/Views/HeatmapWebView.swift"),
            encoding: .utf8
        )
        XCTAssertTrue(source.contains("isFileURL"))
        XCTAssertTrue(source.contains("decisionHandler(.cancel)"))
        XCTAssertTrue(source.contains("kssSetHeatmapB64"))
        XCTAssertTrue(source.contains("HeatmapTape.canShow"))
    }
}
