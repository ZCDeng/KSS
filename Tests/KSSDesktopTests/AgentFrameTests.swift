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
          "model": "gpt-test",
          "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100, "cached_input_tokens": 12},
          "context_usage": {"used": 1200, "limit": 4000, "percent": 30, "estimated": true},
          "existing_run_id": "r0",
          "is_error": false,
          "termination_reason": "stop",
          "memory_candidate": {"id": "m1", "text": "remember this", "source": "chat"},
          "evidence_summary": {"kssTruthCount": 1, "externalSourceCount": 0, "injectionWarningCount": 0, "conflictCount": 0},
          "future_field": {"must": "be ignored"}
        }
        """.utf8)

        let frame = try JSONDecoder().decode(AgentFrame.self, from: data)
        XCTAssertEqual(frame.protocolVersion, 1)
        XCTAssertEqual(frame.sessionId, "s1")
        XCTAssertEqual(frame.runId, "r1")
        XCTAssertEqual(frame.sequence, 4)
        XCTAssertEqual(frame.type, "message_delta")
        XCTAssertEqual(frame.delta, "hello")
        XCTAssertEqual(frame.model, "gpt-test")
        XCTAssertEqual(frame.usage?.inputTokens, 80)
        XCTAssertEqual(frame.usage?.outputTokens, 20)
        XCTAssertEqual(frame.usage?.totalTokens, 100)
        XCTAssertEqual(frame.usage?.cachedInputTokens, 12)
        XCTAssertEqual(frame.contextUsage?.percent, 30)
        XCTAssertEqual(frame.contextUsage?.estimated, true)
        XCTAssertEqual(frame.existingRunId, "r0")
        XCTAssertEqual(frame.isError, false)
        XCTAssertEqual(frame.terminationReason, "stop")
        XCTAssertEqual(frame.memoryCandidate?.id, "m1")
        XCTAssertEqual(frame.evidenceSummary?.kssTruthCount, 1)
    }

    func testAgentUsageDecodesOpenAIAliases() throws {
        let frame = try decodeFrame("""
        {"type":"agent_end","usage":{
          "prompt_tokens":90,
          "completion_tokens":10,
          "total_tokens":100,
          "cache_read_tokens":25,
          "reasoning_tokens":4
        }}
        """)

        XCTAssertEqual(frame.usage?.inputTokens, 90)
        XCTAssertEqual(frame.usage?.outputTokens, 10)
        XCTAssertEqual(frame.usage?.totalTokens, 100)
        XCTAssertEqual(frame.usage?.cachedInputTokens, 25)
        XCTAssertEqual(frame.usage?.reasoningTokens, 4)
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

    func testDuplicateRunFramesUpdateStateHydrateAndNeverFallbackToLegacy() throws {
        let store = KSSStore(testBridge: nil)
        store.agentSessions = [
            AgentSession(sessionId: "s1", title: "A", messages: [
                AgentHydratedMessage(id: "old", role: "assistant", text: "旧内容"),
            ])
        ]
        store.openAgentSession("s1")
        store.chatMessages = [
            ChatMessage(role: .user, text: "重复输入"),
            ChatMessage(role: .assistant, text: "", numbersUnverified: true),
        ]

        let frame = try decodeFrame("""
        {"protocol_version":1,"session_id":"s1","run_id":"attempt-2","sequence":1,
         "type":"agent_end","reason":"already_running","existing_run_id":"run-1",
         "model":"gpt-test","usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10},
         "is_error":false}
        """)
        XCTAssertTrue(store.applyAgentFrame(frame))
        XCTAssertEqual(store.agentExistingRunId, "run-1")
        XCTAssertEqual(store.agentTerminationReason, "already_running")
        XCTAssertEqual(store.agentModel, "gpt-test")
        XCTAssertEqual(store.agentUsage?.totalTokens, 10)
        XCTAssertEqual(store.agentLastEventIsError, false)
        XCTAssertEqual(KSSStore.duplicateAgentReason(for: frame), "already_running")
        XCTAssertTrue(BridgeClient.isAgentDuplicateTerminal(frame))
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "already_running",
            terminationReason: "already_running",
            userAborted: false,
            assistantEmpty: true,
            assistantIsError: false))
        let duplicateCompleted = try decodeFrame(
            #"{"type":"duplicate_completed","existing_run_id":"run-0"}"#)
        XCTAssertEqual(KSSStore.duplicateAgentReason(for: duplicateCompleted), "duplicate_completed")
        XCTAssertTrue(BridgeClient.isAgentDuplicateTerminal(duplicateCompleted))
        let confirm = try decodeFrame(#"{"type":"confirm_required","call_id":"c1"}"#)
        XCTAssertFalse(BridgeClient.isAgentDuplicateTerminal(confirm))

        let response = AgentSessionListResponse(
            sessions: [
                AgentSession(sessionId: "s1", title: "A", messages: [
                    AgentHydratedMessage(id: "u1", role: "user", text: "原问题"),
                    AgentHydratedMessage(id: "a1", role: "assistant", text: "已完成答案"),
                ])
            ],
            selectedSessionId: "s1")
        XCTAssertTrue(store.applyAgentSessionHydration(
            response,
            sessionId: "s1",
            triggeringRunId: "attempt-2"))
        XCTAssertEqual(store.chatMessages.map(\.text), ["原问题", "已完成答案"])
    }

    func testAgentStreamWaitsForAgentEndAfterTurnEnd() throws {
        let turnEnd = try JSONDecoder().decode(
            AgentFrame.self,
            from: Data(#"{"protocol_version":1,"type":"turn_end","session_id":"s","run_id":"r","sequence":1}"#.utf8)
        )
        let agentEnd = try JSONDecoder().decode(
            AgentFrame.self,
            from: Data(#"{"protocol_version":1,"type":"agent_end","session_id":"s","run_id":"r","sequence":2}"#.utf8)
        )

        XCTAssertFalse(BridgeClient.isAgentStreamTerminal(turnEnd))
        XCTAssertTrue(BridgeClient.isAgentStreamTerminal(agentEnd))
    }

    func testDuplicateHydrationCannotOverwriteAnotherSelectedSession() {
        let store = KSSStore(testBridge: nil)
        store.agentSessions = [
            AgentSession(sessionId: "s2", title: "B", messages: [
                AgentHydratedMessage(id: "b1", role: "assistant", text: "会话 B"),
            ])
        ]
        store.openAgentSession("s2")

        let response = AgentSessionListResponse(
            sessions: [
                AgentSession(sessionId: "s1", title: "A", messages: [
                    AgentHydratedMessage(id: "a1", role: "assistant", text: "会话 A"),
                ])
            ],
            selectedSessionId: "s1")
        XCTAssertFalse(store.applyAgentSessionHydration(
            response,
            sessionId: "s1",
            triggeringRunId: "attempt-1"))
        XCTAssertEqual(store.selectedAgentSessionId, "s2")
        XCTAssertEqual(store.chatMessages.map(\.text), ["会话 B"])
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
