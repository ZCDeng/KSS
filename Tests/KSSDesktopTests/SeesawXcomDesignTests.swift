import XCTest

final class SeesawXcomDesignTests: XCTestCase {
    private var source: String {
        get throws {
            let sourceURL = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appending(path: "Sources/KSSDesktop/Views/Seesaw/AIChatView.swift")
            return try String(contentsOf: sourceURL, encoding: .utf8)
        }
    }

    private var contentSource: String {
        get throws {
            let sourceURL = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appending(path: "Sources/KSSDesktop/Views/ContentView.swift")
            return try String(contentsOf: sourceURL, encoding: .utf8)
        }
    }

    func testComposerSubmitIsImeSafeAndUnifiedWithStop() throws {
        let source = try source
        // 回车经 onSubmit 发送：IME 组词确认的回车不触发提交；⌥↩ 原生换行。
        // @file 悬浮面板打开时回车先选中引用，仍不绕过 onSubmit。
        XCTAssertFalse(source.contains("onKeyPress(.return"))
        XCTAssertTrue(source.contains("submitInput(mode: \"steering\")"))
        XCTAssertTrue(source.contains("if atFilePanelVisible {"))
        // 提交/停止是同一按钮的两个态，不再有并排的独立停止按钮。
        XCTAssertFalse(source.contains("focusStopButton"))
        XCTAssertFalse(source.contains("xcomStopButton"))
        XCTAssertTrue(source.contains("store.isChatStreaming ? \"stop.fill\" : \"arrow.up\""))
        // 消息悬停有显性复制入口。
        XCTAssertTrue(source.contains("private func messageActionRow"))
    }

    func testAllThemesUseSharedFocusShell() throws {
        let source = try source
        XCTAssertTrue(source.contains("focusSeesawShell(size: geo.size)"))
        XCTAssertTrue(source.contains("private func focusSeesawShell"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.feedColumnWidth"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.headerHeight"))
        XCTAssertFalse(source.contains("if isXcom"))
    }

    func testFocusShellKeepsAuxiliarySurfacesOutOfConversationColumn() throws {
        let source = try source
        // legacy xcom/classic shell 已删除：Focus Layout 是唯一 body 路径。
        XCTAssertFalse(source.contains("private func xcomSeesawShell"))
        XCTAssertFalse(source.contains("private func classicSeesawShell"))
        let focusStart = try XCTUnwrap(source.range(of: "private func focusSeesawShell"))
        let focus = String(source[focusStart.lowerBound..<source.endIndex])
        XCTAssertTrue(focus.contains("focusSessionPalette"))
        XCTAssertTrue(focus.contains("focusSkillPalette"))
        XCTAssertTrue(focus.contains("focusContextPopover"))
        XCTAssertFalse(focus.contains("xcomAgentSidebar"))
        XCTAssertFalse(focus.contains("xcomUtilityPanel"))
    }

    func testFocusShellRendersEveryOverlayFromOneSharedSurface() throws {
        let source = try source
        let focusStart = try XCTUnwrap(source.range(of: "private func focusSeesawShell"))
        let focus = String(source[focusStart.lowerBound..<source.endIndex])

        XCTAssertTrue(focus.contains("focusOverlaySurface"))
        XCTAssertFalse(focus.contains(".popover(isPresented: overlayBinding(.sessions)"))
        let overlayStart = try XCTUnwrap(source.range(of: "private func focusOverlayContent"))
        let overlayEnd = try XCTUnwrap(source.range(of: "private func focusHeader", range: overlayStart.upperBound..<source.endIndex))
        let overlayContent = String(source[overlayStart.lowerBound..<overlayEnd.lowerBound])
        XCTAssertTrue(overlayContent.contains("case .skills:"))
        XCTAssertTrue(overlayContent.contains("focusSkillPalette"))
        XCTAssertTrue(overlayContent.contains("case .context:"))
        XCTAssertTrue(overlayContent.contains("focusContextPopover"))
    }

    func testFocusShellUsesResponsiveOpenWorkerStyleInspector() throws {
        let source = try source
        XCTAssertTrue(source.contains("let persistentInspector = size.width >= 1180"))
        XCTAssertTrue(source.contains("focusInspector"))
        XCTAssertTrue(source.contains("showInspectorDrawer"))
        XCTAssertTrue(source.contains("toggleInspectorSection"))
        XCTAssertTrue(source.contains("case liveMarket"))
        XCTAssertTrue(source.contains("agentLiveMarketContexts"))
        XCTAssertTrue(source.contains("private var focusEmptyConversation"))
        XCTAssertTrue(source.contains("SeesawXcomChrome.composerColumnWidth"))
    }

