import SwiftUI

/// 色聚合视图（plan R17/R18）：配额条 + 七个区块。
///
/// 七块 = 五个行业色块（列节点）+ 红区块（列个股，块头写明红是个股区位）+ 未定色区（列节点）。
/// 未定色节点不归入任何色块，只出现在末尾那一块（AE5）。
///
/// 版面用自算的 masonry 而不是 `LazyVGrid`：网格把同一行的格子按行高对齐，而这七块的行数
/// 从 0 跳到 37，`LazyVGrid` 默认还会把矮块在行内垂直居中——真机上就是三个块顶边不齐、
/// 中间一大片空白。masonry 逐块塞进当前最矮的一列，块与块之间没有行的概念，也就没有这个问题。
struct InvestabilityColorView: View {
    @Environment(\.kssTheme) private var theme
    var map: ExposureMap
    var quota: ExposureQuota?
    var exposureByCode: [String: ExposureStock]?
    /// 由父视图量好的可用宽度，决定分几列。
    var availableWidth: CGFloat
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
            masonry
        }
    }

    // MARK: - 版面

    private var columnCount: Int {
        ExposureMasonry.columnCount(forWidth: availableWidth)
    }

    private var masonry: some View {
        let columns = ExposureMasonry.distribute(blocks, columns: columnCount)
        return HStack(alignment: .top, spacing: 14) {
            ForEach(Array(columns.enumerated()), id: \.offset) { _, column in
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(column) { block in
                        blockView(block)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .top)
            }
        }
    }

    /// 七块的显示顺序：五色按暴露程度由低到高，再红区，最后未定色。
    /// masonry 只决定塞进哪一列，不打乱这个顺序。
    private var blocks: [ExposureMasonry.Block] {
        var out: [ExposureMasonry.Block] = ExposureFilter.paletteOrder.compactMap { key in
            guard let color = map.palette[key] else { return nil }
            let nodes = map.nodes.filter { $0.primaryColor == key }
            return .init(id: key, kind: .color(key), title: color.label,
                         caption: color.meaning, rows: nodes.count)
        }
        out.append(.init(id: "__red", kind: .red, title: "红区（个股）",
                         caption: "8 问答「是」达 5 项 · 是个股区位，不是行业色",
                         rows: max(1, quota?.redSymbols.count ?? 0)))
        out.append(.init(id: "__pending", kind: .pending, title: "未定色",
                         caption: "源文未给到节点级色标，或两处标注冲突 · 不由实现方补判断",
                         rows: map.nodes.filter(\.isPending).count))
        return out
    }

    // MARK: - 区块

    @ViewBuilder
    private func blockView(_ block: ExposureMasonry.Block) -> some View {
        switch block.kind {
        case .color(let key):
            card(tint: theme.exposureColorOrUnknown(key),
                 title: block.title, caption: block.caption,
                 count: "\(block.rows) 节点") {
                ForEach(map.nodes.filter { $0.primaryColor == key }) { node in nodeRow(node) }
            }
        case .pending:
            card(tint: theme.exposurePending, title: block.title, caption: block.caption,
                 count: "\(map.nodes.filter(\.isPending).count) 节点") {
                ForEach(map.nodes.filter(\.isPending)) { node in nodeRow(node) }
            }
        case .red:
            redCard
        }
    }

    /// 红区块列的是个股不是节点——红不是行业色，块头必须把这件事说出来（R17）。
    private var redCard: some View {
        let symbols = quota?.redSymbols ?? []
        return card(tint: theme.exposureRed, title: "红区（个股）",
                    caption: "8 问答「是」达 5 项 · 是个股区位，不是行业色",
                    count: "\(symbols.count) 只") {
            if symbols.isEmpty {
                Text("当前口径内没有红区个股")
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.vertical, 2)
            } else {
                ForEach(symbols, id: \.self) { symbol in
                    Button { onSelectSymbol(symbol) } label: {
                        HStack(spacing: 7) {
                            ExposureDot(exposure: exposureByCode?[symbol], size: 9)
                            Text(symbol)
                                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                                .foregroundStyle(theme.textPrimary)
                            Spacer(minLength: 4)
                            if let zone = exposureByCode?[symbol]?.zone {
                                ExposureZoneLabel(zone: zone, fontSize: 10)
                            }
                        }
                        .padding(.vertical, 3)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    /// 区块卡：顶边一条 3pt 通栏色条。色条的面积比任何圆点都大，一眼就知道这块是什么色，
    /// 块内的节点行因此不必再各带一个色点。
    private func card<Content: View>(
        tint: Color, title: String, caption: String, count: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle().fill(tint).frame(height: 3)
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(title)
                        .font(KSSFont.themed(14, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(caption)
                        .font(KSSFont.themed(10.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    Text(count)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                        .fixedSize()
                }
                VStack(alignment: .leading, spacing: 0) { content() }
            }
            .padding(.horizontal, 14)
            .padding(.top, 11)
            .padding(.bottom, 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius, style: .continuous)
                .stroke(theme.hairline)
        )
    }

    /// 节点行：名字 + 所属组（R17 要求标注所属组），点击进展开区。
    private func nodeRow(_ node: ExposureNode) -> some View {
        Button {
            selectedNodeId = selectedNodeId == node.nodeId ? nil : node.nodeId
        } label: {
            HStack(spacing: 6) {
                Text(node.name)
                    .font(KSSFont.themed(12.5, .semibold, theme: theme))
                    .foregroundStyle(node.nodeId == selectedNodeId ? theme.accent : theme.textPrimary)
                    .lineLimit(1)
                if !node.attachedStocks.isEmpty {
                    Text("\(node.attachedStocks.count)")
                        .font(.system(size: 10.5, weight: .bold, design: .monospaced))
                        .foregroundStyle(theme.accent)
                }
                if node.stale {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundStyle(theme.ma5)
                }
                Spacer(minLength: 8)
                Text(map.axes[node.axis]?.groups[node.group] ?? node.group)
                    .font(KSSFont.themed(10.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
                    .fixedSize()
            }
            .padding(.vertical, 3.5)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - masonry 分栏（纯函数，可单测）

/// 把高矮不齐的区块塞进 N 列。
///
/// 规则：保持区块的语义顺序（五色刻度 → 红区 → 未定色），逐块放进当前累计高度最矮的一列。
/// 不按高度排序——排序能把列压得更平，但会打乱那条有序的暴露刻度，读者就没法按
/// 「低暴露到反制筹码」的顺序扫下来。列间高度差换阅读顺序，这笔交易是划算的。
enum ExposureMasonry {
    enum Kind: Equatable {
        case color(String)
        case red
        case pending
    }

    struct Block: Identifiable, Equatable {
        let id: String
        let kind: Kind
        let title: String
        let caption: String
        /// 块内行数，用来估高度。行高一致，所以行数即相对高度。
        let rows: Int

        /// 估算高度：卡头固定开销 + 行数。单位是「行」，只用于比较，不是 pt。
        var weight: Int { rows + 3 }
    }

    /// 可用宽度决定列数。单列最窄 320pt——再窄节点名与组名会挤在一起。
    static func columnCount(forWidth width: CGFloat) -> Int {
        if width >= 1000 { return 3 }
        if width >= 660 { return 2 }
        return 1
    }

    static func distribute(_ blocks: [Block], columns: Int) -> [[Block]] {
        let n = max(1, columns)
        var out = Array(repeating: [Block](), count: n)
        var heights = Array(repeating: 0, count: n)
        for block in blocks {
            var target = 0
            for index in 1..<n where heights[index] < heights[target] { target = index }
            out[target].append(block)
            heights[target] += block.weight
        }
        return out
    }
}

// MARK: - 配额条（R18）

/// 组合暴露配额。两轨不相加：主轨五色占比合计 100%，副轨红区用同一分母单独一条。
/// 分母是已完成主节点标注的只数，未标注与待定色都不进分母，其只数单独标出（AE6）。
///
/// 六个等宽格子里各带一条色条与一个大数字：样本够时数字是百分比，样本不足时数字是只数。
/// 两种情形共用同一套格子，页面不会因为样本够不够而换一副样子。
struct InvestabilityQuotaBar: View {
    @Environment(\.kssTheme) private var theme
    var quota: ExposureQuota?
    var palette: [String: ExposurePaletteColor]
    @Binding var capDraft: String
    var onApplyCap: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
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
                cells(quota)
                if !quota.sampleInsufficient { mainTrack(quota) }
                footnote(quota)
            } else {
                Text("配额暂不可用（investability-summary 未返回）")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius, style: .continuous)
                .stroke(theme.hairline)
        )
    }

    /// 橙+紫合计上限的设定入口（R18）。留空即不判定，这是默认。
    private var capField: some View {
        HStack(spacing: 5) {
            Text("橙+紫上限")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
            TextField("未设", text: $capDraft)
                .textFieldStyle(.plain)
                .font(.system(size: 11.5, design: .monospaced))
                .multilineTextAlignment(.trailing)
                .frame(width: 46)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(theme.surfaceContainer,
                            in: RoundedRectangle(cornerRadius: 5, style: .continuous))
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

    /// 六个等宽格：五个行业色（主轨）+ 红区（副轨）。副轨用同一分母，不与主轨相加。
    private func cells(_ quota: ExposureQuota) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(ExposureFilter.paletteOrder.enumerated()), id: \.element) { index, key in
                cell(tint: theme.exposureColorOrUnknown(key),
                     value: quota.sampleInsufficient
                        ? "\(quota.counts[key] ?? 0)"
                        : KSSFormat.number(quota.ratios[key] ?? 0, digits: 1) + "%",
                     label: palette[key]?.label ?? ExposureFilter.fallbackLabel(key),
                     sub: quota.sampleInsufficient ? "只" : "\(quota.counts[key] ?? 0) 只",
                     divider: true,
                     highlighted: quota.overCap == true && (key == "orange" || key == "purple"))
                    .zIndex(Double(6 - index))
            }
            cell(tint: theme.exposureRed,
                 value: quota.sampleInsufficient
                    ? "\(quota.redCount)"
                    : KSSFormat.number(quota.redRatio ?? 0, digits: 1) + "%",
                 label: "红区",
                 sub: quota.sampleInsufficient ? "只 · 副轨" : "\(quota.redCount) 只 · 副轨",
                 divider: false,
                 highlighted: false)
        }
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(theme.hairline)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func cell(
        tint: Color, value: String, label: String, sub: String,
        divider: Bool, highlighted: Bool
    ) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(tint)
                .frame(height: 4)
            Text(value)
                .font(.system(size: 16, weight: .bold, design: .monospaced))
                .foregroundStyle(theme.textPrimary)
            HStack(spacing: 4) {
                Text(label)
                    .font(KSSFont.themed(10.5, .semibold, theme: theme))
                    .foregroundStyle(theme.textBody)
                Text(sub)
                    .font(KSSFont.themed(10, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(highlighted ? theme.exposureRed.opacity(0.10) : Color.clear)
        .overlay(alignment: .trailing) {
            if divider { Rectangle().fill(theme.hairline).frame(width: 1) }
        }
    }

    /// 主轨条：五色按占比连成一条，配上限参考线。样本不足时不画（没有百分比可画）。
    ///
    /// 两处刻意的做法：段间留 1pt canvas 缝——深绿与浅绿共边且色差本来就小，没有缝时
    /// 边界读不出来；参考线画成上下各出头 4pt 的立柱而不是段内竖线——线画在色段上时
    /// 明度未知（画在紫段上实测只有 2.39 对比），而「已越上限」恰恰是最该被看见的信号。
    private func mainTrack(_ quota: ExposureQuota) -> some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .leading) {
                HStack(spacing: 1) {
                    ForEach(ExposureFilter.paletteOrder, id: \.self) { key in
                        let ratio = quota.ratios[key] ?? 0
                        // 占比极小的档位不能塌成亚像素细缝，给 2pt 保底。
                        Rectangle()
                            .fill(theme.exposureColorOrUnknown(key))
                            .frame(width: ratio <= 0 ? 0 : max(2, width * ratio / 100))
                    }
                    Spacer(minLength: 0)
                }
                .frame(height: 8)
                .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))

                if let cap = quota.cap, cap >= 0, cap <= 100 {
                    // 参考线在「橙+紫这条尾巴恰好等于上限」的位置：右侧余量即上限。
                    Rectangle()
                        .fill(theme.textPrimary)
                        .frame(width: 2, height: 16)
                        .offset(x: max(0, width * (100 - cap) / 100) - 1)
                        .help("橙+紫上限 \(KSSFormat.number(cap, digits: 1))%")
                }
            }
            .frame(height: 16)
        }
        .frame(height: 16)
    }

    private func footnote(_ quota: ExposureQuota) -> some View {
        FlowLayout(spacing: 14, lineSpacing: 4) {
            if quota.sampleInsufficient {
                Text("样本不足 · 已完成主节点标注 \(quota.denominator) 只，需 ≥ \(quota.minSample) 只才出百分比")
                    .foregroundStyle(theme.ma5)
            } else {
                Text("分母 \(quota.denominator) 只（已完成主节点标注）")
            }
            Text("未标注 \(quota.unlabelledCount) 只不进分母")
            Text("待定色 \(quota.pendingColorCount) 只不进分母")
            if quota.overCap == true, let cap = quota.cap {
                Text("已越 橙+紫 \(KSSFormat.number(cap, digits: 1))% 上限")
                    .foregroundStyle(theme.exposureRed)
            }
        }
        .font(.system(size: 10.5, design: .monospaced))
        .foregroundStyle(theme.textSecondary)
    }
}
