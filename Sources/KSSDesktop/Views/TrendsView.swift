import SwiftUI

/// 趋势页：统一月历（底色=增量资金热度 · 字=强势板块）+ 本周时间线 + 当日明细（板块/代表股优先）。
enum TrendRecSort: Hashable {
    case none      // 维持后端下发顺序（默认）
    case name
    case t1
    case t5
    case t20
}

struct TrendsView: View {
    @Environment(\.kssTheme) private var theme
    var month: TrendMonth?
    var detail: TrendDayDetail?
    var selectedDate: String?
    var loading: Bool
    var onLoadMonth: (String) -> Void
    var onSelectDay: (String) -> Void
    var onSelectSymbol: (String) -> Void

    @State private var currentMonth: String = ""   // YYYY-MM
    @State private var recSortKey: TrendRecSort = .none
    @State private var recSortAsc = false
    @State private var capitalExpanded = false

    private let cellHeight: CGFloat = 60

    private var cellByDate: [String: TrendDayCell] {
        Dictionary(uniqueKeysWithValues: (month?.days ?? []).map { ($0.date, $0) })
    }

    var body: some View {
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageTitle("趋势观察", subtitle: "资金热度 + 强势板块 · 按天扫月 · 点日看明细")

                    monthHeader

                    // 单一大月历：底色=增量资金 · 字=顶板块
                    SectionHeader(
                        "趋势月历",
                        caption: "底色=增量资金强度（红流入 / 绿流出）· 字=强势板块 · 点日看明细"
                    )
                    unifiedGrid
                    if !loading, (month?.days ?? []).isEmpty {
                        Text("本月暂无归档数据")
                            .font(.system(size: 12))
                            .foregroundStyle(theme.textSecondary)
                    }

                    SectionHeader("本周", caption: "最近 5 个交易日")
                    weekTimeline

                    if let d = detail, d.found {
                        SectionHeader(dayTitle(d.date), caption: "热点板块 · 代表股 · 资金摘要")
                        dayDetail(d)
                    } else if selectedDate != nil {
                        Text("该日暂无归档数据")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.textSecondary)
                    }
                }
                .frame(width: w, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
        .background(theme.canvas)
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
                .font(KSSFont.harmonyNumber(18))
                .foregroundStyle(theme.textPrimary)
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

    // MARK: 统一月历（底色=inflowScore · 字=topSector）

    private var unifiedGrid: some View {
        let days = Self.daysInMonth(currentMonth)
        let leading = Self.leadingBlanks(currentMonth)
        let cols = Array(repeating: GridItem(.flexible(), spacing: 6), count: 7)
        return VStack(alignment: .leading, spacing: 8) {
            weekdayHeader(size: 10)
            LazyVGrid(columns: cols, spacing: 6) {
                ForEach(0..<leading, id: \.self) { _ in Color.clear.frame(height: cellHeight) }
                ForEach(days, id: \.self) { date in unifiedCell(date) }
            }
        }
        .padding(12)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .opacity(loading && month == nil ? 0.55 : 1)
    }

    @ViewBuilder
    private func unifiedCell(_ date: String) -> some View {
        let cell = cellByDate[date]
        let day = String(date.suffix(2))
        let isSelected = date == selectedDate
        let weekend = Self.isWeekend(date)
        let strongHeat = (cell?.inflowScore).map { abs($0) >= 0.55 } ?? false
        let sectorInk: Color = (isSelected || strongHeat) ? theme.textPrimary : theme.accent
        Button { if cell != nil { onSelectDay(date) } } label: {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 2) {
                    Text(day)
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(cell != nil ? theme.textPrimary : theme.textSecondary.opacity(0.5))
                    Spacer(minLength: 0)
                    if let c = cell, let s = c.inflowScore, abs(s) >= 0.45 {
                        Image(systemName: (c.inflowDir == "out") ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
                            .font(.system(size: 7, weight: .bold))
                            .foregroundStyle((c.inflowDir == "out") ? theme.down : theme.up)
                    }
                }
                Spacer(minLength: 0)
                if let c = cell, let top = c.topSector {
                    Text(top)
                        .font(.system(size: 10.5, weight: .bold))
                        .foregroundStyle(sectorInk)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                    if c.sectorCount > 1 {
                        Text("+\(c.sectorCount - 1)")
                            .font(.system(size: 8.5))
                            .foregroundStyle(theme.textSecondary)
                    }
                } else if cell != nil {
                    Text("—")
                        .font(.system(size: 10))
                        .foregroundStyle(theme.textSecondary.opacity(0.5))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: cellHeight)
            .padding(.horizontal, 6).padding(.vertical, 5)
            .background(inflowBackground(cell, weekend: weekend))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.shapeS)
                    .strokeBorder(isSelected ? theme.accent : .clear, lineWidth: 2)
            )
            .clipShape(RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        }
        .buttonStyle(.plain)
        .disabled(cell == nil)
        .help(unifiedHelp(date: date, cell: cell, weekend: weekend))
    }

    private func inflowBackground(_ cell: TrendDayCell?, weekend: Bool) -> some View {
        Group {
            if let c = cell, let s = c.inflowScore {
                let base = (c.inflowDir == "out") ? theme.down : theme.up
                base.opacity(0.12 + 0.65 * min(abs(s), 1))
            } else if let c = cell {
                // 有归档但无资金分：浅中性底，仍可点
                c.flags.sector ? theme.accent.opacity(0.06) : theme.canvas.opacity(0.55)
            } else if weekend {
                Color.clear
            } else {
                theme.textSecondary.opacity(0.07)
            }
        }
    }

    private func unifiedHelp(date: String, cell: TrendDayCell?, weekend: Bool) -> String {
        guard let c = cell else { return weekend ? "非交易日" : "无归档数据" }
        var parts: [String] = [date]
        if let s = c.inflowScore {
            parts.append(String(format: "增量资金 %.2f", s))
        }
        if let top = c.topSector {
            parts.append(top)
        }
        return parts.joined(separator: " · ")
    }

    private func weekdayHeader(size: CGFloat) -> some View {
        HStack(spacing: 6) {
            ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { wd in
                Text(wd).font(.system(size: size, weight: .semibold))
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity)
            }
        }
    }

    // MARK: 本周时间线

    private var weekTimeline: some View {
        let recent = Array((month?.days ?? []).filter { $0.hasData }.suffix(5))
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                if recent.isEmpty {
                    Text(loading ? "加载中…" : "本月暂无交易日数据")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
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
                    .foregroundStyle(theme.textPrimary)
                if let n = c.north {
                    Text(String(format: "北向 %+.1f亿", n.money))
                        .font(.system(size: 12, weight: .heavy))
                        .foregroundStyle(n.dir == "out" ? theme.down : theme.up)
                }
                if c.flags.sector { Text("板块 \(c.sectorCount)").font(.system(size: 10.5)).foregroundStyle(theme.textSecondary) }
                if c.recCount > 0 {
                    HStack(spacing: 4) {
                        Text("推荐 \(c.recCount)").font(.system(size: 10.5)).foregroundStyle(theme.textSecondary)
                        if let avg = c.recAvgFwd {
                            Text(String(format: "T+5 %+.1f%%", avg))
                                .font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(avg >= 0 ? theme.up : theme.down)
                        }
                    }
                }
            }
            .frame(width: 124, alignment: .leading)
            .padding(11)
            .background(theme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                    .strokeBorder(isSel ? theme.accent : theme.textSecondary.opacity(0.12), lineWidth: isSel ? 2 : 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: 当日明细（板块 + 代表股优先，资金摘要次之）

    @ViewBuilder
    private func dayDetail(_ d: TrendDayDetail) -> some View {
        let cell = cellByDate[d.date]
        VStack(alignment: .leading, spacing: 12) {
            // 1) 热点板块
            if !d.sectorTop.isEmpty {
                Text("热点强势板块").font(.system(size: 12, weight: .bold)).foregroundStyle(theme.textSecondary)
                FlowChipsTrends(themes: d.sectorTop)
            }

            // 2) 代表股 = 当日推荐 recs
            if !d.recs.isEmpty {
                Text("代表股 · 后续表现").font(.system(size: 12, weight: .bold)).foregroundStyle(theme.textSecondary)
                VStack(spacing: 6) {
                    recHeaderRow
                    ForEach(sortedRecs(d.recs)) { r in recRow(r) }
                }
            } else if d.sectorTop.isEmpty {
                Text("该日暂无板块与代表股明细")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textSecondary)
            }

            // 3) 资金摘要（次要，可展开看明细）
            capitalSummary(d, cell: cell)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }

    @ViewBuilder
    private func capitalSummary(_ d: TrendDayDetail, cell: TrendDayCell?) -> some View {
        let hasNorth = d.north != nil
        let hasEtfs = !(d.etfs ?? []).isEmpty
        let hasScore = cell?.inflowScore != nil
        if hasNorth || hasEtfs || hasScore {
            DisclosureGroup(isExpanded: $capitalExpanded) {
                HStack(spacing: 12) {
                    if let n = d.north {
                        statTile("北向资金", String(format: "%+.1f 亿", n.money), n.dir == "out" ? theme.down : theme.up)
                    }
                    ForEach(d.etfs ?? []) { e in
                        statTile(e.name, e.pct.map { String(format: "%+.2f%%", $0) } ?? "—",
                                 (e.pct ?? 0) >= 0 ? theme.up : theme.down)
                    }
                    if let s = cell?.inflowScore {
                        statTile("增量资金", String(format: "%+.2f", s), s >= 0 ? theme.up : theme.down)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.top, 8)
            } label: {
                HStack(spacing: 8) {
                    Text("资金摘要")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(theme.textSecondary)
                    if let n = d.north {
                        Text(String(format: "北向 %+.1f亿", n.money))
                            .font(.system(size: 12, weight: .semibold, design: .monospaced))
                            .foregroundStyle(n.dir == "out" ? theme.down : theme.up)
                    }
                    if let s = cell?.inflowScore {
                        Text(String(format: "增量 %+.2f", s))
                            .font(.system(size: 12, weight: .semibold, design: .monospaced))
                            .foregroundStyle(s >= 0 ? theme.up : theme.down)
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func statTile(_ label: String, _ value: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11)).foregroundStyle(theme.textSecondary)
            Text(value).font(KSSFont.harmonyNumber(18)).foregroundStyle(tint)
        }
        .frame(minWidth: 96, alignment: .leading)
        .padding(10)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
    }

    private var recHeaderRow: some View {
        HStack(spacing: 8) {
            SortHeaderCell(title: "名称", key: TrendRecSort.name, selection: $recSortKey, ascending: $recSortAsc,
                           alignment: .leading, width: 150)
            SortHeaderCell(title: "T+1", key: TrendRecSort.t1, selection: $recSortKey, ascending: $recSortAsc,
                           alignment: .trailing, width: 56)
            SortHeaderCell(title: "T+5", key: TrendRecSort.t5, selection: $recSortKey, ascending: $recSortAsc,
                           alignment: .trailing, width: 56)
            SortHeaderCell(title: "T+20", key: TrendRecSort.t20, selection: $recSortKey, ascending: $recSortAsc,
                           alignment: .trailing, width: 56)
            Spacer()
        }
    }

    /// 当日推荐排序：默认维持下发顺序；数值列降序大在前、nil 末尾；名称走 localizedCompare。
    private func sortedRecs(_ recs: [TrendRec]) -> [TrendRec] {
        guard recSortKey != .none else { return recs }
        if recSortKey == .name {
            return recs.sorted {
                let r = $0.name.localizedCompare($1.name)
                return recSortAsc ? r == .orderedAscending : r == .orderedDescending
            }
        }
        let value: (TrendRec) -> Double? = { rec in
            switch recSortKey {
            case .t1: return rec.fwd.t1
            case .t5: return rec.fwd.t5
            case .t20: return rec.fwd.t20
            default: return nil
            }
        }
        return recs.sorted { a, b in
            switch (value(a), value(b)) {
            case let (x?, y?): return recSortAsc ? x < y : x > y
            case (nil, _?): return false   // nil 永远排在末尾
            case (_?, nil): return true
            case (nil, nil): return false
            }
        }
    }

    private func recRow(_ r: TrendRec) -> some View {
        Button { onSelectSymbol(r.symbol) } label: {
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Text(r.name).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(theme.textPrimary).lineLimit(1)
                    Text(r.symbol).font(.system(size: 10, design: .monospaced)).foregroundStyle(theme.textSecondary)
                }
                .frame(width: 150, alignment: .leading)
                fwdCell(r.fwd.t1)
                fwdCell(r.fwd.t5)
                fwdCell(r.fwd.t20)
                Spacer()
                if let asof = r.fwd.asof {
                    Text("@\(String(asof.suffix(5)))").font(.system(size: 9.5, design: .monospaced)).foregroundStyle(theme.textSecondary)
                }
            }
            .padding(.vertical, 5).padding(.horizontal, 8)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
        }
        .buttonStyle(.plain)
    }

    private func fwdCell(_ v: Double?) -> some View {
        Text(v.map { String(format: "%+.1f", $0) } ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(v == nil ? theme.textSecondary : ((v ?? 0) >= 0 ? theme.up : theme.down))
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
    @Environment(\.kssTheme) private var theme
    var themes: [TrendSectorTheme]
    var body: some View {
        FlexWrap(spacing: 8, lineSpacing: 8) {
            ForEach(themes) { t in
                HStack(spacing: 5) {
                    Text(t.name).font(.system(size: 12, weight: .bold)).foregroundStyle(theme.textPrimary)
                    if let r = t.past5Ret {
                        Text(String(format: "%+.1f%%", r))
                            .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                            .foregroundStyle(r >= 0 ? theme.up : theme.down)
                    }
                }
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(theme.accent.opacity(0.10), in: Capsule())
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
