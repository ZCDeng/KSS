import AppKit
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

/// 缺凭证优雅降级卡片（plan 2026-07-12-005 / U9，R12）：某数据源未配置时统一呈现
/// "未配置 X，去设置里填" + 跳转按钮，替代报错/空白/崩溃。凭证已配但请求本身失败
/// 走各面板既有错误路径，不用这张卡——两种情况不能混淆（AE1 的反面）。
struct MissingCredentialCard: View {
    @Environment(\.kssTheme) private var theme
    var sourceDisplayName: String
    var onOpenSettings: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "key.slash")
                .font(.system(size: 15))
                .foregroundStyle(theme.ma5)
            Text("未配置 \(sourceDisplayName)，去设置里填")
                .font(KSSFont.themed(13, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Spacer()
            Button("去设置", action: onOpenSettings)
                .buttonStyle(.bordered)
                .controlSize(.small)
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.ma5.opacity(0.08), in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).strokeBorder(theme.ma5.opacity(0.25), lineWidth: 1))
    }
}

// MARK: - Dashboard strip card（市场速览小卡标准件）

/// 第一行速览小卡规格：等高、顶对齐、单行最多 5 张、等分动态宽度。
enum DashboardStripCardSpec {
    static let height: CGFloat = 88
    static let titleRowHeight: CGFloat = 22
    static let valueRowMinHeight: CGFloat = 28
    static let maxPerRow = 5
    static let spacing: CGFloat = 12
    static let padding: CGFloat = 14
}

/// 标准速览小卡：标题行 + 主值行；固定高度；trailing（如 Sparkle）叠在右上角，不把标题行撑偏。
struct DashboardStripCard<Value: View, Trailing: View>: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var meta: String? = nil
    var isLive: Bool = false
    @ViewBuilder var trailing: () -> Trailing
    @ViewBuilder var value: () -> Value

    private var hasTrailing: Bool { Trailing.self != EmptyView.self }

    init(
        title: String,
        meta: String? = nil,
        isLive: Bool = false,
        @ViewBuilder trailing: @escaping () -> Trailing,
        @ViewBuilder value: @escaping () -> Value
    ) {
        self.title = title
        self.meta = meta
        self.isLive = isLive
        self.trailing = trailing
        self.value = value
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // 标题行固定高度；Sparkle/plus 直接进标题 HStack，避免 overlay 被裁切或点不中。
            HStack(spacing: 6) {
                Text(title)
                    .font(KSSFont.themed(13.5, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                if isLive {
                    Text("实时")
                        .font(KSSFont.themed(9, .bold, theme: theme))
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(theme.accent.opacity(0.12), in: Capsule())
                }
                Spacer(minLength: 4)
                if let meta, !meta.isEmpty {
                    Text(meta)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
                if hasTrailing {
                    trailing()
                        .layoutPriority(1)
                }
            }
            .frame(height: DashboardStripCardSpec.titleRowHeight, alignment: .center)

            value()
                .frame(
                    maxWidth: .infinity,
                    minHeight: DashboardStripCardSpec.valueRowMinHeight,
                    alignment: .leading
                )

            Spacer(minLength: 0)
        }
        .frame(
            maxWidth: .infinity,
            minHeight: DashboardStripCardSpec.height,
            maxHeight: DashboardStripCardSpec.height,
            alignment: .topLeading
        )
        .kssCard(padding: DashboardStripCardSpec.padding)
        // 等分宽：父 HStack 里每张卡都拉满分配宽度
        .frame(maxWidth: .infinity, maxHeight: DashboardStripCardSpec.height, alignment: .top)
    }
}

extension DashboardStripCard where Trailing == EmptyView {
    init(
        title: String,
        meta: String? = nil,
        isLive: Bool = false,
        @ViewBuilder value: @escaping () -> Value
    ) {
        self.init(title: title, meta: meta, isLive: isLive, trailing: { EmptyView() }, value: value)
    }
}

/// 速览行：顶对齐 + 等分宽。子卡须自带 `frame(maxWidth: .infinity)`。
struct DashboardStripCardRow<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        HStack(alignment: .top, spacing: DashboardStripCardSpec.spacing) {
            content()
        }
        .frame(height: DashboardStripCardSpec.height, alignment: .top)
    }
}

// MARK: - Dashboard chrome / Sparkle 标准件

/// 盯盘区动作图标规格：与 Seesaw 侧栏同色（textPrimary / 黑墨），统一尺寸。
enum DashboardChromeIconSpec {
    static let pointSize: CGFloat = 18
    static let weight: Font.Weight = .semibold
    static let hitSize: CGFloat = 28
}

enum DashboardChromeIconKind {
    case sparkles
    case plus

    var systemName: String {
        switch self {
        case .sparkles: return "sparkles"
        case .plus: return "plus.circle.fill"
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .sparkles: return "自然语言"
        case .plus: return "列表追加"
        }
    }
}

