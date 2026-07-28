import SwiftUI

enum BacktestSort: String, CaseIterable, Identifiable {
    case updated = "更新时间"
    case title = "标题"
    var id: String { rawValue }
}

struct BacktestsView: View {
    @Environment(\.kssTheme) private var theme
    var reports: [BacktestReport]
    var tracking: TrackingSummary
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReport: (String) -> Void
    var onOpenExternally: (String) -> Void

    @State private var selectedReport: BacktestReport?
    @State private var sort: BacktestSort = .updated
    @State private var ascending = false
    @State private var hoveredReportID: String?

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

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
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                listToolbar
                List(sortedReports) { report in
                    let isOn = selectedReport?.id == report.id
                    Button { selectedReport = report } label: {
                        BacktestReportRow(report: report)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .listRowBackground(
                        XcomListChrome.listSelectionFill(
                            isOn: isOn,
                            isHovered: isXcom && hoveredReportID == report.id,
                            theme: theme
                        )
                    )
                    .listRowSeparator(isXcom ? .visible : .automatic)
                    .onHover { hovering in
                        guard isXcom else { return }
                        hoveredReportID = hovering ? report.id : (hoveredReportID == report.id ? nil : hoveredReportID)
                    }
                }
                .scrollContentBackground(.hidden)
                .background(theme.canvas)
            }
            .frame(width: XcomListChrome.listColumnWidth(theme.system))

            Divider().overlay(theme.hairline)

            detailColumn
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                .background(theme.canvas)
        }
        .background(theme.canvas)
        .onAppear {
            if selectedReport == nil {
                selectedReport = sortedReports.first
            }
            if let selectedReport, detail?.path != selectedReport.path {
                onSelectReport(selectedReport.path)
            }
        }
        .onChange(of: selectedReport) { _, report in
            if let report {
                onSelectReport(report.path)
            }
        }
    }

    private var listToolbar: some View {
        VStack(spacing: 0) {
            if isXcom {
                Text("AI回测")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.top, 14)
                    .padding(.bottom, 6)
            }
            HStack {
                SortControl(
                    options: BacktestSort.allCases.map { ($0, $0.rawValue) },
                    selection: $sort,
                    ascending: $ascending
                )
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, isXcom ? 6 : 8)

            if !isXcom {
                HStack(spacing: 6) {
                    SortHeaderCell(title: "标题", key: BacktestSort.title, selection: $sort, ascending: $ascending,
                                   alignment: .leading)
                    SortHeaderCell(title: "更新时间", key: BacktestSort.updated, selection: $sort, ascending: $ascending,
                                   alignment: .trailing, width: 96)
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 4)
            } else {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }
        }
    }

    private var detailColumn: some View {
        VStack(alignment: .leading, spacing: isXcom ? 14 : 12) {
            if isXcom {
                VStack(alignment: .leading, spacing: 4) {
                    Text(selectedReport?.title ?? "AI回测")
                        .font(KSSFont.themed(XcomListChrome.detailTitlePointSize(theme.system), .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .textSelection(.enabled)
                    Text("跟踪 · \(tracking.nDaysLogged) 日日志 · \(tracking.nDaysWithReturns) 日可评估")
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
            } else {
                PageTitle("AI回测", subtitle: selectedReport?.title)
            }

            HStack(spacing: 10) {
                StatTile(title: "日志天数", value: "\(tracking.nDaysLogged)")
                StatTile(title: "可评估天数", value: "\(tracking.nDaysWithReturns)")
                StatTile(title: "Sharpe", value: KSSFormat.number(tracking.sharpe), tint: theme.signColor(tracking.sharpe))
                StatTile(title: "胜率", value: KSSFormat.percent(tracking.winRate))
            }

            if let selectedReport {
                HStack(alignment: .firstTextBaseline) {
                    if !isXcom {
                        BacktestDetailHeader(report: selectedReport)
                    } else if !selectedReport.metrics.isEmpty {
                        BacktestDetailHeader(report: selectedReport)
                    }
                    Spacer()
                    if isXcom {
                        Button { onOpenExternally(selectedReport.path) } label: {
                            Image(systemName: "arrow.up.right.square")
                                .font(KSSFont.themed(14, .semibold, theme: theme))
                                .foregroundStyle(theme.accent)
                        }
                        .buttonStyle(.plain)
                        .help("用 MarkEdit 打开当前报告")
                    } else {
                        Button { onOpenExternally(selectedReport.path) } label: {
                            Image(systemName: "doc.text")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .tint(theme.accent)
                        .help("用 MarkEdit 打开当前报告")
                    }
                }
                if isLoadingDetail && selectedPath == selectedReport.path {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if detail?.path == selectedReport.path, let detail {
                    MarkdownWebView(text: detail.text)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius))
                        .overlay(
                            RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius)
                                .stroke(theme.hairline)
                        )
                } else {
                    MarkdownWebView(text: selectedReport.excerpt)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius))
                        .overlay(
                            RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius)
                                .stroke(theme.hairline)
                        )
                }
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "chart.xyaxis.line")
                        .font(.largeTitle)
                        .foregroundStyle(theme.textSecondary)
                    Text("选择一份回测/分析报告")
                        .font(KSSFont.themed(16, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(isXcom ? 20 : 16)
    }
}

struct BacktestReportRow: View {
    @Environment(\.kssTheme) private var theme
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(report.title)
                .font(KSSFont.themed(
                    XcomListChrome.isXcom(theme.system) ? 15 : 14.5,
                    .bold,
                    theme: theme
                ))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(2)
            HStack(spacing: 6) {
                Image(systemName: "clock")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Text(report.updatedAt)
                    .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if let firstMetric = report.metrics.first {
                Text("\(firstMetric.name): \(firstMetric.value)")
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 3)
    }
}

struct BacktestCard: View {
    @Environment(\.kssTheme) private var theme
    var report: BacktestReport

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(report.title)
                .font(KSSFont.themed(15.5, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(2)
            Text(report.updatedAt)
                .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            ForEach(report.metrics.prefix(4), id: \.self) { metric in
                HStack {
                    Text(metric.name)
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                    Text(metric.value)
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textPrimary)
                }
            }
            Text(report.excerpt)
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(3)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, minHeight: 188, maxHeight: 188, alignment: .topLeading)
        .kssCard(padding: 14)
    }
}

struct BacktestDetailHeader: View {
    @Environment(\.kssTheme) private var theme
    var report: BacktestReport

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !isXcom {
                Text(report.title)
                    .font(KSSFont.themed(21, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .textSelection(.enabled)
            }
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
