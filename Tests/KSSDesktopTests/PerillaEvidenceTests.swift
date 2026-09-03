import XCTest
@testable import KSSDesktop

final class PerillaEvidenceTests: XCTestCase {
    func testPerillaPickDecodesPointInTimeEvidenceKeys() throws {
        let data = Data(#"""
        {
          "symbol": "688120.SH",
          "name": "华海清科",
          "chains": "半导体",
          "layer": 4,
          "role": "equipment",
          "moat": "全球2家国内独家",
          "locked": true,
          "tier": "core",
          "score": 0.742,
          "assessmentStatus": "needs_review",
          "evidenceHistory": [{
            "as_of": "2026-06-30",
            "published_at": "2026-08-22",
            "retrieved_at": "2026-09-03T16:38:02+08:00",
            "source_kind": "official_periodic_report",
            "source_url": "https://example.com/report.pdf",
            "verdict": "support",
            "future_field": "ignored"
          }]
        }
        """#.utf8)

        let pick = try JSONDecoder().decode(PerillaPick.self, from: data)

        XCTAssertEqual(pick.evidenceHistory?.first?.asOf, "2026-06-30")
        XCTAssertEqual(pick.evidenceHistory?.first?.publishedAt, "2026-08-22")
        XCTAssertEqual(pick.evidenceHistory?.first?.sourceKind, "official_periodic_report")
        XCTAssertEqual(pick.evidenceHistory?.first?.verdict, "support")
    }
}
