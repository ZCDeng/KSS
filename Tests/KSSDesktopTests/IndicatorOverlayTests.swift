import XCTest
@testable import KSSDesktop

/// U6/U9（plan 2026-07-12-004）：通用多指标 overlay 的 Codable 往返 + 动态字段 series 解码
/// + snake_case 重编码（chart.html 消费的 JS 层协议）；会话开场候选建议（IndicatorSuggestion）解码。
final class IndicatorOverlayTests: XCTestCase {

    private func data(_ json: String) -> Data { Data(json.utf8) }

    func testIndicatorSignalDecodesCamelCaseBridgeFields() throws {
        let json = """
        {"indicatorId":"ma1","asof":"2026-07-10","status":"ok","action":"BUY",
         "prevAction":"STAY_FLAT","position":"LONG","predScore":0.42,"predBias":"bullish",
         "family":"ma_cross","unpinned":false,"ruleSentence":"均线交叉：金叉入场、死叉离场",
         "execNote":"note"}
        """
        let sig = try JSONDecoder().decode(IndicatorSignal.self, from: data(json))
        XCTAssertEqual(sig.indicatorId, "ma1")
        XCTAssertEqual(sig.action, "BUY")
        XCTAssertEqual(sig.predScore, 0.42)
        XCTAssertEqual(sig.family, "ma_cross")
    }

    func testIndicatorSeriesPointPicksFirstNumericFieldRegardlessOfName() throws {
        // ma_cross 族：ma_fast/ma_slow 两个数值字段，取第一个非 date 的。
        let maPoint = try JSONDecoder().decode(
            IndicatorSeriesPoint.self, from: data(#"{"date":"2026-07-01","ma_fast":12.1,"ma_slow":11.9}"#)
        )
        XCTAssertEqual(maPoint.date, "2026-07-01")
        XCTAssertNotNil(maPoint.value)

        // rsi_threshold 族：单一数值字段 rsi。
        let rsiPoint = try JSONDecoder().decode(
            IndicatorSeriesPoint.self, from: data(#"{"date":"2026-07-02","rsi":55.3}"#)
        )
        XCTAssertEqual(rsiPoint.date, "2026-07-02")
        XCTAssertEqual(rsiPoint.value, 55.3)
    }

    func testIndicatorSeriesPointMissingNumericFieldYieldsNilValue() throws {
        let point = try JSONDecoder().decode(
            IndicatorSeriesPoint.self, from: data(#"{"date":"2026-07-03","rsi":null}"#)
        )
        XCTAssertEqual(point.date, "2026-07-03")
        XCTAssertNil(point.value)
    }

    func testIndicatorOverlayDecodesMarkersAndSeries() throws {
        let json = """
        {"indicatorId":"ma1","status":"ok","reason":"",
         "markers":[{"time":"2026-07-01","position":"belowBar","color":"#26a69a","shape":"arrowUp","text":"买"}],
         "series":[{"date":"2026-07-01","ma_fast":12.1,"ma_slow":11.9}]}
        """
        let ov = try JSONDecoder().decode(IndicatorOverlay.self, from: data(json))
        XCTAssertEqual(ov.indicatorId, "ma1")
        XCTAssertEqual(ov.markers?.count, 1)
        XCTAssertEqual(ov.series?.count, 1)
        XCTAssertEqual(ov.series?.first?.date, "2026-07-01")
    }

    func testEncodeIndicatorOverlaysProducesSnakeCaseForJSLayer() throws {
        let overlay = IndicatorOverlay(
            indicatorId: "ma1", status: "ok", reason: "",
            markers: [MIMarker(time: "2026-07-01", position: "belowBar", color: "#26a69a", shape: "arrowUp", text: "买")],
            series: [IndicatorSeriesPoint(date: "2026-07-01", value: 12.1)]
        )
        let json = StockDetailView.encodeIndicatorOverlays([overlay])
        // chart.html 的 applyIndicatorOverlays 按 snake_case 读 indicator_id；series 项按
        // {date, value}（唯一数值键）— 两者都必须在重编码后的 JSON 里以 snake_case 出现。
        XCTAssertTrue(json.contains("\"indicator_id\":\"ma1\""), json)
        XCTAssertTrue(json.contains("\"date\":\"2026-07-01\""), json)
        XCTAssertTrue(json.contains("\"value\":12.1") || json.contains("\"value\" : 12.1"), json)
    }

    func testEncodeIndicatorOverlaysEmptyOrNilYieldsEmptyArray() {
        XCTAssertEqual(StockDetailView.encodeIndicatorOverlays(nil), "[]")
        XCTAssertEqual(StockDetailView.encodeIndicatorOverlays([]), "[]")
    }

    func testIndicatorSignalIdentifiableFallsBackToStableUnknown() {
        let sig = IndicatorSignal(indicatorId: nil)
        XCTAssertEqual(sig.id, "unknown")
        // 稳定性：多次访问同一实例的 id 不应变化（区别于 UUID() 每次生成新值的坑）。
        XCTAssertEqual(sig.id, sig.id)
    }

    // MARK: - U9: 会话开场候选建议

    func testIndicatorSuggestionDecodesWithCandidate() throws {
        let json = """
        {"family":"ma_cross","params":{"fast":5,"slow":20,"kind":"sma"},
         "reason":"自选中还没有 ma_cross 族的信号覆盖","suggestedSymbols":["688017.SH","688322.SH"]}
        """
        let s = try JSONDecoder().decode(IndicatorSuggestion.self, from: data(json))
        XCTAssertEqual(s.family, "ma_cross")
        XCTAssertEqual(s.reason, "自选中还没有 ma_cross 族的信号覆盖")
        XCTAssertEqual(s.suggestedSymbols, ["688017.SH", "688322.SH"])
    }

    func testIndicatorSuggestionDecodesWithNoCandidate() throws {
        // bridge 无候选时只返回 family=null + reason，无 params/suggestedSymbols 键。
        let json = #"{"family":null,"reason":"基元库内候选已覆盖或均在 NO-GO 记忆内"}"#
        let s = try JSONDecoder().decode(IndicatorSuggestion.self, from: data(json))
        XCTAssertNil(s.family)
        XCTAssertNil(s.suggestedSymbols)
    }
}
