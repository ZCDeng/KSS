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
        let duplicateId = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g1","event_id":"e1","sequence":2,"timestamp":"now","type":"task_started"}"#)
        let gap = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g1","event_id":"e3","sequence":3,"timestamp":"later","type":"evidence_added"}"#)

        let store = KSSStore(testBridge: nil)
        XCTAssertTrue(store.applyResearchEvent(first))
        XCTAssertFalse(store.applyResearchEvent(duplicate))
        XCTAssertFalse(store.applyResearchEvent(duplicateId))
        XCTAssertTrue(store.applyResearchEvent(gap))
        XCTAssertEqual(store.researchEventsByGoal["g1"]?.map(\.sequence), [1, 3])
        XCTAssertEqual(store.researchSequenceIssues["g1"], "研究事件丢帧：预期 2，收到 3")
    }

    func testResearchSnapshotHydratesBeforeReplayAndTaskEventsReduceState() throws {
        let snapshot = try decodeEvent(#"""
        {
          "protocol_version": 1,
          "goal_id": "g-live",
          "event_id": "snapshot:g-live:0",
          "sequence": 0,
          "timestamp": "now",
          "type": "research_snapshot",
          "snapshot": {
            "goal_id": "g-live",
            "profile_id": "investment-weekly-v3",
            "objective": "实时归约",
            "status": "running",
            "tasks": [
              {"task_id": "t-live", "title": "采集", "status": "running"},
              {"task_id": "t-next", "title": "编译", "status": "pending"}
            ]
          }
        }
        """#)
        let ended = try decodeEvent(
            #"{"protocol_version":1,"goal_id":"g-live","event_id":"e1","sequence":1,"timestamp":"later","type":"task_end","task_id":"t-live","status":"succeeded"}"#)

        let store = KSSStore(testBridge: nil)
        XCTAssertTrue(store.applyResearchEvent(snapshot))
        XCTAssertEqual(store.selectedResearchGoal?.status, "running")
        XCTAssertEqual(store.researchEventsByGoal["g-live"], nil)

        XCTAssertTrue(store.applyResearchEvent(ended))
        XCTAssertEqual(store.selectedResearchGoal?.tasks.first?.status, "succeeded")
        XCTAssertEqual(store.selectedResearchGoal?.progress, 0.5)
        XCTAssertEqual(store.researchGoals.first?.progress, 0.5)
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

    func testDefaultWeeklyInputsFreezeASevenDayWindowAndAsOf() throws {
        var components = DateComponents()
        components.calendar = Calendar(identifier: .gregorian)
        components.timeZone = TimeZone(secondsFromGMT: 0)
        components.year = 2026
        components.month = 7
        components.day = 26
        let reference = try XCTUnwrap(components.date)

        let inputs = KSSStore.defaultResearchInputs(referenceDate: reference)

        XCTAssertEqual(inputs["date_range"], "2026-07-20_to_2026-07-26")
        XCTAssertEqual(inputs["as_of"], "2026-07-26")
    }

    private func decodeEvent(_ json: String) throws -> ResearchEvent {
        try JSONDecoder().decode(ResearchEvent.self, from: Data(json.utf8))
    }
}
