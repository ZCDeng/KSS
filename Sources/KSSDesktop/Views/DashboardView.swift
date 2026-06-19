import SwiftUI

struct DashboardView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void
    var onOpenSection: (WorkspaceSection) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                // 数据日期 / 股票数 / 最新推荐 等全局指标统一只在边栏状态区呈现，
                // 此处不再重复（避免标题副标题 + KPI 条 + 边栏三处同值）。
                PageTitle("总览", subtitle: "本地量化研究工作台 · log_mv 选股 / 紫苏叶供应链 / 北证扫描")

                // 1) 主区两栏：今日推荐 | 纸交易跟踪 + 资产计数
                //    左栏按内容宽度封顶（避免表格被拉得过宽留大片空白），
                //    多出的横向空间收进两栏之间的留白槽，右栏继续贴右。
                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionHeader("今日推荐", caption: "log_mv 反向选出的低市值 Top 5 · 买入 T+1 开盘")
                        TodayPicksList(items: Array(snapshot.recommendations.prefix(5)), onSelect: onSelectSymbol)
                    }
                    .frame(maxWidth: 760, alignment: .topLeading)

                    Spacer(minLength: 14)

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
                    .frame(width: 340, alignment: .topLeading)
                }

                // 2) 紫苏叶选股：供应链护城河评分
                if let picks = snapshot.perillaPicks, !picks.isEmpty {
                    SectionHeader("紫苏叶选股", caption: "🌿 供应链护城河评分 Top · 按 perilla_score 排序 · 点击看个股")
                    PerillaPicksTable(items: picks, onSelect: onSelectSymbol)
                }

                // 3) 北证 50 扫描
                if let scan = snapshot.bjScan {
                    SectionHeader("北证 50 扫描", caption: "扫描表评分 Top 标的 · 点击看个股")
                    BJScanSection(scan: scan, onSelect: onSelectSymbol)
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
    }
}

/// 今日推荐：固定列宽的对齐表格（排名 / 名称 / 代码 / 行业 / 状态 / 权重）。
/// 列宽全部固定，表头与每一行共用，保证网格逐列对齐；代码与行业拆成独立列填满版面，
/// 消除名称与右侧之间的大片留白。
struct TodayPicksList: View {
    var items: [Recommendation]
    var onSelect: (String) -> Void

    private let wRank: CGFloat = 38
    private let wName: CGFloat = 128
    private let wSymbol: CGFloat = 116
    private let wIndustry: CGFloat = 104
    private let wStatus: CGFloat = 92
    private let wWeight: CGFloat = 80
    private let colSpacing: CGFloat = 14
    private let rowPadH: CGFloat = 14

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(KSSTheme.hairline)
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                Button { onSelect(item.symbol) } label: { row(item) }
                    .buttonStyle(.plain)
                if index < items.count - 1 {
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
            Text("名称").frame(width: wName, alignment: .leading)
            Text("代码").frame(width: wSymbol, alignment: .leading)
            Text("行业").frame(width: wIndustry, alignment: .leading)
            Spacer(minLength: 12)
            Text("状态").frame(width: wStatus, alignment: .leading)
            Text("权重").frame(width: wWeight, alignment: .trailing)
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
                .frame(width: wIndustry, alignment: .leading)
            Spacer(minLength: 12)
            StatusBadge.tracking(item.status)
                .frame(width: wStatus, alignment: .leading)
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

/// 紫苏叶选股表：供应链护城河评分 Top 标的（全宽，产业链列自适应填满）。
struct PerillaPicksTable: View {
    var items: [PerillaPick]
    var onSelect: (String) -> Void

    private let wName: CGFloat = 132
    private let wSymbol: CGFloat = 116
    private let wLayer: CGFloat = 116
    private let wMoat: CGFloat = 172
    private let wScore: CGFloat = 64
    private let colSpacing: CGFloat = 14
    private let rowPadH: CGFloat = 14

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(KSSTheme.hairline)
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                Button { onSelect(item.symbol) } label: { row(item) }
                    .buttonStyle(.plain)
                if index < items.count - 1 {
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
            Text("名称").frame(width: wName, alignment: .leading)
            Text("代码").frame(width: wSymbol, alignment: .leading)
            Text("产业链").frame(maxWidth: .infinity, alignment: .leading)
            Text("层级").frame(width: wLayer, alignment: .leading)
            Text("护城河").frame(width: wMoat, alignment: .leading)
            Text("评分").frame(width: wScore, alignment: .trailing)
        }
        .font(.system(size: 10.5, weight: .medium))
        .tracking(0.5)
        .foregroundStyle(KSSTheme.textSecondary)
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func row(_ item: PerillaPick) -> some View {
        HStack(spacing: colSpacing) {
            Text(item.name)
                .font(.system(size: 14.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
                .frame(width: wName, alignment: .leading)
            Text(item.symbol)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(1)
                .frame(width: wSymbol, alignment: .leading)
            Text(item.chains.isEmpty ? "—" : item.chains)
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(layerLabel(item))
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
                .lineLimit(1)
                .frame(width: wLayer, alignment: .leading)
            HStack(spacing: 5) {
                Text(item.moat)
                    .font(.system(size: 12))
                    .foregroundStyle(KSSTheme.textBody)
                    .lineLimit(1)
                if item.locked {
                    Image(systemName: "lock.fill")
                        .font(.system(size: 9))
                        .foregroundStyle(KSSTheme.accent)
                }
            }
            .frame(width: wMoat, alignment: .leading)
            Text(String(format: "%.2f", item.score))
                .font(.system(size: 13.5, weight: .heavy, design: .monospaced))
                .foregroundStyle(KSSTheme.accent)
                .lineLimit(1)
                .frame(width: wScore, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 11)
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
        return "L\(item.layer) · \(roleCN)"
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
                        .font(.system(size: 24, weight: .heavy).monospacedDigit())
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
                .font(.system(size: 19, weight: .bold).monospacedDigit())
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
                .font(.title3.weight(.bold).monospacedDigit())
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
