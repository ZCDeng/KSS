import SwiftUI

struct DashboardView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void
    var onOpenSection: (WorkspaceSection) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageTitle("总览", subtitle: "数据日期 \(snapshot.latestDataDate ?? "-") · 更新 \(snapshot.recommendationDate ?? "-")")

                // 1) KPI 概览条
                HStack(alignment: .top, spacing: 10) {
                    StatTile(title: "数据日期", value: snapshot.latestDataDate ?? "-")
                    StatTile(title: "股票池", value: "\(snapshot.stockCount)")
                    StatTile(title: "最新推荐", value: snapshot.recommendationDate ?? "-")
                    StatTile(title: "跟踪 Sharpe", value: KSSFormat.number(snapshot.tracking.sharpe), tint: KSSTheme.signColor(snapshot.tracking.sharpe))
                }

                // 2) 主区两栏：今日推荐 | 纸交易跟踪 + 资产计数
                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionHeader("今日推荐", caption: "log_mv 反向选出的低市值 Top 5 · 买入 T+1 开盘")
                        TodayPicksList(items: Array(snapshot.recommendations.prefix(5)), onSelect: onSelectSymbol)
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)

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

/// 今日推荐：紧凑编号列表（替代之前占地的大卡片）。
struct TodayPicksList: View {
    var items: [Recommendation]
    var onSelect: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                Button { onSelect(item.symbol) } label: {
                    HStack(spacing: 12) {
                        Text("#\(item.rank)")
                            .font(.system(size: 15, weight: .heavy, design: .monospaced))
                            .foregroundStyle(KSSTheme.accent)
                            .frame(width: 36, alignment: .leading)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(.system(size: 14.5, weight: .bold))
                                .foregroundStyle(KSSTheme.textPrimary)
                            Text("\(item.symbol) · \(item.industry)")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(KSSTheme.textSecondary)
                        }
                        Spacer()
                        StatusBadge.tracking(item.status)
                        Text(KSSFormat.percent(item.weight))
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                            .frame(width: 56, alignment: .trailing)
                    }
                    .contentShape(Rectangle())
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                }
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
