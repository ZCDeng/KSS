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

extension KSSThemeTokens {
    /// 分段控件浮起块背景色：亮色下 `surface`，暗色下 `surfaceRaised`。
    /// `surface` 在暗色主题下不可靠——部分设计系统（如 xcom）与 `surfaceContainer` 数值完全相同，
    /// 逐一核对全部设计系统的暗色 Seed 后确认 `surfaceRaised` 才是恒亮于 `surfaceContainer` 的那个
    /// （见 docs/plans/2026-07-11-006-fix-intel-radar-tab-affordance-plan.md 的教训）。
    var segmentedActiveBackground: Color {
        appearance == .dark ? surfaceRaised : surface
    }
}

/// 凹槽容器：铺一层 `surfaceContainer` 底，包住一组互斥切换项。
/// 供子项内容不只是纯文字（如 IntelView 赛道行的色点+计数角标）、无法直接套 `KSSSegmentedControl` 的场景使用。
struct KSSSegmentedGroove<Content: View>: View {
    @Environment(\.kssTheme) private var theme
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(4)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: theme.chipRadius))
    }
}

extension View {
    /// 分段控件内浮起子项的背景：激活态填充 `segmentedActiveBackground` + 轻投影，未激活态透明（无 hover 态）。
    func kssSegmentedItemStyle(isActive: Bool, theme: KSSThemeTokens) -> some View {
        self
            .background(
                RoundedRectangle(cornerRadius: theme.chipRadius)
                    .fill(isActive ? theme.segmentedActiveBackground : Color.clear)
            )
            .shadow(color: isActive ? Color.black.opacity(0.08) : .clear, radius: 2, x: 0, y: 1)
    }
}

/// 分段控件（凹槽 + 浮起块）：贴合自定义设计系统的 Tab 切换视觉，替代原生 `.pickerStyle(.segmented)`
/// （原生分段控件走系统外观，盖不掉自定义主题）。`stretch` 关闭时内容自适应宽度（默认，对齐原生
/// `.pickerStyle(.segmented) + .fixedSize()` 的观感）；开启时均分可用宽度（如撑满某个内容列的场景）。
struct KSSSegmentedControl<Key: Hashable>: View {
    @Environment(\.kssTheme) private var theme
    var options: [(key: Key, label: String)]
    @Binding var selection: Key
    var stretch: Bool = false

    var body: some View {
        KSSSegmentedGroove {
            HStack(spacing: 4) {
                ForEach(options, id: \.key) { option in
                    let isActive = option.key == selection
                    Button {
                        withAnimation(.easeOut(duration: 0.15)) { selection = option.key }
                    } label: {
                        Text(option.label)
                            .font(KSSFont.themed(13, isActive ? .semibold : .medium, theme: theme))
                            .foregroundStyle(isActive ? theme.textPrimary : theme.textSecondary)
                            .frame(maxWidth: stretch ? .infinity : nil)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .kssSegmentedItemStyle(isActive: isActive, theme: theme)
                            .accessibilityAddTraits(isActive ? .isSelected : [])
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .frame(maxWidth: stretch ? .infinity : nil, alignment: .leading)
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
