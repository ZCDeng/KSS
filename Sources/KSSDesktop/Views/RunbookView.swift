import SwiftUI

struct RunbookView: View {
    var pythonEnvironment: PythonEnvironment?
    var isRunning: Bool
    var results: [TaskRunResult]
    var scheduledJobs: [ScheduledJob]
    var scheduledBusy: Set<String>
    var scheduledBatchBusy: Bool
    var scheduledBatchNote: String?
    var onRun: (KSSTask) -> Void
    var onLoadSchedules: () -> Void
    var onRerunSchedule: (String) -> Void
    var onToggleSchedule: (String, Bool) -> Void
    var onCatchUp: () -> Void
    var onRerunMany: ([String]) -> Void
    var onDismissBatchNote: () -> Void

    private var quickTasks: [KSSTask] {
        KSSTask.allCases.filter { $0.lane == "轻量" }
    }

    private var fullTasks: [KSSTask] {
        KSSTask.allCases.filter { $0.lane == "正式" }
    }

    var body: some View {
        // M3：内容封顶 1080 居中，统一外边距（与总览一致）。
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageTitle("任务", subtitle: "本地数据 / 正式脚本运行台")
                    PythonEnvironmentBanner(environment: pythonEnvironment)

                    SectionHeader("轻量任务")
                    TaskGrid(tasks: quickTasks, isRunning: isRunning, onRun: onRun)

                    SectionHeader("正式任务")
                    TaskGrid(tasks: fullTasks, isRunning: isRunning, onRun: onRun)

                    SectionHeader("定时任务")
                    ScheduledTasksSection(
                        jobs: scheduledJobs,
                        busy: scheduledBusy,
                        batchBusy: scheduledBatchBusy,
                        batchNote: scheduledBatchNote,
                        onRerun: onRerunSchedule,
                        onToggle: onToggleSchedule,
                        onCatchUp: onCatchUp,
                        onRerunMany: onRerunMany,
                        onDismissBatchNote: onDismissBatchNote
                    )

                    SectionHeader("任务记录")
                    if results.isEmpty {
                        Text("暂无任务运行记录")
                            .font(.system(size: 13.5))
                            .foregroundStyle(KSSTheme.textSecondary)
                    } else {
                        VStack(spacing: 10) {
                            ForEach(results) { result in
                                TaskResultCard(result: result)
                            }
                        }
                    }
                }
                .frame(width: w, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
        .task { onLoadSchedules() }
    }
}

/// 定时任务（launchd）面板：健康汇总 + 关机漏跑补跑 + 按分类分组（每类批量重跑）+ 行级重跑/启停。
struct ScheduledTasksSection: View {
    var jobs: [ScheduledJob]
    var busy: Set<String>
    var batchBusy: Bool
    var batchNote: String?
    var onRerun: (String) -> Void
    var onToggle: (String, Bool) -> Void
    var onCatchUp: () -> Void
    var onRerunMany: ([String]) -> Void
    var onDismissBatchNote: () -> Void

    /// 分类顺序（与 bridge LABEL_CATEGORY/CATEGORY_ORDER 对齐；未列出的排末尾）。
    private static let categoryOrder = ["数据更新", "扫描选股", "板块复盘", "盘中快讯", "纸交易", "校验回测", "系统", "其他"]

    private var groups: [(category: String, jobs: [ScheduledJob])] {
        let grouped = Dictionary(grouping: jobs, by: \.category)
        return grouped
            .map { (category: $0.key, jobs: $0.value.sorted { $0.schedule < $1.schedule }) }
            .sorted { a, b in
                let ia = Self.categoryOrder.firstIndex(of: a.category) ?? Int.max
                let ib = Self.categoryOrder.firstIndex(of: b.category) ?? Int.max
                return ia == ib ? a.category < b.category : ia < ib
            }
    }

    private var staleJobs: [ScheduledJob] { jobs.filter { $0.stale } }

