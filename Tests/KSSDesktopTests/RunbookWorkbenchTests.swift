import XCTest
@testable import KSSDesktop

final class RunbookWorkbenchTests: XCTestCase {
    func testWorkbenchTasksAreTodayJobsOnly() {
        XCTAssertEqual(
            KSSTask.workbenchTasks,
            [
                .previewPicks, .generatePicks, .paperSummary,
                .logmvBacktest, .radarArchiveAnalysis, .updateCsData,
            ])
        XCTAssertTrue(KSSTask.workbenchTasks.contains(.updateCsData))
        XCTAssertFalse(KSSTask.workbenchTasks.contains(.formalDailyPicks))
        XCTAssertFalse(KSSTask.workbenchTasks.contains(.formalDailyReview))
        XCTAssertFalse(KSSTask.workbenchTasks.contains(.formalSectorReview))
        XCTAssertFalse(KSSTask.workbenchTasks.contains(.refreshSectorRotation))
        XCTAssertFalse(KSSTask.workbenchTasks.contains(.styleContrastDaily))
    }

    func testWorkbenchBlurbsAreSpecific() {
        for task in KSSTask.workbenchTasks {
            XCTAssertFalse(task.workbenchBlurb.isEmpty, task.title)
            XCTAssertFalse(
                task.workbenchBlurb.contains("在设置 → 定时任务中管理这条管道。"),
                task.title)
        }
    }

    func testSettingsTasksCategoryIsRenamed() {
        XCTAssertEqual(SettingsCategory.tasks.label, "定时任务")
        XCTAssertEqual(SettingsCategory.logs.label, "日志")
        XCTAssertEqual(SettingsCategory.tasks.tab, .operations)
    }

    func testEODChainPreservesOrderAndSkipsMissing() {
        let picks = job(label: "com.zcdeng.kss.formal_daily_picks", title: "正式每日选股")
        let review = job(label: "com.zcdeng.kss.formal_daily_review", title: "正式每日复盘")
        let extra = job(label: "com.zcdeng.kss.news_digest_postclose", title: "舆情热点")
        let chain = RunbookEODChain.jobs(from: [review, extra, picks])
        XCTAssertEqual(chain.map(\.suffix), ["formal_daily_picks", "formal_daily_review"])
    }

    func testRunbookItemIdentity() {
        XCTAssertEqual(RunbookItem.today(.updateCsData).id, "today-update-cs-data")
        XCTAssertEqual(RunbookItem.pipeline.id, "pipeline")
        XCTAssertNotEqual(RunbookItem.research("a"), RunbookItem.research("b"))
    }

    private func job(label: String, title: String) -> ScheduledJob {
        ScheduledJob(
            label: label, title: title, category: "扫描选股", schedule: "工作日 23:00",
            scheduleStruct: nil, script: "demo.sh", enabled: true, needsInstall: false,
            loaded: true, running: false, lastStatus: "success", lastRunAt: nil, lastLine: nil,
            stale: false, missedCycles: 0, expectedAt: nil, nextRunAt: nil)
    }
}
