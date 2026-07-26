import XCTest
@testable import KSSDesktop

@MainActor
final class ResearchProtocolTests: XCTestCase {
    func testResearchResponseDecodesTolerantly() throws {
        let data = Data(#"""
        {
          "protocol_version": 1,
          "goals": [{
            "goal_id": "goal-1",
            "session_id": "session-1",
            "profile_id": "deep",
            "objective": "研究储能产业链",
            "status": "running",
            "progress": 0.35,
            "created_at": "2026-07-26T09:00:00Z",
            "updated_at": "2026-07-26T09:10:00Z",
            "future": {"ignored": true}
          }],
          "goal": {
            "goal_id": "goal-1",
            "profile_id": "deep",
            "objective": "研究储能产业链",
            "status": "running",
            "criteria": [{"criterion_id": "c1", "title": "覆盖主要公司", "status": "met"}],
            "tasks": [{"task_id": "t1", "title": "检索财报", "status": "completed"}],
            "evidence": [{"evidence_id": "e1", "title": "公司公告", "source": "SSE", "url": "https://example.com"}],
            "audit": [{"event_id": "a1", "type": "goal_created", "timestamp": "2026-07-26T09:00:00Z"}],
            "artifacts": [{
              "artifact_id": "art-1",
              "kind": "report",
              "logical_name": "research.md",
              "media_type": "text/markdown",
              "size_bytes": 128,
              "sha256": "abc",
              "relative_path": "drafts/research.md",
              "created_at": "2026-07-26T09:12:00Z",
              "draft": true
            }]
          }
        }
        """#.utf8)

        let response = try JSONDecoder().decode(ResearchResponse.self, from: data)
        XCTAssertEqual(response.goals.first?.goalId, "goal-1")
        XCTAssertEqual(response.goals.first?.progress, 0.35)
        XCTAssertEqual(response.goal?.criteria.first?.id, "c1")
        XCTAssertEqual(response.goal?.tasks.first?.id, "t1")
        XCTAssertEqual(response.goal?.evidence.first?.source, "SSE")
        XCTAssertEqual(response.goal?.artifacts.first?.logicalName, "research.md")
        XCTAssertEqual(response.goal?.artifacts.first?.isDraft, true)
    }

    func testResearchDetailDefaultsMissingCollectionsToEmpty() throws {
        let data = Data(#"""
        {"goal":{"goal_id":"g","profile_id":"default","objective":"目标","status":"created"}}
        """#.utf8)
        let response = try JSONDecoder().decode(ResearchResponse.self, from: data)
        XCTAssertEqual(response.goals, [])
        XCTAssertEqual(response.goal?.criteria, [])
        XCTAssertEqual(response.goal?.tasks, [])
        XCTAssertEqual(response.goal?.evidence, [])
        XCTAssertEqual(response.goal?.audit, [])
        XCTAssertEqual(response.goal?.artifacts, [])
    }

    func testResearchResponseAcceptsCurrentSidecarDetailAndSnapshot() throws {
        let data = Data(#"""
        {
          "goal": "goal-2",
          "detail": {
            "goal_id": "goal-2",
            "profile_id": "investment-weekly-v3",
            "objective": "生成投资周报",
            "status": "draft",
            "snapshot": {
              "snapshot_id": "snap-1",
              "as_of": "2026-07-17",
              "created_at": "2026-07-26T10:00:00Z"
            },
            "events": [{
              "protocol_version": 1,
              "goal_id": "goal-2",
              "event_id": "evt-1",
              "sequence": 1,
              "timestamp": "2026-07-26T10:00:00Z",
              "type": "goal_created",
              "status": "draft"
            }]
          }
        }
        """#.utf8)
        let response = try JSONDecoder().decode(ResearchResponse.self, from: data)
        XCTAssertEqual(response.goal?.goalId, "goal-2")
        XCTAssertEqual(response.goal?.snapshot?.asOf, "2026-07-17")
        XCTAssertEqual(response.goal?.events.first?.sequence, 1)
    }

    func testResearchEventReducerDeduplicatesAndReportsGap() throws {
        let first = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g1","event_id":"e1","sequence":1,"timestamp":"now","type":"task_started","task_id":"t1","status":"running"}"#)
        let duplicate = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g1","event_id":"e1b","sequence":1,"timestamp":"now","type":"task_started"}"#)
        let gap = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g1","event_id":"e3","sequence":3,"timestamp":"later","type":"evidence_added"}"#)

        let store = KSSStore(testBridge: nil)
        XCTAssertTrue(store.applyResearchEvent(first))
        XCTAssertFalse(store.applyResearchEvent(duplicate))
        XCTAssertTrue(store.applyResearchEvent(gap))
        XCTAssertEqual(store.researchEventsByGoal["g1"]?.map(\.sequence), [1, 3])
        XCTAssertEqual(store.researchSequenceIssues["g1"], "研究事件丢帧：预期 2，收到 3")
    }

    func testAgentFrameDecodesResearchCandidateWithoutStartingGoal() throws {
        let data = Data(#"""
        {
          "type": "research_candidate",
          "research_candidate": {
            "objective": "深入研究 RSI 阈值",
            "profile_id": "default",
            "session_id": "s1"
          }
        }
        """#.utf8)
        let frame = try JSONDecoder().decode(AgentFrame.self, from: data)
        XCTAssertEqual(frame.researchCandidate?.objective, "深入研究 RSI 阈值")

        let store = KSSStore(testBridge: nil)
        XCTAssertTrue(store.applyAgentFrame(frame))
        XCTAssertEqual(store.researchCandidate?.objective, "深入研究 RSI 阈值")
        XCTAssertTrue(store.researchGoals.isEmpty)
    }

    func testArtifactNavigationPolicyBlocksExternalAndFileURLs() {
        XCTAssertTrue(ResearchArtifactNavigationPolicy.allows(URL(string: "about:blank")!))
        XCTAssertFalse(ResearchArtifactNavigationPolicy.allows(URL(string: "https://example.com")!))
        XCTAssertFalse(ResearchArtifactNavigationPolicy.allows(URL(fileURLWithPath: "/tmp/private.html")))
        XCTAssertFalse(ResearchArtifactNavigationPolicy.allows(URL(string: "data:text/html,hello")!))
    }

    func testArtifactPreviewLoaderRejectsTraversalAndReadsBoundedText() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kss-research-preview-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let report = root.appendingPathComponent("report.md")
        try "safe preview".write(to: report, atomically: true, encoding: .utf8)

        XCTAssertEqual(
            ResearchArtifactPreviewLoader.load(relativePath: "report.md", under: root),
            "safe preview")
        XCTAssertNil(
            ResearchArtifactPreviewLoader.load(relativePath: "../secret.md", under: root))
        XCTAssertNil(
            ResearchArtifactPreviewLoader.load(relativePath: "script.js", under: root))
    }

    private func decodeEvent(_ json: String) throws -> ResearchEvent {
        try JSONDecoder().decode(ResearchEvent.self, from: Data(json.utf8))
    }
}
