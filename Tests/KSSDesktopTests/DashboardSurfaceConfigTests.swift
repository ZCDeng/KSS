import XCTest
@testable import KSSDesktop

/// 盯盘 surface（隔夜追加 / 指标小卡）模型解码与字段映射。
final class DashboardSurfaceConfigTests: XCTestCase {
    private func data(_ json: String) -> Data {
        Data(json.utf8)
    }

    private var decoder: JSONDecoder { JSONDecoder() }

    // MARK: - MarketStrip surface fields

    func testMarketStripDecodesStripMetricAndSurfaceConfig() throws {
        let json = """
        {
          "date": "20260728",
          "etfs": [{"code":"563360.SH","name":"A500ETF","close":1.3,"pct":1.2}],
          "limitBoard": {"maxBoard": 6, "sealRate": 0.55, "total": 61},
          "stripMetric": {
            "metric_id": "limit_max_board",
            "title": "最高连板",
            "value": 6,
            "valueText": "6 板",
            "delta": 6,
            "deltaText": "最高连板",
            "sub": "涨停情绪",
            "reason": null
          },
          "surfaceConfig": {
            "overnightAppend": [
              {
                "code": "AAPL",
                "name": "苹果",
                "kind": "yfinance",
                "kind_source": "candidate_table",
                "probe_close": 190.5
              }
            ],
            "stripMetricId": "limit_max_board",
            "degraded": false,
            "error": null
          },
          "overnightUS": [
            {
              "code": "NVDA",
              "name": "英伟达",
              "close": 100.0,
              "pct": 1.5,
              "date": "20260728",
              "isUserAppended": false
            },
            {
              "code": "AAPL",
              "name": "苹果",
              "close": 190.5,
              "pct": 0.8,
              "date": "20260728",
              "isUserAppended": true,
              "kindSource": "candidate_table",
              "probeClose": 190.5
            }
          ]
        }
        """
        let strip = try decoder.decode(MarketStrip.self, from: data(json))
        XCTAssertEqual(strip.stripMetric?.metricId, "limit_max_board")
        XCTAssertEqual(strip.stripMetric?.title, "最高连板")
        XCTAssertEqual(strip.stripMetric?.value, 6)
        XCTAssertEqual(strip.stripMetric?.valueText, "6 板")
        XCTAssertEqual(strip.limitBoard?.maxBoard, 6)
        XCTAssertEqual(strip.surfaceConfig?.stripMetricId, "limit_max_board")
        XCTAssertEqual(strip.surfaceConfig?.overnightAppend?.count, 1)
        XCTAssertEqual(strip.surfaceConfig?.overnightAppend?.first?.code, "AAPL")
        XCTAssertEqual(strip.surfaceConfig?.overnightAppend?.first?.kindSource, "candidate_table")
        XCTAssertEqual(strip.surfaceConfig?.overnightAppend?.first?.probeClose, 190.5)

        let user = strip.overnightUS?.first { $0.code == "AAPL" }
        XCTAssertEqual(user?.isUserAppended, true)
        XCTAssertEqual(user?.probeClose, 190.5)
        let system = strip.overnightUS?.first { $0.code == "NVDA" }
        XCTAssertEqual(system?.isUserAppended, false)
    }

    func testMarketStripMissingSurfaceFieldsStillDecodes() throws {
        let json = """
        {
          "date": "20260728",
          "etfs": [],
          "overnightUS": [
            {"code":"IXIC","name":"纳斯达克","close":25000,"pct":-0.5}
          ]
        }
        """
        let strip = try decoder.decode(MarketStrip.self, from: data(json))
        XCTAssertNil(strip.stripMetric)
        XCTAssertNil(strip.surfaceConfig)
        XCTAssertEqual(strip.overnightUS?.count, 1)
        XCTAssertNil(strip.overnightUS?.first?.isUserAppended)
        XCTAssertNil(strip.overnightUS?.first?.pending)
    }

