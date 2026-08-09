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
                .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .animation(KSSTheme.motionStandard, value: selectedNodeId)
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
        // 色聚合的分栏数要按实际可用宽度定（展开区打开时会少 320pt），
        // 所以宽度在这里量一次往下传，而不是让子视图各量各的。
        GeometryReader { proxy in
            let contentW = min(max(proxy.size.width - 32, 280), 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header(map)
                    if view == .lanes {
                        lanesView(map)
                    } else {
                        InvestabilityColorView(
                            map: map,
                            quota: store.exposureQuota,
                            exposureByCode: store.exposureByCode,
                            availableWidth: contentW,
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
                .frame(width: contentW, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 18)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
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

    /// 图例。五个行业色是一条**有序的暴露刻度**（底座 → 反制筹码），所以画成一条连续色带
    /// 而不是五个孤立的点：色带把相邻两色摆在一起，深绿与浅绿的差别才看得出来，
    /// 而一排 8pt 圆点做不到这件事。非行业色的几个标记单独一行，避免混进刻度。
    private func legend(_ map: ExposureMap) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 0) {
                    ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                        Rectangle()
                            .fill(theme.exposureColorOrUnknown(key))
                            .frame(height: 10)
                            .frame(maxWidth: .infinity)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))
                HStack(spacing: 0) {
                    ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(map.palette[key]?.label ?? ExposureFilter.fallbackLabel(key))
                                .font(KSSFont.themed(11.5, .bold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                            Text(map.palette[key]?.meaning ?? "")
                                .font(KSSFont.themed(9.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .frame(maxWidth: 560, alignment: .leading)

            FlowLayout(spacing: 16, lineSpacing: 6) {
                legendMark(
                    swatch: { AnyView(ExposureSwatch(color: theme.exposurePending)) },
                    text: "待定色", help: "源文未给到节点级色标，或两处标注冲突")
                legendMark(
                    swatch: { AnyView(Circle().fill(theme.textSecondary)
                        .frame(width: 9, height: 9)) },
                    text: "未上图（个股）", help: "个股尚未挂到任何节点；圆形与已上图的方块区分")
                legendMark(
                    swatch: { AnyView(ExposureSwatch(color: theme.exposureRed)) },
                    text: "红区（个股区位，非行业色）", help: "8 问答「是」达 5 项")
                legendMark(
                    swatch: { AnyView(Text("3")
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundStyle(theme.accent)
                        .frame(width: 13)) },
                    text: "节点名后的数字 = 挂了几只票",
                    help: "只有挂了票的节点才出数字")
                legendMark(
                    swatch: { AnyView(Text("灰")
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .frame(width: 13)) },
                    text: "灰名带「无标的」= 已确认无标的，其余为未核",
                    help: "未核尚未人工确认过有无标的，不计入无暴露结论；已确认无标的才计入")
            }
        }
        .padding(.top, 2)
    }

    private func legendMark(
        swatch: () -> AnyView, text: String, help: String
    ) -> some View {
        HStack(spacing: 6) {
            swatch()
            Text(text)
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .help(help)
    }

    // MARK: - 泳道视图（R16）

    /// 泳道。15 条泳道各自套一张卡会把页面切成 15 个盒子，节点反而退到背景里；
    /// 改成表格式的分隔线，卡的边框省下来的对比度留给节点色轨。
    private func lanesView(_ map: ExposureMap) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            ForEach(InvestabilityLaneBuilder.sections(map: map)) { section in
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 10) {
                        Text(section.title)
                            .font(KSSFont.themed(15.5, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(section.lanes.reduce(0) { $0 + $1.nodes.count }) 节点")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                        Rectangle()
                            .fill(theme.hairline)
                            .frame(height: 1)
                    }
                    .padding(.bottom, 4)
                    ForEach(Array(section.lanes.enumerated()), id: \.element.id) { index, lane in
                        if index > 0 {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }
                        laneRow(lane, palette: map.palette)
                    }
                }
            }
        }
    }

    private func laneRow(
        _ lane: InvestabilityLaneBuilder.Lane, palette: [String: ExposurePaletteColor]
    ) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Text(lane.title)
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textBody)
                .frame(width: 96, alignment: .leading)
                .padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                ForEach(InvestabilityLaneBuilder.colorRuns(lane.nodes)) { run in
                    InvestabilityColorRunRow(
                        run: run,
                        palette: palette,
                        selectedNodeId: selectedNodeId,
                        onTap: { toggle($0) }
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 7)
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

    /// 一条色段：泳道内连续同色的一串节点。
    struct ColorRun: Identifiable {
        let colorKey: String        // 五色之一，或 `pending`
        let nodes: [ExposureNode]
        var id: String { colorKey }
    }

    /// 把已按色排好序的节点切成色段。段序即色序（`sortedByColor` 的结果），
    /// 所以只需扫一遍看相邻两个色键是否相同，不重新排序也不重新分组。
    static func colorRuns(_ nodes: [ExposureNode]) -> [ColorRun] {
        var out: [ColorRun] = []
        var currentKey: String?
        var bucket: [ExposureNode] = []
        for node in nodes {
            let key = node.isPending ? "pending" : node.primaryColor
            if key != currentKey {
                if let currentKey, !bucket.isEmpty {
                    out.append(ColorRun(colorKey: currentKey, nodes: bucket))
                }
                currentKey = key
                bucket = []
            }
            bucket.append(node)
        }
        if let currentKey, !bucket.isEmpty {
            out.append(ColorRun(colorKey: currentKey, nodes: bucket))
        }
        return out
    }

    static func colorRank(_ node: ExposureNode) -> Int {
        if node.isPending { return ExposureFilter.paletteOrder.count }
        return ExposureFilter.paletteOrder.firstIndex(of: node.primaryColor)
            ?? ExposureFilter.paletteOrder.count
    }
}

// MARK: - 色段行与节点标签

/// 一条色段：色块 + 色名 + 该色下的节点名。
///
/// 这一版把节点的**容器整个去掉了**。上一版每个节点是一张带边框带底色的瓦片，
/// 103 个节点就是 103 圈边框、103 块底色、103 次色名——页面上量最大的东西全是
/// 装饰，节点名反而被压成了其中一层。改成按色分段后：边框与底色为零，色名一段
/// 只说一次，色块在固定的 x 位置排成一列，眼睛顺着这一列往下扫就能读出整条泳道
/// 的色分布。节点名回到满对比度的正文。
///
/// R2 仍然成立：色块与色名在同一行紧挨着，色从来不是唯一信息载体，只是配对关系
/// 从「每个节点各配一次」提到了「每段配一次」。
struct InvestabilityColorRunRow: View {
    @Environment(\.kssTheme) private var theme
    var run: InvestabilityLaneBuilder.ColorRun
    var palette: [String: ExposurePaletteColor]
    var selectedNodeId: String?
    var onTap: (ExposureNode) -> Void

    private var colorLabel: String {
        run.colorKey == "pending"
            ? "待定色"
            : (palette[run.colorKey]?.label ?? ExposureFilter.fallbackLabel(run.colorKey))
    }

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            ExposureSwatch(color: theme.exposureColorOrUnknown(run.colorKey), size: 12)
                .padding(.top, 4)
            Text(colorLabel)
                .font(KSSFont.themed(10.5, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 38, alignment: .leading)
                .padding(.top, 3)
            FlowLayout(spacing: 8, lineSpacing: 3) {
                ForEach(run.nodes) { node in
                    InvestabilityNodeLabel(
                        node: node,
                        isSelected: node.nodeId == selectedNodeId,
                        onTap: { onTap(node) }
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(colorLabel) · \(run.nodes.count) 个节点")
    }
}

/// 一个节点：静止时是纯文字，交互时才长出背景。
///
/// 三态不再靠边框虚实区分（实测虚线与实线的描边对比只有 1.25，那个区分在真机上
/// 不成立）：挂了票给等宽数字，已确认无标的给灰名加「无标的」，未核是零装饰的
/// 默认态。开局 103 个节点全是未核，默认态零装饰意味着页面开局就是干净的。
struct InvestabilityNodeLabel: View {
    @Environment(\.kssTheme) private var theme
    var node: ExposureNode
    var isSelected: Bool
    var onTap: () -> Void

    @State private var isHovering = false

    private var isEmptyConfirmed: Bool { node.state == .confirmedEmpty }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 4) {
                Text(node.name)
                    .font(KSSFont.themed(13, isSelected ? .bold : .medium, theme: theme))
                    .foregroundStyle(nameColor)
                    .lineLimit(1)
                if let tier = node.tierBadge {
                    Text(tier)
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .baselineOffset(3)
                }
                if node.state == .hasStocks {
                    Text("\(node.attachedStocks.count)")
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundStyle(theme.accent)
                }
                if isEmptyConfirmed {
                    Text("无标的")
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                if node.stale {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(background)
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
        .help(helpText)
        .accessibilityLabel(helpText)
    }

    private var nameColor: Color {
        if isSelected { return theme.accent }
        return isEmptyConfirmed ? theme.textSecondary : theme.textPrimary
    }

    @ViewBuilder
    private var background: some View {
        if isSelected {
            theme.accent.opacity(0.14)
        } else if isHovering {
            theme.surfaceContainer
        } else {
            Color.clear
        }
    }

    private var helpText: String {
        var parts = [node.name]
        switch node.state {
        case .hasStocks:      parts.append("挂 \(node.attachedStocks.count) 只")
        case .confirmedEmpty: parts.append("已人工确认无标的")
        case .unreviewed:     parts.append("未核，尚未确认有无标的")
        }
        if node.stale { parts.append("复核已超 120 天") }
        return parts.joined(separator: " · ")
    }
}
