import SwiftUI

/// 色聚合视图（plan R17/R18）：配额条 + 七个区块。
///
/// 七块 = 五个行业色块（列节点）+ 红区块（列个股，块头写明红是个股区位）+ 未定色区（列节点）。
/// 未定色节点不归入任何色块，只出现在末尾那一块（AE5）。
struct InvestabilityColorView: View {
    @Environment(\.kssTheme) private var theme
    var map: ExposureMap
    var quota: ExposureQuota?
    var exposureByCode: [String: ExposureStock]?
    @Binding var selectedNodeId: String?
    @Binding var capDraft: String
    var onApplyCap: (String) -> Void
    var onSelectSymbol: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            InvestabilityQuotaBar(
                quota: quota,
                palette: map.palette,
                capDraft: $capDraft,
                onApplyCap: onApplyCap
            )
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 12)],
                      alignment: .leading, spacing: 12) {
                ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                    if let color = map.palette[key] {
                        colorBlock(key: key, color: color)
                    }
                }
                redBlock
                pendingBlock
            }
        }
    }

    // MARK: - 五个行业色块

    private func colorBlock(key: String, color: ExposurePaletteColor) -> some View {
        let nodes = map.nodes.filter { $0.primaryColor == key }
        return block(
            tint: theme.exposureColor(forKey: key) ?? theme.textSecondary,
            title: color.label,
            caption: color.meaning,
            count: "\(nodes.count) 节点"
        ) {
            ForEach(nodes) { node in nodeRow(node) }
        }
    }

    /// 红区块列的是个股不是节点——红不是行业色，块头必须把这件事说出来（R17）。
    private var redBlock: some View {
        let symbols = quota?.redSymbols ?? []
        return block(
            tint: theme.up,
            title: "红区（个股）",
            caption: "8 问答「是」达 5 项 · 是个股区位，不是行业色",
            count: "\(symbols.count) 只"
        ) {
            if symbols.isEmpty {
                Text("当前口径内没有红区个股")
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            } else {
                ForEach(symbols, id: \.self) { symbol in
                    Button { onSelectSymbol(symbol) } label: {
                        HStack(spacing: 6) {
                            ExposureDot(exposure: exposureByCode?[symbol], size: 7)
                            Text(symbol)
                                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                                .foregroundStyle(theme.textPrimary)
                            Spacer(minLength: 4)
                            if let zone = exposureByCode?[symbol]?.zone {
                                ExposureZoneLabel(zone: zone, fontSize: 10)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    /// 未定色区：主色为 pending 的节点单列（R4/AE5）。
    private var pendingBlock: some View {
        let nodes = map.nodes.filter(\.isPending)
        return block(
            tint: theme.exposurePending,
            title: "未定色",
            caption: "源文未给到节点级色标，或两处标注冲突 · 不由实现方补判断",
            count: "\(nodes.count) 节点"
        ) {
            ForEach(nodes) { node in nodeRow(node) }
        }
    }

    // MARK: - 组件

    private func block<Content: View>(
        tint: Color, title: String, caption: String, count: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(tint).frame(width: 9, height: 9)
                Text(title)
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer(minLength: 4)
                Text(count)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text(caption)
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider().overlay(theme.hairline)
            VStack(alignment: .leading, spacing: 4) { content() }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    /// 节点行：名字 + 所属组（R17 要求标注所属组），点击进展开区。
    private func nodeRow(_ node: ExposureNode) -> some View {
        Button {
            selectedNodeId = selectedNodeId == node.nodeId ? nil : node.nodeId
        } label: {
            HStack(spacing: 6) {
                Text(node.name)
                    .font(KSSFont.themed(12, .semibold, theme: theme))
                    .foregroundStyle(node.nodeId == selectedNodeId ? theme.accent : theme.textPrimary)
                    .lineLimit(1)
                if node.stale {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundStyle(theme.ma5)
                }
                Spacer(minLength: 4)
                Text(map.axes[node.axis]?.groups[node.group] ?? node.group)
                    .font(KSSFont.themed(10.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
                if !node.attachedStocks.isEmpty {
                    Text("\(node.attachedStocks.count)")
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(theme.accent)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 配额条（R18）

/// 组合暴露配额。两轨不相加：主轨五色占比合计 100%，副轨红区用同一分母单独一条。
/// 分母是已完成主节点标注的只数，未标注与待定色都不进分母，其只数单独标出（AE6）。
struct InvestabilityQuotaBar: View {
    @Environment(\.kssTheme) private var theme
    var quota: ExposureQuota?
    var palette: [String: ExposurePaletteColor]
    @Binding var capDraft: String
    var onApplyCap: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("组合暴露配额")
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text("只数口径，非仓位")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Spacer(minLength: 8)
                capField
            }

            if let quota {
                if quota.sampleInsufficient {
                    insufficientSample(quota)
                } else {
                    track(quota)
                    ratioLegend(quota)
                    redTrack(quota)
                }
                footnote(quota)
            } else {
                Text("配额暂不可用（investability-summary 未返回）")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    /// 橙+紫合计上限的设定入口（R18）。留空即不判定，这是默认。
    private var capField: some View {
        HStack(spacing: 5) {
            Text("橙+紫上限")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
            TextField("留空不判定", text: $capDraft)
                .textFieldStyle(.plain)
                .font(.system(size: 11.5, design: .monospaced))
                .frame(width: 70)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                .onSubmit { onApplyCap(capDraft.trimmingCharacters(in: .whitespaces)) }
            Text("%")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Button("应用") { onApplyCap(capDraft.trimmingCharacters(in: .whitespaces)) }
                .buttonStyle(.plain)
                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                .foregroundStyle(theme.accent)
        }
    }

    /// 主轨：五色堆叠 + 上限参考线。越线时橙紫两段加粗描边。
    private func track(_ quota: ExposureQuota) -> some View {
        GeometryReader { geo in
            let width = geo.size.width
            let over = quota.overCap == true
            ZStack(alignment: .leading) {
                HStack(spacing: 0) {
                    ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                        let ratio = quota.ratios[key] ?? 0
                        Rectangle()
                            .fill(theme.exposureColor(forKey: key) ?? theme.textSecondary)
                            .frame(width: max(0, width * ratio / 100))
                            .overlay(
                                Rectangle()
                                    .stroke(theme.textPrimary,
                                            lineWidth: over && (key == "orange" || key == "purple") ? 1.5 : 0)
                            )
                    }
                }
                if let cap = quota.cap, cap >= 0, cap <= 100 {
                    // 参考线画在「橙+紫这条尾巴恰好等于上限」的位置：右侧余量即上限。
                    Rectangle()
                        .fill(theme.textPrimary)
                        .frame(width: 1.5)
                        .offset(x: max(0, width * (100 - cap) / 100))
                        .help("橙+紫上限 \(KSSFormat.number(cap, digits: 1))%")
                }
            }
        }
        .frame(height: 16)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 4).stroke(theme.hairline))
    }

    private func ratioLegend(_ quota: ExposureQuota) -> some View {
        FlowLayout(spacing: 12, lineSpacing: 4) {
            ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                HStack(spacing: 4) {
                    Circle()
                        .fill(theme.exposureColor(forKey: key) ?? theme.textSecondary)
                        .frame(width: 7, height: 7)
                    Text("\(palette[key]?.label ?? key) \(KSSFormat.number(quota.ratios[key] ?? 0, digits: 1))% · \(quota.counts[key] ?? 0) 只")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
    }

    /// 副轨：红区占比，用同一分母，不与主轨相加。
    private func redTrack(_ quota: ExposureQuota) -> some View {
        HStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle().fill(theme.surfaceRaised)
                    Rectangle()
                        .fill(theme.up)
                        .frame(width: max(0, geo.size.width * (quota.redRatio ?? 0) / 100))
                }
            }
            .frame(height: 8)
            .clipShape(RoundedRectangle(cornerRadius: 3))
            Text("副轨 红区 \(KSSFormat.number(quota.redRatio ?? 0, digits: 1))% · \(quota.redCount) 只（同分母，不与主轨相加）")
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .fixedSize()
        }
    }

    /// 已标注不足 10 只不出百分比，改显示各色只数（R18）。
    private func insufficientSample(_ quota: ExposureQuota) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("样本不足：已完成主节点标注 \(quota.denominator) 只，需 ≥ \(quota.minSample) 只才出百分比")
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.ma5)
            FlowLayout(spacing: 12, lineSpacing: 4) {
                ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                    HStack(spacing: 4) {
                        Circle()
                            .fill(theme.exposureColor(forKey: key) ?? theme.textSecondary)
                            .frame(width: 7, height: 7)
                        Text("\(palette[key]?.label ?? key) \(quota.counts[key] ?? 0) 只")
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                    }
                }
                HStack(spacing: 4) {
                    Circle().fill(theme.up).frame(width: 7, height: 7)
                    Text("红区 \(quota.redCount) 只")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
    }

    private func footnote(_ quota: ExposureQuota) -> some View {
        HStack(spacing: 10) {
            Text("分母 \(quota.denominator) 只（已完成主节点标注）")
            Text("未标注 \(quota.unlabelledCount) 只不进分母")
            Text("待定色 \(quota.pendingColorCount) 只不进分母")
            if quota.overCap == true, let cap = quota.cap {
                Text("已越 橙+紫 \(KSSFormat.number(cap, digits: 1))% 上限")
                    .foregroundStyle(theme.ma5)
            }
            Spacer(minLength: 0)
        }
        .font(.system(size: 10.5, design: .monospaced))
        .foregroundStyle(theme.textSecondary)
    }
}
