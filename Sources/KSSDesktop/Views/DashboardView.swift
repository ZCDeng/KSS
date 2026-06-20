import SwiftUI

struct DashboardView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void
    var onOpenSection: (WorkspaceSection) -> Void

    // Material 3 响应式栅格：统一外边距 / 沟槽，内容封顶居中，断点决定主区列数。
    private let margin: CGFloat = 24
    private let gutter: CGFloat = 20
    private let sectionSpacing: CGFloat = 22
    private let maxContent: CGFloat = 1040

    var body: some View {
        GeometryReader { geo in
            let contentW = min(geo.size.width - margin * 2, maxContent)
            ScrollView {
                VStack(alignment: .leading, spacing: sectionSpacing) {
                    HStack(alignment: .top) {
                        PageTitle("今日总览", subtitle: "本地量化研究工作台 · log_mv 选股 / 紫苏叶供应链 / 北证扫描")
                        Spacer(minLength: 16)
                        EditorialDateView()
                    }

                    // 第一行：市场速览（A500ETF ×2 + 北向资金）
                    if let strip = snapshot.marketStrip,
                       (!strip.etfs.isEmpty || strip.northMoney != nil) {
                        MarketStripRow(strip: strip)
                    }

                    // 第二行：指数（上证 / 纳斯达克 / 恒生）
                    if let indices = snapshot.marketStrip?.indices, !indices.isEmpty {
                        MarketIndexRow(indices: indices)
                    }

                    // 指数跑马灯：紧贴指数行下方，无标题，13 指数按涨跌幅排序滚动
                    if let board = snapshot.marketStrip?.indexBoard, !board.isEmpty {
                        IndexMarquee(indices: board)
                    }

                    if let pulse = snapshot.sectorReviews?.first, !pulse.themes.isEmpty {
                        SectorPulseStrip(pulse: pulse)
                    }

                    mainRow(contentW: contentW)

                    if let picks = snapshot.perillaPicks, !picks.isEmpty {
                        SectionHeader("紫苏叶选股", caption: "🌿 供应链护城河评分 Top · 按 perilla_score 排序 · 点击看个股")
                        PerillaPicksTable(items: picks, onSelect: onSelectSymbol)
                    }

                    if let scan = snapshot.bjScan {
                        SectionHeader("北证 50 扫描", caption: "扫描表评分 Top 标的 · 点击看个股")
                        BJScanSection(scan: scan, onSelect: onSelectSymbol)
                    }

                    // 底部：指数一览
                    if let board = snapshot.marketStrip?.indexBoard, !board.isEmpty {
                        SectionHeader("指数一览", caption: "常用宽基 / 主题指数当日表现")
                        IndexBoardGrid(indices: board)
                    }
                }
                .frame(width: contentW, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)   // 内容块居中，余量进外边距
                .padding(.vertical, margin)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
    }

    /// 主区：今日推荐 | 纸交易跟踪。宽屏并排（推荐自适应 + 跟踪定宽），窄屏纵向堆叠。
    @ViewBuilder
    private func mainRow(contentW: CGFloat) -> some View {
        let trackingW = min(max(contentW * 0.32, 300), 360)
        let twoCol = contentW >= 720

        if twoCol {
            HStack(alignment: .top, spacing: gutter) {
                picksColumn.frame(maxWidth: .infinity, alignment: .topLeading)
                trackingColumn.frame(width: trackingW, alignment: .topLeading)
            }
        } else {
            VStack(alignment: .leading, spacing: sectionSpacing) {
                picksColumn
                trackingColumn
            }
        }
    }

    private var picksColumn: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("今日推荐", caption: "log_mv 反向选出的低市值 Top 5 · 买入 T+1 开盘")
            TodayPicksList(items: Array(snapshot.recommendations.prefix(5)), onSelect: onSelectSymbol)
        }
    }

    private var trackingColumn: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("纸交易跟踪", caption: "log_mv 策略纸面累计表现")
            TrackingSummaryCard(tracking: snapshot.tracking)
            HStack(spacing: 10) {
                CountCard(icon: "doc.text.magnifyingglass", count: snapshot.reviews.count, unit: "篇", label: "复盘") {
                    onOpenSection(.reviews)
                }
                CountCard(icon: "chart.xyaxis.line", count: snapshot.backtests.count, unit: "份", label: "回测") {
                    onOpenSection(.backtests)
                }
            }
        }
    }
}