    func testIndexQuotePendingDecodes() throws {
        let json = """
        {
          "code": "AMD",
          "name": "超威",
          "close": 0,
          "pct": 0,
          "pending": true,
          "isUserAppended": true
        }
        """
        let q = try decoder.decode(IndexQuote.self, from: data(json))
        XCTAssertEqual(q.pending, true)
        XCTAssertEqual(q.isUserAppended, true)
        XCTAssertEqual(q.code, "AMD")
    }

    // MARK: - Surface API payloads

    func testSurfaceGetResponseDecodes() throws {
        let json = """
        {
          "ok": true,
          "config": {
            "overnight_us": {
              "append": [{"code":"AAPL","name":"苹果","kind":"yfinance","kind_source":"plus"}]
            },
            "strip_metric": {"metric_id": "limit_seal_rate"}
          },
          "candidates": [
            {"code":"AAPL","name":"苹果","kind":"yfinance"},
            {"code":"NVDA","name":"英伟达","kind":"yfinance"}
          ],
          "metrics": [
            {"metric_id":"limit_max_board","title":"最高连板","description":"连板高度"}
          ],
          "stripMetric": {
            "metric_id": "limit_seal_rate",
            "title": "封板率",
            "value": 55.0,
            "valueText": "55.0%",
            "deltaText": "封板率",
            "sub": "涨停情绪"
          }
        }
        """
        let resp = try decoder.decode(SurfaceGetResponse.self, from: data(json))
        XCTAssertEqual(resp.ok, true)
        XCTAssertEqual(resp.config?.stripMetric?.metricId, "limit_seal_rate")
        XCTAssertEqual(resp.config?.overnightUs?.append?.first?.code, "AAPL")
        XCTAssertEqual(resp.candidates?.count, 2)
        XCTAssertEqual(resp.metrics?.first?.metricId, "limit_max_board")
        XCTAssertEqual(resp.stripMetric?.valueText, "55.0%")
    }

    func testSurfaceApplyFailureDecodes() throws {
        let json = """
        {"ok": false, "error": "第一行已固定展示北向资金"}
        """
        let resp = try decoder.decode(SurfaceApplyResponse.self, from: data(json))
        XCTAssertEqual(resp.ok, false)
        XCTAssertTrue(resp.error?.contains("北向") == true)
    }

    func testSurfaceNlInterpretOvernightDecodes() throws {
        let json = """
        {
          "ok": true,
          "region": "overnight_us",
          "action": "overnight_append",
          "partial": false,
          "ops": [{
            "op": "overnight_append",
            "code": "AAPL",
            "name": "苹果",
            "kind": "yfinance",
            "kind_source": "candidate_table",
            "added_via": "nl",
            "probe_close": 190.0
          }],
          "previews": [{
            "op": "overnight_append",
            "code": "AAPL",
            "name": "苹果",
            "close": 190.0,
            "pct": 1.0,
            "label": "追加 苹果 (AAPL)"
          }],
          "items": [{"status":"ok","token":"苹果","code":"AAPL","name":"苹果","close":190.0,"pct":1.0}],
          "failed": [],
          "error": null,
          "error_zh": null,
          "suggestions": []
        }
        """
        let resp = try decoder.decode(SurfaceNlInterpretResponse.self, from: data(json))
        XCTAssertEqual(resp.ok, true)
        XCTAssertEqual(resp.ops?.first?.code, "AAPL")
        XCTAssertEqual(resp.ops?.first?.probeClose, 190.0)
        XCTAssertEqual(resp.previews?.first?.label, "追加 苹果 (AAPL)")
        XCTAssertEqual(resp.previews?.first?.close, 190.0)
        let encoded = SurfaceBindEncoding.encodeOps(resp.ops ?? [])
        XCTAssertNotNil(encoded)
        XCTAssertTrue(encoded?.contains("overnight_append") == true)
        XCTAssertTrue(encoded?.contains("AAPL") == true)
    }

