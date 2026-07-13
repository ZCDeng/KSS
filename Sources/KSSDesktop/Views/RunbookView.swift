import SwiftUI

struct RunbookView: View {
    @Environment(\.kssTheme) private var theme
    var pythonEnvironment: PythonEnvironment?
    var isRunning: Bool
    var results: [TaskRunResult]
    var onRun: (KSSTask) -> Void

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
                    PageTitle("任务台", subtitle: "本地数据 / 正式脚本运行台")
                    PythonEnvironmentBanner(environment: pythonEnvironment)

                    SectionHeader("轻量任务")
                    TaskGrid(tasks: quickTasks, isRunning: isRunning, onRun: onRun)

                    SectionHeader("正式任务")
                    TaskGrid(tasks: fullTasks, isRunning: isRunning, onRun: onRun)

                    SectionHeader("任务记录")
                    if results.isEmpty {
                        Text("暂无任务运行记录")
                            .font(KSSFont.themed(13.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
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
            .background(theme.canvas)
        }
        .background(theme.canvas)
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
            Image(systemName: job.running ? "arrow.triangle.2.circlepath" : "clock.arrow.circlepath")
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

struct PythonEnvironmentBanner: View {
    @Environment(\.kssTheme) private var theme
    var environment: PythonEnvironment?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: environment?.usable == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(KSSFont.themed(18, theme: theme))
                .foregroundStyle(environment?.usable == true ? theme.accent : theme.ma5)
            VStack(alignment: .leading, spacing: 3) {
                Text(environment?.usable == true ? "正式 Python 环境就绪" : "正式 Python 环境不可用")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text(environment?.selected ?? "缺少 pandas / lightgbm / tushare / akshare 解释器")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct TaskGrid: View {
    @Environment(\.kssTheme) private var theme
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
                            .foregroundStyle(theme.accent)
                            .frame(width: 24)
                        Text(task.title)
                            .font(KSSFont.themed(14, .bold, theme: theme))
                            .lineLimit(1)
                        Spacer()
                        if isRunning {
                            ProgressView()
                                .controlSize(.small)
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 58, alignment: .leading)
                    .kssCard(padding: 14)
                }
                .buttonStyle(.plain)
                .disabled(isRunning)
            }
        }
    }
}

struct TaskResultCard: View {
    @Environment(\.kssTheme) private var theme
    var result: TaskRunResult

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(result.title)
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                StatusBadge.task(result.status)
            }
            Text(result.summary)
                .font(KSSFont.themed(13.5, theme: theme))
                .foregroundStyle(theme.textPrimary)
            if !result.artifacts.isEmpty {
                Text(result.artifacts.joined(separator: "  "))
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
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
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}
