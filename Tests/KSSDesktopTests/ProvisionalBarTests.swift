import XCTest
@testable import KSSDesktop

/// R6 U7：日 K 当日未收盘 bar 组装（StockDetailView.appendingProvisionalBar）门控矩阵。
final class ProvisionalBarTests: XCTestCase {
    private let today = "2026-07-15"

    private var history: [PricePoint] {
        [
            PricePoint(date: "2026-07-11", open: 10.0, high: 10.5, low: 9.8, close: 10.2,
                       pctChange: 1.0, volume: 1000, amount: 1e6, provisional: nil),
            PricePoint(date: "2026-07-14", open: 10.2, high: 10.6, low: 10.0, close: 10.4,
                       pctChange: 1.96, volume: 1200, amount: 1.2e6, provisional: nil),
        ]
    }

    private func quote(
        lastDone: Double? = 10.8, prevClose: Double? = 10.4,
        open: Double? = 10.5, high: Double? = 10.9, low: Double? = 10.3,
        volume: Double? = 800, asof: String? = "2026-07-15T14:30:00+08:00",
        error: String? = nil
    ) -> LongbridgeQuote {
        LongbridgeQuote(
            symbol: "688114.SH", lastDone: lastDone, prevClose: prevClose,
            open: open, high: high, low: low, volume: volume, turnover: 8e5,
            tradeStatus: "Normal", sourceAsofTs: asof, eligibility: "forward_observed",
            routedProvider: "longbridge", manifestStale: false, error: error, hint: nil
        )
    }

    // (a) 交易时段 + live quote → 追加当日 bar（OHLC/volume/涨跌全带）
    func testAppendsBarDuringSessionWithLiveQuote() {
        let out = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(), isTradingSession: true, today: today)
        XCTAssertEqual(out.count, 3)
        let bar = out.last!
        XCTAssertEqual(bar.date, today)
        XCTAssertEqual(bar.provisional, true)
        XCTAssertEqual(bar.close, 10.8)
        XCTAssertEqual(bar.volume, 800)
        XCTAssertEqual(bar.pctChange!, (10.8 - 10.4) / 10.4 * 100, accuracy: 0.001)
    }

    // (b) 非交易时段不拼
    func testNoBarOffSession() {
        let out = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(), isTradingSession: false, today: today)
        XCTAssertEqual(out.count, 2)
    }

    // (c) quote 缺 high/low 不拼
    func testNoBarWhenOHLCIncomplete() {
        let out = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(high: nil), isTradingSession: true, today: today)
        XCTAssertEqual(out.count, 2)
    }

    // (d) 序列末行已是今天（数据晚到）不重复拼
    func testNoDuplicateWhenTodayAlreadyPresent() {
        var pts = history
        pts.append(PricePoint(date: today, open: 10.5, high: 10.9, low: 10.3, close: 10.7,
                              pctChange: nil, volume: nil, amount: nil, provisional: nil))
        let out = StockDetailView.appendingProvisionalBar(
            to: pts, quote: quote(), isTradingSession: true, today: today)
        XCTAssertEqual(out.count, 3)
    }

    // (e) prevClose 缺失 → 序列末收盘反推涨跌
    func testPctFallsBackToLastClose() {
        let out = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(prevClose: nil), isTradingSession: true, today: today)
        XCTAssertEqual(out.last!.pctChange!, (10.8 - 10.4) / 10.4 * 100, accuracy: 0.001)
    }

    // (f) sourceAsofTs 日期 ≠ 今天（或缺失）不拼——盘后重启无幽灵 bar
    func testNoBarWhenAsofIsStaleSession() {
        let stale = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(asof: "2026-07-14T15:00:00+08:00"),
            isTradingSession: true, today: today)
        XCTAssertEqual(stale.count, 2)
        let missing = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(asof: nil), isTradingSession: true, today: today)
        XCTAssertEqual(missing.count, 2)
    }

    // (g) quote 带 error（非 live）不拼
    func testNoBarWhenQuoteNotLive() {
        let out = StockDetailView.appendingProvisionalBar(
            to: history, quote: quote(error: "auth_failed"), isTradingSession: true, today: today)
        XCTAssertEqual(out.count, 2)
    }
}
