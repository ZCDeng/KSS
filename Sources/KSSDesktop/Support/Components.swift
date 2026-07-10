import SwiftUI

/// Unified status chip: SF Symbol icon + Chinese label + tint. Every status in
/// the app (推荐跟踪 / 任务执行) renders through this so the language stays consistent.
/// tint 既可由调用点传当前主题 token，也可用语义 role 让静态工厂在 body 里解析。
struct StatusBadge: View {
    enum Role { case up, down, neutral, success, skipped, failure, accent }

    @Environment(\.kssTheme) private var theme
    var icon: String
    var text: String
    var explicitTint: Color?
    var role: Role?
    var emphasized: Bool = false

    /// 调用点已有环境 token 时直接传色（如 `theme.accent`）。
    init(icon: String, text: String, tint: Color, emphasized: Bool = false) {
        self.icon = icon; self.text = text
        self.explicitTint = tint; self.role = nil; self.emphasized = emphasized
    }

    /// 语义 role：供无法访问环境的静态工厂使用，在 body 里解析为主题色。
    init(icon: String, text: String, role: Role, emphasized: Bool = false) {
        self.icon = icon; self.text = text
        self.explicitTint = nil; self.role = role; self.emphasized = emphasized
    }

    private var tint: Color {
        if let explicitTint { return explicitTint }
        switch role ?? .neutral {
        case .up:       return theme.up
        case .down:     return theme.down
        case .neutral:  return theme.textSecondary
        case .success:  return theme.accent
        case .skipped:  return theme.ma5
        case .failure:  return theme.up
        case .accent:   return theme.accent
        }
    }

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
            return StatusBadge(icon: "arrow.up.right", text: "上涨", role: .up)
        case "negative":
            return StatusBadge(icon: "arrow.down.right", text: "下跌", role: .down)
        default:
            return StatusBadge(icon: "clock", text: "待 T+2", role: .neutral)
        }
    }

    /// 任务执行状态。用语义色（成功 accent / 跳过橙 / 失败红），不蹭价格红绿。
    static func task(_ status: String) -> StatusBadge {
        switch status {
        case "success":
            return StatusBadge(icon: "checkmark.circle.fill", text: "成功", role: .success, emphasized: true)
        case "skipped":
            return StatusBadge(icon: "minus.circle.fill", text: "跳过", role: .skipped, emphasized: true)
        default:
            return StatusBadge(icon: "xmark.octagon.fill", text: "失败", role: .failure, emphasized: true)
        }
    }
}

/// Large, prominent page title for detail panes. 标题字族随设计系统（serif / sans / mono）。
struct PageTitle: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var subtitle: String?

    init(_ title: String, subtitle: String? = nil) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(KSSFont.themed(28, .bold, theme: theme, design: theme.titleDesign))
                .foregroundStyle(theme.textPrimary)
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
        }
    }
}

/// 可点击排序列头：点击切到该列（默认降序），已选中再点切换升/降。
/// 与 SortControl 共享同一对 selection/ascending 绑定，下拉控件与列头状态一致。
/// width=nil 时占满弹性宽度，否则固定宽度（对齐数据行列宽）。
struct SortHeaderCell<Key: Hashable>: View {
    @Environment(\.kssTheme) private var theme
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
                    .foregroundStyle(active ? theme.textPrimary : theme.textSecondary)
                Image(systemName: active ? (ascending ? "chevron.up" : "chevron.down") : "arrow.up.arrow.down")
                    .font(.system(size: 7, weight: .bold))
                    .foregroundStyle(active ? theme.accent : theme.textSecondary.opacity(0.35))
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
    @Environment(\.kssTheme) private var theme
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
        .foregroundStyle(theme.textSecondary)
    }
}
