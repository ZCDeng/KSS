import SwiftUI

enum RunbookItem: Hashable, Identifiable {
    case today(KSSTask)
    case research(String)
    case pipeline

    var id: String {
        switch self {
        case .today(let task): return "today-\(task.rawValue)"
        case .research(let goalId): return "research-\(goalId)"
        case .pipeline: return "pipeline"
        }
    }
}

struct RunbookView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    var pythonEnvironment: PythonEnvironment?
    var isRunning: Bool
    var results: [TaskRunResult]
    var onRun: (KSSTask) -> Void

    @State private var selection: RunbookItem = .today(.previewPicks)
    @State private var showingCreateResearch = false

    private var listedResearchGoals: [ResearchGoalSummary] {
        RunbookResearchList.listed(store.researchGoals)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            listColumn
                .frame(width: SettingsFormStyle.navWidth)
                .frame(maxHeight: .infinity, alignment: .top)
            Divider().overlay(theme.hairline)
            detailPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(theme.canvas)
        .sheet(isPresented: $showingCreateResearch) {
            ResearchCreateGoalSheet(store: store, isPresented: $showingCreateResearch) { goalId in
                selection = .research(goalId)
            }
        }
        .task {
            if store.researchGoals.isEmpty {
                await store.loadResearchGoals()
            }
            if store.scheduledJobs.isEmpty {
                await store.loadScheduledJobs()
            }
            applyIncomingSelection()
        }
        .onChange(of: store.researchCandidate?.objective) { _, _ in
            applyIncomingSelection()
        }
        .onChange(of: store.selectedResearchGoalId) { _, goalId in
            if case .research = selection, let goalId {
                selection = .research(goalId)
            }
            pruneHiddenResearchSelection()
        }
        .onChange(of: store.researchGoals) { _, _ in
            pruneHiddenResearchSelection()
        }
    }

    private func applyIncomingSelection() {
        let reveal = store.runbookRevealResearch
        if reveal { store.runbookRevealResearch = false }
        if store.researchCandidate != nil {
            showingCreateResearch = true
            return
        }
        if reveal, let goalId = store.selectedResearchGoalId, !goalId.isEmpty {
            if let goal = store.researchGoals.first(where: { $0.goalId == goalId }),
               !RunbookResearchList.isListed(goal) {
                selection = .today(.previewPicks)
                return
            }
            selection = .research(goalId)
            Task { await store.openResearchGoal(goalId) }
        }
    }

    private func pruneHiddenResearchSelection() {
        guard case .research(let id) = selection else { return }
        if let goal = store.researchGoals.first(where: { $0.goalId == id }),
           !RunbookResearchList.isListed(goal) {
            selection = .today(.previewPicks)
        }
    }

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("任务台")
                .font(KSSFont.themed(SettingsFormStyle.navTitleSize, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .padding(.horizontal, 16)
                .padding(.top, 20)
                .padding(.bottom, 8)

            pythonChip
                .padding(.horizontal, 16)
                .padding(.bottom, 16)

            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    listSection("今日作业") {
                        ForEach(KSSTask.workbenchTasks) { task in
                            SettingsNavRow(
                                title: task.title,
                                selected: selection == .today(task)
                            ) {
                                selection = .today(task)
                            }
                        }
                    }

                    listSection("深度研究", count: listedResearchGoals.count, trailing: {
                        HStack(spacing: 2) {
                            Button {
                                Task { await store.loadResearchGoals() }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                                    .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                            }
                            .buttonStyle(.borderless)
                            .help("刷新研究目标")
                            Button {
                                showingCreateResearch = true
                            } label: {
                                Image(systemName: "plus")
                                    .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                            }
                            .buttonStyle(.borderless)
                            .help("新建研究目标")
                        }
                    }) {
                        if let candidate = store.researchCandidate {
                            VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                                Text("Seesaw 建议")
                                    .font(KSSFont.themed(SettingsFormStyle.meta, .semibold, theme: theme))
                                    .foregroundStyle(theme.accent)
                                Text(candidate.objective)
                                    .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                                    .foregroundStyle(theme.textPrimary)
                                    .lineLimit(3)
                                SettingsBorderedAction(title: "创建为研究目标") {
                                    showingCreateResearch = true
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(.info, padding: SettingsFormStyle.bannerPadding)
                        }

                        if listedResearchGoals.isEmpty {
                            SettingsHintText(text: "暂无深度研究", empty: true)
                                .padding(.horizontal, SettingsFormStyle.navRowHPadding)
                                .padding(.vertical, 8)
                        } else {
                            ForEach(listedResearchGoals) { goal in
                                SettingsNavRow(
                                    title: goal.objective,
                                    selected: selection == .research(goal.goalId)
                                ) {
                                    selection = .research(goal.goalId)
                                    Task { await store.openResearchGoal(goal.goalId) }
                                } trailing: {
                                    if let caption = progressCaption(goal) {
                                        SettingsStatusCapsule(text: caption)
                                    }
                                }
                            }
                        }
                    }

                    listSection("今日管线") {
                        SettingsNavRow(
                            title: "盘后事件链",
                            selected: selection == .pipeline
                        ) {
                            selection = .pipeline
                        } trailing: {
                            SettingsStatusCapsule(text: pipelineCaption)
                        }
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 16)
            }
        }
        .background(theme.canvas)
    }

    private var pipelineCaption: String {
        let chain = RunbookEODChain.jobs(from: store.scheduledJobs)
        if chain.isEmpty { return "读取中" }
        let done = chain.filter { $0.health == .ok || $0.health == .running }.count
        let stale = store.scheduledJobs.filter(\.stale).count
        let failed = store.scheduledJobs.filter { $0.health == .failed }.count
        if stale > 0 { return "漏跑 \(stale)" }
        if failed > 0 { return "失败 \(failed)" }
        return "\(done)/\(chain.count)"
    }

    private func progressCaption(_ goal: ResearchGoalSummary) -> String? {
        guard let progress = goal.progress else { return nil }
        return "\(Int((progress * 100).rounded()))%"
    }

    @ViewBuilder
    private var detailPane: some View {
        switch selection {
        case .today(let task):
            TodayJobDetail(
                task: task,
                result: results.first { $0.taskId == task.rawValue },
                isThisRunning: isRunning && store.activeFormalTaskId == task.rawValue,
                isBusy: isRunning,
                onRun: { onRun(task) },
                onOpenLogs: { store.openSettings(category: .logs) }
            )
        case .research:
            ResearchWorkbenchView(store: store)
        case .pipeline:
            PipelineStatusDetail(
                jobs: RunbookEODChain.jobs(from: store.scheduledJobs),
                staleCount: store.scheduledJobs.filter(\.stale).count,
                onOpenSchedule: { store.openSettings(category: .tasks) },
                onOpenLogs: { store.openSettings(category: .logs) }
            )
        }
    }

    private var pythonChip: some View {
        let usable = pythonEnvironment?.usable == true
        return SettingsStatusCapsule(
            text: usable ? "Python 就绪" : "Python 不可用",
            tint: usable ? nil : theme.ma5
        )
        .help(pythonEnvironment?.selected ?? "缺少正式解释器")
    }

    private func listSection<Content: View>(
        _ title: String,
        count: Int? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        listSection(title, count: count, trailing: { EmptyView() }, content: content)
    }

    private func listSection<Content: View, Trailing: View>(
        _ title: String,
        count: Int? = nil,
        @ViewBuilder trailing: () -> Trailing,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                Text(title)
                    .font(KSSFont.themed(SettingsFormStyle.sectionHeader, .bold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                if let count {
                    Text("\(count)")
                        .font(KSSFont.themed(10.5, .bold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(theme.textSecondary.opacity(0.12), in: Capsule())
                }
                Spacer(minLength: 0)
                trailing()
            }
            .padding(.horizontal, SettingsFormStyle.navRowHPadding)
            .padding(.top, 10)
            .padding(.bottom, 4)
            content()
        }
    }
}

struct TodayJobDetail: View {
    @Environment(\.kssTheme) private var theme
    var task: KSSTask
    var result: TaskRunResult?
    var isThisRunning: Bool
    var isBusy: Bool
    var onRun: () -> Void
    var onOpenLogs: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SettingsFormStyle.blockSpacing) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                        Text(task.title)
                            .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        SettingsHintText(text: task.workbenchBlurb)
                    }
                    Spacer(minLength: 12)
                    SettingsPrimaryAction(
                        title: "运行",
                        systemImage: "play.fill",
                        busy: isThisRunning,
                        disabled: isBusy,
                        action: onRun
                    )
                    .help(isBusy && !isThisRunning ? "已有任务在跑" : "运行此作业")
                }

                if let result {
                    TaskResultCard(result: result, compact: true)
                } else {
                    SettingsHintText(text: "还没有运行记录。点运行后摘要会出现在这里。", empty: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .kssCard(padding: SettingsFormStyle.cardPadding)
                }

                SettingsBorderedAction(title: "打开完整日志", action: onOpenLogs)
            }
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, SettingsFormStyle.detailHPadding)
            .padding(.vertical, SettingsFormStyle.detailVPadding)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }
}

