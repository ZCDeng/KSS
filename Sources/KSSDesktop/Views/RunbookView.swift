import SwiftUI

struct RunbookView: View {
    var pythonEnvironment: PythonEnvironment?
    var isRunning: Bool
    var results: [TaskRunResult]
    var scheduledJobs: [ScheduledJob]
    var scheduledBusy: Set<String>
    var onRun: (KSSTask) -> Void
    var onLoadSchedules: () -> Void
    var onRerunSchedule: (String) -> Void
    var onToggleSchedule: (String, Bool) -> Void

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
                        onRerun: onRerunSchedule,
                        onToggle: onToggleSchedule
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

/// 定时任务（launchd）面板：调度 / 上次运行 / 重跑 / 启停。
struct ScheduledTasksSection: View {
    var jobs: [ScheduledJob]
    var busy: Set<String>
    var onRerun: (String) -> Void
    var onToggle: (String, Bool) -> Void

    var body: some View {
        if jobs.isEmpty {
            Text("正在读取定时任务…")
                .font(.system(size: 13.5))
                .foregroundStyle(KSSTheme.textSecondary)
        } else {
            VStack(spacing: 8) {
                ForEach(jobs) { job in
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
}

struct ScheduledJobRow: View {
    var job: ScheduledJob
    var busy: Bool
    var onRerun: () -> Void
    var onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(KSSTheme.accent)
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
                }
                if let line = job.lastLine {
                    Text(line)
                        .font(.system(size: 11))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer(minLength: 8)

            scheduledStatusBadge(job.lastStatus)

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
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: 10))
        .opacity(job.enabled ? 1 : 0.55)   // 停用态整行降透明
    }

    /// launchd 上次退出态：成功/失败/未知（未知含本次开机后未运行）。
    private func scheduledStatusBadge(_ status: String) -> StatusBadge {
        switch status {
        case "success":
            return StatusBadge(icon: "checkmark.circle.fill", text: "成功", tint: KSSTheme.accent, emphasized: true)
        case "failed":
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", tint: KSSTheme.up, emphasized: true)
        default:
            return StatusBadge(icon: "clock.badge.questionmark", text: "未知", tint: KSSTheme.textSecondary)
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
                    .background(KSSTheme.canvas, in: RoundedRectangle(cornerRadius: 6))
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
