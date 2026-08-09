import SwiftUI

/// 节点展开区（plan R19）。两个视图共用这一个组件，分上下两段：
/// 上段是节点自身信息（主色、次色与限定说明、读法、源文依据、复核日期），
/// 下段是挂在该节点下的个股。只有上段能回答「这个格子为什么是这个色」。
struct InvestabilityNodePanel: View {
    @Environment(\.kssTheme) private var theme
    var node: ExposureNode
    var palette: [String: ExposurePaletteColor]
    var axes: [String: ExposureAxis]
    var exposureByCode: [String: ExposureStock]?
    var onSelectSymbol: (String) -> Void
    var onClose: () -> Void
    var onSetCoverage: (Bool) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                Divider().overlay(theme.hairline)
                nodeInfo
                Divider().overlay(theme.hairline)
                attachedStocks
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(theme.surface)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 3) {
                Text(node.name)
                    .font(KSSFont.themed(17, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text(pathText)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            Spacer(minLength: 4)
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.textSecondary)
                    .frame(width: 22, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("关闭展开区")
        }
    }

    private var pathText: String {
        let axisLabel = axes[node.axis]?.label ?? node.axis
        let groupLabel = axes[node.axis]?.groups[node.group] ?? node.group
        var text = "\(axisLabel) / \(groupLabel)"
        if let tier = node.tierBadge { text += " · \(tier)" }
        return text
    }

    // MARK: - 上段：节点信息

    private var nodeInfo: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("节点信息")
            // 主色是这张面板的主角：用整条色轨承载，与泳道瓦片同一套视觉语言。
            HStack(spacing: 0) {
                Rectangle()
                    .fill(theme.exposureColor(forKey: node.primaryColor) ?? theme.exposurePending)
                    .frame(width: 4)
                Text(Self.primaryText(node: node, palette: palette))
                    .font(KSSFont.themed(13, .semibold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .padding(.leading, 9)
                    .padding(.trailing, 10)
                    .padding(.vertical, 7)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(
                (theme.exposureColor(forKey: node.primaryColor) ?? theme.exposurePending)
                    .opacity(0.10)
            )
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
            if let secondary = Self.secondaryText(node: node, palette: palette) {
                infoRow("次色与限定", secondary)
            }
            if !node.reading.isEmpty {
                infoRow("读法", node.reading)
            }
            if !node.sourceRef.isEmpty {
                infoRow("源文依据", node.sourceRef)
            }
            infoRow("最近复核", reviewText)
            coverageRow
        }
    }

    private var reviewText: String {
        let date = node.lastReviewed.isEmpty ? "未记录" : node.lastReviewed
        return node.stale ? "\(date) · 已超 120 天，待复核" : date
    }

    /// 覆盖态与人工确认入口（R9）。挂了票就不该再声明「无标的」，此时按钮不出现。
    @ViewBuilder
    private var coverageRow: some View {
        switch node.state {
        case .hasStocks:
            infoRow("覆盖", "已挂 \(node.attachedStocks.count) 只")
        case .confirmedEmpty:
            VStack(alignment: .leading, spacing: 5) {
                infoRow("覆盖", "已人工确认无标的"
                        + (node.confirmedAt?.isEmpty == false ? " · \(node.confirmedAt ?? "")" : "")
                        + "（计入无暴露结论）")
                Button("撤销确认") { onSetCoverage(false) }
                    .buttonStyle(.plain)
                    .font(KSSFont.themed(11.5, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
            }
        case .unreviewed:
            VStack(alignment: .leading, spacing: 5) {
                infoRow("覆盖", "未核 · 还没人工确认过这个方向有没有标的，不计入无暴露结论")
                Button("确认无标的") { onSetCoverage(true) }
                    .buttonStyle(.plain)
                    .font(KSSFont.themed(11.5, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
            }
        }
    }

    // MARK: - 下段：挂载个股

    private var attachedStocks: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel("挂载个股 · \(node.attachedStocks.count)")
            if node.attachedStocks.isEmpty {
                Text(node.state == .confirmedEmpty
                     ? "已确认这个方向没有可投标的。"
                     : "还没有票挂到这个节点。在个股详情或推荐页的灰点上补标。")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ForEach(node.attachedStocks, id: \.self) { symbol in
                    Button { onSelectSymbol(symbol) } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(symbol)
                                    .font(.system(size: 12.5, weight: .semibold, design: .monospaced))
                                    .foregroundStyle(theme.textPrimary)
                                Spacer(minLength: 4)
                                if exposureByCode?[symbol]?.primaryNode?.nodeId != node.nodeId {
                                    Text("副节点")
                                        .font(KSSFont.themed(10, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                }
                            }
                            ExposureBadge(
                                exposure: exposureByCode?[symbol],
                                loaded: exposureByCode != nil
                            )
                        }
                        .padding(.vertical, 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: - 组件与纯函数

    private func infoRow(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(KSSFont.themed(10.5, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textBody)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func SectionLabel(_ text: String) -> some View {
        Text(text)
            .font(KSSFont.themed(11, .bold, theme: theme))
            .foregroundStyle(theme.accent)
            .textCase(nil)
    }

    /// 主色文案：待定色不假装自己是某个色（R4）。
    static func primaryText(node: ExposureNode, palette: [String: ExposurePaletteColor]) -> String {
        if node.isPending { return "待定色 · 源文未给到节点级色标" }
        guard let color = palette[node.primaryColor] else { return node.primaryColor }
        return "\(color.label) · \(color.meaning)"
    }

    /// 次色与限定说明（R3 / AE7）。两者都可单独缺席；都缺席返回 nil，不渲染空行。
    /// 源文的红色限定按 R3 改写进 `qualifier`，所以这里永远拼不出一个红色次色。
    static func secondaryText(node: ExposureNode, palette: [String: ExposurePaletteColor]) -> String? {
        let colorLabel = node.secondaryColor.isEmpty
            ? nil
            : (palette[node.secondaryColor]?.label ?? node.secondaryColor)
        let qualifier = node.qualifier.trimmingCharacters(in: .whitespacesAndNewlines)
        switch (colorLabel, qualifier.isEmpty) {
        case (nil, true):          return nil
        case (nil, false):         return qualifier
        case (let label?, true):   return "次色 \(label)"
        case (let label?, false):  return "次色 \(label) · \(qualifier)"
        }
    }
}
