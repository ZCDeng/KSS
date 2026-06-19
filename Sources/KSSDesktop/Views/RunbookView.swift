import SwiftUI

struct RunbookView: View {
    var pythonEnvironment: PythonEnvironment?
    var isRunning: Bool
    var results: [TaskRunResult]
    var onRun: (KSSTask) -> Void

    private var quickTasks: [KSSTask] {
        KSSTask.allCases.filter { $0.lane == "Quick" }
    }

    private var fullTasks: [KSSTask] {
        KSSTask.allCases.filter { $0.lane == "Full" }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PythonEnvironmentBanner(environment: pythonEnvironment)

                SectionHeader("Quick")
                TaskGrid(tasks: quickTasks, isRunning: isRunning, onRun: onRun)

                SectionHeader("Full")
                TaskGrid(tasks: fullTasks, isRunning: isRunning, onRun: onRun)

                SectionHeader("Task Log")
                if results.isEmpty {
                    Text("No task runs yet")
                        .foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 12) {
                        ForEach(results) { result in
                            TaskResultCard(result: result)
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
        .navigationTitle("Runbook")
    }
}

struct PythonEnvironmentBanner: View {
    var environment: PythonEnvironment?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: environment?.usable == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(environment?.usable == true ? .green : .orange)
            VStack(alignment: .leading, spacing: 3) {
                Text(environment?.usable == true ? "Full Python environment ready" : "Full Python environment unavailable")
                    .font(.headline)
                Text(environment?.selected ?? "No interpreter with pandas/lightgbm/tushare/akshare")
                    .font(.caption.monospaced())
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
                            .frame(width: 24)
                        Text(task.title)
                            .font(.headline)
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

    private var statusColor: Color {
        switch result.status {
        case "success": return KSSTheme.down
        case "skipped": return KSSTheme.ma5
        default: return KSSTheme.up
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle()
                    .fill(statusColor)
                    .frame(width: 9, height: 9)
                Text(result.title)
                    .font(.headline)
                Spacer()
                Text(result.status)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            Text(result.summary)
                .font(.callout)
            if !result.artifacts.isEmpty {
                Text(result.artifacts.joined(separator: "  "))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            if !result.stdout.isEmpty {
                Text(result.stdout)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
            }
            if !result.stderr.isEmpty {
                Text(result.stderr)
                    .font(.caption.monospaced())
                    .foregroundStyle(KSSTheme.up)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}
