import AppKit
import SwiftUI

/// 侧栏导航自定义图标（PDF，`currentColor` 导出 → template 着色）。
/// 资源：`Resources/Icons/nav/{base}-outline.pdf` / `{base}-filled.pdf`
enum SidebarNavIconCatalog {
    /// 与设计稿文件名映射；无映射的 section 继续走 SF Symbol。
    static func resourceBase(for section: WorkspaceSection) -> String? {
        switch section {
        case .dashboard: return "dashboard"
        case .recommendations: return "recommendations"
        case .watchlist: return "watchlist"
        case .themes: return "themes"
        case .trends: return "trends"
        case .reviews: return "reviews"
        case .newsDigest: return "newsDigest"
        case .backtests: return "backtests"
        case .stocks: return "stocks"
        case .aiChat: return "seesaw"
        case .runbook, .architecture, .settings:
            return nil
        }
    }

    static func image(base: String, filled: Bool) -> NSImage? {
        let name = filled ? "\(base)-filled" : "\(base)-outline"
        // Resources/Icons 整目录 copy 后路径为 Icons/nav/…
        if let url = KSSResources.bundle.url(
            forResource: name,
            withExtension: "pdf",
            subdirectory: "Icons/nav"
        ) {
            return NSImage(contentsOf: url)
        }
        // 兜底：不带 subdirectory（部分 SPM 布局会平铺）
        if let url = KSSResources.bundle.url(forResource: name, withExtension: "pdf") {
            return NSImage(contentsOf: url)
        }
        return nil
    }
}

/// 侧栏 section 图标：有自定义 PDF 用 outline/filled；否则 SF Symbol。
struct SidebarSectionIcon: View {
    let section: WorkspaceSection
    var filled: Bool
    var pointSize: CGFloat
    var frameWidth: CGFloat?
    var fontWeight: Font.Weight?
    var chirpWeight: Font.Weight?
    var theme: KSSThemeTokens

    var body: some View {
        Group {
            if let base = SidebarNavIconCatalog.resourceBase(for: section),
               let ns = SidebarNavIconCatalog.image(base: base, filled: filled) {
                Image(nsImage: ns)
                    .resizable()
                    .renderingMode(.template)
                    .scaledToFit()
                    .frame(width: pointSize, height: pointSize)
            } else {
                Image(systemName: section.symbol)
                    .symbolVariant(filled ? .fill : .none)
                    .font(KSSFont.themed(
                        pointSize,
                        fontWeight ?? .semibold,
                        chirpWeight: chirpWeight ?? (filled ? .heavy : .regular),
                        theme: theme
                    ))
                    .fontWeight(fontWeight)
            }
        }
        .frame(width: frameWidth ?? pointSize)
    }
}