/// 今日推荐：固定列宽的对齐表格（排名 / 名称 / 代码 / 行业 / 状态 / 权重）。
/// 列宽全部固定，表头与每一行共用，保证网格逐列对齐；代码与行业拆成独立列填满版面，
/// 消除名称与右侧之间的大片留白。
enum TodayPickSort: Hashable {
    case rank, name, symbol, industry, status, weight, open, close
}

struct TodayPicksList: View {
    var items: [Recommendation]
    var onSelect: (String) -> Void

    @State private var sortKey: TodayPickSort = .rank
    @State private var ascending = false

    private let wRank: CGFloat = 36
    private let wName: CGFloat = 104
    private let wSymbol: CGFloat = 88
    private let wStatus: CGFloat = 76
    private let wOpen: CGFloat = 60
    private let wClose: CGFloat = 60
    private let wWeight: CGFloat = 68
    private let colSpacing: CGFloat = 12
    private let rowPadH: CGFloat = 14

    private var sortedItems: [Recommendation] {
        let asc = ascending
        switch sortKey {
        case .rank:
            // 默认（asc=false）即 #1→#5，与原视觉一致；排名列无可点列头，asc 不会再切
            return items.sorted { asc ? $0.rank > $1.rank : $0.rank < $1.rank }
        case .name:
            return items.sorted {
                let r = $0.name.localizedCompare($1.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .symbol:
            return items.sorted {
                let r = $0.symbol.localizedCompare($1.symbol)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .industry:
            return items.sorted {
                let r = $0.industry.localizedCompare($1.industry)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .status:
            return items.sorted {
                let r = $0.status.localizedCompare($1.status)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .weight:
            return items.sorted { asc ? $0.weight < $1.weight : $0.weight > $1.weight }
        case .open:
            return items.sorted { byNumber($0.latestOpen, $1.latestOpen, asc: asc) }
        case .close:
            return items.sorted { byNumber($0.latestClose, $1.latestClose, asc: asc) }
        }
    }

    /// 价格显示：两位小数，缺失显示「—」。
    private func priceText(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.2f", v)
    }

    /// 数值列比较：降序大在前、升序小在前，nil 恒排末尾。
    private func byNumber(_ a: Double?, _ b: Double?, asc: Bool) -> Bool {
        switch (a, b) {
        case let (x?, y?): return asc ? x < y : x > y
        case (nil, _?): return false
        case (_?, nil): return true
        case (nil, nil): return false
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(KSSTheme.hairline)
            let rows = sortedItems
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                Button { onSelect(item.symbol) } label: { row(item) }
                    .buttonStyle(.plain)
                if index < rows.count - 1 {
                    Divider().overlay(KSSTheme.hairline)
                }
            }
        }
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
    }

    private var header: some View {
        HStack(spacing: colSpacing) {
            Text("排名").frame(width: wRank, alignment: .leading)
            SortHeaderCell(title: "名称", key: TodayPickSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wName)
            SortHeaderCell(title: "代码", key: TodayPickSort.symbol, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wSymbol)
            SortHeaderCell(title: "行业", key: TodayPickSort.industry, selection: $sortKey, ascending: $ascending,
                           alignment: .leading)
            SortHeaderCell(title: "状态", key: TodayPickSort.status, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wStatus)
            SortHeaderCell(title: "开盘", key: TodayPickSort.open, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wOpen)
            SortHeaderCell(title: "收盘", key: TodayPickSort.close, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wClose)
            SortHeaderCell(title: "权重", key: TodayPickSort.weight, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wWeight)
        }
        .font(.system(size: 10.5, weight: .medium))
        .tracking(0.5)
        .foregroundStyle(KSSTheme.textSecondary)
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func row(_ item: Recommendation) -> some View {
        HStack(spacing: colSpacing) {
            Text("#\(item.rank)")
                .font(.system(size: 15, weight: .heavy, design: .monospaced))
                .foregroundStyle(KSSTheme.accent)
                .frame(width: wRank, alignment: .leading)
            Text(item.name.isEmpty ? item.symbol : item.name)
                .font(.system(size: 14.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
                .frame(width: wName, alignment: .leading)
            Text(item.symbol)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(1)
                .frame(width: wSymbol, alignment: .leading)
            Text(item.industry.isEmpty ? "—" : item.industry)
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            StatusBadge.tracking(item.status)
                .frame(width: wStatus, alignment: .leading)
            Text(priceText(item.latestOpen))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(width: wOpen, alignment: .trailing)
            Text(priceText(item.latestClose))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
                .frame(width: wClose, alignment: .trailing)
            Text(KSSFormat.percent(item.weight))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(1)
                .frame(width: wWeight, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 11)
    }
}

/// 紫苏叶选股表：供应链护城河评分 + 日/周/月/年涨幅 + PE/PB + 流通市值（全宽）。
/// 名称下挂代码、产业链下挂层级·护城河，省出横向空间给行情/估值列。
enum PerillaSort: Hashable {
    case none, name, chains, ret1d, ret5d, ret20d, retYear, pe, pb, mv, score
}

struct PerillaPicksTable: View {
    var items: [PerillaPick]
    var onSelect: (String) -> Void

    // 默认 .none = 保持 bridge 返回的原始顺序（不打乱当前视觉）
    @State private var sortKey: PerillaSort = .none
    @State private var ascending = false

    private let wName: CGFloat = 124
    private let wRet: CGFloat = 58
    private let wPe: CGFloat = 60
    private let wPb: CGFloat = 50
    private let wMv: CGFloat = 80
    private let wScore: CGFloat = 52
    private let colSpacing: CGFloat = 10
    private let rowPadH: CGFloat = 14

    // 数值列：降序=大在前，nil 排末尾（无论升降）。
    private func byNumber(_ a: Double?, _ b: Double?, asc: Bool) -> Bool {
        switch (a, b) {
        case let (x?, y?): return asc ? x < y : x > y
        case (nil, _?): return false   // nil 永远靠后
        case (_?, nil): return true
        case (nil, nil): return false
        }
    }

    private var sortedItems: [PerillaPick] {
        let asc = ascending
        switch sortKey {
        case .none:
            return items
        case .name:
            return items.sorted {
                let r = $0.name.localizedCompare($1.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .chains:
            return items.sorted {
                let r = $0.chains.localizedCompare($1.chains)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .ret1d:  return items.sorted { byNumber($0.ret1d, $1.ret1d, asc: asc) }
        case .ret5d:  return items.sorted { byNumber($0.ret5d, $1.ret5d, asc: asc) }
        case .ret20d: return items.sorted { byNumber($0.ret20d, $1.ret20d, asc: asc) }
        case .retYear: return items.sorted { byNumber($0.retYear, $1.retYear, asc: asc) }
        case .pe:     return items.sorted { byNumber($0.pe, $1.pe, asc: asc) }
        case .pb:     return items.sorted { byNumber($0.pb, $1.pb, asc: asc) }
        case .mv:     return items.sorted { byNumber($0.circMvYi, $1.circMvYi, asc: asc) }
        case .score:  return items.sorted { asc ? $0.score < $1.score : $0.score > $1.score }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(KSSTheme.hairline)
            let rows = sortedItems
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                Button { onSelect(item.symbol) } label: { row(item) }
                    .buttonStyle(.plain)
                if index < rows.count - 1 {
                    Divider().overlay(KSSTheme.hairline)
                }
            }
        }
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
    }

    private var header: some View {
        HStack(spacing: colSpacing) {
            SortHeaderCell(title: "名称 / 代码", key: PerillaSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wName)
            SortHeaderCell(title: "产业链 / 层级·护城河", key: PerillaSort.chains, selection: $sortKey, ascending: $ascending,
                           alignment: .leading)
            SortHeaderCell(title: "日", key: PerillaSort.ret1d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "周", key: PerillaSort.ret5d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "月", key: PerillaSort.ret20d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "年", key: PerillaSort.retYear, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "PE", key: PerillaSort.pe, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wPe)
            SortHeaderCell(title: "PB", key: PerillaSort.pb, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wPb)
            SortHeaderCell(title: "流通市值", key: PerillaSort.mv, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wMv)
            SortHeaderCell(title: "评分", key: PerillaSort.score, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wScore)
        }
        .font(.system(size: 10.5, weight: .medium))
        .tracking(0.3)
        .foregroundStyle(KSSTheme.textSecondary)
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func row(_ item: PerillaPick) -> some View {
        HStack(spacing: colSpacing) {
            // 名称 + 代码
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                    .lineLimit(1)
                Text(item.symbol)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
            .frame(width: wName, alignment: .leading)

            // 产业链 + 层级·护城河
            VStack(alignment: .leading, spacing: 2) {
                Text(item.chains.isEmpty ? "—" : item.chains)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(KSSTheme.textBody)
                    .lineLimit(1)
                HStack(spacing: 5) {
                    Text("\(layerLabel(item)) · \(item.moat)")
                        .font(.system(size: 10.5))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .lineLimit(1)
                    if item.locked {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 8))
                            .foregroundStyle(KSSTheme.accent)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            retCell(item.ret1d)
            retCell(item.ret5d)
            retCell(item.ret20d)
            retCell(item.retYear)

            Text(numText(item.pe))
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(width: wPe, alignment: .trailing)
            Text(numText(item.pb))
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(width: wPb, alignment: .trailing)
            Text(mvText(item.circMvYi))
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(width: wMv, alignment: .trailing)

            Text(String(format: "%.2f", item.score))
                .font(.system(size: 13, weight: .heavy, design: .monospaced))
                .foregroundStyle(KSSTheme.accent)
                .lineLimit(1)
                .frame(width: wScore, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func retCell(_ value: Double?) -> some View {
        Text(value.map { KSSFormat.percent($0) } ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(value == nil ? KSSTheme.textSecondary : KSSTheme.signColor(value!))
            .lineLimit(1)
            .frame(width: wRet, alignment: .trailing)
    }

    private func numText(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f", v)
    }

    private func mvText(_ yi: Double?) -> String {
        guard let yi else { return "—" }
        if yi >= 1000 { return String(format: "%.0f亿", yi) }
        return String(format: "%.1f亿", yi)
    }

    private func layerLabel(_ item: PerillaPick) -> String {
        let roleCN: String
        switch item.role {
        case "material": roleCN = "材料"
        case "equipment": roleCN = "设备"
        case "component": roleCN = "零部件"
        case "assembly": roleCN = "整机"
        default: roleCN = item.role
        }
        return "L\(item.layer)·\(roleCN)"
    }
}

/// 今日板块信息图：6 个主题卡片，资金申赎 + 近 5 日涨幅 + 强势确认分级。
struct SectorPulseStrip: View {
    var pulse: SectorPulse

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2).fill(KSSTheme.accent).frame(width: 4, height: 18)
                Text("今日板块")
                    .font(KSSFont.serif(18, .semibold))
                    .foregroundStyle(KSSTheme.textPrimary)
                Text(regimeText)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(pulse.regimeInRegime == true ? KSSTheme.up : KSSTheme.textSecondary)
                Spacer()
                Text("资金正=申购/负=赎回 · 5日赎回≥2%=强势确认")
                    .font(.system(size: 11))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
            .padding(.top, 6)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 152), spacing: 12)], spacing: 12) {
                ForEach(pulse.themes) { theme in
                    SectorChip(theme: theme)
                }
            }
        }
    }

    private var regimeText: String {
        guard let mom = pulse.regimeMom20 else { return "" }
        let on = pulse.regimeInRegime == true
        return "动量 \(String(format: "%.1f", mom)) · \(on ? "趋势确认" : "震荡")"
    }
}

struct SectorChip: View {
    var theme: SectorTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 5) {
                Text(theme.name)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                    .lineLimit(1)
                if theme.accel {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(KSSTheme.accent)
                        .help("资金加速")
                }
                Spacer(minLength: 4)
                gradeBadge
            }
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("近5日")
                    .font(.system(size: 10))
                    .foregroundStyle(KSSTheme.textSecondary)
                Text(theme.past5Ret.map { KSSFormat.percent($0 / 100) } ?? "—")
                    .font(KSSFont.harmonyNumber(18))
                    .foregroundStyle(KSSTheme.signColor(theme.past5Ret ?? 0))
            }
            HStack(spacing: 10) {
                flowItem("1日", theme.flow1d)
                flowItem("5日", theme.flow5d)
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 12)
    }

    private var gradeBadge: some View {
        let warn = theme.divergence || theme.grade.contains("预警") || theme.grade.contains("见顶")
        let strong = theme.grade.contains("强势")
        let bg = warn ? KSSTheme.up : (strong ? KSSTheme.accent : KSSTheme.textSecondary.opacity(0.18))
        let fg = (warn || strong) ? Color.white : KSSTheme.textBody
        return Text(theme.divergence ? "见顶预警" : theme.grade)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(fg)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(bg, in: Capsule())
    }

    /// 资金流（正=申购/负=赎回）。语义上「赎回≠利空」由分级徽标承载，故此处中性着色，
    /// 只呈现方向与量级，避免把申购误读成上涨。
    private func flowItem(_ label: String, _ flow: Double?) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(.system(size: 10))
                .foregroundStyle(KSSTheme.textSecondary)
            Text(flow.map { String(format: "%+.1f", $0) } ?? "—")
                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
        }
        .lineLimit(1)
        .fixedSize()
    }
}

/// 编辑风日期戳：大号衬线 MM.DD + 小号 年/星期 右侧堆叠（复刻杂志日期设计）。
struct EditorialDateView: View {
    var date = Date()

    var body: some View {
        HStack(alignment: .top, spacing: 7) {
            Text(monthDay)
                .font(.system(size: 34, weight: .bold, design: .serif))
                .foregroundStyle(KSSTheme.textPrimary)
                .monospacedDigit()
            VStack(alignment: .leading, spacing: 1) {
                Text(year)
                    .foregroundStyle(KSSTheme.textSecondary)
                Text(weekday)
                    .foregroundStyle(KSSTheme.accent)
            }
            .font(.system(size: 12, weight: .semibold, design: .serif))
            .padding(.top, 3)
        }
        .fixedSize()
    }

    private var comps: DateComponents {
        Calendar.current.dateComponents([.year, .month, .day], from: date)
    }
    private var monthDay: String { String(format: "%02d.%02d", comps.month ?? 0, comps.day ?? 0) }
    private var year: String { String(comps.year ?? 0) }
    private var weekday: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateFormat = "EEE"
        return f.string(from: date).uppercased()
    }
}

/// 总览第一行市场速览：A500ETF(563360/159361) 当日 + 北向资金净流入。
struct MarketStripRow: View {
    var strip: MarketStrip

    var body: some View {
        HStack(spacing: 12) {
            ForEach(strip.etfs) { etf in
                card(title: etf.name,
                     sub: etf.code,
                     value: String(format: "%.3f", etf.close),
                     delta: etf.pct,
                     deltaText: String(format: "%+.2f%%", etf.pct))
            }
            if let nm = strip.northMoney {
                let yi = nm / 10000.0
                card(title: "北向资金",
                     sub: northSub,
                     value: String(format: "%+.1f", yi) + " 亿",
                     delta: yi,
                     deltaText: yi >= 0 ? "净流入" : "净流出")
            }
        }
    }

    private var northSub: String {
        guard let d = strip.northDate, d.count == 8 else { return "沪深港通" }
        return "\(d.prefix(4))-\(d.dropFirst(4).prefix(2))-\(d.suffix(2))"
    }

    private func card(title: String, sub: String, value: String, delta: Double, deltaText: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(title)
                    .font(.system(size: 13.5, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text(sub)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(value)
                    .font(KSSFont.harmonyNumber(22))
                    .foregroundStyle(KSSTheme.signColor(delta))
                    .lineLimit(1)
                Text(deltaText)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.signColor(delta))
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

/// 指数一览：13 个常用指数自适应网格（名称 / 收盘 / 涨跌%，红涨绿跌）。
struct IndexBoardGrid: View {
    var indices: [IndexQuote]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 158), spacing: 10)], spacing: 10) {
            ForEach(indices) { idx in
                VStack(alignment: .leading, spacing: 5) {
                    Text(idx.name)
                        .font(.system(size: 12.5, weight: .bold))
                        .foregroundStyle(KSSTheme.textPrimary)
                        .lineLimit(1)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(String(format: "%.2f", idx.close))
                            .font(KSSFont.harmonyNumber(16))
                            .foregroundStyle(KSSTheme.signColor(idx.pct))
                            .lineLimit(1)
                        Spacer(minLength: 0)
                        Text(String(format: "%+.2f%%", idx.pct))
                            .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KSSTheme.signColor(idx.pct))
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .kssCard(padding: 11)
            }
        }
    }
}

/// 指数跑马灯：13 指数按涨跌幅降序，TimelineView 驱动无缝循环横向滚动。
/// 参照 M3 carousel —— 圆角容器(shapeL) + 两端淡出遮罩 + 一致项高，展示型不可点。
struct IndexMarquee: View {
    var indices: [IndexQuote]

