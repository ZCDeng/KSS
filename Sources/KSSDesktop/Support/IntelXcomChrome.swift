import Foundation

/// 资讯雷达 chrome 策略：xcom timeline vs 经典读报台（plan 2026-07-23-002）。
/// 纯函数、无 SwiftUI 依赖，供 `IntelView` 与单元测试共用。
enum IntelXcomChrome {
    static func isXcom(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 扁平 timeline cell，而非 qmreader entry-card。
    static func usesTimelineList(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 赛道 / 阅读 Tab 用底蓝下划线，而非凹槽分段。
    static func usesUnderlineTabs(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 去掉 PageTitle 大墙，统计一行、工具图标化。
    static func usesSlimHeader(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 全景不放在赛道上方大卡，迁到无选中详情空态。
    static func demotesPanoramaToEmptyDetail(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 今日要点默认弱化 chrome（单行折叠、无 accent 强描边卡）。
    static func usesCollapsedDigestChrome(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 经典 entry-card 圆角阴影路径。
    static func usesEntryCardChrome(_ system: KSSDesignSystem) -> Bool {
        system != .xcom
    }

    /// 赛道色点：xcom 去掉以贴近 Paper 兴趣 Tab；经典保留。
    static func showTrackColorDot(_ system: KSSDesignSystem) -> Bool {
        system != .xcom
    }

    /// 列表行间距（xcom hairline 贴行 → 0）。
    static func listRowSpacing(_ system: KSSDesignSystem) -> CGFloat {
        system == .xcom ? 0 : 8
    }

    /// 列表内容区外 padding（xcom 全宽贴边）。
    static func listContentPadding(_ system: KSSDesignSystem) -> CGFloat {
        system == .xcom ? 0 : 8
    }

    /// 详情标题字号（xcom 线程感 18，经典杂志 22）。
    static func detailTitlePointSize(_ system: KSSDesignSystem) -> CGFloat {
        system == .xcom ? 18 : 22
    }

    /// 列表选中 chrome。
    enum SelectionChrome: Equatable {
        /// 圆角卡 + 描边 + 阴影
        case entryCard
        /// 浅底 + 左侧 accent 条，无阴影
        case timelineFill
    }

    static func selectionChrome(_ system: KSSDesignSystem) -> SelectionChrome {
        system == .xcom ? .timelineFill : .entryCard
    }

    /// 行 hover 叠加透明度（对齐侧栏量级）。
    static func hoverOverlayOpacity(appearance: KSSAppearance, isXcom: Bool) -> Double {
        guard isXcom else { return 0 }
        return appearance == .dark ? 0.10 : 0.07
    }
}