    func testFocusHeaderDoesNotDuplicateRailOrComposerControls() throws {
        let source = try source
        let start = try XCTUnwrap(source.range(of: "private func focusHeader"))
        let end = try XCTUnwrap(source.range(of: "private var focusInspector", range: start.upperBound..<source.endIndex))
        let header = String(source[start.lowerBound..<end.lowerBound])

        XCTAssertTrue(header.contains("toggleOverlay(.sessions)"))
        XCTAssertTrue(header.contains("showInspectorDrawer"))
        XCTAssertFalse(header.contains("toggleOverlay(.context)"))
        XCTAssertFalse(header.contains("toggleOverlay(.skills)"))
        XCTAssertFalse(header.contains("seesawPage = isInModelsWorkspace ? .conversation : .models"))
    }

    func testFocusEmptyStateUsesTaskRowsAndComposerOwnsModelStatus() throws {
        let source = try source
        // 首页 doodle:空态顶部的 Seesaw 描边动效字标(Google 式产品气息)。
        XCTAssertTrue(source.contains("SeesawWordmark()"))
        // 新会话入口常驻 header 右侧,不再只藏在会话面板里。
        XCTAssertTrue(source.contains("store.createAgentSession()"))
        XCTAssertTrue(source.contains("private var focusResearchTaskRows"))
        XCTAssertTrue(source.contains("researchTaskRow("))
        XCTAssertTrue(source.contains("$0.name == starter.skillId"))
        XCTAssertTrue(source.contains("private var composerInlineStatus"))
        XCTAssertTrue(source.contains("if let pending = store.pendingWriteConfirm"))
        // FlowDown-style: send sits on the same row as TextField (no stacked control bar).
        XCTAssertTrue(source.contains("FlowDown-inspired input"))
        XCTAssertTrue(source.contains("focusSendButton"))
        XCTAssertFalse(source.contains("private var focusProviderIssue"))
    }

    func testStreamingEmptyBubbleShowsThinkingAndToolProgress() throws {
        let source = try source
        XCTAssertTrue(source.contains("streamingThinkLabel"))
        XCTAssertTrue(source.contains("正在调用 \\(tool)…"))
        XCTAssertTrue(source.contains("message.thinkingBlocks.isEmpty"))
        XCTAssertTrue(source.contains("Text(streaming ? \"正在思考…\" : \"思考过程\")"))
        XCTAssertTrue(source.contains("AgentThinkingDisclosure("))
        XCTAssertTrue(source.contains("streaming: store.isChatStreaming"))
    }

    func testRailOnlyShowsContextualMarketAndWorkState() throws {
        let source = try source
        let start = try XCTUnwrap(source.range(of: "private var focusInspector"))
        let end = try XCTUnwrap(source.range(of: "private func inspectorSection", range: start.upperBound..<source.endIndex))
        let inspector = String(source[start.lowerBound..<end.lowerBound])

        XCTAssertTrue(inspector.contains("if store.isChatStreaming || store.agentSteeringCount"))
        XCTAssertTrue(inspector.contains("if !store.agentLiveMarketContexts.isEmpty"))
        XCTAssertTrue(inspector.contains("if hasEvidenceOrAttachments"))
        XCTAssertTrue(inspector.contains("opens: .skills"))
        XCTAssertTrue(inspector.contains("opens: .context"))
        XCTAssertFalse(inspector.contains("历史问题不会隐式请求"))
    }

    func testModelsBelongToSeesawRatherThanGlobalSettings() throws {
        let source = try source
        XCTAssertTrue(source.contains("private var seesawModelsPage"))
        XCTAssertTrue(source.contains("case providerDetail(String)"))
        XCTAssertTrue(source.contains("private func seesawProviderDetail"))
        XCTAssertTrue(source.contains("private func providerCatalogCard"))
        XCTAssertTrue(source.contains("private func providerModelRow"))
        XCTAssertTrue(source.contains("saveProviderCredential"))
        XCTAssertFalse(source.contains("SettingsCredentialsSection("))
        XCTAssertFalse(source.contains("focusSource: .llm"))
    }

