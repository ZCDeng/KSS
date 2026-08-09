import SwiftUI

/// 可投资地图在四处落点的共用徽标（plan 2026-08-09-001 U6）。
///
/// 四处（自选 / 股票池列表、推荐页、盯盘两张信号卡）共用这一个组件与一份标注字典，
/// 不给任一视图单开读路径。色永远与文本标签成对出现（R2）——色点单独出现时，
/// 色盲用户与截图读者都拿不到信息。
///
/// 未加载态是独立的一态：标注字典异步加载，`loaded == false` 时渲染中性空心环而不是
/// 灰点，否则每次启动全部个股会先闪一遍「未上图」再跳成真值。
struct ExposureBadge: View {
    @Environment(\.kssTheme) private var theme

    var exposure: ExposureStock?
    /// 标注字典是否已加载。false 时 `exposure` 为 nil 只说明「还不知道」。
    var loaded: Bool
    /// 行内紧凑版（列表行第三行）与详情版（个股详情卡）字号不同，语义相同。
    var compact: Bool = true
    /// 未上图时是否画灰点。四处列表行的灰点由行尾的 `ExposureMarkButton` 承担（它要可点，
    /// 必须是行按钮的兄弟节点），此处传 false，否则同一行会出现两个灰点。
    /// 「未上图」文案两种情形都留在这里显示（R8 要求色与文本成对出现）。
    var showsDotWhenUnlabelled: Bool = true

    private var dotSize: CGFloat { compact ? 9 : 11 }
    private var fontSize: CGFloat { compact ? 10.5 : 12.5 }

    var body: some View {
        if !loaded {
            // 中性占位：空心环 + 无文案。与实心灰点（未上图）在形状上就分得开。
            HStack(spacing: 5) {
                Circle()
                    .strokeBorder(theme.outlineVariant, lineWidth: 1)
                    .frame(width: dotSize, height: dotSize)
                Text("标注加载中")
                    .font(KSSFont.themed(fontSize, theme: theme))
                    .foregroundStyle(theme.outlineVariant)
            }
            .help("可投资地图标注尚未加载")
            .accessibilityLabel("可投资地图标注加载中")
        } else {
            HStack(spacing: 5) {
                if showsDotWhenUnlabelled || (exposure?.state ?? .unlabelled) != .unlabelled {
                    ExposureDot(exposure: exposure, size: dotSize)
                }
                Text(stateText)
                    .font(KSSFont.themed(fontSize, .semibold, theme: theme))
                    .foregroundStyle(stateTint)
                    .lineLimit(1)
                if let stale = exposure?.isStale, stale {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: fontSize - 0.5, weight: .semibold))
                        .foregroundStyle(theme.ma5)
                        .help("主节点复核日期已超 120 天")
                }
                if let zone = exposure?.zone, exposure?.state != .unlabelled {
                    ExposureZoneLabel(zone: zone, fontSize: fontSize)
                }
            }
            .help(helpText)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(helpText)
        }
    }

    private var stateText: String {
        guard let exposure else { return "未上图" }
        return exposure.stateLabel.isEmpty ? "未上图" : exposure.stateLabel
    }

    private var stateTint: Color {
        guard let exposure else { return theme.textSecondary }
        switch exposure.state {
        case .unlabelled:   return theme.textSecondary
        case .pendingColor: return theme.exposurePending
        case .labelled:     return theme.exposureColorOrUnknown(exposure.colorKey)
        }
    }

    private var helpText: String {
        guard let exposure else { return "未上图 · 尚未挂到任何地图节点" }
        var parts: [String] = [stateText]
        if let node = exposure.primaryNode {
            parts.append("主节点 \(node.name)")
        }
        parts.append(exposure.zone.display)
        if exposure.isStale { parts.append("主节点复核已超 120 天") }
        return parts.joined(separator: " · ")
    }
}

/// 色块本体。
///
/// 形状本身也是一路信息，不只是色：**未上图是圆点，已上图是方块**。深绿与浅绿在 8pt
/// 圆点上分不开是实测过的（xcom 亮色下 #0D4627 与 #2A8452 都是暗绿），所以已上图改用
/// 圆角方块——同样的边长下色面积多约三成，且方/圆的差别在缩略尺寸下先于色被看见。
struct ExposureDot: View {
    @Environment(\.kssTheme) private var theme
    var exposure: ExposureStock?
    var size: CGFloat = 9

    var body: some View {
        switch exposure?.state ?? .unlabelled {
        case .unlabelled:
            Circle()
                .fill(theme.textSecondary)
                .frame(width: size - 1, height: size - 1)
        case .pendingColor:
            RoundedRectangle(cornerRadius: 2.5, style: .continuous)
                .fill(theme.exposurePending)
                .overlay(
                    RoundedRectangle(cornerRadius: 2.5, style: .continuous)
                        .strokeBorder(theme.textPrimary.opacity(0.28), lineWidth: 1)
                )
                .frame(width: size, height: size)
        case .labelled:
            RoundedRectangle(cornerRadius: 2.5, style: .continuous)
                .fill(theme.exposureColorOrUnknown(exposure?.colorKey ?? ""))
                .frame(width: size, height: size)
        }
    }
}

/// 图例与区块头用的纯色样本。比色点大一圈，因为图例是学这套色的地方。
struct ExposureSwatch: View {
    var color: Color
    var size: CGFloat = 13
    var body: some View {
        RoundedRectangle(cornerRadius: 3, style: .continuous)
            .fill(color)
            .frame(width: size, height: size)
    }
}

