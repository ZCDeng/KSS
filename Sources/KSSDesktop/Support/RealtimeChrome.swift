import SwiftUI

// MARK: - 四态实时状态徽标（价格页）

/// 交易时段 + 新鲜 / 已过期 / 非实时 / 实时源未连接 / 非交易时段。
struct RealtimeStatusBadge: View {
    @Environment(\.kssTheme) private var theme
    var freshness: RealtimeFreshness
    var hours: TradingHours?
    var authFailed: Bool
    var updatedAt: Date?
    var onRetry: () -> Void

    var body: some View {
        if let hours, !hours.isTradingSession {
            HStack(spacing: 4) {
                Image(systemName: "clock")
                Text("非交易时段")
            }
            .font(.caption2)
            .foregroundStyle(theme.textSecondary)
        } else if authFailed {
            Button(action: onRetry) {
                HStack(spacing: 4) {
                    Image(systemName: "clock.badge.exclamationmark")
                    Text("实时源未连接")
                    Text("重试").underline()
                }
                .font(.caption2)
                .foregroundStyle(theme.down)
            }
            .buttonStyle(.plain)
        } else if freshness == .stale {
            HStack(spacing: 4) {
                Image(systemName: "clock.badge.exclamationmark")
                    .foregroundStyle(theme.ma5)
                Text("已过期")
                    .foregroundStyle(theme.ma5)
                if let ts = updatedAt {
                    Text(Self.formatted(ts))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .font(.caption2)
        } else if freshness == .fresh {
            HStack(spacing: 4) {
                Image(systemName: "clock")
                    .foregroundStyle(theme.up)
                Text("实时")
                    .foregroundStyle(theme.up)
                if let ts = updatedAt {
                    Text(Self.formatted(ts))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .font(.caption2)
        } else {
            Button(action: onRetry) {
                HStack(spacing: 4) {
                    Image(systemName: "clock")
                    Text("非实时")
                    Text("重试").underline()
                }
                .font(.caption2)
                .foregroundStyle(theme.textSecondary)
            }
            .buttonStyle(.plain)
        }
    }

    static func formatted(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return "更新于 \(f.string(from: date))"
    }
}

// MARK: - 日线新鲜度（与实时 badge 并列，语义分离）

/// 列表紧凑 / 详情完整；陈旧时可点触发全池 update-cs-data 确认流。
struct DailyFreshnessLabel: View {
    @Environment(\.kssTheme) private var theme
    var barDate: String?
    var referenceTradeDate: String?
    /// true → `07-09` / `陈旧·06-26`；false → `日线截至 …`
    var compact: Bool = false
    var isRunning: Bool = false
    var onRequestUpdate: () -> Void = {}

    private var status: DailyBarFreshness.Status {
        DailyBarFreshness.status(barDate: barDate, referenceTradeDate: referenceTradeDate)
    }

    private var text: String {
        if isRunning {
            return compact ? "更新中…" : "日线更新中…"
        }
        return compact
            ? DailyBarFreshness.compactLabel(barDate: barDate, referenceTradeDate: referenceTradeDate)
            : DailyBarFreshness.detailLabel(barDate: barDate, referenceTradeDate: referenceTradeDate)
    }

    private var tint: Color {
        if isRunning { return theme.textSecondary }
        switch status {
        case .stale: return theme.ma5
        case .missing: return theme.textSecondary
        case .fresh: return theme.textSecondary
        }
    }

    var body: some View {
        if status == .stale && !isRunning {
            Button(action: onRequestUpdate) {
                labelRow(tappable: true)
            }
            .buttonStyle(.plain)
            .help("点击更新全池日线（update-cs-data）")
        } else {
            labelRow(tappable: false)
        }
    }

    private func labelRow(tappable: Bool) -> some View {
        HStack(spacing: 4) {
            // 列表紧凑态避免 N 行 ProgressView 同时转；详情才显示 spinner
            if isRunning && !compact {
                ProgressView().controlSize(.mini)
            } else if status == .stale {
                Image(systemName: "exclamationmark.triangle.fill")
            } else if status == .missing {
                Image(systemName: "calendar.badge.exclamationmark")
            } else if !compact {
                Image(systemName: "calendar")
            }
            Text(text)
            if tappable {
                Text("更新").underline()
            }
        }
        .font(.caption2)
        .foregroundStyle(tint)
        .lineLimit(1)
    }
}

// MARK: - 紧凑状态点（非价格页）

/// 绿=新鲜实时 / 黄=已过期 / 灰=非实时或非交易 / 红=未连接。
struct RealtimeStatusDot: View {
    @Environment(\.kssTheme) private var theme
    var freshness: RealtimeFreshness
    var hours: TradingHours?
    var authFailed: Bool
    var updatedAt: Date?

    private var tint: Color {
        if authFailed { return theme.down }
        if let hours, !hours.isTradingSession { return theme.textSecondary }
        switch freshness {
        case .fresh: return theme.up
        case .stale: return theme.ma5
        case .missing: return theme.textSecondary
        }
    }

    private var label: String {
        if authFailed { return "实时源未连接" }
        if let hours, !hours.isTradingSession { return "非交易时段" }
        switch freshness {
        case .fresh:
            if let ts = updatedAt { return "实时 · \(RealtimeStatusBadge.formatted(ts))" }
            return "实时"
        case .stale:
            if let ts = updatedAt { return "已过期 · \(RealtimeStatusBadge.formatted(ts))" }
            return "已过期"
        case .missing:
            return "非实时"
        }
    }

    var body: some View {
        Circle()
            .fill(tint)
            .frame(width: 7, height: 7)
            .help(label)
            .accessibilityLabel(label)
    }
}

// MARK: - 变价轻闪

/// StatTile 变体：数值可闪（样式对齐 Dashboard `StatTile`）。
struct LiveStatTile: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var value: Double
    var text: String
    var tint: Color?
    var isLive: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(.system(size: 10.5, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(theme.textSecondary)
            LivePriceText(
                value: value,
                text: text,
                baseColor: tint ?? theme.textPrimary,
                isLive: isLive,
                font: KSSFont.harmonyNumber(20)
            )
            .lineLimit(1)
            .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

/// 数值变化时短暂高亮（~0.4s）；首帧不闪；`isLive` 为 false 时不闪。
struct LivePriceText: View {
    @Environment(\.kssTheme) private var theme
    var value: Double
    var text: String
    var baseColor: Color
    var isLive: Bool = true
    var font: Font = .body

    @State private var flashColor: Color?
    @State private var flashTask: Task<Void, Never>?

    var body: some View {
        Text(text)
            .font(font)
            .foregroundStyle(flashColor ?? baseColor)
            .onChange(of: value) { oldValue, newValue in
                guard isLive, oldValue != newValue else { return }
                flashTask?.cancel()
                let direction = newValue > oldValue ? theme.up : theme.down
                flashColor = direction
                flashTask = Task {
                    try? await Task.sleep(nanoseconds: 400_000_000)
                    guard !Task.isCancelled else { return }
                    await MainActor.run { flashColor = nil }
                }
            }
            .onDisappear {
                flashTask?.cancel()
            }
    }
}
