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
        for item in ["tushare", "longbridge", "telegram", "llm"] {
            XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: item), .dataSources)
        }
    }

    func testUnrecognizedItemFallsBackToKeys() {
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "venv"), .keys)
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "storage"), .keys)
        XCTAssertEqual(SettingsTabRouting.targetTab(forSelfCheckItem: "something_new"), .keys)
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
}
