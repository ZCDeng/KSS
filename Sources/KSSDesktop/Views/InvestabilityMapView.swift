import SwiftUI

/// 可投资地图（plan 2026-08-09-001 U5）。
///
/// 两个视图共用同一个展开区：泳道按产业链排（R16），色聚合按五色 + 红区 + 未定色分七块
/// （R17）。展开区放右侧固定栏而不是行内插入——R16 要求页面只做纵向滚动，行内插入会把
/// 泳道的流式换行顶得来回跳。
///
/// 页面有四态，不能让降级后的空树和桥坏掉长一个样：加载中 / 桥接失败（带重试与失败命令名）
/// / 配置为空（KTD7 降级）/ 正常。
struct InvestabilityMapView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    var onSelectSymbol: (String) -> Void

    /// 橙+紫合计上限（R18）。留空即不判定越线，这是默认。
    @AppStorage("investabilityCapPct") private var capPct = ""
    @State private var view: MapViewMode = .lanes
    @State private var selectedNodeId: String?
    @State private var capDraft = ""

    enum MapViewMode: String, CaseIterable, Identifiable {
        case lanes = "产业链泳道"
        case colors = "按色聚合"
        var id: String { rawValue }
    }

    var body: some View {
        HStack(spacing: 0) {
            main
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if let node = selectedNode {
                Divider().overlay(theme.hairline)
                InvestabilityNodePanel(
                    node: node,
                    palette: store.exposureMap?.palette ?? [:],
                    axes: store.exposureMap?.axes ?? [:],
                    exposureByCode: store.exposureByCode,
                    onSelectSymbol: onSelectSymbol,
                    onClose: { selectedNodeId = nil },
                    onSetCoverage: { confirmed in
                        Task { await store.setExposureNodeCoverage(nodeId: node.nodeId, confirmed: confirmed) }
                    }
                )
                .frame(width: 320)
            }
        }
        .background(theme.canvas)
        .task {
            capDraft = capPct
            if store.exposureMap == nil { await store.loadInvestabilityMap(capPct: capPct) }
            await store.loadExposureLabels()
        }
    }

    private var selectedNode: ExposureNode? {
        guard let id = selectedNodeId else { return nil }
        return store.exposureMap?.nodes.first { $0.nodeId == id }
    }

    // MARK: - 四态

    @ViewBuilder
    private var main: some View {
        if let message = store.exposureMapError {
            failureState(message)
        } else if let map = store.exposureMap {
            if map.nodes.isEmpty {
                emptyConfigState
            } else {
                content(map)
            }
        } else {
            loadingState
        }
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("加载可投资地图…")
                .font(KSSFont.themed(13, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// 桥接失败：给出失败的命令名，否则排查只能靠猜。
    private func failureState(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 26))
                .foregroundStyle(theme.ma5)
            Text("地图数据取不到")
                .font(KSSFont.themed(16, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(message)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
            Button {
                Task { await store.loadInvestabilityMap(capPct: capPct) }
            } label: {
                Label("重试", systemImage: "arrow.clockwise")
                    .font(KSSFont.themed(13, .semibold, theme: theme))
            }
            .buttonStyle(.bordered)
            .tint(theme.accent)
            .disabled(store.exposureMapLoading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    /// 配置为空 = 加载器按 KTD7 降级返回了空树，桥本身是好的。文案必须与上面那条区分开。
    private var emptyConfigState: some View {
        VStack(spacing: 10) {
            Image(systemName: "map")
                .font(.system(size: 26))
                .foregroundStyle(theme.textSecondary)
            Text("地图配置为空")
                .font(KSSFont.themed(16, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text("桥接正常但节点树读出 0 个节点：kss/config/investability_map.yaml 缺失或全部节点被跳过。\n检查该文件是否随包分发，以及日志里的逐节点告警。")
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
            Button {
                Task { await store.loadInvestabilityMap(capPct: capPct) }
            } label: {
                Label("重新加载", systemImage: "arrow.clockwise")
                    .font(KSSFont.themed(13, .semibold, theme: theme))
            }
            .buttonStyle(.bordered)
            .tint(theme.accent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    // MARK: - 正常态

    private func content(_ map: ExposureMap) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header(map)
                if view == .lanes {
                    lanesView(map)
                } else {
                    InvestabilityColorView(
                        map: map,
                        quota: store.exposureQuota,
                        exposureByCode: store.exposureByCode,
                        selectedNodeId: $selectedNodeId,
                        capDraft: $capDraft,
                        onApplyCap: { value in
                            capPct = value
                            Task { await store.reloadInvestabilityQuota(capPct: value) }
                        },
                        onSelectSymbol: onSelectSymbol
                    )
                }
            }
            .frame(maxWidth: 1080, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 16)
            .padding(.vertical, 18)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }

    /// 页头常驻源版本与全表最旧复核日期（R23）；陈旧节点数一并给出，否则「最旧」是个孤零零的日期。
    private func header(_ map: ExposureMap) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                PageTitle("可投资地图", subtitle: "\(map.nodes.count) 个子行业节点 · 色描述规则风险暴露，不下买卖判断")
                Spacer(minLength: 12)
                VStack(alignment: .trailing, spacing: 4) {
                    Text("源 \(map.sourceVersion.isEmpty ? "—" : map.sourceVersion)")
                        .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                    HStack(spacing: 5) {
                        if map.staleCount > 0 {
                            Image(systemName: "clock.badge.exclamationmark")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(theme.ma5)
                        }
                        Text("最旧复核 \(map.oldestReviewed.isEmpty ? "—" : map.oldestReviewed)"
                             + (map.staleCount > 0 ? " · 超 120 天 \(map.staleCount) 个" : ""))
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(map.staleCount > 0 ? theme.ma5 : theme.textSecondary)
                    }
                }
            }
            KSSSegmentedControl(
                options: MapViewMode.allCases.map { ($0, $0.rawValue) },
                selection: $view
            )
            legend(map)
        }
    }

    /// 分段图例：行业五色 / 个股红区 / 未定色 / 未上图灰点 / 节点两态。
    private func legend(_ map: ExposureMap) -> some View {
        FlowLayout(spacing: 12, lineSpacing: 6) {
            ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                if let color = map.palette[key] {
                    legendItem(dot: theme.exposureColor(forKey: key), text: color.label, help: color.meaning)
                }
            }
            legendItem(dot: theme.exposurePending, text: "待定色", help: "源文未给到节点级色标，或两处标注冲突")
            legendItem(dot: theme.textSecondary.opacity(0.55), text: "未上图", help: "个股尚未挂到任何节点")
            legendItem(dot: theme.up, text: "红区（个股）", help: "8 问答「是」达 5 项，是个股区位不是行业色")
            legendItem(dot: nil, text: "未核 / 已确认无标的", help: "虚线描边=未核；空心描边=人工确认过无标的")
        }
        .padding(.vertical, 4)
    }

    private func legendItem(dot: Color?, text: String, help: String) -> some View {
        HStack(spacing: 5) {
            if let dot {
                Circle().fill(dot).frame(width: 8, height: 8)
            } else {
                Circle()
                    .strokeBorder(theme.outlineVariant, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                    .frame(width: 8, height: 8)
            }
            Text(text)
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .help(help)
    }

    // MARK: - 泳道视图（R16）

    private func lanesView(_ map: ExposureMap) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            ForEach(InvestabilityLaneBuilder.sections(map: map)) { section in
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Text(section.title)
                            .font(KSSFont.themed(15, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(section.lanes.reduce(0) { $0 + $1.nodes.count }) 节点")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                        Spacer()
                    }
                    ForEach(section.lanes) { lane in
                        laneRow(lane)
                    }
                }
            }
        }
    }

    private func laneRow(_ lane: InvestabilityLaneBuilder.Lane) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(lane.title)
                .font(KSSFont.themed(12.5, .semibold, theme: theme))
                .foregroundStyle(theme.textBody)
                .frame(width: 108, alignment: .leading)
                .padding(.top, 4)
            FlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(lane.nodes) { node in
                    InvestabilityNodeChip(
                        node: node,
                        isSelected: node.nodeId == selectedNodeId,
                        onTap: { toggle(node) }
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 12)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    private func toggle(_ node: ExposureNode) {
        selectedNodeId = selectedNodeId == node.nodeId ? nil : node.nodeId
    }
}

// MARK: - 泳道拆分

/// 把节点树拆成主轴分节 + 组泳道。顺序取节点在 YAML 里的首次出现序——
/// `axes` 经 JSON 字典往返后已经没有顺序了，靠它排会每次启动都换一个样。
enum InvestabilityLaneBuilder {
    struct Lane: Identifiable {
        let axis: String
        let group: String
        let title: String
        let nodes: [ExposureNode]
        var id: String { "\(axis)/\(group)" }
    }

    struct Section: Identifiable {
        let axis: String
        let title: String
        let lanes: [Lane]
        var id: String { axis }
    }

    static func sections(map: ExposureMap) -> [Section] {
        var axisOrder: [String] = []
        var groupOrder: [String: [String]] = [:]
        var bucket: [String: [(Int, ExposureNode)]] = [:]

        for (index, node) in map.nodes.enumerated() {
            if !axisOrder.contains(node.axis) { axisOrder.append(node.axis) }
            var groups = groupOrder[node.axis] ?? []
            if !groups.contains(node.group) {
                groups.append(node.group)
                groupOrder[node.axis] = groups
            }
            bucket["\(node.axis)/\(node.group)", default: []].append((index, node))
        }

        return axisOrder.map { axis in
            let lanes = (groupOrder[axis] ?? []).map { group -> Lane in
                let entries = bucket["\(axis)/\(group)"] ?? []
                return Lane(
                    axis: axis,
                    group: group,
                    title: map.axes[axis]?.groups[group] ?? group,
                    nodes: sortedByColor(entries)
                )
            }
            return Section(axis: axis, title: map.axes[axis]?.label ?? axis, lanes: lanes)
        }
    }

    /// 泳道内按色排序（R16），同色保持 YAML 原序；未定色排最后。
    static func sortedByColor(_ entries: [(Int, ExposureNode)]) -> [ExposureNode] {
        entries
            .sorted { a, b in
                let ra = colorRank(a.1), rb = colorRank(b.1)
                return ra == rb ? a.0 < b.0 : ra < rb
            }
            .map(\.1)
    }

    static func colorRank(_ node: ExposureNode) -> Int {
        if node.isPending { return ExposureFilter.paletteOrder.count }
        return ExposureFilter.paletteOrder.firstIndex(of: node.primaryColor)
            ?? ExposureFilter.paletteOrder.count
    }
}

// MARK: - 节点 chip

/// 节点 chip。三种覆盖态用描边区分（R9）：实线=有票、空心细描边=已确认无标的、
/// 虚线=未核。色永远同时给点与名字，不靠单一色载信息（R2）。
struct InvestabilityNodeChip: View {
    @Environment(\.kssTheme) private var theme
    var node: ExposureNode
    var isSelected: Bool
    var onTap: () -> Void

    private var color: Color {
        theme.exposureColor(forKey: node.primaryColor) ?? theme.exposurePending
    }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 5) {
                Circle().fill(color).frame(width: 7, height: 7)
                Text(node.name)
                    .font(KSSFont.themed(12, .semibold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                if let tier = node.tierBadge {
                    Text(tier)
                        .font(KSSFont.themed(9.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 3)
                        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 3))
                }
                if node.stale {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundStyle(theme.ma5)
                }
                if !node.attachedStocks.isEmpty {
                    Text("\(node.attachedStocks.count)")
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(theme.onAccent)
                        .padding(.horizontal, 4).padding(.vertical, 0.5)
                        .background(theme.accent, in: Capsule())
                } else if node.state == .confirmedEmpty {
                    Text("无标的")
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                } else {
                    Text("未核")
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(background)
            .overlay(border)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .help(helpText)
        .accessibilityLabel(helpText)
    }

    @ViewBuilder
    private var background: some View {
        switch node.state {
        case .hasStocks:      Capsule().fill(color.opacity(0.14))
        case .confirmedEmpty: Capsule().fill(Color.clear)
        case .unreviewed:     Capsule().fill(theme.surfaceRaised.opacity(0.5))
        }
    }

    @ViewBuilder
    private var border: some View {
        switch node.state {
        case .hasStocks:
            Capsule().stroke(isSelected ? theme.accent : color.opacity(0.5),
                             lineWidth: isSelected ? 1.6 : 1)
        case .confirmedEmpty:
            Capsule().stroke(isSelected ? theme.accent : theme.outlineVariant,
                             lineWidth: isSelected ? 1.6 : 1)
        case .unreviewed:
            Capsule().stroke(isSelected ? theme.accent : theme.outlineVariant,
                             style: StrokeStyle(lineWidth: isSelected ? 1.6 : 1, dash: [3, 2]))
        }
    }

    private var helpText: String {
        var parts = [node.name]
        parts.append(node.isPending ? "待定色" : node.primaryColor)
        switch node.state {
        case .hasStocks:      parts.append("挂 \(node.attachedStocks.count) 只")
        case .confirmedEmpty: parts.append("已人工确认无标的")
        case .unreviewed:     parts.append("未核，尚未确认有无标的")
        }
        if node.stale { parts.append("复核已超 120 天") }
        return parts.joined(separator: " · ")
    }
}
