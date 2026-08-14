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

    func testThinkingFramesDecodeAndReduceWithoutTouchingVisibleText() throws {
        let store = KSSStore(testBridge: nil)
        store.chatMessages = [
            ChatMessage(role: .assistant, text: "", numbersUnverified: true),
        ]
        let assistantId = try XCTUnwrap(store.chatMessages.first?.id)

        for json in [
            #"{"type":"thinking_start","run_id":"r1","sequence":1,"content_index":0,"provider":"anthropic","model":"claude-test"}"#,
            #"{"type":"thinking_delta","run_id":"r1","sequence":2,"content_index":0,"delta":"检查证据"}"#,
            #"{"type":"thinking_delta","run_id":"r1","sequence":3,"content_index":0,"delta":"与数字"}"#,
            #"{"type":"thinking_end","run_id":"r1","sequence":4,"content_index":0,"signature":"opaque-signature"}"#,
        ] {
            XCTAssertTrue(store.applyAgentFrame(try decodeFrame(json), assistantId: assistantId))
        }

        XCTAssertEqual(store.chatMessages[0].text, "")
        XCTAssertEqual(store.chatMessages[0].thinkingBlocks.count, 1)
        XCTAssertEqual(store.chatMessages[0].thinkingBlocks[0].text, "检查证据与数字")
        XCTAssertEqual(store.chatMessages[0].thinkingBlocks[0].signature, "opaque-signature")
        XCTAssertEqual(store.agentProvider, "anthropic")
        XCTAssertEqual(store.agentModel, "claude-test")
    }

    func testFrameDecodesProviderRouteContentBlocksAndAttachments() throws {
        let frame = try decodeFrame("""
        {"type":"message_end","provider":"openai","model":"gpt-test","content_index":2,
         "provider_route":{"provider_id":"openai","model_id":"gpt-test","thinking_level":"high"},
         "content_blocks":[
           {"type":"thinking","content_index":0,"text":"reason","redacted":false},
           {"type":"text","content_index":1,"text":"answer"}
         ],
         "attachments":[
           {"id":"att-1","name":"chart.png","mime_type":"image/png","size_bytes":42,
            "sha256":"abc","status":"ready"}
         ]}
        """)

        XCTAssertEqual(frame.provider, "openai")
        XCTAssertEqual(frame.contentIndex, 2)
        XCTAssertEqual(frame.providerRoute?.modelId, "gpt-test")
        XCTAssertEqual(frame.contentBlocks?.first?.type, "thinking")
        XCTAssertEqual(frame.attachments?.first?.name, "chart.png")
        XCTAssertEqual(frame.attachments?.first?.isReady, true)
    }

    func testLiveMarketContextFrameKeepsQuoteProvenanceForRightRail() throws {
        let frame = try decodeFrame("""
        {"type":"live_context","run_id":"r1","sequence":5,"items":[{
          "kind":"market_live_context","snapshot_id":"lmc-1234",
          "symbols":["000001.SH"],"source_asof_ts":"2026-07-27T10:31:00+08:00",
          "retrieved_at":"2026-07-27T10:31:02+08:00","eligibility":"forward_observed",
          "provenance":"kss_live_market_context","rows":[{
            "symbol":"000001.SH","routed_provider":"longbridge",
            "quote":{"symbol":"000001.SH","last_done":3421.5,"source_asof_ts":"2026-07-27T10:31:00+08:00"}
          }],"warnings":["forward_observed_non_pit"],"errors":[]
        }]}
        """)

        XCTAssertEqual(frame.liveContexts?.first?.snapshotID, "lmc-1234")
        XCTAssertEqual(frame.liveContexts?.first?.rows.first?.quote?.lastDone, 3421.5)
        XCTAssertEqual(frame.liveContexts?.first?.rows.first?.routedProvider, "longbridge")
    }

    func testAdditiveProviderAndAttachmentResponsesAllowSparsePayloads() throws {
        let providerResponse = try JSONDecoder().decode(
            AgentProvidersResponse.self,
            from: Data(#"{"status":"starting"}"#.utf8))
        XCTAssertTrue(providerResponse.providers.isEmpty)
        XCTAssertEqual(providerResponse.status, "starting")

        let testedProviderResponse = try JSONDecoder().decode(
            AgentProvidersResponse.self,
            from: Data(#"{"source":"llm","ok":true,"status":"ready","latency_ms":12.5,"hint":"stream ok","candidates":[{"role":"primary","model":"gpt-test","ok":true,"latency_ms":12.5,"hint":"stream ok"}]}"#.utf8))
        XCTAssertEqual(testedProviderResponse.source, "llm")
        XCTAssertEqual(testedProviderResponse.ok, true)
        XCTAssertEqual(testedProviderResponse.latencyMs, 12.5)
        XCTAssertEqual(testedProviderResponse.candidates?.first?.hint, "stream ok")

        let attachmentResponse = try JSONDecoder().decode(
            AgentAttachmentsResponse.self,
            from: Data(#"{"attachment":{"id":"a1","filename":"note.md","mime_type":"text/markdown","extraction_status":"extracted"}}"#.utf8))
        XCTAssertEqual(attachmentResponse.allAttachments.map(\.id), ["a1"])
        XCTAssertEqual(attachmentResponse.allAttachments.first?.name, "note.md")

        let model = try JSONDecoder().decode(
            AgentModelDescriptor.self,
            from: Data(#"{"provider_id":"openai","model_id":"gpt-test","supports_images":true}"#.utf8))
        XCTAssertEqual(model.id, "gpt-test")
        XCTAssertEqual(model.providerId, "openai")
        XCTAssertEqual(model.supportsImages, true)
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

    func testRepeatedMessageStartCreatesDistinctAssistantBubbles() throws {
        let store = KSSStore(testBridge: nil)
        store.chatMessages = [
            ChatMessage(role: .user, text: "开始"),
            ChatMessage(role: .assistant, text: "", numbersUnverified: true),
        ]
        let initialAssistantId = try XCTUnwrap(store.chatMessages.last?.id)

        for json in [
            #"{"type":"message_start","run_id":"r1","sequence":1}"#,
            #"{"type":"message_delta","run_id":"r1","sequence":2,"delta":"第一段"}"#,
            #"{"type":"message_end","run_id":"r1","sequence":3}"#,
            #"{"type":"message_start","run_id":"r1","sequence":4}"#,
            #"{"type":"message_delta","run_id":"r1","sequence":5,"delta":"后续段"}"#,
            #"{"type":"message_end","run_id":"r1","sequence":6}"#,
        ] {
            XCTAssertTrue(
                store.applyAgentFrame(
                    try decodeFrame(json),
                    assistantId: initialAssistantId))
        }

        XCTAssertEqual(
            store.chatMessages.filter { $0.role == .assistant }.map(\.text),
            ["第一段", "后续段"])
    }

    func testOldAgentStreamCannotOwnNewerChatSurface() {
        let oldStreamId = UUID()
        let newStreamId = UUID()

        XCTAssertFalse(
            KSSStore.agentStreamOwnsChatSurface(
                endingStreamId: oldStreamId,
                activeStreamId: newStreamId))
        XCTAssertTrue(
            KSSStore.agentStreamOwnsChatSurface(
                endingStreamId: newStreamId,
                activeStreamId: newStreamId))
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

    func testAgentSessionHydratesContentBlocksAndAttachmentReferences() throws {
        let data = Data("""
        {"sessions":[{"session_id":"s1","title":"A","archived":false,
          "messages":[
            {"id":"u1","role":"user","content":[
              {"type":"text","content_index":0,"text":"看这张图"},
              {"type":"attachment_ref","content_index":1,"attachment_id":"att-1","mime_type":"image/png"}
            ],"attachments":[
              {"id":"att-1","name":"chart.png","mime_type":"image/png","size_bytes":2048,"status":"ready"}
            ]},
            {"id":"a1","role":"assistant","content_blocks":[
              {"type":"thinking","content_index":0,"text":"先核对图表","provider":"openai"},
              {"type":"text","content_index":1,"text":"已核对"}
            ]}
          ]}]}
        """.utf8)
        let response = try JSONDecoder().decode(AgentSessionListResponse.self, from: data)
        let store = KSSStore(testBridge: nil)
        store.agentSessions = response.sessions
        store.openAgentSession("s1")

        XCTAssertEqual(store.chatMessages.map(\.text), ["看这张图", "已核对"])
        XCTAssertEqual(store.chatMessages[0].attachments.first?.id, "att-1")
        XCTAssertEqual(store.chatMessages[1].thinkingBlocks.first?.text, "先核对图表")
    }

    func testAbortNeverFallsBackToLegacyChat() {
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "client_abort", userAborted: true, assistantEmpty: true, assistantIsError: false))
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "aborted", userAborted: false, assistantEmpty: true, assistantIsError: false))
        XCTAssertTrue(KSSStore.shouldFallbackToLegacyAgent(
            error: "Agent 连接中断", userAborted: false, assistantEmpty: true, assistantIsError: false))
        XCTAssertFalse(KSSStore.shouldFallbackToLegacyAgent(
            error: "Agent 响应超时",
            terminationReason: "unable_to_complete",
            userAborted: false,
            assistantEmpty: true,
            assistantIsError: false))
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

    func testQueueUpdateAcceptedAndRejectedPreserveServerTruth() throws {
        let store = KSSStore(testBridge: nil)
        let accepted = try decodeFrame("""
        {"protocol_version":1,"session_id":"s1","run_id":"r1","sequence":1,
         "type":"queue_update","operation":"accepted",
         "item":{"id":"q1","client_message_id":"m1","session_id":"s1","run_id":"r1",
         "mode":"steering","content":"补充条件","status":"queued","created_at":1.5},
         "queued_inputs":[{"id":"q1","client_message_id":"m1","session_id":"s1","run_id":"r1",
         "mode":"steering","content":"补充条件","status":"queued","created_at":1.5}],
         "steering_count":1,"follow_up_count":0}
        """)
        XCTAssertTrue(store.applyAgentFrame(accepted))
        XCTAssertEqual(store.agentQueuedInputs.map(\.id), ["q1"])
        XCTAssertEqual(store.agentSteeringCount, 1)
        XCTAssertEqual(store.agentFollowUpCount, 0)
        XCTAssertEqual(store.agentQueueAcknowledgement?.clientMessageId, "m1")
        XCTAssertEqual(store.agentQueueAcknowledgement?.accepted, true)

        let rejected = try decodeFrame("""
        {"protocol_version":1,"session_id":"s1","run_id":"r1","sequence":2,
         "type":"queue_update","operation":"rejected","reason":"run_id_mismatch"}
        """)
        XCTAssertTrue(store.applyAgentFrame(rejected))
        XCTAssertEqual(store.agentQueuedInputs.map(\.id), ["q1"])
        XCTAssertEqual(rejected.reason, "run_id_mismatch")

        let applied = try decodeFrame("""
        {"protocol_version":1,"session_id":"s1","run_id":"r1","sequence":3,
         "type":"queue_update","operation":"applied",
         "item":{"id":"q1","mode":"steering","content":"补充条件","status":"applied"},
         "queued_inputs":[{"id":"q1","mode":"steering","content":"补充条件","status":"applied"}],
         "steering_count":1,"follow_up_count":0}
        """)
        XCTAssertTrue(store.applyAgentFrame(applied))
        XCTAssertTrue(store.agentQueuedInputs.isEmpty)
        XCTAssertEqual(store.agentSteeringCount, 0)
    }

    func testQueueEditorClearsOnlyForMatchingAcceptedAcknowledgement() {
        let accepted = AgentQueueAcknowledgement(
            clientMessageId: "m1", accepted: true, operation: "accepted")
        let rejected = AgentQueueAcknowledgement(
            clientMessageId: "m1", accepted: false, operation: "rejected")

        XCTAssertTrue(KSSStore.shouldClearQueuedEditor(
            acknowledgement: accepted, pendingClientMessageId: "m1"))
        XCTAssertFalse(KSSStore.shouldClearQueuedEditor(
            acknowledgement: rejected, pendingClientMessageId: "m1"))
        XCTAssertFalse(KSSStore.shouldClearQueuedEditor(
            acknowledgement: accepted, pendingClientMessageId: "m2"))
    }

    func testSessionHydrationRestoresOnlyPendingQueueInputs() {
        let store = KSSStore(testBridge: nil)
        store.agentSessions = [
            AgentSession(
                sessionId: "s1",
                title: "A",
                queuedInputs: [
                    AgentQueuedInput(
                        id: "q1", mode: "follow_up", content: "恢复后续",
                        status: "restored"),
                    AgentQueuedInput(
                        id: "q2", mode: "steering", content: "已执行",
                        status: "applied"),
                ])
        ]

        store.openAgentSession("s1")
        XCTAssertEqual(store.agentQueuedInputs.map(\.id), ["q1"])
        XCTAssertEqual(store.agentSteeringCount, 0)
        XCTAssertEqual(store.agentFollowUpCount, 1)
    }

    func testStructuredMemoryKeepsTrueSourceExpiryReviewAndScore() throws {
        let data = Data("""
        {"memories":[{"id":"m1","kind":"thesis","content":"历史判断",
          "source_session":"s1","source_entry":"e1","tags":["RSI"],
          "status":"approved","created_at":10,"expires_at":20,
          "review_required":true,"score":0.8,"injection_text":"【待复核】历史判断"}],
         "candidates":[],
         "recalls":[{"id":"m1","kind":"thesis","content":"历史判断",
          "source_session":"s1","source_entry":"e1","expires_at":20,
          "review_required":true,"score":0.8,
          "injection_text":"【待复核】历史判断"}]}
        """.utf8)

        let response = try JSONDecoder().decode(AgentMemoriesResponse.self, from: data)
        XCTAssertEqual(response.memories[0].text, "历史判断")
        XCTAssertEqual(response.memories[0].sourceSession, "s1")
        XCTAssertEqual(response.memories[0].sourceEntry, "e1")
        XCTAssertEqual(response.memories[0].expiresAt, 20)
        XCTAssertEqual(response.memories[0].reviewRequired, true)
        XCTAssertEqual(response.memories[0].score, 0.8)
        XCTAssertEqual(response.recalls?[0].id, "m1")
        XCTAssertEqual(response.recalls?[0].title, "thesis · 待复核")
        XCTAssertEqual(response.recalls?[0].source, "s1 · e1")
        XCTAssertEqual(response.recalls?[0].injectionText, "【待复核】历史判断")
    }

    private func decodeFrame(_ json: String) throws -> AgentFrame {
        try JSONDecoder().decode(AgentFrame.self, from: Data(json.utf8))
    }
}
