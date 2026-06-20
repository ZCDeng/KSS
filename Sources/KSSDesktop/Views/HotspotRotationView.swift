import SwiftUI

/// 板块热点信息图：把 hotspot-rotation 模型的当日快照做成一眼读懂的轮动判断图。
/// 数据源 = snapshot.latestSectorRotation（Phase 4 已解码的 HotspotRotationSummary）。
struct HotspotRotationView: View {
    var rotation: HotspotRotationSummary?
    /// 点击板块名跳概念主题页（可选）。
    var onOpenThemes: () -> Void
    /// 点击妖王股票 → 不在池则导入。
    var onSelectSymbol: (String) -> Void

    // M3：内容封顶 1080 居中，统一外边距。
    private let margin: CGFloat = 24
    private let gutter: CGFloat = 20

    var body: some View {
        GeometryReader { geo in
            let contentW = min(geo.size.width - margin * 2, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    PageTitle("妖板情绪", subtitle: "价格面情绪雷达 · 涨幅排名+持续频次 · 不代表资金面强势（强势看趋势/复盘）")

                    if let r = rotation {
                        headerStrip(r)
                        SectionHeader("轮动象限")
                        quadrantGrid(r, contentW: contentW)
                        if !r.topLeaders.isEmpty {
                            SectionHeader("妖王榜")
                            leaderBoard(r.topLeaders, contentW: contentW)
                        }
                    } else {
                        emptyState
                    }
                }
                .frame(width: contentW, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, margin)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
    }

    // MARK: 顶部：日期 + 板块数 + 回看 + 覆盖率

    private func headerStrip(_ r: HotspotRotationSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                statTile("\(r.boardCount)", "今日板块")
                statTile("\(r.lookbackDays ?? 0)", "回看交易日")
                statTile("\(r.mainline.count + r.demonBoard.count + r.oldHotspotFading.count)", "在榜象限板块")
                Spacer(minLength: 0)
                if let date = r.tradeDate, date.count == 8 {
                    Text("\(date.prefix(4)).\(date.dropFirst(4).prefix(2)).\(date.suffix(2))")
                        .font(KSSFont.serif(20, .bold))
                        .foregroundStyle(KSSTheme.textPrimary)
                }
            }
            HStack(spacing: gutter) {
                coverageMeter("历史覆盖", r.historyCoverage, KSSTheme.ma20)
                // 龙头映射仅供妖王榜展示，不参与象限分类（命名空间对不齐，结构性偏低）。
                coverageMeter("龙头映射", r.leaderCoverage, KSSTheme.accent)
            }
        }
        .padding(16)
        .kssCard()
    }

    private func statTile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(KSSFont.harmonyNumber(22))
                .foregroundStyle(KSSTheme.textPrimary)
            Text(label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(KSSTheme.textSecondary)
        }
        .frame(minWidth: 72, alignment: .leading)
    }

    private func coverageMeter(_ label: String, _ value: Double?, _ tint: Color) -> some View {
        let v = max(0, min(1, value ?? 0))
        return VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(label)
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(KSSTheme.textSecondary)
                Spacer()
                Text("\(Int((value ?? 0) * 100))%")
                    .font(.system(size: 11.5, weight: .bold, design: .monospaced))
                    .foregroundStyle(tint)
            }
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(KSSTheme.surfaceRaised)
                    Capsule().fill(tint).frame(width: max(4, g.size.width * v))
                }
            }
            .frame(height: 7)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: 四象限

    private func quadrantGrid(_ r: HotspotRotationSummary, contentW: CGFloat) -> some View {
        let cols = contentW >= 720 ? 3 : 1
        return LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: gutter), count: cols),
            spacing: gutter
        ) {
            quadrantCard("妖板", "高位异动 / 题材接力（本页主信号）", r.demonBoard, KSSTheme.accent, "bolt.fill")
            quadrantCard("主线", "今日爆发 × 持续在前（较少见）", r.mainline, KSSTheme.up, "flame.fill")
            quadrantCard("退潮", "曾强势但今日走弱", r.oldHotspotFading, KSSTheme.textSecondary, "arrow.down.right")
        }
    }

    private func quadrantCard(_ title: String, _ caption: String, _ boards: [String], _ tint: Color, _ icon: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: icon).font(.system(size: 13, weight: .bold)).foregroundStyle(tint)
                Text(title).font(.system(size: 15, weight: .bold)).foregroundStyle(KSSTheme.textPrimary)
                Spacer()
                Text("\(boards.count)")
                    .font(.system(size: 16, weight: .heavy).monospacedDigit())
                    .foregroundStyle(tint)
            }
            Text(caption)
                .font(.system(size: 11))
                .foregroundStyle(KSSTheme.textSecondary)
            if boards.isEmpty {
                Text("今日无")
                    .font(.system(size: 12))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .padding(.top, 2)
            } else {
                FlowChips(items: boards, tint: tint)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.cardRadius)
                .stroke(tint.opacity(0.25), lineWidth: 1)
        )
    }

    // MARK: 妖王榜

    private func leaderBoard(_ leaders: [HotspotLeaderStock], contentW: CGFloat) -> some View {
        let cols = contentW >= 720 ? 2 : 1
        return LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: gutter), count: cols),
            spacing: 10
        ) {
            ForEach(Array(leaders.prefix(10).enumerated()), id: \.element.id) { idx, leader in
                leaderRow(rank: idx + 1, leader: leader)
            }
        }
    }

    private func leaderRow(rank: Int, leader: HotspotLeaderStock) -> some View {
        Button { onSelectSymbol(leader.symbol) } label: {
            HStack(spacing: 12) {
                Text("\(rank)")
                    .font(.system(size: 14, weight: .heavy).monospacedDigit())
                    .foregroundStyle(rank <= 3 ? KSSTheme.accent : KSSTheme.textSecondary)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(leader.name)
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(KSSTheme.textPrimary)
                        Text(leader.symbol)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                        Text("霸榜 \(leader.appearances) 次")
                            .font(.system(size: 10.5, weight: .bold))
                            .foregroundStyle(KSSTheme.up)
                            .padding(.horizontal, 6).padding(.vertical, 1.5)
                            .background(KSSTheme.up.opacity(0.12), in: Capsule())
                    }
                    if let positions = leader.positions, !positions.isEmpty {
                        Text(positions.prefix(4).map(Self.shortPosition).joined(separator: "  "))
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("\(leader.name) \(leader.symbol) · 点击查看 / 不在池则导入")
    }

    /// "2026-06-18/龙一" → "06-18 龙一"
    private static func shortPosition(_ p: String) -> String {
        let parts = p.split(separator: "/")
        guard parts.count == 2 else { return p }
        let date = String(parts[0])
        let md = date.count >= 10 ? String(date.dropFirst(5)) : date
        return "\(md) \(parts[1])"
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("暂无热点轮动数据")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
            Text("运行「任务 · 定时任务 · 板块热点轮动归档」或等当日收盘后任务自动归档后查看。")
                .font(.system(size: 13))
                .foregroundStyle(KSSTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .kssCard()
    }
}

/// 简易流式标签（板块名长度不一，比 LazyVGrid 自适应更紧凑）。
struct FlowChips: View {
    var items: [String]
    var tint: Color

    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            ForEach(items, id: \.self) { name in
                Text(name)
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(KSSTheme.textBody)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(tint.opacity(0.10), in: Capsule())
                    .overlay(Capsule().stroke(tint.opacity(0.22), lineWidth: 0.5))
                    .lineLimit(1).fixedSize()
            }
        }
    }
}

/// 轻量流式布局（macOS 13+ Layout 协议）：标签从左到右排满换行。
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > maxW, x > 0 {
                x = 0; y += lineH + lineSpacing; lineH = 0
            }
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
        return CGSize(width: maxW == .infinity ? x : maxW, height: y + lineH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        let maxW = bounds.width
        var x: CGFloat = bounds.minX, y: CGFloat = bounds.minY, lineH: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x - bounds.minX + s.width > maxW, x > bounds.minX {
                x = bounds.minX; y += lineH + lineSpacing; lineH = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
    }
}