    private let gap: CGFloat = 10
    private let speed: Double = 42            // 滚动速度 pts/s
    @State private var rowWidth: CGFloat = 0  // 单份内容宽（含内部间距）

    private var sorted: [IndexQuote] {
        indices.sorted { $0.pct > $1.pct }    // 涨幅高→低
    }

    var body: some View {
        // GeometryReader 取内容列实际宽，把滚动行钉在该宽度内（leading 对齐 + clip），
        // 避免溢出内容列右边距冲到窗口边缘。
        GeometryReader { geo in
            TimelineView(.animation) { timeline in
                let period = rowWidth + gap    // 一个循环周期 = 单份宽 + 拼接缝
                let elapsed = timeline.date.timeIntervalSinceReferenceDate
                let offset = period > 0
                    ? -CGFloat((elapsed * speed).truncatingRemainder(dividingBy: Double(period)))
                    : 0
                HStack(spacing: gap) {
                    row(measured: true)
                    row(measured: false)       // 第二份用于无缝衔接
                }
                .offset(x: offset)
                .frame(width: geo.size.width, alignment: .leading)
            }
        }
        .frame(height: 46)
        .clipped()
        .mask(edgeFade)                        // M3 carousel 两端淡出
        .onPreferenceChange(MarqueeWidthKey.self) { rowWidth = $0 }
    }

