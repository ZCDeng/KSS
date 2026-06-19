import SwiftUI

enum RecSort: String, CaseIterable, Identifiable {
    case rank = "排名"
    case weight = "权重"
    case tracking = "跟踪收益"
    var id: String { rawValue }
}

enum RecTab: String, CaseIterable, Identifiable {
    case current = "当日推荐"
    case history = "往期跟踪"
    var id: String { rawValue }
}

struct RecommendationsView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void

    @State private var tab: RecTab = .current
    @State private var sort: RecSort = .rank
    @State private var ascending = true

    private var sortedRecs: [Recommendation] {
        snapshot.recommendations.sorted { a, b in
            switch sort {
            case .rank: return ascending ? a.rank < b.rank : a.rank > b.rank
            case .weight: return ascending ? a.weight < b.weight : a.weight > b.weight
            case .tracking: return ascending ? (a.trackingReturn ?? 0) < (b.trackingReturn ?? 0) : (a.trackingReturn ?? 0) > (b.trackingReturn ?? 0)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PageTitle("推荐", subtitle: snapshot.recommendationDate)
                .padding(.horizontal, 16)
                .padding(.top, 14)

            Picker("", selection: $tab) {
                ForEach(RecTab.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 4)

            if tab == .current {
                currentTab
            } else {
                historyTab
            }
        }
        .background(KSSTheme.canvas)
    }

    // MARK: - 当日推荐 (aligned table)

    private var currentTab: some View {
        VStack(spacing: 0) {
            HStack {
                SortControl(
                    options: RecSort.allCases.map { ($0, $0.rawValue) },
                    selection: $sort,
                    ascending: $ascending
                )
                Spacer()
                Text("\(sortedRecs.count) 只")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            // column header
            HStack(spacing: 12) {
                Text("#").frame(width: 44, alignment: .leading)
                Text("名称 / 代码").frame(maxWidth: .infinity, alignment: .leading)
                Text("状态").frame(width: 96, alignment: .center)
                Text("log_mv").frame(width: 86, alignment: .trailing)
                Text("权重").frame(width: 64, alignment: .trailing)
                Text("跟踪").frame(width: 84, alignment: .trailing)
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(KSSTheme.textSecondary)
            .padding(.horizontal, 16)
            .padding(.bottom, 6)

            List(sortedRecs) { item in
                Button { onSelectSymbol(item.symbol) } label: {
                    HStack(spacing: 12) {
                        Text("#\(item.rank)")
                            .font(.system(size: 16, weight: .heavy, design: .monospaced))
                            .foregroundStyle(KSSTheme.accent)
                            .frame(width: 44, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(.system(size: 15.5, weight: .bold))
                                .foregroundStyle(KSSTheme.textPrimary)
                            Text("\(item.symbol) · \(item.industry)")
                                .font(.system(size: 11.5, design: .monospaced))
                                .foregroundStyle(KSSTheme.textSecondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        StatusBadge.tracking(item.status).frame(width: 96, alignment: .center)
                        Text(KSSFormat.number(item.factorValue, digits: 3))
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KSSTheme.textPrimary)
                            .frame(width: 86, alignment: .trailing)
                        Text(KSSFormat.percent(item.weight))
                            .font(.system(size: 13, design: .monospaced))
                            .foregroundStyle(KSSTheme.textPrimary)
                            .frame(width: 64, alignment: .trailing)
                        Text(KSSFormat.percent(item.trackingReturn))
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KSSTheme.signColor(item.trackingReturn))
                            .frame(width: 84, alignment: .trailing)
                    }
                    .contentShape(Rectangle())
                    .padding(.vertical, 3)
                }
                .buttonStyle(.plain)
                .listRowBackground(KSSTheme.surface)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
    }

    // MARK: - 往期跟踪 (by 日/周/月)

    private var historyTab: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("预测日").frame(width: 110, alignment: .leading)
                Text("选股").frame(width: 44, alignment: .trailing)
                Text("日 (1d)").frame(maxWidth: .infinity, alignment: .trailing)
                Text("周 (5d)").frame(maxWidth: .infinity, alignment: .trailing)
                Text("月 (20d)").frame(maxWidth: .infinity, alignment: .trailing)
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(KSSTheme.textSecondary)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            let days = snapshot.recommendationTracking ?? []
            if days.isEmpty {
                Text("暂无往期推荐记录")
                    .font(.system(size: 13))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(days) { day in
                    HStack(spacing: 12) {
                        HStack(spacing: 6) {
                            Image(systemName: "calendar").font(.system(size: 10, weight: .semibold)).foregroundStyle(KSSTheme.accent)
                            Text(day.date).font(.system(size: 13, weight: .bold, design: .monospaced)).foregroundStyle(KSSTheme.textPrimary)
                        }
                        .frame(width: 110, alignment: .leading)
                        Text("\(day.nPicks)")
                            .font(.system(size: 13, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                            .frame(width: 44, alignment: .trailing)
                        horizonCell(day.ret1d)
                        horizonCell(day.ret5d)
                        horizonCell(day.ret20d)
                    }
                    .padding(.vertical, 4)
                    .listRowBackground(KSSTheme.surface)
                }
                .scrollContentBackground(.hidden)
                .background(KSSTheme.canvas)
            }
        }
    }

    private func horizonCell(_ value: Double?) -> some View {
        Text(value == nil ? "待结算" : KSSFormat.percent(value))
            .font(.system(size: 13.5, weight: .semibold, design: .monospaced))
            .foregroundStyle(value == nil ? KSSTheme.textSecondary : KSSTheme.signColor(value))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}
