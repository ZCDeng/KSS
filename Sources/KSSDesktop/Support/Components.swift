import SwiftUI

/// Unified status chip: SF Symbol icon + Chinese label + tint. Every status in
/// the app (推荐跟踪 / 任务执行) renders through this so the language stays consistent.
struct StatusBadge: View {
    var icon: String
    var text: String
    var tint: Color
    var emphasized: Bool = false

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .bold))
            Text(text)
                .font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 9)
        .padding(.vertical, 4)
        .background(tint.opacity(emphasized ? 0.18 : 0.12), in: Capsule())
    }
}

extension StatusBadge {
    /// 推荐 / 跟踪状态：T+2 收益方向。红涨绿跌。
    static func tracking(_ status: String) -> StatusBadge {
        switch status {
        case "positive":
            return StatusBadge(icon: "arrow.up.right", text: "上涨", tint: KSSTheme.up)
        case "negative":
            return StatusBadge(icon: "arrow.down.right", text: "下跌", tint: KSSTheme.down)
        default:
            return StatusBadge(icon: "clock", text: "待 T+2", tint: KSSTheme.textSecondary)
        }
    }

    /// 任务执行状态。用语义色（成功蓝 / 跳过橙 / 失败红），不蹭价格红绿。
    static func task(_ status: String) -> StatusBadge {
        switch status {
        case "success":
            return StatusBadge(icon: "checkmark.circle.fill", text: "成功", tint: KSSTheme.accent, emphasized: true)
        case "skipped":
            return StatusBadge(icon: "minus.circle.fill", text: "跳过", tint: KSSTheme.ma5, emphasized: true)
        default:
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", tint: KSSTheme.up, emphasized: true)
        }
    }
}

/// Large, prominent page title for detail panes — bold, oversized, with an
/// optional subtitle. Gives every screen a clear, eye-catching heading.
struct PageTitle: View {
    var title: String
    var subtitle: String?

    init(_ title: String, subtitle: String? = nil) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(KSSFont.serif(28, .bold))
                .foregroundStyle(KSSTheme.textPrimary)
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
        }
    }
}

/// Compact sort control used at the head of every list. Shows the current key
/// and toggles ascending/descending; the caller owns the binding.
/// 可点击排序列头：点击切到该列（默认降序），已选中再点切换升/降。
/// 与 SortControl 共享同一对 selection/ascending 绑定，下拉控件与列头状态一致。
/// width=nil 时占满弹性宽度（maxWidth:.infinity），否则固定宽度（对齐数据行列宽）。
struct SortHeaderCell<Key: Hashable>: View {
    let title: String
    let key: Key
    @Binding var selection: Key
    @Binding var ascending: Bool
    var alignment: Alignment = .leading
    var width: CGFloat? = nil

    private var active: Bool { selection == key }

    var body: some View {
        Button {
            if active { ascending.toggle() } else { selection = key; ascending = false }
        } label: {
            HStack(spacing: 3) {
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(active ? KSSTheme.textPrimary : KSSTheme.textSecondary)
                Image(systemName: active ? (ascending ? "chevron.up" : "chevron.down") : "arrow.up.arrow.down")
                    .font(.system(size: 7, weight: .bold))
                    .foregroundStyle(active ? KSSTheme.accent : KSSTheme.textSecondary.opacity(0.35))
            }
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: alignment)
            .frame(width: width, alignment: alignment)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("点击按「\(title)」排序")
    }
}

struct SortControl<Key: Hashable>: View {
    var options: [(key: Key, label: String)]
    @Binding var selection: Key
    @Binding var ascending: Bool

    var body: some View {
        HStack(spacing: 8) {
            Menu {
                ForEach(options, id: \.key) { option in
                    Button {
                        selection = option.key
                    } label: {
                        Label(option.label, systemImage: selection == option.key ? "checkmark" : "")
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.up.arrow.down")
                        .font(.system(size: 11, weight: .semibold))
                    Text(options.first { $0.key == selection }?.label ?? "排序")
                        .font(.system(size: 12.5, weight: .semibold))
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()

            Button {
                ascending.toggle()
            } label: {
                Image(systemName: ascending ? "arrow.up" : "arrow.down")
                    .font(.system(size: 11, weight: .bold))
            }
            .buttonStyle(.plain)
            .help(ascending ? "升序" : "降序")
        }
        .foregroundStyle(KSSTheme.textSecondary)
    }
}