/// 区位标。红区做成实心方角标签，视觉权重不低于色块（R22）；其余为次级文本。
/// 用圆角矩形而不是胶囊：胶囊在这套界面里表示可切换/可移除，区位是只读判定结果。
/// 文案是 Python 算好的成品串（KTD2），这里不拼、不改口径。
struct ExposureZoneLabel: View {
    @Environment(\.kssTheme) private var theme
    var zone: ExposureZone
    var fontSize: CGFloat = 10.5

    var body: some View {
        if zone.isRed {
            Text(zone.display)
                .font(KSSFont.themed(fontSize, .bold, theme: theme))
                .foregroundStyle(.white)
                .padding(.horizontal, 5)
                .padding(.vertical, 1.5)
                .background(theme.exposureRed, in: RoundedRectangle(cornerRadius: 4, style: .continuous))
                // 描边负责边界，填充负责白字。两者不能由同一个色兼顾：
                // 白字要 4.5:1 就把红压到 L ≤ 0.183，而偏亮的暗色 surface 要 3:1 又要求
                // L ≥ 0.199——这两个区间不相交，所以边界交给描边。
                .overlay(
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .stroke(theme.textPrimary.opacity(0.22), lineWidth: 1)
                )
                .lineLimit(1)
        } else {
            Text(zone.display)
                .font(KSSFont.themed(fontSize, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
        }
    }
}

/// 行尾可点灰点：未标注个股的就地补标入口（R22 / F2）。
///
/// 必须作为行按钮的**兄弟节点**放在行里，嵌套进行按钮会被 macOS List 吞掉点击
/// （自选星标按钮踩过同一个坑）。已标注与未加载态渲染等宽占位，避免行宽跳动。
struct ExposureMarkButton: View {
    @Environment(\.kssTheme) private var theme
    var exposure: ExposureStock?
    var loaded: Bool
    var action: () -> Void

    private var isUnlabelled: Bool { loaded && (exposure?.state ?? .unlabelled) == .unlabelled }

    var body: some View {
        Group {
            if isUnlabelled {
                Button(action: action) {
                    Circle()
                        .fill(theme.textSecondary)
                        .frame(width: 9, height: 9)
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("未上图 · 点击挂到可投资地图节点")
                .accessibilityLabel("未上图，点击挂到可投资地图节点")
                .accessibilityAddTraits(.isButton)
            } else {
                Color.clear.frame(width: 24, height: 24)
            }
        }
    }
}

// MARK: - 四处落点的注入包

/// 暴露数据 + 写回调的一次性打包。四处落点各自加五个参数会让调用点无法读，
/// 而每个视图自己去读桥会造成四份不同步的字典——这两条都是 U6 明确要避免的。
struct ExposureContext {
    var byCode: [String: ExposureStock]?
    var palette: [String: ExposurePaletteColor] = [:]
    var axes: [String: ExposureAxis] = [:]
    /// 点灰点：打开节点选择器。
    var onMark: (String) -> Void = { _ in }
    /// 整体替换某票标注（改主 / 增删副 / 传空主即清空）。
    var onSetLabel: (String, String, [String]) -> Void = { _, _, _ in }
    /// 写单题 8 问，await 返回后区位串已刷新。
    var onSetAnswer: (String, Int, String) async -> Void = { _, _, _ in }
    /// 让助手给 8 问出草稿。草稿只到界面，逐题人工「采纳」才写库。
    var onDraftAnswers: (String) -> Void = { _ in }
    var drafts: [String: ExposureAnswerDrafts] = [:]
    var draftingSymbol: String?

    /// 标注字典是否已加载。false 时四处落点渲染中性占位而不是灰点。
    var loaded: Bool { byCode != nil }

    func stock(_ symbol: String) -> ExposureStock? { byCode?[symbol] }
}

// MARK: - 按色筛选（R20）

/// 自选 / 股票池列表的按色筛选项。筛选项以文本呈现，不用纯色块作可点目标（R20）。
enum ExposureFilter: Hashable {
    case all
    case color(String)      // 五色之一的机器键
    case pendingColor       // 已上图 · 待定色
    case unlabelled         // 未上图

    /// 某只票是否命中当前筛选。字典未加载时一律放行——过滤掉全部行只会让人以为池空了。
    func matches(_ exposure: ExposureStock?, loaded: Bool) -> Bool {
        guard loaded else { return true }
        switch self {
        case .all:
            return true
        case .unlabelled:
            return (exposure?.state ?? .unlabelled) == .unlabelled
        case .pendingColor:
            return exposure?.state == .pendingColor
        case .color(let key):
            return exposure?.state == .labelled && exposure?.colorKey == key
        }
    }

    /// 筛选条的可选项：全部 + 色板顺序的五色 + 待定色 + 未上图。
    /// 色板还没拉回来时用内置中文标签兜底——筛选条不该等地图页被打开过才有得选。
    static func options(palette: [String: ExposurePaletteColor]) -> [(ExposureFilter, String)] {
        var out: [(ExposureFilter, String)] = [(.all, "全部")]
        for key in paletteOrder {
            out.append((.color(key), palette[key]?.label ?? fallbackLabel(key)))
        }
        out.append((.pendingColor, "待定色"))
        out.append((.unlabelled, "未上图"))
        return out
    }

    /// 五色的固定呈现顺序：暴露程度由低到高，与源文色条同序。
    static let paletteOrder = ["deep_green", "light_green", "yellow", "orange", "purple"]

    static func fallbackLabel(_ key: String) -> String {
        switch key {
        case "deep_green":  return "深绿"
        case "light_green": return "浅绿"
        case "yellow":      return "黄"
        case "orange":      return "橙"
        case "purple":      return "紫"
        default:            return key
        }
    }
}
