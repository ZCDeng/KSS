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

    func testRetiredInvestmentDailyIsHiddenFromWorkbench() {
        let daily = ResearchGoalSummary(
            goalId: "d1",
            profileId: "investment-daily-v1",
            objective: "投资分析日报 2026-08-13",
            status: "created",
            progress: 0)
        let dailyByName = ResearchGoalSummary(
            goalId: "d2",
            profileId: "investment-weekly-v3",
            objective: "投资分析日报 2026-08-12",
            status: "created",
            progress: 0)
        let completedDaily = ResearchGoalSummary(
            goalId: "d3",
            profileId: "investment-daily-v1",
            objective: "投资分析日报 2026-08-14",
            status: "completed",
            progress: 1)
        let weekly = ResearchGoalSummary(
            goalId: "w1",
            profileId: "investment-weekly-v3",
            objective: "投资分析周报 2026-08-10 至 2026-08-14",
            status: "running",
            progress: 0.66)
        let scan = ResearchGoalSummary(
            goalId: "s1",
            profileId: "left-scan",
            objective: "左侧机会扫描 · 2026-08-17",
            status: "completed",
            progress: 1)

        XCTAssertFalse(RunbookResearchList.isListed(daily))
        XCTAssertFalse(RunbookResearchList.isListed(dailyByName))
        XCTAssertFalse(RunbookResearchList.isListed(completedDaily))
        XCTAssertTrue(RunbookResearchList.isListed(weekly))
        XCTAssertTrue(RunbookResearchList.isListed(scan))
        XCTAssertEqual(
            RunbookResearchList.listed([daily, dailyByName, completedDaily, weekly, scan]).map(\.goalId),
            ["w1", "s1"])
        XCTAssertFalse(RunbookResearchList.isCreatableProfile("investment-daily-v1"))
        XCTAssertTrue(RunbookResearchList.isCreatableProfile("investment-weekly-v3"))
    }

    private func job(label: String, title: String) -> ScheduledJob {
        ScheduledJob(
            label: label, title: title, category: "扫描选股", schedule: "工作日 23:00",
            scheduleStruct: nil, script: "demo.sh", enabled: true, needsInstall: false,
            loaded: true, running: false, lastStatus: "success", lastRunAt: nil, lastLine: nil,
            stale: false, missedCycles: 0, expectedAt: nil, nextRunAt: nil)
    }
}