struct DashboardChromeIcon: View {
    @Environment(\.kssTheme) private var theme
    let kind: DashboardChromeIconKind
    var enabled: Bool = true

    var body: some View {
        Group {
            if kind == .sparkles,
               let url = KSSResources.bundle.url(
                   forResource: "DashboardSparkleIcon", withExtension: "png"
               ),
               let img = NSImage(contentsOf: url) {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
                    .frame(
                        width: DashboardChromeIconSpec.pointSize + 2,
                        height: DashboardChromeIconSpec.pointSize + 2
                    )
                    .opacity(enabled ? 1 : 0.45)
            } else {
                Image(systemName: kind.systemName)
                    .font(.system(
                        size: DashboardChromeIconSpec.pointSize,
                        weight: DashboardChromeIconSpec.weight
                    ))
                    // 用 accent 提对比度，避免 xcom 深色卡上 textPrimary 与底色糊在一起
                    .foregroundStyle(
                        enabled ? theme.accent : theme.textSecondary.opacity(0.45)
                    )
            }
        }
        .frame(width: DashboardChromeIconSpec.hitSize, height: DashboardChromeIconSpec.hitSize)
        .contentShape(Rectangle())
        .accessibilityLabel(kind.accessibilityLabel)
        .accessibilityAddTraits(.isButton)
    }
}

struct DashboardChromeIconButton: View {
    let kind: DashboardChromeIconKind
    var help: String
    var disabled: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            DashboardChromeIcon(kind: kind, enabled: !disabled)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .help(help)
    }
}

/// Sparkle 标准能力：一个入口 = 自然语言 Tab + 列表兜底 Tab。
/// 未来任何卡片挂此组件即同时获得 NL 绑定与列表兜底。
/// `listContent` 接收 `dismiss` 闭包：列表选中后调用即可关 sheet。
struct DashboardSparkleControl<ListContent: View>: View {
    @Environment(\.kssTheme) private var theme

    var help: String = "自然语言 / 列表"
    var disabled: Bool = false
    var sheetTitle: String

    // NL
    var region: String
    var nlPlaceholder: String
    var nlExamples: [String] = []
    var bridge: BridgeClient?
    var onOpenAI: (() -> Void)? = nil
    var onDraft: (SurfaceBindDraft) -> Void

    // List 兜底
    var listTabTitle: String = "列表选择"
    var onListTabAppear: (() -> Void)? = nil
    @ViewBuilder var listContent: (_ dismiss: @escaping () -> Void) -> ListContent

    @State private var showSheet = false
    @State private var tab: SparkleTab = .natural
    @State private var nlText = ""
    @State private var nlBusy = false
    @State private var nlError: String?
    @FocusState private var nlFocused: Bool

    private enum SparkleTab: String, CaseIterable, Identifiable, Hashable {
        case natural
        case list
        var id: String { rawValue }

        func label(listTitle: String) -> String {
            switch self {
            case .natural: return "自然语言"
            case .list: return listTitle
            }
        }
    }

    private var tabOptions: [(key: SparkleTab, label: String)] {
        SparkleTab.allCases.map { ($0, $0.label(listTitle: listTabTitle)) }
    }

    var body: some View {
        // 始终可见；disabled 只挡点击（列表/NL 由 sheet 内自行处理 bridge 缺失）。
        DashboardChromeIconButton(
            kind: .sparkles,
            help: help,
            disabled: disabled
        ) {
            showSheet = true
        }
        .sheet(isPresented: $showSheet) {
            sheetBody
        }
    }

