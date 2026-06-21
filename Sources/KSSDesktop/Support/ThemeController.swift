import SwiftUI
import Combine

/// 可观察的主题控制器：读写 `designSystemId` + `appearanceMode` 两个持久化键，
/// 规范化缺失/非法/遗留值，并向视图树发布当前 token 与 WebView payload。
/// 注入 `UserDefaults` 便于测试（teardown 清理 suite）。
final class ThemeController: ObservableObject {
    @Published private(set) var designSystem: KSSDesignSystem
    @Published private(set) var appearance: KSSAppearance

    private let defaults: UserDefaults

    static let designSystemKey = "designSystemId"
    static let appearanceKey = "appearanceMode"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let ds = KSSDesignSystem.normalized(defaults.string(forKey: Self.designSystemKey))
        let ap = KSSAppearance.normalized(defaults.string(forKey: Self.appearanceKey))
        self.designSystem = ds
        self.appearance = ap
        // 首次读取即把规范化结果写回（缺失/非法/遗留 system → dark；缺失/非法设计系统 → clayM3）。
        defaults.set(ds.rawValue, forKey: Self.designSystemKey)
        defaults.set(ap.rawValue, forKey: Self.appearanceKey)
    }

    var palette: KSSPalette { ThemeCatalog.palette(for: designSystem, appearance: appearance) }
    var tokens: KSSThemeTokens { palette.tokens }
    var webPayload: KSSWebThemePayload { palette.webPayload }
    var colorScheme: ColorScheme { appearance.colorScheme }

    /// 当前「设计系统 · 亮/暗」只读摘要（菜单首行）。
    var summary: String { "\(designSystem.displayName) · \(appearance.displayName)" }

    func select(system: KSSDesignSystem) {
        guard system != designSystem else { return }
        designSystem = system
        defaults.set(system.rawValue, forKey: Self.designSystemKey)
    }

    func select(appearance: KSSAppearance) {
        guard appearance != self.appearance else { return }
        self.appearance = appearance
        defaults.set(appearance.rawValue, forKey: Self.appearanceKey)
    }
}
