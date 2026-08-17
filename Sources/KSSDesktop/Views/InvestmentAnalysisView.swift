import SwiftUI
import UniformTypeIdentifiers

/// 日报与周报档案。页面只允许用户显式导入本地受控语料；研究执行、重试和
/// 发布仍留在任务台，避免读取报告时意外启动模型或工具。
struct InvestmentAnalysisView: View {
    enum Cadence: String, CaseIterable, Identifiable {
        case daily = "日报"
        case weekly = "周报"
        var id: String { rawValue }
        var profileId: String { self == .daily ? "investment-daily-v1" : "investment-weekly-v3" }
    }

    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    @State private var cadence: Cadence = .daily
    @State private var selectedID: String?
    @State private var hoveredID: String?
    @State private var showCorpusImporter = false
    @State private var corpusImportMessage: String?

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }
    private var reports: [InvestmentAnalysisReportSummary] {
        store.investmentAnalysisReports.filter { $0.profileId == cadence.profileId }
    }
    private var selected: InvestmentAnalysisReportSummary? {
        reports.first(where: { $0.goalId == selectedID })
    }
    private var selectedGoal: ResearchGoalDetail? {
        guard let selected, store.selectedResearchGoal?.goalId == selected.goalId else { return nil }
        return store.selectedResearchGoal
    }

    var body: some View {
        // The divider expands this workspace to the available height.  Without
        // explicit top alignment, SwiftUI centers the intrinsic-height list
        // column beside it and leaves the cadence tabs in the middle of tall
        // windows.
        HStack(alignment: .top, spacing: 0) {
            VStack(spacing: 0) {
                tabBar
                reportList
                    // The empty/loading state owns the space below the tabs;
                    // it must not influence the position of the tab bar.
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .frame(width: XcomListChrome.listColumnWidth(theme.system))
            .frame(maxHeight: .infinity, alignment: .top)
            Divider().overlay(theme.hairline)
            detailPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(theme.canvas)
        .task { await refresh(selecting: nil) }
        .onChange(of: cadence) { _, _ in
            selectedID = nil
            Task { await refresh(selecting: nil) }
        }
        .onChange(of: selectedID) { _, goalID in
            if let goalID { Task { await store.openInvestmentAnalysisReport(goalID) } }
        }
        .fileImporter(
            isPresented: $showCorpusImporter,
            allowedContentTypes: [UTType(filenameExtension: "jsonl") ?? .plainText],
            allowsMultipleSelection: false
        ) { result in
            guard case let .success(urls) = result, let url = urls.first else {
                if case let .failure(error) = result {
                    corpusImportMessage = "选择语料失败：\(error.localizedDescription)"
                }
                return
            }
            Task {
                let didAccess = url.startAccessingSecurityScopedResource()
                defer {
                    if didAccess { url.stopAccessingSecurityScopedResource() }
                }
                let imported = await store.importInvestmentAnalystCorpus(
                    at: url,
                    profileId: cadence.profileId,
                    cadence: cadence == .daily ? "daily" : "weekly")
                corpusImportMessage = imported
                    ? "语料来源与哈希已登记。正式报告仍需独立抽取、复核和证据审计。"
                    : (store.errorMessage ?? "语料导入失败")
            }
        }
        .alert(
            "分析师语料",
            isPresented: Binding(
                get: { corpusImportMessage != nil },
                set: { if !$0 { corpusImportMessage = nil } })
        ) {
            Button("好", role: .cancel) { corpusImportMessage = nil }
            if store.selectedResearchGoalId != nil {
                Button("查看研究过程") {
                    corpusImportMessage = nil
                    store.openRunbook(focusingResearch: true)
                }
            }
        } message: {
            Text(corpusImportMessage ?? "")
        }
    }

    @ViewBuilder private var tabBar: some View {
        if isXcom {
            XcomUnderlineTabBar(
                options: Cadence.allCases.map { ($0, $0.rawValue) },
                selection: $cadence,
                stretch: true)
        } else {
            KSSSegmentedControl(
                options: Cadence.allCases.map { ($0, $0.rawValue) },
                selection: $cadence,
                stretch: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
        }
    }

    private var reportList: some View {
        Group {
            if store.isLoadingResearch && store.investmentAnalysisReports.isEmpty {
                ProgressView("正在读取报告…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if reports.isEmpty {
                ContentUnavailableView(
                    cadence == .daily ? "暂无投资分析日报" : "暂无投资分析周报",
                    systemImage: "doc.text.image",
                    description: Text(
                        cadence == .daily
                            ? "每晚 20:00 从 Google Drive「左侧机会扫描」写入当天报告。"
                            : "定时研究完成并通过审计后会归档在这里。"
                    ))
            } else {
                List(selection: $selectedID) {
                    ForEach(reports) { report in
                        InvestmentAnalysisRow(report: report)
                            .tag(report.goalId)
                            .listRowBackground(
                                XcomListChrome.listSelectionFill(
                                    isOn: selectedID == report.goalId,
                                    isHovered: isXcom && hoveredID == report.goalId,
                                    theme: theme))
                            .onHover { hovering in
                                hoveredID = hovering ? report.goalId : (hoveredID == report.goalId ? nil : hoveredID)
                            }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
            }
        }
        .background(theme.canvas)
    }

    @ViewBuilder private var detailPane: some View {
        if let report = selected {
            if let goal = selectedGoal {
                InvestmentAnalysisDetail(goal: goal, report: report, stateRoot: store.bridge?.stateRoot, onOpenResearch: {
                    store.openRunbook(focusingResearch: true)
                }, onImportCorpus: { showCorpusImporter = true })
            } else if store.isLoadingResearch {
                ProgressView("正在打开报告…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("报告详情不可用", systemImage: "exclamationmark.triangle")
            }
        } else {
            VStack(spacing: 14) {
                ContentUnavailableView(
                    reports.isEmpty ? "尚无投资分析" : "选择一份投资分析",
                    systemImage: "doc.text.image",
                    description: Text(
                        reports.isEmpty
                            ? "可导入受控 JSONL 语料开始研究；未通过复核与证据门的内容不会成为正式报告。"
                            : "日报和周报均会保留审计结果与证据时点。"))
                Button {
                    showCorpusImporter = true
                } label: {
                    Label("导入分析师语料", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderedProminent)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func refresh(selecting requested: String?) async {
        await store.loadInvestmentAnalysisReports(cadence: cadence == .daily ? "daily" : "weekly")
        let visible = reports
        let target = requested
            ?? visible.first(where: { $0.auditStatus == "pass" })?.goalId
            ?? visible.first?.goalId
        if selectedID != target { selectedID = target }
    }
}

private struct InvestmentAnalysisRow: View {
    @Environment(\.kssTheme) private var theme
    let report: InvestmentAnalysisReportSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text(report.dateStart ?? report.createdAt ?? "未定日期")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                Spacer(minLength: 4)
                statusBadge
            }
            Text(report.title)
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(2)
            Text(report.isDraft ? "草稿 · 审计待通过" : "审计通过 · 正式归档")
                .font(KSSFont.themed(12, .medium, theme: theme))
                .foregroundStyle(report.isDraft ? theme.ma5 : theme.up)
                .lineLimit(1)
        }
        .padding(.vertical, 5)
    }

    @ViewBuilder private var statusBadge: some View {
        if report.auditStatus == "pass" {
            StatusBadge(icon: "checkmark.seal.fill", text: "通过", role: .success)
        } else if report.isDraft {
            StatusBadge(icon: "doc.badge.ellipsis", text: "草稿", role: .skipped)
        } else {
            StatusBadge(icon: "exclamationmark.triangle.fill", text: report.goalStatus, role: .failure)
        }
    }
}

private struct InvestmentAnalysisDetail: View {
    @Environment(\.kssTheme) private var theme
    let goal: ResearchGoalDetail
    let report: InvestmentAnalysisReportSummary
    let stateRoot: URL?
    let onOpenResearch: () -> Void
    let onImportCorpus: () -> Void

    private var htmlArtifact: ResearchArtifact? {
        goal.artifacts.last(where: { $0.kind == "report_html" })
    }
    private var audit: ResearchAuditEntry? { goal.audit.last }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(report.title)
                        .font(KSSFont.themed(XcomListChrome.detailTitlePointSize(theme.system), .bold, theme: theme))
                    Text("\(report.dateStart ?? "未定") · 截至 \(report.asOf ?? "未记录")")
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                Button(action: onImportCorpus) {
                    Label("导入新语料", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(theme.accent)
                Button(action: onOpenResearch) {
                    Label("查看研究过程", systemImage: "arrow.up.right.square")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(theme.accent)
            }
            .padding(.horizontal, 22).padding(.top, 18).padding(.bottom, 12)

            HStack(spacing: 12) {
                detailMetric("审计", audit?.status == "pass" ? "通过" : "草稿/阻断")
                detailMetric("证据时点", report.asOf ?? "未记录")
                detailMetric("对象哈希", String((report.objectHash ?? "未生成").prefix(12)))
            }
            .padding(.horizontal, 22).padding(.bottom, 12)

            Divider().overlay(theme.hairline)
            if let artifact = htmlArtifact {
                ResearchArtifactPreview(artifact: artifact, stateRoot: stateRoot)
            } else {
                ContentUnavailableView("报告尚未编译", systemImage: "doc.badge.ellipsis",
                                       description: Text(audit?.message ?? "等待研究流程生成可预览 HTML。"))
            }
        }
        .background(theme.canvas)
    }

    private func detailMetric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(KSSFont.themed(11, .semibold, theme: theme)).foregroundStyle(theme.textSecondary)
            Text(value).font(.system(size: 12, weight: .medium, design: .monospaced)).lineLimit(1)
        }
    }
}
