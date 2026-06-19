import SwiftUI

enum BacktestSort: String, CaseIterable, Identifiable {
    case updated = "更新时间"
    case title = "标题"
    var id: String { rawValue }
}

struct BacktestsView: View {
    var reports: [BacktestReport]
    var tracking: TrackingSummary
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReport: (String) -> Void

    @State private var selectedReport: BacktestReport?
    @State private var sort: BacktestSort = .updated
    @State private var ascending = false

    private var sortedReports: [BacktestReport] {
        reports.sorted { a, b in
            let lhs: String, rhs: String
            switch sort {
            case .updated: lhs = a.updatedAt; rhs = b.updatedAt
            case .title: lhs = a.title; rhs = b.title
            }
            return ascending ? lhs < rhs : lhs > rhs
        }
    }

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                HStack {
                    SortControl(
                        options: BacktestSort.allCases.map { ($0, $0.rawValue) },
                        selection: $sort,
                        ascending: $ascending
                    )
                    Spacer()
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)

                List(sortedReports, selection: $selectedReport) { report in
                    BacktestReportRow(report: report)
                        .tag(report)
                }
                .scrollContentBackground(.hidden)
                .background(KSSTheme.canvas)
            }
            .navigationSplitViewColumnWidth(min: 280, ideal: 340)
        } detail: {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    StatTile(title: "日志天数", value: "\(tracking.nDaysLogged)")
                    StatTile(title: "可评估天数", value: "\(tracking.nDaysWithReturns)")
                    StatTile(title: "Sharpe", value: KSSFormat.number(tracking.sharpe), tint: KSSTheme.signColor(tracking.sharpe))
                    StatTile(title: "胜率", value: KSSFormat.percent(tracking.winRate))
                }

                if let selectedReport {
                    BacktestDetailHeader(report: selectedReport)
                    if isLoadingDetail && selectedPath == selectedReport.path {
                        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if detail?.path == selectedReport.path, let detail {
                        MarkdownWebView(text: detail.text)
                            .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                            .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
                    } else {
                        MarkdownWebView(text: selectedReport.excerpt)
                            .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                            .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
                    }
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "chart.xyaxis.line")
                            .font(.largeTitle)
                            .foregroundStyle(KSSTheme.textSecondary)
                        Text("选择一份回测/分析报告")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(KSSTheme.textPrimary)
                        Text("在左侧挑选报告，查看完整 Markdown 证据。")
                            .font(.system(size: 13))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(KSSTheme.canvas)
        }
        .navigationTitle("回测")
        .onAppear {
            if selectedReport == nil {
                selectedReport = sortedReports.first
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
        VStack(alignment: .leading, spacing: 4) {
            Text(report.title)
                .font(.system(size: 14.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(2)
            HStack(spacing: 6) {
                Image(systemName: "clock")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(KSSTheme.textSecondary)
                Text(report.updatedAt)
                    .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            if let firstMetric = report.metrics.first {
                Text("\(firstMetric.name): \(firstMetric.value)")
                    .font(.system(size: 11.5))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 3)
    }
}

struct BacktestCard: View {
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(report.title)
                .font(.system(size: 15.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(2)
            Text(report.updatedAt)
                .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
            ForEach(report.metrics.prefix(4), id: \.self) { metric in
                HStack {
                    Text(metric.name)
                        .font(.system(size: 13))
                        .foregroundStyle(KSSTheme.textSecondary)
                    Spacer()
                    Text(metric.value)
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundStyle(KSSTheme.textPrimary)
                }
            }
            Text(report.excerpt)
                .font(.system(size: 12))
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
        VStack(alignment: .leading, spacing: 8) {
            Text(report.title)
                .font(.system(size: 21, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
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