    func testFocusComposerIsSharedAndSkillsRemainExplicit() throws {
        let source = try source
        XCTAssertTrue(source.contains("private var focusComposer"))
        XCTAssertTrue(source.contains("focusSessionSkillChips"))
        XCTAssertTrue(source.contains("availableSkillStarters"))
        XCTAssertTrue(source.contains("private var focusSkillPalette"))
        XCTAssertTrue(source.contains("Toggle(\"启用\""))
        XCTAssertTrue(source.contains("加入本会话"))
        XCTAssertTrue(source.contains("focusSkillFilterTabs"))
    }

    func testFocusConversationKeepsOneComposerAndUsesLandscapeUtilitySurfaces() throws {
        let source = try source
        let start = try XCTUnwrap(source.range(of: "private var focusConversationWorkspace"))
        let end = try XCTUnwrap(source.range(of: "private var focusEmptyConversation", range: start.upperBound..<source.endIndex))
        let workspace = String(source[start.lowerBound..<end.lowerBound])
        XCTAssertEqual(workspace.components(separatedBy: "focusComposer").count - 1, 1)
        XCTAssertTrue(source.contains("focusOverlaySize(for: overlay, in: size)"))
        XCTAssertTrue(source.contains("Color.clear"))
        XCTAssertFalse(source.contains("Color.black\n                .opacity(theme.appearance"))
    }

    func testAssistantTranscriptUsesBlockMarkdownRenderer() throws {
        let source = try source
        let start = try XCTUnwrap(source.range(of: "private func focusMessageCell"))
        let end = try XCTUnwrap(source.range(of: "private func focusToolRow", range: start.upperBound..<source.endIndex))
        let messageCell = String(source[start.lowerBound..<end.lowerBound])
        XCTAssertTrue(messageCell.contains("SeesawMarkdownView(markdown: message.text, errorTint:"))
        XCTAssertFalse(messageCell.contains("markdownText(message.text)\n                        .font(KSSFont.themed(15"))
    }

    func testConversationContentSupportsSelectionAndWholeMessageCopy() throws {
        let source = try source
        let start = try XCTUnwrap(source.range(of: "private func focusMessageCell"))
        let end = try XCTUnwrap(
            source.range(of: "private func focusToolRow", range: start.upperBound..<source.endIndex)
        )
        let messageCell = String(source[start.lowerBound..<end.lowerBound])

        XCTAssertTrue(source.contains("import AppKit"))
        XCTAssertTrue(source.contains("private func copyMessageText(_ text: String)"))
        XCTAssertTrue(source.contains("NSPasteboard.general"))
        XCTAssertEqual(
            messageCell.components(
                separatedBy: "Button(\"复制内容\", systemImage: \"doc.on.doc\")"
            ).count - 1,
            2
        )
        XCTAssertTrue(messageCell.contains(".textSelection(.enabled)"))
    }

    func testSeesawNavigationCollapseIsTransient() throws {
        let source = try contentSource
        XCTAssertTrue(source.contains("@State private var seesawNavigationExpanded"))
        XCTAssertTrue(source.contains("private var effectiveSidebarCollapsed"))
        XCTAssertTrue(source.contains("if store.selectedSection == .aiChat"))
        XCTAssertTrue(source.contains("return !seesawNavigationExpanded"))
        XCTAssertTrue(source.contains("AIChatView(globalNavigationExpanded: $seesawNavigationExpanded)"))
    }

    func testEnteringSeesawDoesNotAnimateThePreviousDetailThroughFocusWorkspace() throws {
        let source = try contentSource
        XCTAssertTrue(source.contains("private var seesawDetailTransition"))
        XCTAssertTrue(source.contains("if store.selectedSection == .aiChat { return .identity }"))
        XCTAssertTrue(source.contains(".transition(seesawDetailTransition)"))
        XCTAssertTrue(source.contains(".animation(seesawDetailAnimation, value: store.selectedSection)"))
    }

    func testResolveWriteConfirmUsesControlChannelWithoutBlockingReader() throws {
        let storeURL = try XCTUnwrap(URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appending(path: "Sources/KSSDesktop/Services/KSSStore.swift"))
        let source = try String(contentsOf: storeURL, encoding: .utf8)
        XCTAssertTrue(source.contains("control.confirm(runId: activeAgentRunId, callId: confirm.callId"))
        XCTAssertTrue(source.contains("return false"))
        let bridgeURL = storeURL.deletingLastPathComponent().appending(path: "BridgeClient.swift")
        let bridge = try String(contentsOf: bridgeURL, encoding: .utf8)
        XCTAssertTrue(bridge.contains("onConfirmRequired no longer blocks the reader"))
    }

}