    private func row(measured: Bool) -> some View {
        HStack(spacing: gap) {
            ForEach(sorted) { chip($0) }
        }
        .background {
            if measured {
                GeometryReader { g in
                    Color.clear.preference(key: MarqueeWidthKey.self, value: g.size.width)
                }
            }
        }
    }

    private func chip(_ idx: IndexQuote) -> some View {
        HStack(spacing: 6) {
            Image(systemName: idx.pct >= 0 ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(KSSTheme.signColor(idx.pct))
            Text(idx.name)
                .font(.system(size: 12.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
            Text(String(format: "%.2f", idx.close))
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(1)
            Text(String(format: "%+.2f%%", idx.pct))
                .font(.system(size: 12, weight: .heavy, design: .monospaced))
                .foregroundStyle(KSSTheme.signColor(idx.pct))
                .lineLimit(1)
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeL)
                .strokeBorder(KSSTheme.signColor(idx.pct).opacity(0.18), lineWidth: 1)
        )
        .fixedSize()
    }

    private var edgeFade: some View {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0),
                .init(color: .black, location: 0.035),
                .init(color: .black, location: 0.965),
                .init(color: .clear, location: 1),
            ],
            startPoint: .leading, endPoint: .trailing
        )
    }
}

private struct MarqueeWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

