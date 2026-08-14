import XCTest
@testable import KSSDesktop

final class SettingsTabTests: XCTestCase {
    private func job(
        label: String = "com.zcdeng.kss.demo",
        enabled: Bool = true,
        needsInstall: Bool? = false,
        running: Bool = false,
        lastStatus: String = "success",
        stale: Bool = false
    ) -> ScheduledJob {
        ScheduledJob(
            label: label, title: "demo", category: "系统", schedule: "工作日 17:00",
            scheduleStruct: nil, script: "demo.sh", enabled: enabled, needsInstall: needsInstall,
            loaded: true, running: running, lastStatus: lastStatus, lastRunAt: nil, lastLine: nil,
            stale: stale, missedCycles: 0, expectedAt: nil, nextRunAt: nil
        )
    }

    // MARK: - Covers AE2: 数据源 tab 状态点判定

    func testDataSourcesBadgeWhenAnyUnconfigured() {
        XCTAssertTrue(SettingsTabRouting.dataSourcesNeedsBadge(
            configured: [true, true, false, true], testsOK: []))
    }

    func testDataSourcesNoBadgeWhenAllConfiguredAndTestsPass() {
        XCTAssertFalse(SettingsTabRouting.dataSourcesNeedsBadge(
            configured: [true, true, true, true], testsOK: [true, true]))
    }

    func testDataSourcesBadgeWhenRecentTestFailed() {
        XCTAssertTrue(SettingsTabRouting.dataSourcesNeedsBadge(
            configured: [true, true, true, true], testsOK: [true, false]))
    }

    // MARK: - 定时任务 tab 状态点判定

    func testScheduledTasksBadgeWhenAnyStaleFailedOrNeedsInstall() {
        XCTAssertTrue(SettingsTabRouting.scheduledTasksNeedsBadge(jobs: [
            job(needsInstall: true),
        ]))
        XCTAssertTrue(SettingsTabRouting.scheduledTasksNeedsBadge(jobs: [
            job(needsInstall: false, stale: true),
        ]))
        XCTAssertTrue(SettingsTabRouting.scheduledTasksNeedsBadge(jobs: [
            job(needsInstall: false, lastStatus: "failed"),
        ]))
    }

    func testScheduledTasksNoBadgeWhenAllHealthy() {
        XCTAssertFalse(SettingsTabRouting.scheduledTasksNeedsBadge(jobs: [
            job(), job(label: "com.zcdeng.kss.demo2"),
        ]))
    }

    // MARK: - 自检 fail 项 → 目标 tab 映射

    func testCredentialItemsRouteToDataSources() {
        for item in ["tushare", "longbridge", "telegram", "research", "llm"] {
            XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: item), .dataSources)
        }
    }

    func testUnrecognizedItemFallsBackToKeys() {
        // 未知项落 selfCheck，投影到 credentials tab（与 .keys 别名同 tab）
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "venv"), .credentials)
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "storage"), .credentials)
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "something_new"), .credentials)
    }

    // MARK: - SettingsCategory 深链（plan 2026-07-23-003）

    func testCredentialItemsRouteToMatchingCategory() {
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "tushare"), .tushare)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "longbridge"), .longbridge)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "intraday_secrets"), .longbridge)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "telegram"), .telegram)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "research"), .research)
        // LLM credentials are configured in Seesaw Models, not a global
        // Settings category. The self-check row owns the explicit deep link.
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "llm"), .selfCheck)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "yupi"), .yupi)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "openrouter"), .yupi)
    }

    func testUnknownSelfCheckFallsBackToSelfCheckCategory() {
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "venv"), .selfCheck)
        XCTAssertEqual(SettingsTabRouting.targetCategory(forSelfCheckItem: "storage"), .selfCheck)
    }

    func testCategoryProjectsToClassicTab() {
        XCTAssertEqual(SettingsCategory.tushare.tab, .credentials)
        XCTAssertEqual(SettingsCategory.research.tab, .credentials)
        XCTAssertEqual(SettingsCategory.yupi.tab, .credentials)
        XCTAssertEqual(SettingsCategory.tasks.tab, .operations)
        XCTAssertEqual(SettingsCategory.logs.tab, .operations)
        XCTAssertEqual(SettingsCategory.selfCheck.tab, .credentials)
    }

    func testTabDefaultCategory() {
        XCTAssertEqual(SettingsTab.credentials.defaultCategory, .selfCheck)
        XCTAssertEqual(SettingsTab.operations.defaultCategory, .tasks)
    }

    func testCategoryOrderIsStable() {
        XCTAssertEqual(
            SettingsCategory.allCases.map(\.rawValue),
            ["selfCheck", "tushare", "longbridge", "telegram", "research", "yupi", "tasks", "logs"]
        )
    }

    func testCategoryBadgeForUnconfiguredSource() {
        XCTAssertTrue(SettingsTabRouting.categoryNeedsBadge(
            .tushare,
            isSourceConfigured: { $0 == "tushare" ? false : true },
            testOK: { _ in nil },
            jobs: []
        ))
        XCTAssertFalse(SettingsTabRouting.categoryNeedsBadge(
            .tushare,
            isSourceConfigured: { _ in true },
            testOK: { _ in true },
            jobs: []
        ))
    }

    // MARK: - SettingsTab 枚举完整性（R4：合并为 2 tab，旧名经由别名归并）

    func testTwoTabsPresent() {
        XCTAssertEqual(Set(SettingsTab.allCases), [.credentials, .operations])
    }

    func testLegacyTabAliasesMapToMergedTabs() {
        XCTAssertEqual(SettingsTab.keys, .credentials)
        XCTAssertEqual(SettingsTab.dataSources, .credentials)
        XCTAssertEqual(SettingsTab.scheduledTasks, .operations)
        XCTAssertEqual(SettingsTab.logs, .operations)
    }

    func testResearchKeysAreInjectedViaKeychain() {
        for key in ["KSS_RESEARCH_PROVIDER", "KSS_RESEARCH_FETCH_PROVIDER",
                    "KSS_RESEARCH_FIXTURE_PATH", "JINA_API_KEY", "SERPER_API_KEY"] {
            XCTAssertTrue(KeychainStore.managedKeys.contains(key), key)
        }
    }

    func testCredentialHydrationDoesNotLookLikeAnUnsavedUserEdit() {
        XCTAssertFalse(SettingsCredentialChangePolicy.shouldMarkDirty(isHydrating: true))
        XCTAssertTrue(SettingsCredentialChangePolicy.shouldMarkDirty(isHydrating: false))
    }
}
