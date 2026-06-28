import XCTest
@testable import KSSDesktop

final class ChatEvidenceTests: XCTestCase {
    func testChatFrameDecodesEvidencePayload() throws {
        let data = Data("""
        {
          "type": "tool_done",
          "name": "research_bundle",
          "evidenceSummary": {
            "kssTruthCount": 0,
            "externalSourceCount": 2,
            "injectionWarningCount": 1,
            "conflictCount": 0,
            "provider": "fixture"
          },
          "evidenceDrawer": {
            "kssTruth": [],
            "externalSources": [
              {
                "title": "Policy A",
                "url": "https://example.com/a",
                "sourceTier": "official_or_primary",
                "retrievedAt": "2026-06-22T00:00:00+08:00",
                "cacheStatus": "cached",
                "excerpt": "A",
                "usedFor": "external_background_only"
              }
            ],
            "warnings": [
              {"type": "prompt_injection", "severity": "danger", "message": "blocked"},
              {"type": "kss_web_conflict", "severity": "warning", "message": "KSS local truth wins"}
            ]
          }
        }
        """.utf8)

        let frame = try JSONDecoder().decode(ChatFrame.self, from: data)
        XCTAssertEqual(frame.type, "tool_done")
        XCTAssertEqual(frame.evidenceSummary?.externalSourceCount, 2)
        XCTAssertEqual(frame.evidenceSummary?.provider, "fixture")
        XCTAssertEqual(frame.evidenceDrawer?.externalSources.first?.sourceTier, "official_or_primary")
        XCTAssertEqual(frame.evidenceDrawer?.warnings.first?.type, "prompt_injection")
        XCTAssertEqual(frame.evidenceDrawer?.warnings.last?.type, "kss_web_conflict")
    }

    func testEvidenceSummaryAndDrawerMerge() {
        var summary = ChatEvidenceSummary(kssTruthCount: 1, externalSourceCount: 0, injectionWarningCount: 0, conflictCount: 0, provider: nil)
        summary.merge(ChatEvidenceSummary(kssTruthCount: 0, externalSourceCount: 2, injectionWarningCount: 1, conflictCount: 1, provider: "fixture"))
        XCTAssertEqual(summary.kssTruthCount, 1)
        XCTAssertEqual(summary.externalSourceCount, 2)
        XCTAssertEqual(summary.injectionWarningCount, 1)
        XCTAssertEqual(summary.conflictCount, 1)
        XCTAssertEqual(summary.provider, "fixture")
        XCTAssertTrue(summary.hasEvidence)

        var drawer = ChatEvidenceDrawer(
            kssTruth: [ChatKSSTruthEvidence(label: "get_stock", tool: "get_stock", fields: ["pctChange"], provenance: "kss_tool_truth")],
            externalSources: [],
            warnings: []
        )
        drawer.merge(ChatEvidenceDrawer(
            kssTruth: [],
            externalSources: [ChatExternalSourceEvidence(title: "A", url: "https://example.com/a", sourceTier: "official_or_primary", retrievedAt: "now", cacheStatus: "cached", excerpt: "A", usedFor: "external_background_only")],
            warnings: [ChatEvidenceWarning(type: "provider_unavailable", severity: "info", message: "disabled")]
        ))
        XCTAssertEqual(drawer.kssTruth.count, 1)
        XCTAssertEqual(drawer.externalSources.count, 1)
        XCTAssertEqual(drawer.warnings.count, 1)
    }
}