/// 总览第二行：上证 / 纳斯达克 / 恒生 指数当日。
struct MarketIndexRow: View {
    var indices: [IndexQuote]

    var body: some View {
        HStack(spacing: 12) {
            ForEach(indices) { idx in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        Text(idx.name)
                            .font(.system(size: 13.5, weight: .bold))
                            .foregroundStyle(KSSTheme.textPrimary)
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        Text(dateLabel(idx.date))
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                            .lineLimit(1)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(String(format: "%.2f", idx.close))
                            .font(KSSFont.harmonyNumber(22))
                            .foregroundStyle(KSSTheme.signColor(idx.pct))
                            .lineLimit(1)
                        Text(String(format: "%+.2f%%", idx.pct))
                            .font(.system(size: 12, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KSSTheme.signColor(idx.pct))
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .kssCard(padding: 14)
            }
        }
    }

    private func dateLabel(_ raw: String?) -> String {
        guard let raw, raw.count == 8 else { return raw ?? "" }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }
}

/// 计数卡：复盘 / 回测这类「只看数量、点击跳转」的内容，不占大版面。
struct CountCard: View {
    var icon: String
    var count: Int
    var unit: String
    var label: String
    var onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: icon)
                        .font(.system(size: 14))
                        .foregroundStyle(KSSTheme.accent)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(KSSTheme.textSecondary)
                }
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text("\(count)")
                        .font(KSSFont.harmonyNumber(24))
                        .foregroundStyle(KSSTheme.textPrimary)
                    Text(unit)
                        .font(.system(size: 12))
                        .foregroundStyle(KSSTheme.textSecondary)
                }
                Text(label)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: 12)
        }
        .buttonStyle(.plain)
    }
}

