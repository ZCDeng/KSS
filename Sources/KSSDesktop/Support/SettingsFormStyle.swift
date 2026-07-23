import SwiftUI

/// xcom 设置页表单视觉令牌——以「任务」分区（`ScheduledTasksSection` / `ScheduledJobRow`）为标准。
/// 自检 / 凭证 / yupi / 日志 在 xcom 下复用同一套字号、间距、按钮与状态胶囊。
enum SettingsFormStyle {
    /// 区块之间（健康卡 / 分类块 / 源卡）
    static let blockSpacing: CGFloat = 12
    /// 卡内主列
    static let cardInnerSpacing: CGFloat = 12
    /// 分类头与行之间
    static let groupSpacing: CGFloat = 8
    /// 行内标题与 meta
    static let titleMetaSpacing: CGFloat = 3
    /// 行横向元素
    static let rowHSpacing: CGFloat = 12
    /// 详情区水平 padding
    static let detailHPadding: CGFloat = 20
    /// 详情区垂直 padding
    static let detailVPadding: CGFloat = 16
    /// 主内容卡 padding（对齐 job row 11、health 12）
    static let cardPadding: CGFloat = 12
    /// 信息/提示条 padding
    static let bannerPadding: CGFloat = 10

    // 字号（pt）
    static let pageTitle: CGFloat = 14.5      // = job.title
    static let itemTitle: CGFloat = 14.5
    static let sectionHeader: CGFloat = 12.5  // = category header
    static let bodyHint: CGFloat = 12.5       // = batch note / 说明
    static let emptyHint: CGFloat = 13.5
    static let fieldLabel: CGFloat = 12.5
    static let meta: CGFloat = 11.5           // = schedule capsule / last run
    static let metaSmall: CGFloat = 11
    static let actionLabel: CGFloat = 12      // = 重跑
    static let actionLabelSm: CGFloat = 11.5  // = 同步
    static let primaryAction: CGFloat = 13    // = 一键补跑
    static let monoMeta: CGFloat = 11

    static func usesTasksStandard(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }
}

// MARK: - 可复用小件（xcom 设置）

/// 说明/空态提示（任务空态 13.5 / 批注 12.5）。
struct SettingsHintText: View {
    @Environment(\.kssTheme) private var theme
    var text: String
    var empty: Bool = false

    var body: some View {
        Text(text)
            .font(KSSFont.themed(
                empty ? SettingsFormStyle.emptyHint : SettingsFormStyle.bodyHint,
                theme: theme
            ))
            .foregroundStyle(theme.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// 状态胶囊（对齐 job.schedule 胶囊：11.5 semibold + secondary 底）。
struct SettingsStatusCapsule: View {
    @Environment(\.kssTheme) private var theme
    var text: String
    var tint: Color? = nil

    var body: some View {
        let color = tint ?? theme.textSecondary
        Text(text)
            .font(KSSFont.themed(SettingsFormStyle.meta, .semibold, theme: theme))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 1.5)
            .background(color.opacity(0.12), in: Capsule())
    }
}

/// 次要操作：`.bordered` + Label 12 semibold（对齐「重跑」）。
struct SettingsBorderedAction: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var systemImage: String?
    var busy: Bool = false
    var disabled: Bool = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            if busy {
                ProgressView().controlSize(.small)
            } else if let systemImage {
                Label(title, systemImage: systemImage)
                    .font(KSSFont.themed(SettingsFormStyle.actionLabel, .semibold, theme: theme))
                    .labelStyle(.titleAndIcon)
            } else {
                Text(title)
                    .font(KSSFont.themed(SettingsFormStyle.actionLabel, .semibold, theme: theme))
            }
        }
        .buttonStyle(.bordered)
        .disabled(disabled || busy)
    }
}

/// 主操作：`.borderedProminent` + 13 bold（对齐「一键补跑」）。
struct SettingsPrimaryAction: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var systemImage: String?
    var busy: Bool = false
    var disabled: Bool = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            if busy {
                ProgressView().controlSize(.small)
            } else if let systemImage {
                Label(title, systemImage: systemImage)
                    .font(KSSFont.themed(SettingsFormStyle.primaryAction, .bold, theme: theme))
            } else {
                Text(title)
                    .font(KSSFont.themed(SettingsFormStyle.primaryAction, .bold, theme: theme))
            }
        }
        .buttonStyle(.borderedProminent)
        .tint(theme.accent)
        .disabled(disabled || busy)
    }
}

/// 信息条（对齐 batchNoteBar / catchUp 用 kssCard）。
struct SettingsInfoBanner: View {
    @Environment(\.kssTheme) private var theme
    var text: String
    var isError: Bool = false
    var systemImage: String = "info.circle.fill"

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundStyle(isError ? theme.up : theme.accent)
            Text(text)
                .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(isError ? .warning : .info, padding: SettingsFormStyle.bannerPadding)
    }
}
