import SwiftUI

/// 趋势页（U6）：月热力格（北向底色 + 板块点 + 推荐微条 + 三态）+ 本周时间线 + 当日明细。
/// Dayflow 设计参照（GitHub 活跃度格 + 日卡），原生 SwiftUI，红涨绿跌，M3 shape/motion。
struct TrendsView: View {
    var month: TrendMonth?
    var detail: TrendDayDetail?
    var selectedDate: String?
    var loading: Bool
    var onLoadMonth: (String) -> Void
    var onSelectDay: (String) -> Void
    var onSelectSymbol: (String) -> Void

    @State private var currentMonth: String = ""   // YYYY-MM

    private var cellByDate: [String: TrendDayCell] {
        Dictionary(uniqueKeysWithValues: (month?.days ?? []).map { ($0.date, $0) })
    }

    var body: some View {
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageTitle("趋势", subtitle: "主力资金 / 板块 / 推荐 · 按天看本周本月变化")

                    monthHeader
                    legendRow
                    heatGrid

                    SectionHeader("本周", caption: "最近 5 个交易日")
                    weekTimeline

                    if let d = detail, d.found {
                        SectionHeader(dayTitle(d.date), caption: "当日明细 · 点股票即导入")
                        dayDetail(d)
                    } else if selectedDate != nil {
                        Text("该日暂无归档数据")
                            .font(.system(size: 13))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                }
                .frame(width: w, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
        .task {
            if currentMonth.isEmpty {
                currentMonth = month?.month ?? Self.monthString(Date())
                onLoadMonth(currentMonth)
            }
        }
    }

    // MARK: 月导航

    private var monthHeader: some View {
        HStack(spacing: 12) {
            Button { shiftMonth(-1) } label: {
                Image(systemName: "chevron.left").font(.system(size: 14, weight: .bold))
            }
            .buttonStyle(.bordered)
            Text(currentMonth)
                .font(.system(size: 18, weight: .heavy, design: .rounded).monospacedDigit())
                .foregroundStyle(KSSTheme.textPrimary)
                .frame(minWidth: 92)
            Button { shiftMonth(1) } label: {
                Image(systemName: "chevron.right").font(.system(size: 14, weight: .bold))
            }
            .buttonStyle(.bordered)
            .disabled(currentMonth >= Self.monthString(Date()))   // 不越过本月
            if loading {
                ProgressView().controlSize(.small).padding(.leading, 4)
            }
            Spacer()
        }
    }

    private var legendRow: some View {
        HStack(spacing: 14) {
            legendChip(color: KSSTheme.up, "北向流入", icon: "arrowtriangle.up.fill")
            legendChip(color: KSSTheme.down, "流出", icon: "arrowtriangle.down.fill")
            HStack(spacing: 5) {
                Circle().fill(KSSTheme.accent).frame(width: 6, height: 6)
                Text("板块强势").font(.system(size: 11)).foregroundStyle(KSSTheme.textSecondary)
            }
            HStack(spacing: 5) {
                RoundedRectangle(cornerRadius: 1).fill(KSSTheme.up).frame(width: 12, height: 3)
                Text("推荐均 T+5").font(.system(size: 11)).foregroundStyle(KSSTheme.textSecondary)
            }
            Spacer()
        }
    }

    private func legendChip(color: Color, _ label: String, icon: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 8, weight: .bold)).foregroundStyle(color)
            RoundedRectangle(cornerRadius: 3).fill(color.opacity(0.5)).frame(width: 12, height: 12)
            Text(label).font(.system(size: 11)).foregroundStyle(KSSTheme.textSecondary)
        }
    }

    // MARK: 月热力格

    private var heatGrid: some View {
        let days = Self.daysInMonth(currentMonth)
        let leading = Self.leadingBlanks(currentMonth)   // 月首前补空格（周一为列首）
        let cols = Array(repeating: GridItem(.flexible(), spacing: 6), count: 7)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { wd in
                    Text(wd).font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .frame(maxWidth: .infinity)
                }
            }
            LazyVGrid(columns: cols, spacing: 6) {
                ForEach(0..<leading, id: \.self) { _ in Color.clear.frame(height: 52) }
                ForEach(days, id: \.self) { date in
                    heatCell(date)
                }
            }
        }
        .padding(12)
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }

    @ViewBuilder
    private func heatCell(_ date: String) -> some View {
        let cell = cellByDate[date]
        let day = String(date.suffix(2))
        let isSelected = date == selectedDate
        let weekend = Self.isWeekend(date)
        Button {
            if cell != nil { onSelectDay(date) }
        } label: {
            VStack(spacing: 3) {
                Text(day)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(cell != nil ? KSSTheme.textPrimary : KSSTheme.textSecondary.opacity(0.5))
                Spacer(minLength: 0)
                // 北向方向微标（色盲兜底）
                if let c = cell, let n = c.north {
                    Image(systemName: n.dir == "out" ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
                        .font(.system(size: 7, weight: .bold))
                        .foregroundStyle(n.dir == "out" ? KSSTheme.down : KSSTheme.up)
                }
                // 板块点 + 推荐微条
                HStack(spacing: 3) {
                    if let c = cell, c.flags.sector {
                        Circle().fill(KSSTheme.accent.opacity(0.4 + 0.6 * (c.sectorHeat ?? 0)))
                            .frame(width: 5, height: 5)
                    }
                    if let c = cell, let avg = c.recAvgFwd {
                        RoundedRectangle(cornerRadius: 1)
                            .fill(avg >= 0 ? KSSTheme.up : KSSTheme.down)
                            .frame(width: 10, height: 3)
                    }
                }
                .frame(height: 6)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(cellBackground(cell, weekend: weekend))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.shapeS)
                    .strokeBorder(isSelected ? KSSTheme.accent : .clear, lineWidth: 2)
            )
            .clipShape(RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        }
        .buttonStyle(.plain)
        .disabled(cell == nil)
        .help(cell == nil ? (weekend ? "非交易日" : "无归档数据") : date)
    }

    private func cellBackground(_ cell: TrendDayCell?, weekend: Bool) -> some View {
        Group {
            if let c = cell, let n = c.north {
                let base = n.dir == "out" ? KSSTheme.down : KSSTheme.up
                base.opacity(0.12 + 0.55 * (c.heat ?? 0))
            } else if weekend {
                Color.clear     // 非交易日：空
            } else {
                KSSTheme.textSecondary.opacity(0.07)   // 交易日缺数据：淡灰
            }
        }
    }

    // MARK: 本周时间线

    private var weekTimeline: some View {
        let recent = Array((month?.days ?? []).filter { $0.hasData }.suffix(5))
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                if recent.isEmpty {
                    Text("本月暂无交易日数据").font(.system(size: 12)).foregroundStyle(KSSTheme.textSecondary)
                }
                ForEach(recent) { c in weekCard(c) }
            }
        }
    }

    private func weekCard(_ c: TrendDayCell) -> some View {
        let isSel = c.date == selectedDate
        return Button { onSelectDay(c.date) } label: {
            VStack(alignment: .leading, spacing: 6) {
                Text(String(c.date.suffix(5)))   // MM-DD
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textPrimary)
                if let n = c.north {
                    Text(String(format: "北向 %+.1f亿", n.money))
                        .font(.system(size: 12, weight: .heavy))
                        .foregroundStyle(n.dir == "out" ? KSSTheme.down : KSSTheme.up)
                }
                if c.flags.sector { Text("板块 \(c.sectorCount)").font(.system(size: 10.5)).foregroundStyle(KSSTheme.textSecondary) }
                if c.recCount > 0 {
                    HStack(spacing: 4) {
                        Text("推荐 \(c.recCount)").font(.system(size: 10.5)).foregroundStyle(KSSTheme.textSecondary)
                        if let avg = c.recAvgFwd {
                            Text(String(format: "T+5 %+.1f%%", avg))
                                .font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(avg >= 0 ? KSSTheme.up : KSSTheme.down)
                        }
                    }
                }
            }
            .frame(width: 124, alignment: .leading)
            .padding(11)
            .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                    .strokeBorder(isSel ? KSSTheme.accent : KSSTheme.textSecondary.opacity(0.12), lineWidth: isSel ? 2 : 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: 当日明细

    @ViewBuilder
    private func dayDetail(_ d: TrendDayDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            // 主力资金
            HStack(spacing: 14) {
                if let n = d.north {
                    statTile("北向资金", String(format: "%+.1f 亿", n.money), n.dir == "out" ? KSSTheme.down : KSSTheme.up)
                }
                ForEach(d.etfs ?? []) { e in
                    statTile(e.name, e.pct.map { String(format: "%+.2f%%", $0) } ?? "—",
                             (e.pct ?? 0) >= 0 ? KSSTheme.up : KSSTheme.down)
                }
                Spacer()
            }
            // 板块
            if !d.sectorTop.isEmpty {
                Text("板块强势主题").font(.system(size: 12, weight: .bold)).foregroundStyle(KSSTheme.textSecondary)
                FlowChipsTrends(themes: d.sectorTop)
            }
            // 推荐 T+N
            if !d.recs.isEmpty {
                Text("当日推荐 · 后续表现").font(.system(size: 12, weight: .bold)).foregroundStyle(KSSTheme.textSecondary)
                VStack(spacing: 6) {
                    recHeaderRow
                    ForEach(d.recs) { r in recRow(r) }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }

    private func statTile(_ label: String, _ value: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11)).foregroundStyle(KSSTheme.textSecondary)
            Text(value).font(.system(size: 18, weight: .heavy).monospacedDigit()).foregroundStyle(tint)
        }
        .frame(minWidth: 96, alignment: .leading)
        .padding(10)
        .background(KSSTheme.canvas, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
    }

    private var recHeaderRow: some View {
        HStack(spacing: 8) {
            Text("名称").font(.system(size: 10.5)).foregroundStyle(KSSTheme.textSecondary).frame(width: 150, alignment: .leading)
            Group { Text("T+1"); Text("T+5"); Text("T+20") }
                .font(.system(size: 10.5)).foregroundStyle(KSSTheme.textSecondary).frame(width: 56, alignment: .trailing)
            Spacer()
        }
    }

    private func recRow(_ r: TrendRec) -> some View {
        Button { onSelectSymbol(r.symbol) } label: {
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Text(r.name).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(KSSTheme.textPrimary).lineLimit(1)
                    Text(r.symbol).font(.system(size: 10, design: .monospaced)).foregroundStyle(KSSTheme.textSecondary)
                }
                .frame(width: 150, alignment: .leading)
                fwdCell(r.fwd.t1)
                fwdCell(r.fwd.t5)
                fwdCell(r.fwd.t20)
                Spacer()
                if let asof = r.fwd.asof {
                    Text("@\(String(asof.suffix(5)))").font(.system(size: 9.5, design: .monospaced)).foregroundStyle(KSSTheme.textSecondary)
                }
            }
            .padding(.vertical, 5).padding(.horizontal, 8)
            .background(KSSTheme.canvas, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        }
        .buttonStyle(.plain)
    }

    private func fwdCell(_ v: Double?) -> some View {
        Text(v.map { String(format: "%+.1f", $0) } ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(v == nil ? KSSTheme.textSecondary : ((v ?? 0) >= 0 ? KSSTheme.up : KSSTheme.down))
            .frame(width: 56, alignment: .trailing)
    }

    // MARK: helpers

    private func shiftMonth(_ delta: Int) {
        guard let m = Self.addMonths(currentMonth, delta) else { return }
        currentMonth = m
        onLoadMonth(m)
    }

    private func dayTitle(_ date: String) -> String { date }

    static func monthString(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM"; return f.string(from: d)
    }
    static func addMonths(_ month: String, _ delta: Int) -> String? {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM"
        guard let d = f.date(from: month),
              let nd = Calendar.current.date(byAdding: .month, value: delta, to: d) else { return nil }
        return f.string(from: nd)
    }
    static func daysInMonth(_ month: String) -> [String] {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM"
        guard let d = f.date(from: month),
              let range = Calendar.current.range(of: .day, in: .month, for: d) else { return [] }
        return range.map { String(format: "%@-%02d", month, $0) }
    }
    static func leadingBlanks(_ month: String) -> Int {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM"
        guard let d = f.date(from: month) else { return 0 }
        let wd = Calendar.current.component(.weekday, from: d)   // 1=周日..7=周六
        return (wd + 5) % 7   // 转成周一为 0
    }
    static func isWeekend(_ date: String) -> Bool {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
        guard let d = f.date(from: date) else { return false }
        let wd = Calendar.current.component(.weekday, from: d)
        return wd == 1 || wd == 7
    }
}

/// 板块主题 chips（grade + past5Ret）。
struct FlowChipsTrends: View {
    var themes: [TrendSectorTheme]
    var body: some View {
        FlexWrap(spacing: 8, lineSpacing: 8) {
            ForEach(themes) { t in
                HStack(spacing: 5) {
                    Text(t.name).font(.system(size: 12, weight: .bold)).foregroundStyle(KSSTheme.textPrimary)
                    if let r = t.past5Ret {
                        Text(String(format: "%+.1f%%", r))
                            .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                            .foregroundStyle(r >= 0 ? KSSTheme.up : KSSTheme.down)
                    }
                }
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(KSSTheme.accent.opacity(0.10), in: Capsule())
            }
        }
    }
}

/// 极简流式换行布局（chips 用）。
struct FlexWrap: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > maxW, x > 0 { x = 0; y += lineH + lineSpacing; lineH = 0 }
            x += s.width + spacing; lineH = max(lineH, s.height)
        }
        return CGSize(width: maxW == .infinity ? x : maxW, height: y + lineH)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineH: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > bounds.maxX, x > bounds.minX { x = bounds.minX; y += lineH + lineSpacing; lineH = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing; lineH = max(lineH, s.height)
        }
    }
}