/// 纸交易跟踪汇总卡：年化 / Sharpe / 回撤 / 胜率 / 样本。
struct TrackingSummaryCard: View {
    var tracking: TrackingSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                metric("年化", KSSFormat.percent(tracking.annualized), KSSTheme.signColor(tracking.annualized))
                metric("Sharpe", KSSFormat.number(tracking.sharpe), KSSTheme.signColor(tracking.sharpe))
                metric("最大回撤", KSSFormat.percent(tracking.maxDrawdown), KSSTheme.signColor(tracking.maxDrawdown))
                metric("胜率", KSSFormat.percent(tracking.winRate), KSSTheme.textPrimary)
            }
            Divider().overlay(KSSTheme.hairline)
            HStack {
                Text("样本天数")
                    .font(.system(size: 12)).foregroundStyle(KSSTheme.textSecondary)
                Spacer()
                Text("\(tracking.nDaysWithReturns) / \(tracking.nDaysLogged)")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textPrimary)
            }
            if let message = tracking.message {
                Text(message)
                    .font(.system(size: 11.5))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }

    private func metric(_ label: String, _ value: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 10, weight: .medium)).tracking(0.5)
                .foregroundStyle(KSSTheme.textSecondary)
            Text(value)
                .font(KSSFont.harmonyNumber(19))
                .foregroundStyle(tint)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct SectionHeader: View {
    var title: String
    var caption: String?

    init(_ title: String, caption: String? = nil) {
        self.title = title
        self.caption = caption
    }

    var body: some View {
        // Bold section title with a blurple accent bar + optional caption.
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(KSSTheme.accent)
                    .frame(width: 4, height: 18)
                Text(title)
                    .font(KSSFont.serif(18, .semibold))
                    .foregroundStyle(KSSTheme.textPrimary)
            }
            if let caption {
                Text(caption)
                    .font(.system(size: 11.5))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
        }
        .padding(.top, 6)
    }
}

