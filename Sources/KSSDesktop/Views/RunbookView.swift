import SwiftUI

struct RunbookView: View {
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
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                PythonEnvironmentBanner(environment: pythonEnvironment)

                SectionHeader("轻量任务")
                TaskGrid(tasks: quickTasks, isRunning: isRunning, onRun: onRun)

                SectionHeader("正式任务")
                TaskGrid(tasks: fullTasks, isRunning: isRunning, onRun: onRun)

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
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
        .navigationTitle("任务")
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
