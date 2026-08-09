import SwiftUI

/// 节点选择器与 8 问录入（plan 2026-08-09-001 U7）。
///
/// 写路径不接写确认弹窗：那道闸守的是 agent 发起的写，界面直写与自选点星同性质
/// （plan U7 / KTD8）。取消不写库。

// MARK: - 节点选择器（R22）

/// 就地补标与改标共用的选择器。按所属组分节，顶部输入框对节点名与组名即时过滤，
/// 未定色节点以未定色样式正常可选。一次编辑一只票的整套标注（主节点 + 副节点）。
struct ExposureNodePickerView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    var symbol: String
    var onClose: () -> Void

    @State private var query = ""
    @State private var primary: String = ""
    @State private var secondaries: Set<String> = []
    @State private var seeded = false
    @State private var saving = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            if let map = store.exposureMap, !map.nodes.isEmpty {
                searchField
                list(map)
            } else if store.exposureMapLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text(store.exposureMapError ?? "地图配置为空，没有可选节点。")
                    .font(KSSFont.themed(12.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider().overlay(theme.hairline)
            footer
        }
        .frame(width: 560, height: 620)
        .background(theme.canvas)
        .task {
            if store.exposureMap == nil { await store.loadInvestabilityMap() }
            guard !seeded else { return }
            seeded = true
            let current = store.exposureByCode?[symbol]
            primary = current?.primaryNode?.nodeId ?? ""
            secondaries = Set((current?.secondaryNodes ?? []).map(\.nodeId))
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("挂到可投资地图")
                .font(KSSFont.themed(16, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text("\(symbol) · 主节点决定该票显示的行业色，副节点可多选")
                .font(KSSFont.themed(11.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textSecondary)
            TextField("过滤节点名或组名", text: $query)
                .textFieldStyle(.plain)
                .font(KSSFont.themed(13, theme: theme))
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        .padding(.horizontal, 14)
        .padding(.top, 10)
    }

    private func list(_ map: ExposureMap) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12, pinnedViews: [.sectionHeaders]) {
                ForEach(InvestabilityLaneBuilder.sections(map: map)) { section in
                    let lanes = section.lanes.compactMap { lane -> InvestabilityLaneBuilder.Lane? in
                        let hits = lane.nodes.filter { matches($0, groupTitle: lane.title) }
                        return hits.isEmpty ? nil : InvestabilityLaneBuilder.Lane(
                            axis: lane.axis, group: lane.group, title: lane.title, nodes: hits)
                    }
                    if !lanes.isEmpty {
                        Section {
                            ForEach(lanes) { lane in
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(lane.title)
                                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                    ForEach(lane.nodes) { node in row(node, palette: map.palette) }
                                }
                            }
                        } header: {
                            Text(section.title)
                                .font(KSSFont.themed(12.5, .bold, theme: theme))
                                .foregroundStyle(theme.accent)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 4)
                                .background(theme.canvas)
                        }
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .scrollContentBackground(.hidden)
    }

    private func matches(_ node: ExposureNode, groupTitle: String) -> Bool {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return true }
        return node.name.lowercased().contains(q) || groupTitle.lowercased().contains(q)
    }

    /// 一行 = 主节点单选 + 副节点勾选。两者互斥：选为主就从副里摘掉。
    private func row(_ node: ExposureNode, palette: [String: ExposurePaletteColor]) -> some View {
        let isPrimary = primary == node.nodeId
        let isSecondary = secondaries.contains(node.nodeId)
        return HStack(spacing: 8) {
            Button {
                primary = isPrimary ? "" : node.nodeId
                if !isPrimary { secondaries.remove(node.nodeId) }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: isPrimary ? "largecircle.fill.circle" : "circle")
                        .font(.system(size: 12))
                        .foregroundStyle(isPrimary ? theme.accent : theme.textSecondary)
                    Circle()
                        .fill(theme.exposureColor(forKey: node.primaryColor) ?? theme.exposurePending)
                        .frame(width: 8, height: 8)
                    Text(node.name)
                        .font(KSSFont.themed(12.5, isPrimary ? .bold : .regular, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .lineLimit(1)
                    Text(node.isPending ? "待定色" : (palette[node.primaryColor]?.label ?? node.primaryColor))
                        .font(KSSFont.themed(10.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Button {
                if isSecondary { secondaries.remove(node.nodeId) } else {
                    secondaries.insert(node.nodeId)
                    if isPrimary { primary = "" }
                }
            } label: {
                Text(isSecondary ? "副 ✓" : "设为副")
                    .font(KSSFont.themed(10.5, .semibold, theme: theme))
                    .foregroundStyle(isSecondary ? theme.onAccent : theme.textSecondary)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(isSecondary ? theme.accent : theme.surfaceRaised, in: Capsule())
                    .contentShape(Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, 3)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text(primary.isEmpty ? "尚未选主节点" : "主节点已选 · 副节点 \(secondaries.count)")
                .font(KSSFont.themed(11.5, theme: theme))
                .foregroundStyle(primary.isEmpty ? theme.ma5 : theme.textSecondary)
            Spacer()
            Button("取消") { onClose() }
                .keyboardShortcut(.cancelAction)
            Button("保存标注") {
                saving = true
                Task {
                    await store.setExposureLabel(
                        symbol: symbol,
                        primary: primary,
                        secondaries: Array(secondaries).sorted()
                    )
                    saving = false
                    onClose()
                }
            }
            .keyboardShortcut(.defaultAction)
            .disabled(primary.isEmpty || saving)
        }
        .padding(14)
    }
}

// MARK: - 个股详情：节点路径卡（R21）

/// 行业色 + 主副节点路径 + 增改删入口 + 标注最后更新时间。
struct ExposurePathCard: View {
    @Environment(\.kssTheme) private var theme
    var symbol: String
    var exposure: ExposureStock?
    var loaded: Bool
    var axes: [String: ExposureAxis]
    var palette: [String: ExposurePaletteColor]
    var onEdit: () -> Void
    var onRemoveSecondary: (String) -> Void
    var onDeletePrimary: () -> Void

    @State private var confirmDelete = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("可投资地图")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                ExposureBadge(exposure: exposure, loaded: loaded, compact: false)
                Spacer()
                Button(action: onEdit) {
                    Label(exposure?.primaryNode == nil ? "挂到节点" : "改标注",
                          systemImage: "point.3.connected.trianglepath.dotted")
                        .font(KSSFont.themed(12, .semibold, theme: theme))
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
            }

            if let node = exposure?.primaryNode {
                row("主节点", pathText(node), tint: theme.exposureColor(forKey: node.primaryColor))
                if !node.reading.isEmpty {
                    Text(node.reading)
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let secondary = InvestabilityNodePanel.secondaryText(node: node, palette: palette) {
                    Text(secondary)
                        .font(KSSFont.themed(11.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else if loaded {
                Text("未上图 —— 这只票还没挂到任何节点，规则风险这一维度是空的。")
                    .font(KSSFont.themed(12.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }

            if let seconds = exposure?.secondaryNodes, !seconds.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("副节点")
                        .font(KSSFont.themed(10.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        ForEach(seconds) { node in
                            HStack(spacing: 4) {
                                Circle()
                                    .fill(theme.exposureColor(forKey: node.primaryColor) ?? theme.exposurePending)
                                    .frame(width: 6, height: 6)
                                Text(node.name)
                                    .font(KSSFont.themed(11.5, theme: theme))
                                    .foregroundStyle(theme.textBody)
                                Button { onRemoveSecondary(node.nodeId) } label: {
                                    Image(systemName: "xmark")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(theme.textSecondary)
                                }
                                .buttonStyle(.plain)
                                .help("移除副节点 \(node.name)")
                            }
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(theme.surfaceRaised, in: Capsule())
                        }
                    }
                }
            }

            HStack(spacing: 10) {
                if let updated = exposure?.labelUpdatedAt, !updated.isEmpty {
                    Text("标注更新于 \(updated)")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                if exposure?.primaryNode != nil {
                    Button("删除主节点") { confirmDelete = true }
                        .buttonStyle(.plain)
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        .foregroundStyle(theme.ma5)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
        .confirmationDialog("删除主节点", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除并回到未上图", role: .destructive) { onDeletePrimary() }
            Button("取消", role: .cancel) {}
        } message: {
            Text("\(symbol) 的全部节点标注会被清空，四处落点回到未上图态。8 问答案不受影响。")
        }
    }

    private func pathText(_ node: ExposureNode) -> String {
        let axisLabel = axes[node.axis]?.label ?? node.axis
        let groupLabel = axes[node.axis]?.groups[node.group] ?? node.group
        return "\(axisLabel) / \(groupLabel) / \(node.name)"
    }

    private func row(_ label: String, _ value: String, tint: Color?) -> some View {
        HStack(alignment: .top, spacing: 6) {
            if let tint {
                Circle().fill(tint).frame(width: 8, height: 8).padding(.top, 5)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(KSSFont.themed(10.5, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Text(value)
                    .font(KSSFont.themed(13, .semibold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - 个股详情：8 问录入（R12/R13，F3）

/// 逐题三态录入。每次保存后区位串由桥接重算返回，桌面端不自行计算（KTD2）。
struct ExposureQuestionsCard: View {
    @Environment(\.kssTheme) private var theme
    var symbol: String
    var exposure: ExposureStock?
    var loaded: Bool
    var onAnswer: (Int, String) async -> Void
    /// 助手草稿：只读展示，逐题「采纳」才写库。
    var draft: ExposureAnswerDrafts?
    var isDrafting: Bool = false
    var onRequestDraft: () -> Void = {}

    @State private var expanded = false
    @State private var busyQuestion: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button { withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(theme.textSecondary)
                    Text("8 问尽调")
                        .font(KSSFont.themed(15, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    if let zone = exposure?.zone {
                        ExposureZoneLabel(zone: zone, fontSize: 11.5)
                    }
                    Spacer()
                    Text("已定 \(exposure?.zone.decided ?? 0)/\(ExposureAnswers.questionCount)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                Text("区位与行业色是并列的两个维度，答案不改写行业色。「未知」不计入已定题数。")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                draftBar
                ForEach(Array(ExposureAnswers.questions.enumerated()), id: \.offset) { index, text in
                    questionRow(number: index + 1, text: text)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }

    /// 助手草拟入口与状态。草稿是**草稿**：不入库，也不预选任何选项，
    /// 只在每题下面挂一条「采纳」——写库那一下必须是人点的（KTD8）。
    @ViewBuilder
    private var draftBar: some View {
        HStack(spacing: 8) {
            Button(action: onRequestDraft) {
                HStack(spacing: 5) {
                    if isDrafting { ProgressView().controlSize(.small) }
                    Text(isDrafting ? "助手草拟中…" : (draft == nil ? "让助手草拟" : "重新草拟"))
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.accent)
            .disabled(isDrafting)
            if let failure = draft?.failureText {
                Text(failure)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.ma5)
                    .lineLimit(1)
            } else if draft?.isOK == true {
                Text("草稿不入库，逐题点「采纳」才写")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
    }

    private func questionRow(number: Int, text: String) -> some View {
        let current = exposure?.answers[number]
        return VStack(alignment: .leading, spacing: 3) {
            answerRow(number: number, text: text, current: current)
            if let item = draft?[number], draft?.isOK == true {
                draftRow(number: number, item: item)
            }
        }
        .padding(.vertical, 3)
    }

    /// 一条草稿：模型给的三态 + 一句依据 + 采纳按钮。
    private func draftRow(number: Int, item: ExposureAnswerDraft) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("草稿")
                .font(KSSFont.themed(10, .semibold, theme: theme))
                .foregroundStyle(theme.onAccent)
                .padding(.horizontal, 5).padding(.vertical, 1)
                .background(theme.textSecondary, in: Capsule())
            Text("\(draftValueLabel(item.value))\(item.why.isEmpty ? "" : " · \(item.why)")")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("采纳") {
                busyQuestion = number
                Task {
                    await onAnswer(number, item.value)
                    busyQuestion = nil
                }
            }
            .buttonStyle(.plain)
            .font(KSSFont.themed(11, .semibold, theme: theme))
            .foregroundStyle(theme.accent)
            .disabled(!loaded || busyQuestion != nil)
        }
        .padding(.leading, 26)
    }

    private func draftValueLabel(_ value: String) -> String {
        switch value {
        case "yes": return "是"
        case "no":  return "否"
        default:    return "未知"
        }
    }

    private func answerRow(number: Int, text: String, current: Bool?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text("\(number)")
                .font(.system(size: 11.5, weight: .bold, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 16, alignment: .leading)
            Text(text)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textBody)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 4) {
                answerButton(number: number, title: "是", value: "yes", isOn: current == true)
                answerButton(number: number, title: "否", value: "no", isOn: current == false)
                answerButton(number: number, title: "未知", value: "unknown", isOn: current == nil)
            }
            .opacity(busyQuestion == number ? 0.5 : 1)
        }
    }

    private func answerButton(number: Int, title: String, value: String, isOn: Bool) -> some View {
        Button {
            busyQuestion = number
            Task {
                await onAnswer(number, value)
                busyQuestion = nil
            }
        } label: {
            Text(title)
                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                .foregroundStyle(isOn ? theme.onAccent : theme.textSecondary)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(isOn ? theme.accent : theme.surfaceRaised, in: Capsule())
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(!loaded || busyQuestion != nil)
    }
}