struct StatTile: View {
    var title: String
    var value: String
    var tint: Color = KSSTheme.textPrimary

    var body: some View {
        // Discord KPI tile: uppercase tracked muted label, display value, optional delta tint.
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(.system(size: 10.5, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(KSSTheme.textSecondary)
            Text(value)
                .font(KSSFont.harmonyNumber(20))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct RecommendationCard: View {
    var item: Recommendation

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("#\(item.rank)")
                    .font(.system(size: 13, weight: .bold).monospacedDigit())
                    .foregroundStyle(KSSTheme.accent)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(KSSTheme.accent.opacity(0.15), in: Capsule())
                Spacer()
                StatusBadge.tracking(item.status)
            }
            Text(item.name.isEmpty ? item.symbol : item.name)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
            Text(item.symbol)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
            HStack {
                LabeledMetric("权重", KSSFormat.percent(item.weight))
                LabeledMetric("跟踪", KSSFormat.percent(item.trackingReturn), tint: KSSTheme.signColor(item.trackingReturn))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct BJScanSection: View {
    var scan: BJScan
    var onSelect: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                StatTile(title: "扫描日", value: bjDate(scan.scanDate))
                StatTile(title: "标的数", value: "\(scan.total)")
                StatTile(title: "通过筛选", value: "\(scan.passed)", tint: KSSTheme.accent)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], spacing: 10) {
                ForEach(scan.top) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(.system(size: 14.5, weight: .bold))
                                .foregroundStyle(KSSTheme.textPrimary)
                                .lineLimit(1)
                            Spacer()
                            Text(KSSFormat.number(item.score, digits: 2))
                                .font(.system(size: 13, weight: .heavy, design: .monospaced))
                                .foregroundStyle(KSSTheme.accent)
                        }
                        Text("\(item.symbol) · \(item.industry)")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                            .lineLimit(1)
                        HStack {
                            Text(item.tag)
                                .font(.system(size: 10.5))
                                .foregroundStyle(KSSTheme.textSecondary)
                                .lineLimit(1)
                            Spacer()
                            Text("20日 " + KSSFormat.percent(item.ret20d))
                                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                                .foregroundStyle(KSSTheme.signColor(item.ret20d))
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: 12)
                    .onTapGesture { onSelect(item.symbol) }
                }
            }
        }
    }

    private func bjDate(_ raw: String?) -> String {
        guard let raw, raw.count == 8 else { return raw ?? "-" }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }
}

struct LabeledMetric: View {
    var label: String
    var value: String
    var tint: Color

    init(_ label: String, _ value: String, tint: Color = KSSTheme.textPrimary) {
        self.label = label
        self.value = value
        self.tint = tint
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(KSSTheme.textSecondary)
            Text(value)
                .font(.callout.monospacedDigit())
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
