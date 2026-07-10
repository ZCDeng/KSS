import SwiftUI
import Combine

/// 可观察的主题控制器：读写 `designSystemId` + `appearanceMode` 两个持久化键，
/// 规范化缺失/非法/遗留值，并向视图树发布当前 token 与 WebView payload。
/// 注入 `UserDefaults` 便于测试（teardown 清理 suite）。
final class ThemeController: ObservableObject {
    @Published private(set) var designSystem: KSSDesignSystem
    @Published private(set) var appearance: KSSAppearance
    /// 顶层「新版 x.com / 经典版」模式。切到 xcom 时 designSystem 会被设为 .xcom；
    /// 切回 classic 时从 `lastClassicDesignSystemId` 恢复用户上次的经典选择。
    @Published private(set) var uiGeneration: KSSUIGeneration

    private let defaults: UserDefaults

    static let designSystemKey = "designSystemId"
    static let appearanceKey = "appearanceMode"
    static let uiGenerationKey = "uiGenerationId"
    static let lastClassicDesignSystemKey = "lastClassicDesignSystemId"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let ds = KSSDesignSystem.normalized(defaults.string(forKey: Self.designSystemKey))
        let ap = KSSAppearance.normalized(defaults.string(forKey: Self.appearanceKey))
        let gen = KSSUIGeneration.normalized(defaults.string(forKey: Self.uiGenerationKey))
        self.designSystem = ds
        self.appearance = ap
        self.uiGeneration = gen
        // 首次读取即把规范化结果写回（缺失/非法/遗留 system → dark；缺失/非法设计系统 → clayM3；缺失/非法模式 → classic）。
        defaults.set(ds.rawValue, forKey: Self.designSystemKey)
        defaults.set(ap.rawValue, forKey: Self.appearanceKey)
        defaults.set(gen.rawValue, forKey: Self.uiGenerationKey)
    }

    var palette: KSSPalette { ThemeCatalog.palette(for: designSystem, appearance: appearance) }
    var tokens: KSSThemeTokens { palette.tokens }
    var webPayload: KSSWebThemePayload { palette.webPayload }
    var colorScheme: ColorScheme { appearance.colorScheme }

    /// 当前「模式 · 设计系统 · 亮/暗」只读摘要（菜单首行）。
    var summary: String {
        uiGeneration == .xcom
            ? "\(uiGeneration.displayName) · \(appearance.displayName)"
            : "\(uiGeneration.displayName) · \(designSystem.displayName) · \(appearance.displayName)"
    }

    /// 经典版下切换 8 套设计系统；xcom 模式下忽略（经典版子菜单本身不在 xcom 下渲染，见 ContentView）。
    func select(system: KSSDesignSystem) {
        guard uiGeneration == .classic, system != designSystem else { return }
        designSystem = system
        defaults.set(system.rawValue, forKey: Self.designSystemKey)
    }

    func select(appearance: KSSAppearance) {
        guard appearance != self.appearance else { return }
        self.appearance = appearance
        defaults.set(appearance.rawValue, forKey: Self.appearanceKey)
    }

    /// 顶层新版/经典版切换。切到 xcom 时记住当前经典选择;切回 classic 时恢复它(缺失/非法 → clayM3)。
    func select(generation: KSSUIGeneration) {
        guard generation != uiGeneration else { return }
        if generation == .xcom {
            defaults.set(designSystem.rawValue, forKey: Self.lastClassicDesignSystemKey)
            designSystem = .xcom
            defaults.set(KSSDesignSystem.xcom.rawValue, forKey: Self.designSystemKey)
        } else {
            let restored = KSSDesignSystem.normalized(defaults.string(forKey: Self.lastClassicDesignSystemKey))
            designSystem = restored
            defaults.set(restored.rawValue, forKey: Self.designSystemKey)
        }
        uiGeneration = generation
        defaults.set(generation.rawValue, forKey: Self.uiGenerationKey)
    }
}
