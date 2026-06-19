import SwiftUI

struct BacktestsView: View {
    var reports: [BacktestReport]
    var tracking: TrackingSummary
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReport: (String) -> Void

    @State private var selectedReport: BacktestReport?

    var body: some View {
        NavigationSplitView {
            List(reports, selection: $selectedReport) { report in
                BacktestReportRow(report: report)
                    .tag(report)
            }
            .navigationSplitViewColumnWidth(min: 280, ideal: 340)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(spacing: 12) {
                        StatTile(title: "日志天数", value: "\(tracking.nDaysLogged)")
                        StatTile(title: "可评估天数", value: "\(tracking.nDaysWithReturns)")
                        StatTile(title: "Sharpe", value: KSSFormat.number(tracking.sharpe), tint: KSSTheme.signColor(tracking.sharpe))
                        StatTile(title: "胜率", value: KSSFormat.percent(tracking.winRate))
                    }

                    if let selectedReport {
                        BacktestDetailHeader(report: selectedReport)
                        if isLoadingDetail && selectedPath == selectedReport.path {
                            ProgressView()
                        } else if detail?.path == selectedReport.path, let detail {
                            ReportTextView(detail: detail)
                        } else {
                            Text(selectedReport.excerpt)
                                .font(.body)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    } else {
                        VStack(spacing: 10) {
                            Image(systemName: "chart.xyaxis.line")
                                .font(.largeTitle)
                                .foregroundStyle(.secondary)
                            Text("Select a report")
                                .font(.headline)
                            Text("Choose a backtest or analysis report to inspect the full markdown evidence.")
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 260)
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
        .navigationTitle("Backtests")
        .onAppear {
            if selectedReport == nil {
                selectedReport = reports.first
            }
            if let selectedReport, detail?.path != selectedReport.path {
                onSelectReport(selectedReport.path)
            }
        }
        .onChange(of: selectedReport) { report in
            if let report {
                onSelectReport(report.path)
            }
        }
    }
}

struct BacktestReportRow: View {
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(report.title)
                .font(.headline)
                .lineLimit(2)
            Text(report.updatedAt)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            if let firstMetric = report.metrics.first {
                Text("\(firstMetric.name): \(firstMetric.value)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 4)
    }
}

struct BacktestCard: View {
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(report.title)
                .font(.headline)
                .lineLimit(2)
            Text(report.updatedAt)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            ForEach(report.metrics.prefix(4), id: \.self) { metric in
                HStack {
                    Text(metric.name)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(metric.value)
                        .font(.callout.monospacedDigit())
                }
            }
            Text(report.excerpt)
                .font(.caption)
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct BacktestDetailHeader: View {
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(report.title)
                .font(.title2.weight(.semibold))
                .textSelection(.enabled)
            Text(report.path)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            if !report.metrics.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 10)], spacing: 10) {
                    ForEach(report.metrics, id: \.self) { metric in
                        LabeledMetric(metric.name, metric.value)
                    }
                }
            }
        }
    }
}

struct ReportTextView: View {
    var detail: ReportDetail

    var body: some View {
        Text(detail.text)
            .font(.system(.callout, design: .monospaced))
            .foregroundStyle(KSSTheme.textPrimary)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: 14)
    }
}
