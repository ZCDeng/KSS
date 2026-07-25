import XCTest
@testable import KSSDesktop

@MainActor
final class AgentFrameTests: XCTestCase {
    func testAgentFrameDecodesV1SnakeCasePayload() throws {
        let data = Data("""
        {
          "protocol_version": 1,
          "session_id": "s1",
          "run_id": "r1",
          "sequence": 4,
          "type": "message_delta",
          "delta": "hello",
          "context_usage": {"used": 1200, "limit": 4000, "percent": 30},
          "memory_candidate": {"id": "m1", "text": "remember this", "source": "chat"},
          "evidence_summary": {"kssTruthCount": 1, "externalSourceCount": 0, "injectionWarningCount": 0, "conflictCount": 0}
        }
        """.utf8)

        let frame = try JSONDecoder().decode(AgentFrame.self, from: data)
        XCTAssertEqual(frame.protocolVersion, 1)
        XCTAssertEqual(frame.sessionId, "s1")
        XCTAssertEqual(frame.runId, "r1")
        XCTAssertEqual(frame.sequence, 4)
        XCTAssertEqual(frame.type, "message_delta")
        XCTAssertEqual(frame.delta, "hello")
        XCTAssertEqual(frame.contextUsage?.percent, 30)
        XCTAssertEqual(frame.memoryCandidate?.id, "m1")
        XCTAssertEqual(frame.evidenceSummary?.kssTruthCount, 1)
    }

    func testAgentFrameDeduplicatesSequencesAndReportsGaps() throws {
        let store = KSSStore(testBridge: nil)
        store.chatMessages = [ChatMessage(role: .assistant, text: "", numbersUnverified: true)]
        let assistantId = store.chatMessages[0].id

        let first = try decodeFrame(#"{"type":"message_delta","run_id":"r1","sequence":1,"delta":"A"}"#)
        let duplicate = try decodeFrame(#"{"type":"message_delta","run_id":"r1","sequence":1,"delta":"B"}"#)
        let gap = try decodeFrame(#"{"type":"message_delta","run_id":"r1","sequence":3,"delta":"C"}"#)

        XCTAssertTrue(store.applyAgentFrame(first, assistantId: assistantId))
        XCTAssertFalse(store.applyAgentFrame(duplicate, assistantId: assistantId))
        XCTAssertTrue(store.applyAgentFrame(gap, assistantId: assistantId))
        XCTAssertEqual(store.chatMessages[0].text, "AC")
        XCTAssertEqual(store.agentSequenceIssue, "Agent frame gap: expected 2, got 3")
    }

    func testAgentSessionHydratesCachedMessagesWhenOpened() {
        let store = KSSStore(testBridge: nil)
        store.agentSessions = [
            AgentSession(sessionId: "s1", title: "A", messages: [
                AgentHydratedMessage(id: "u1", role: "user", text: "问"),
                AgentHydratedMessage(
                    id: "a1",
                    role: "assistant",
                    text: "答",
                    evidenceSummary: ChatEvidenceSummary(kssTruthCount: 1, externalSourceCount: 0, injectionWarningCount: 0, conflictCount: 0, provider: nil),
                    evidenceDrawer: nil
                ),
                AgentHydratedMessage(
                    id: "a-tool",
                    role: "assistant",
                    text: "",
                    toolCalls: [AgentHydratedToolCall(id: "tc1", name: "get_snapshot")]
                ),
                AgentHydratedMessage(id: "t1", role: "tool", text: #"{"price":1}"#),
            ])
        ]

        store.openAgentSession("s1")
        XCTAssertEqual(store.chatMessages.map(\.text), ["问", "答"])
        XCTAssertEqual(store.chatMessages.last?.evidenceSummary.kssTruthCount, 1)
    }

    func testAbortNeverFallsBackToLegacyChat() {
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "client_abort", userAborted: true, assistantEmpty: true, assistantIsError: false))
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "aborted", userAborted: false, assistantEmpty: true, assistantIsError: false))
        XCTAssertTrue(KSSStore.shouldFallbackToLegacyAgent(
            error: "Agent 连接中断", userAborted: false, assistantEmpty: true, assistantIsError: false))
    }

    func testAgentCommandResponsesDecodeUniformWireShape() throws {
        let sessionData = Data("""
        {"sessions":[{"session_id":"s1","title":"A","archived":false,"updated_at":"1",
        "messages":[{"id":"u1","role":"user","content":"从 content 恢复"}]}],
        "selected_session_id":"s1"}
        """.utf8)
        let sessions = try JSONDecoder().decode(AgentSessionListResponse.self, from: sessionData)
        XCTAssertEqual(sessions.selectedSessionId, "s1")
        XCTAssertEqual(sessions.sessions[0].messages?[0].text, "从 content 恢复")

        let skillData = Data("""
        {"skills":[{"id":"demo","name":"demo","description":"D","enabled":true,"pinned":true}],
        "diagnostics":[{"code":"duplicate","message":"重复技能","path":"/tmp/SKILL.md"}]}
        """.utf8)
        let skills = try JSONDecoder().decode(AgentSkillsResponse.self, from: skillData)
        XCTAssertEqual(skills.skills[0].enabled, true)
        XCTAssertEqual(skills.skills[0].pinned, true)
        XCTAssertEqual(skills.diagnostics?.first?.code, "duplicate")

        let memoryData = Data("""
        {"memories":[{"id":"m1","text":"偏好 A","source":"s1","archived":false}],
        "candidates":[{"id":"m2","text":"候选 B","source":"s1","status":"proposed"}],
        "recalls":[]}
        """.utf8)
        let memories = try JSONDecoder().decode(AgentMemoriesResponse.self, from: memoryData)
        XCTAssertEqual(memories.memories[0].text, "偏好 A")
        XCTAssertEqual(memories.candidates?[0].status, "proposed")
    }

    private func decodeFrame(_ json: String) throws -> AgentFrame {
        try JSONDecoder().decode(AgentFrame.self, from: Data(json.utf8))
    }
}