struct PipelineStatusDetail: View {
    @Environment(\.kssTheme) private var theme
    var jobs: [ScheduledJob]
    var staleCount: Int
    var onOpenSchedule: () -> Void
    var onOpenLogs: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SettingsFormStyle.blockSpacing) {
                VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                    Text("盘后事件链")
                        .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                    SettingsHintText(text: "选股 → MI → 指标 → 复盘。任务台只看状态；启停、改排期、重跑都在设置 → 定时任务。")
                }

                if jobs.isEmpty {
                    SettingsHintText(text: "尚未读到定时任务。打开设置 → 定时任务可同步 LaunchAgent。", empty: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .kssCard(padding: SettingsFormStyle.cardPadding)
                } else {
                    VStack(alignment: .leading, spacing: SettingsFormStyle.groupSpacing) {
                        ForEach(jobs) { job in
                            HStack(spacing: SettingsFormStyle.rowHSpacing) {
                                Text(job.title)
                                    .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                                Spacer()
                                SettingsStatusCapsule(text: job.schedule)
                                pipelineBadge(job.health)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: SettingsFormStyle.cardPadding)
                }

                if staleCount > 0 {
                    SettingsInfoBanner(
                        text: "另有 \(staleCount) 条定时任务漏跑，去设置里一键补跑。",
                        isError: false,
                        systemImage: "exclamationmark.triangle.fill"
                    )
                }

                HStack(spacing: SettingsFormStyle.rowHSpacing) {
                    SettingsPrimaryAction(title: "管理排期", action: onOpenSchedule)
                    SettingsBorderedAction(title: "查看日志", action: onOpenLogs)
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, SettingsFormStyle.detailHPadding)
            .padding(.vertical, SettingsFormStyle.detailVPadding)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }

    private func pipelineBadge(_ health: ScheduledJob.Health) -> StatusBadge {
        switch health {
        case .running:
            return StatusBadge(icon: "arrow.triangle.2.circlepath", text: "运行中", tint: theme.accent, emphasized: true)
        case .needsInstall:
            return StatusBadge(icon: "arrow.down.doc.fill", text: "需同步", tint: theme.ma5, emphasized: true)
        case .stale:
            return StatusBadge(icon: "exclamationmark.triangle.fill", text: "漏跑", tint: theme.ma5, emphasized: true)
        case .failed:
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", tint: theme.up, emphasized: true)
        case .disabled:
            return StatusBadge(icon: "pause.circle.fill", text: "停用", tint: theme.textSecondary)
        case .ok:
            return StatusBadge(icon: "checkmark.circle.fill", text: "正常", tint: theme.accent, emphasized: true)
        }
    }
}

/// 定时任务（launchd）面板：健康汇总 + 关机漏跑补跑 + 按分类分组（每类批量重跑）+ 行级重跑/启停。
/// U5（plan 2026-07-12-005）：迁入设置页「任务」分区，本页只保留手动任务运行台；组件本身不动。
struct ScheduledTasksSection: View {
    @Environment(\.kssTheme) private var theme
    var jobs: [ScheduledJob]
    var categoryOrder: [String]
    var busy: Set<String>
    var batchBusy: Bool
    var batchNote: String?
    var onRerun: (String) -> Void
    var onToggle: (String, Bool) -> Void
    var onSync: (String) -> Void
    var onCatchUp: () -> Void
    var onRerunMany: ([String]) -> Void
    var onDismissBatchNote: () -> Void
    /// 排期编辑（label, ScheduleStruct 新值）（U6）。
    var onEditSchedule: (String, ScheduleStruct) -> Void

    private var groups: [(category: String, jobs: [ScheduledJob])] {
        let grouped = Dictionary(grouping: jobs, by: \.category)
        return grouped
            .map { (category: $0.key, jobs: $0.value.sorted { $0.schedule < $1.schedule }) }
            .sorted { a, b in
                let ia = categoryOrder.firstIndex(of: a.category) ?? Int.max
                let ib = categoryOrder.firstIndex(of: b.category) ?? Int.max
                return ia == ib ? a.category < b.category : ia < ib
            }
    }

    private var staleJobs: [ScheduledJob] { jobs.filter { $0.stale } }

    var body: some View {
        if jobs.isEmpty {
            Text("正在读取定时任务…")
                .font(KSSFont.themed(13.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
        } else {
            VStack(spacing: 12) {
                healthSummary
                if !staleJobs.isEmpty { catchUpBanner }
                if let note = batchNote { batchNoteBar(note) }
                ForEach(groups, id: \.category) { group in
                    categoryBlock(group.category, group.jobs)
                }
            }
        }
    }

    // 健康汇总：正常 / 漏跑 / 失败 / 停用 计数。
    private var healthSummary: some View {
        let ok = jobs.filter { $0.health == .ok || $0.health == .running }.count
        let needsSync = jobs.filter { $0.health == .needsInstall }.count
        let stale = jobs.filter { $0.health == .stale }.count
        let failed = jobs.filter { $0.health == .failed }.count
        let off = jobs.filter { $0.health == .disabled }.count
        return HStack(spacing: 10) {
            healthStat("正常", ok, theme.accent)
            healthStat("待同步", needsSync, theme.ma5)
            healthStat("漏跑", stale, theme.ma5)
            healthStat("失败", failed, theme.up)
            healthStat("停用", off, theme.textSecondary)
            Spacer()
            Button {
                onRerunMany([])   // 空 = 全部启用项
            } label: {
                if batchBusy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("全部重跑", systemImage: "arrow.clockwise")
                        .font(KSSFont.themed(12, .semibold, theme: theme))
                }
            }
            .buttonStyle(.bordered)
            .disabled(batchBusy)
            .help("立即重跑所有启用的任务")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 12)
    }

    private func healthStat(_ label: String, _ n: Int, _ tint: Color) -> some View {
        HStack(spacing: 5) {
            Circle().fill(tint).frame(width: 7, height: 7)
            Text("\(label) \(n)")
                .font(KSSFont.themed(12.5, n > 0 ? .bold : .regular, theme: theme))
                .foregroundStyle(n > 0 ? theme.textPrimary : theme.textSecondary)
        }
    }

    // 关机漏跑横幅 + 一键补跑。
    private var catchUpBanner: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                .font(KSSFont.themed(18, .semibold, theme: theme))
                .foregroundStyle(theme.ma5)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(staleJobs.count) 个任务因关机漏跑")
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text(staleJobs.map(\.title).joined(separator: "、"))
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            Button(action: onCatchUp) {
                if batchBusy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("一键补跑", systemImage: "play.circle.fill")
                        .font(KSSFont.themed(13, .bold, theme: theme))
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(theme.accent)
            .disabled(batchBusy)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(.warning, padding: 12)
    }

    private func batchNoteBar(_ note: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "info.circle.fill")
                .foregroundStyle(theme.accent)
            Text(note)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Spacer()
            Button(action: onDismissBatchNote) {
                Image(systemName: "xmark.circle.fill").foregroundStyle(theme.textSecondary)
            }
            .buttonStyle(.borderless)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(.info, padding: 10)
    }

    private func categoryBlock(_ category: String, _ catJobs: [ScheduledJob]) -> some View {
        let enabledLabels = catJobs.filter(\.enabled).map(\.label)
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(category)
                    .font(KSSFont.themed(12.5, .bold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Text("\(catJobs.count)")
                    .font(KSSFont.themed(10.5, .bold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(theme.textSecondary.opacity(0.12), in: Capsule())
                Spacer()
                if !enabledLabels.isEmpty {
                    Button {
                        onRerunMany(enabledLabels)
                    } label: {
                        Label("全部重跑", systemImage: "arrow.clockwise")
                            .font(KSSFont.themed(11, .semibold, theme: theme))
                    }
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                    .disabled(batchBusy)
                    .help("重跑「\(category)」下全部启用任务")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(.filled, padding: 8)
            ForEach(catJobs) { job in
                ScheduledJobRow(
                    job: job,
                    busy: busy.contains(job.label),
                    onRerun: { onRerun(job.label) },
                    onToggle: { onToggle(job.label, $0) },
                    onSync: { onSync(job.label) },
                    onEditSchedule: { onEditSchedule(job.label, $0) }
                )
            }
        }
    }
}

struct ScheduledJobRow: View {
    @Environment(\.kssTheme) private var theme
    var job: ScheduledJob
    var busy: Bool
    var onRerun: () -> Void
    var onToggle: (Bool) -> Void
    var onSync: () -> Void
    var onEditSchedule: (ScheduleStruct) -> Void
    @State private var showEditor = false

    var body: some View {
        HStack(spacing: 12) {
            // 链成员用链条图标区分（schedule 文案已带「随 xx 触发 · 兜底 HH:MM」，R5）
            Image(systemName: job.running ? "arrow.triangle.2.circlepath"
                  : (job.triggeredBy != nil ? "link" : "clock.arrow.circlepath"))
                .font(KSSFont.themed(16, .semibold, theme: theme))
                .foregroundStyle(job.needsInstall == true || job.stale ? theme.ma5 : theme.accent)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(job.title)
                        .font(KSSFont.themed(14.5, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(job.schedule)
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 7).padding(.vertical, 1.5)
                        .background(theme.textSecondary.opacity(0.12), in: Capsule())
                }
                HStack(spacing: 8) {
                    Text(job.script)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                    if let at = job.lastRunAt {
                        Text("· 上次 \(at)")
                            .font(KSSFont.themed(11, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    if let next = job.nextRunAt, job.enabled {
                        Text("· 下次 \(next)")
                            .font(KSSFont.themed(11, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                }
                if job.needsInstall == true {
                    Text("需同步 LaunchAgent 后才能调度")
                        .font(KSSFont.themed(11, .semibold, theme: theme))
                        .foregroundStyle(theme.ma5)
                } else if job.stale {
                    Text("漏跑 \(job.missedCycles) 次" + (job.expectedAt.map { "，应跑于 \($0)" } ?? ""))
                        .font(KSSFont.themed(11, .semibold, theme: theme))
                        .foregroundStyle(theme.ma5)
                } else if let line = job.lastLine {
                    Text(line)
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer(minLength: 8)

            if job.needsInstall == true {
                Button(action: onSync) {
                    if busy {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("同步", systemImage: "arrow.down.doc.fill")
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            .labelStyle(.titleAndIcon)
                    }
                }
                .buttonStyle(.bordered)
                .disabled(busy)
                .help("立即同步 LaunchAgent")
            }

            healthBadge(job.health)

            Button {
                showEditor = true
            } label: {
                Image(systemName: "pencil.circle")
                    .font(.system(size: 15))
            }
            .buttonStyle(.borderless)
            .disabled(busy)
            .help("编辑排期")
            .popover(isPresented: $showEditor) {
                ScheduleEditorView(
                    initial: job.scheduleStruct ?? ScheduleStruct(hour: 0, minute: 0, weekdays: nil, weekly: false, weekday: nil),
                    onSave: { updated in
                        showEditor = false
                        onEditSchedule(updated)
                    },
                    onCancel: { showEditor = false }
                )
            }

            Button(action: onRerun) {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("重跑", systemImage: "play.fill")
                        .font(KSSFont.themed(12, .semibold, theme: theme))
                        .labelStyle(.titleAndIcon)
                }
            }
            .buttonStyle(.bordered)
            .disabled(busy || !job.enabled || job.needsInstall == true)
            .help(job.needsInstall == true
                  ? "需先同步 LaunchAgent"
                  : (job.enabled ? "立即重跑" : "已停用，先启用再重跑"))

            Toggle("", isOn: Binding(
                get: { job.enabled },
                set: { onToggle($0) }
            ))
            .labelsHidden()
            .toggleStyle(.switch)
            .tint(theme.accent)
            .disabled(busy || job.needsInstall == true)
            .help(job.needsInstall == true
                  ? "需先同步 LaunchAgent"
                  : (job.enabled ? "点按停用（下次调度不再触发）" : "点按启用"))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 11)
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                .strokeBorder(job.stale ? theme.ma5.opacity(0.4) : .clear, lineWidth: 1)
        )
        .opacity(job.enabled ? 1 : 0.55)   // 停用态整行降透明
    }

    /// 综合健康徽标：运行中 / 漏跑(N) / 失败 / 停用 / 正常。
    private func healthBadge(_ health: ScheduledJob.Health) -> StatusBadge {
        switch health {
        case .running:
            return StatusBadge(icon: "arrow.triangle.2.circlepath", text: "运行中", tint: theme.accent, emphasized: true)
        case .needsInstall:
            return StatusBadge(icon: "arrow.down.doc.fill", text: "需同步", tint: theme.ma5, emphasized: true)
        case .stale:
            return StatusBadge(icon: "exclamationmark.triangle.fill", text: "漏跑\(job.missedCycles)", tint: theme.ma5, emphasized: true)
        case .failed:
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", tint: theme.up, emphasized: true)
        case .disabled:
            return StatusBadge(icon: "pause.circle.fill", text: "停用", tint: theme.textSecondary)
        case .ok:
            return StatusBadge(icon: "checkmark.circle.fill", text: "正常", tint: theme.accent, emphasized: true)
        }
    }
}

/// 排期编辑器（popover，U6）：hour/minute 选择器 + daily 工作日多选 / weekly 单 weekday。
struct ScheduleEditorView: View {
    @Environment(\.kssTheme) private var theme
    var onSave: (ScheduleStruct) -> Void
    var onCancel: () -> Void

    @State private var hour: Int
    @State private var minute: Int
    @State private var weekly: Bool
    @State private var selectedWeekdays: Set<Int>   // daily 形态：空集＝每天
    @State private var weeklyWeekday: Int

    private static let weekdayLabels = ["一", "二", "三", "四", "五", "六", "日"]  // launchd 1-7

    init(initial: ScheduleStruct, onSave: @escaping (ScheduleStruct) -> Void, onCancel: @escaping () -> Void) {
        self.onSave = onSave
        self.onCancel = onCancel
        _hour = State(initialValue: initial.hour)
        _minute = State(initialValue: initial.minute)
        _weekly = State(initialValue: initial.weekly)
        _selectedWeekdays = State(initialValue: Set(initial.weekdays ?? []))
        _weeklyWeekday = State(initialValue: initial.weekday ?? 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("编辑排期").font(KSSFont.themed(14, .bold, theme: theme))

            HStack(spacing: 10) {
                Stepper(value: $hour, in: 0...23) {
                    Text(String(format: "%02d 时", hour))
                        .font(.system(size: 13, design: .monospaced))
                }
                Stepper(value: $minute, in: 0...59) {
                    Text(String(format: "%02d 分", minute))
                        .font(.system(size: 13, design: .monospaced))
                }
            }

            Picker("形态", selection: $weekly) {
                Text("每天/工作日").tag(false)
                Text("每周一次").tag(true)
            }
            .pickerStyle(.segmented)
            .frame(width: 220)

            if weekly {
                Picker("星期", selection: $weeklyWeekday) {
                    ForEach(1...7, id: \.self) { d in
                        Text("周\(Self.weekdayLabels[d - 1])").tag(d)
                    }
                }
                .pickerStyle(.menu)
            } else {
                Text("勾选生效的工作日；不勾＝每天")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                HStack(spacing: 4) {
                    ForEach(1...7, id: \.self) { d in
                        weekdayChip(d)
                    }
                }
            }

            HStack {
                Spacer()
                Button("取消", action: onCancel)
                Button("保存") {
                    let result = ScheduleStruct(
                        hour: hour, minute: minute,
                        weekdays: weekly ? nil : (selectedWeekdays.isEmpty ? nil : selectedWeekdays.sorted()),
                        weekly: weekly,
                        weekday: weekly ? weeklyWeekday : nil
                    )
                    onSave(result)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(16)
        .frame(width: 280)
    }

    private func weekdayChip(_ day: Int) -> some View {
        let on = selectedWeekdays.contains(day)
        return Button {
            if on { selectedWeekdays.remove(day) } else { selectedWeekdays.insert(day) }
        } label: {
            Text(Self.weekdayLabels[day - 1])
                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                .frame(width: 22, height: 22)
                .foregroundStyle(on ? theme.onAccent : theme.textSecondary)
                .background(on ? theme.accent : theme.textSecondary.opacity(0.12), in: Circle())
        }
        .buttonStyle(.plain)
    }
}

struct TaskResultCard: View {
    @Environment(\.kssTheme) private var theme
    var result: TaskRunResult
    var compact: Bool = false
    @State private var showOutput = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(result.title)
                    .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                StatusBadge.task(result.status)
            }
            Text(result.summary)
                .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                .foregroundStyle(theme.textPrimary)
            if !result.artifacts.isEmpty {
                Text(result.artifacts.joined(separator: "  "))
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if !result.stdout.isEmpty || !result.stderr.isEmpty {
                if compact {
                    DisclosureGroup("输出", isExpanded: $showOutput) {
                        outputBlock
                    }
                    .font(KSSFont.themed(12.5, theme: theme))
                } else {
                    outputBlock
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: SettingsFormStyle.cardPadding)
    }

    @ViewBuilder
    private var outputBlock: some View {
        if !result.stdout.isEmpty {
            Text(result.stdout)
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundStyle(theme.textPrimary)
                .textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(theme.canvas, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        }
        if !result.stderr.isEmpty {
            Text(result.stderr)
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundStyle(theme.up)
                .textSelection(.enabled)
        }
    }
}