    func testSurfaceNlInterpretMetricDecodes() throws {
        let json = """
        {
          "ok": true,
          "region": "strip_metric",
          "action": "set_strip_metric",
          "metric_id": "limit_seal_rate",
          "ops": [{"op":"set_strip_metric","metric_id":"limit_seal_rate"}],
          "previews": [{
            "op": "set_strip_metric",
            "metric_id": "limit_seal_rate",
            "title": "封板率",
            "valueText": "55.0%",
            "label": "切换为 封板率（55.0%）"
          }],
          "stripMetric": {
            "metric_id": "limit_seal_rate",
            "title": "封板率",
            "value": 55.0,
            "valueText": "55.0%"
          }
        }
        """
        let resp = try decoder.decode(SurfaceNlInterpretResponse.self, from: data(json))
        XCTAssertEqual(resp.metricId, "limit_seal_rate")
        XCTAssertEqual(resp.previews?.first?.valueText, "55.0%")
        XCTAssertEqual(resp.stripMetric?.title, "封板率")
    }

    func testSurfaceNlInterpretNorthFailsWithSuggestions() throws {
        let json = """
        {
          "ok": false,
          "region": "strip_metric",
          "error": "north_forbidden",
          "error_zh": "第一行已固定展示北向资金，小卡不能再绑北向类指标",
          "suggestions": ["最高连板", "封板率", "科创50", "创业板指"],
          "ops": [],
          "previews": []
        }
        """
        let resp = try decoder.decode(SurfaceNlInterpretResponse.self, from: data(json))
        XCTAssertEqual(resp.ok, false)
        XCTAssertEqual(resp.error, "north_forbidden")
        XCTAssertTrue(resp.errorZh?.contains("北向") == true)
        XCTAssertEqual(resp.suggestions?.count, 4)
    }

    func testStripMetricEmptyReason() throws {
        let json = """
        {
          "metric_id": "limit_max_board",
          "title": "最高连板",
          "value": null,
          "valueText": "—",
          "reason": "no_limit_board"
        }
        """
        let props = try decoder.decode(StripMetricProps.self, from: data(json))
        XCTAssertNil(props.value)
        XCTAssertEqual(props.valueText, "—")
        XCTAssertEqual(props.reason, "no_limit_board")
    }

    /// 合并 pending 追加项的纯逻辑（与 OvernightUSSection.displayOvernight 同语义）。
    func testMergePendingAppendsAfterQuoted() {
        let quoted = [
            IndexQuote(code: "IXIC", name: "纳指", close: 1, pct: 0.1),
            IndexQuote(code: "AAPL", name: "苹果", close: 190, pct: 1, isUserAppended: true),
        ]
        let append = [
            SurfaceAppendItem(code: "AAPL", name: "苹果", kind: "yfinance"),
            SurfaceAppendItem(code: "AMD", name: "超威", kind: "yfinance", probeClose: 120),
        ]
        let merged = Self.mergeDisplay(overnight: quoted, append: append)
        XCTAssertEqual(merged.map(\.code), ["IXIC", "AAPL", "AMD"])
        XCTAssertEqual(merged.first { $0.code == "AMD" }?.pending, true)
        XCTAssertEqual(merged.first { $0.code == "AAPL" }?.pending, nil)
    }

    private static func mergeDisplay(
        overnight: [IndexQuote],
        append: [SurfaceAppendItem]
    ) -> [IndexQuote] {
        var list = overnight
        let have = Set(list.map { $0.code.uppercased() })
        for item in append {
            let code = item.code.uppercased()
            if have.contains(code) { continue }
            list.append(IndexQuote(
                code: code,
                name: item.name ?? code,
                close: item.probeClose ?? 0,
                pct: 0,
                isUserAppended: true,
                pending: true,
                kindSource: item.kindSource,
                probeClose: item.probeClose
            ))
        }
        return list
    }
}