    var body: some View {
        if jobs.isEmpty {
            Text("正在读取定时任务…")
                .font(.system(size: 13.5))
                .foregroundStyle(KSSTheme.textSecondary)
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
        let stale = jobs.filter { $0.health == .stale }.count
        let failed = jobs.filter { $0.health == .failed }.count
        let off = jobs.filter { $0.health == .disabled }.count
        return HStack(spacing: 10) {
            healthStat("正常", ok, KSSTheme.accent)
            healthStat("漏跑", stale, KSSTheme.ma5)
            healthStat("失败", failed, KSSTheme.up)
            healthStat("停用", off, KSSTheme.textSecondary)
            Spacer()
            Button {
                onRerunMany([])   // 空 = 全部启用项
            } label: {
                if batchBusy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("全部重跑", systemImage: "arrow.clockwise")
                        .font(.system(size: 12, weight: .semibold))
                }
            }
            .buttonStyle(.bordered)
            .disabled(batchBusy)
            .help("立即重跑所有启用的任务")
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(KSSTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }

    private func healthStat(_ label: String, _ n: Int, _ tint: Color) -> some View {
        HStack(spacing: 5) {
            Circle().fill(tint).frame(width: 7, height: 7)
            Text("\(label) \(n)")
                .font(.system(size: 12.5, weight: n > 0 ? .bold : .regular))
                .foregroundStyle(n > 0 ? KSSTheme.textPrimary : KSSTheme.textSecondary)
        }
    }

    // 关机漏跑横幅 + 一键补跑。
    private var catchUpBanner: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(KSSTheme.ma5)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(staleJobs.count) 个任务因关机漏跑")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                Text(staleJobs.map(\.title).joined(separator: "、"))
                    .font(.system(size: 11.5))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            Button(action: onCatchUp) {
                if batchBusy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("一键补跑", systemImage: "play.circle.fill")
                        .font(.system(size: 13, weight: .bold))
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(KSSTheme.accent)
            .disabled(batchBusy)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(KSSTheme.ma5.opacity(0.10), in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                .strokeBorder(KSSTheme.ma5.opacity(0.35), lineWidth: 1)
        )
    }

    private func batchNoteBar(_ note: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "info.circle.fill")
                .foregroundStyle(KSSTheme.accent)
            Text(note)
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textPrimary)
            Spacer()
            Button(action: onDismissBatchNote) {
                Image(systemName: "xmark.circle.fill").foregroundStyle(KSSTheme.textSecondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(KSSTheme.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
    }

    private func categoryBlock(_ category: String, _ catJobs: [ScheduledJob]) -> some View {
        let enabledLabels = catJobs.filter(\.enabled).map(\.label)
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(category)
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundStyle(KSSTheme.textSecondary)
                Text("\(catJobs.count)")
                    .font(.system(size: 10.5, weight: .bold))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(KSSTheme.textSecondary.opacity(0.12), in: Capsule())
                Spacer()
                if !enabledLabels.isEmpty {
                    Button {
                        onRerunMany(enabledLabels)
                    } label: {
                        Label("全部重跑", systemImage: "arrow.clockwise")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                    .disabled(batchBusy)
                    .help("重跑「\(category)」下全部启用任务")
                }
            }
            ForEach(catJobs) { job in
                ScheduledJobRow(
                    job: job,
                    busy: busy.contains(job.label),
                    onRerun: { onRerun(job.label) },
                    onToggle: { onToggle(job.label, $0) }
                )
            }
        }
    }
}

struct ScheduledJobRow: View {
    var job: ScheduledJob
    var busy: Bool
    var onRerun: () -> Void
    var onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: job.running ? "arrow.triangle.2.circlepath" : "clock.arrow.circlepath")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(job.stale ? KSSTheme.ma5 : KSSTheme.accent)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(job.title)
                        .font(.system(size: 14.5, weight: .bold))
                        .foregroundStyle(KSSTheme.textPrimary)
                    Text(job.schedule)
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .padding(.horizontal, 7).padding(.vertical, 1.5)
                        .background(KSSTheme.textSecondary.opacity(0.12), in: Capsule())
                }
                HStack(spacing: 8) {
                    Text(job.script)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(KSSTheme.textSecondary)
                    if let at = job.lastRunAt {
                        Text("· 上次 \(at)")
                            .font(.system(size: 11))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                    if let next = job.nextRunAt, job.enabled {
                        Text("· 下次 \(next)")
                            .font(.system(size: 11))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                }
                if job.stale {
                    Text("漏跑 \(job.missedCycles) 次" + (job.expectedAt.map { "，应跑于 \($0)" } ?? ""))
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(KSSTheme.ma5)
                } else if let line = job.lastLine {
                    Text(line)
                        .font(.system(size: 11))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer(minLength: 8)

            healthBadge(job.health)

            Button(action: onRerun) {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Label("重跑", systemImage: "play.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .labelStyle(.titleAndIcon)
                }
            }
            .buttonStyle(.bordered)
            .disabled(busy || !job.enabled)
            .help(job.enabled ? "立即重跑" : "已停用，先启用再重跑")

            Toggle("", isOn: Binding(
                get: { job.enabled },
                set: { onToggle($0) }
            ))
            .labelsHidden()
            .toggleStyle(.switch)
            .tint(KSSTheme.accent)
            .disabled(busy)
            .help(job.enabled ? "点按停用（下次调度不再触发）" : "点按启用")
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(KSSTheme.surfaceContainer, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                .strokeBorder(job.stale ? KSSTheme.ma5.opacity(0.4) : .clear, lineWidth: 1)
        )
        .opacity(job.enabled ? 1 : 0.55)   // 停用态整行降透明
    }

    /// 综合健康徽标：运行中 / 漏跑(N) / 失败 / 停用 / 正常。
    private func healthBadge(_ health: ScheduledJob.Health) -> StatusBadge {
        switch health {
        case .running:
            return StatusBadge(icon: "arrow.triangle.2.circlepath", text: "运行中", tint: KSSTheme.accent, emphasized: true)
        case .stale:
            return StatusBadge(icon: "exclamationmark.triangle.fill", text: "漏跑\(job.missedCycles)", tint: KSSTheme.ma5, emphasized: true)
        case .failed:
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", tint: KSSTheme.up, emphasized: true)
        case .disabled:
            return StatusBadge(icon: "pause.circle.fill", text: "停用", tint: KSSTheme.textSecondary)
        case .ok:
            return StatusBadge(icon: "checkmark.circle.fill", text: "正常", tint: KSSTheme.accent, emphasized: true)
        }
    }
}

struct PythonEnvironmentBanner: View {
    var environment: PythonEnvironment?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: environment?.usable == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 18))
                .foregroundStyle(environment?.usable == true ? KSSTheme.accent : KSSTheme.ma5)
            VStack(alignment: .leading, spacing: 3) {
                Text(environment?.usable == true ? "正式 Python 环境就绪" : "正式 Python 环境不可用")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                Text(environment?.selected ?? "缺少 pandas / lightgbm / tushare / akshare 解释器")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct TaskGrid: View {
    var tasks: [KSSTask]
    var isRunning: Bool
    var onRun: (KSSTask) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 12)], spacing: 12) {
            ForEach(tasks) { task in
                Button {
                    onRun(task)
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: task.systemImage)
                            .font(.title3)
                            .foregroundStyle(KSSTheme.accent)
                            .frame(width: 24)
                        Text(task.title)
                            .font(.system(size: 14, weight: .bold))
                            .lineLimit(1)
                        Spacer()
                        if isRunning {
                            ProgressView()
                                .controlSize(.small)
                        }
                    }
                    .padding(14)
                    .frame(maxWidth: .infinity, minHeight: 58, alignment: .leading)
                }
                .buttonStyle(.bordered)
                .disabled(isRunning)
            }
        }
    }
}

struct TaskResultCard: View {
    var result: TaskRunResult

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(result.title)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                Spacer()
                StatusBadge.task(result.status)
            }
            Text(result.summary)
                .font(.system(size: 13.5))
                .foregroundStyle(KSSTheme.textPrimary)
            if !result.artifacts.isEmpty {
                Text(result.artifacts.joined(separator: "  "))
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            if !result.stdout.isEmpty {
                Text(result.stdout)
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.textPrimary)
                    .textSelection(.enabled)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(KSSTheme.canvas, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
            }
            if !result.stderr.isEmpty {
                Text(result.stderr)
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.up)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}