    private var sheetBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                DashboardChromeIcon(kind: .sparkles)
                Text(sheetTitle)
                    .font(KSSFont.themed(16, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Button("取消") { showSheet = false }
                    .buttonStyle(.plain)
                    .font(KSSFont.themed(13, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 22)
            .padding(.top, 22)
            .padding(.bottom, 12)

            // 与资讯雷达 / 复盘页同一套：xcom 下划线 Tab，其它主题凹槽分段
            Group {
                if IntelXcomChrome.usesUnderlineTabs(theme.system) {
                    XcomUnderlineTabBar(
                        options: tabOptions,
                        selection: $tab,
                        stretch: true
                    )
                } else {
                    KSSSegmentedControl(
                        options: tabOptions,
                        selection: $tab,
                        stretch: true
                    )
                    .padding(.horizontal, 22)
                    .padding(.bottom, 6)
                }
            }
            .onChange(of: tab) { _, new in
                if new == .list { onListTabAppear?() }
            }

            Group {
                switch tab {
                case .natural:
                    naturalTab
                        .padding(22)
                case .list:
                    listContent({ showSheet = false })
                        .padding(22)
                        .frame(maxWidth: .infinity, minHeight: 260, alignment: .topLeading)
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .frame(width: 440)
        .background(theme.canvas)
        .onAppear {
            nlFocused = tab == .natural
            if tab == .list { onListTabAppear?() }
        }
    }

    private var naturalTab: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("用自然语言描述要改的内容，解析后展示代码真值，确认才写入。")
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            TextField(nlPlaceholder, text: $nlText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(KSSFont.themed(14, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(2...4)
                .padding(12)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                .focused($nlFocused)
                .disabled(nlBusy || bridge == nil)
                .onSubmit { interpret() }

            if !nlExamples.isEmpty {
                HStack(spacing: 6) {
                    ForEach(nlExamples.prefix(4), id: \.self) { ex in
                        Button(ex) {
                            nlText = ex
                            nlFocused = true
                        }
                        .buttonStyle(.plain)
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(theme.accent.opacity(0.12), in: Capsule())
                    }
                    Spacer(minLength: 0)
                }
            }

            if let nlError {
                Text(nlError)
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(.red.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 12) {
                if let onOpenAI {
                    Button("AI 辅助") {
                        showSheet = false
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            onOpenAI()
                        }
                    }
                    .disabled(nlBusy)
                }
                Spacer()
                Button("取消") { showSheet = false }
                    .keyboardShortcut(.cancelAction)
                    .disabled(nlBusy)
                Button(nlBusy ? "解析中…" : "解析") { interpret() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(nlBusy || bridge == nil
                              || nlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .opacity(nlBusy ? 0.85 : 1)
    }

    private func interpret() {
        guard let bridge else { return }
        let trimmed = nlText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        nlBusy = true
        nlError = nil
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceNlInterpret(region: region, text: trimmed)
                }.value
                await MainActor.run {
                    nlBusy = false
                    let (draft, err) = SurfaceBindEncoding.draft(from: resp, region: region)
                    if let draft {
                        showSheet = false
                        nlText = ""
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                            onDraft(draft)
                        }
                    } else {
                        nlError = err ?? "无法解析"
                    }
                }
            } catch {
                await MainActor.run {
                    nlBusy = false
                    nlError = error.localizedDescription
                }
            }
        }
    }
}

/// 列表兜底：搜索 + 候选列表（Sparkle 列表 Tab / 独立复用）。
struct DashboardCandidatePickerList: View {
    @Environment(\.kssTheme) private var theme
    var searchPlaceholder: String = "搜索代码或名称"
    var candidates: [SurfaceCandidate]
    var disabledCodes: Set<String> = []
    var isLoading: Bool = false
    @Binding var filter: String
    var onSelect: (SurfaceCandidate) -> Void

    private var filtered: [SurfaceCandidate] {
        let base = candidates.filter { !disabledCodes.contains($0.code.uppercased()) }
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !q.isEmpty else { return base }
        return base.filter {
            $0.code.uppercased().contains(q) || ($0.name ?? "").uppercased().contains(q)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField(searchPlaceholder, text: $filter)
                .textFieldStyle(.roundedBorder)
            if isLoading {
                HStack {
                    Spacer()
                    ProgressView()
                        .controlSize(.small)
                    Text("加载候选…")
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                }
                .frame(maxWidth: .infinity, minHeight: 200)
            } else if filtered.isEmpty {
                Text(candidates.isEmpty ? "暂无候选" : "无匹配项")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 200, alignment: .center)
            } else {
                List {
                    ForEach(filtered) { c in
                        Button {
                            onSelect(c)
                        } label: {
                            HStack {
                                Text(c.code)
                                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                                    .foregroundStyle(theme.textPrimary)
                                Text(c.name ?? "")
                                    .foregroundStyle(theme.textSecondary)
                                Spacer()
                            }
                        }
                        .buttonStyle(.plain)
                        .disabled(disabledCodes.contains(c.code.uppercased()))
                    }
                }
                .frame(minHeight: 220)
            }
        }
    }
}

/// 简单选项列表（如指标小卡白名单），用于 Sparkle 列表 Tab。
struct DashboardSimpleChoiceList: View {
    @Environment(\.kssTheme) private var theme
    var choices: [(id: String, title: String)]
    var selectedId: String? = nil
    var onSelect: (String) -> Void

    var body: some View {
        List {
            ForEach(choices, id: \.id) { choice in
                Button {
                    onSelect(choice.id)
                } label: {
                    HStack {
                        Text(choice.title)
                            .font(KSSFont.themed(14, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Spacer()
                        if selectedId == choice.id {
                            Image(systemName: "checkmark")
                                .foregroundStyle(theme.accent)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .frame(minHeight: 220)
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
    /// 命中 key 的标签右上角画状态点（R2-U4 KTD4：警示黄，「有待处理项」语义）。
    var badgedKeys: Set<Key> = []

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
                            .overlay(alignment: .topTrailing) {
                                if badgedKeys.contains(option.key) {
                                    Circle()
                                        .fill(theme.ma5)
                                        .frame(width: 6, height: 6)
                                        .offset(x: -2, y: 2)
                                }
                            }
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
