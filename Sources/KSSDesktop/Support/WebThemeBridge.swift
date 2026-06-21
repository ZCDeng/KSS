import SwiftUI
import WebKit

// MARK: - WebView payload 环境注入

private struct KSSWebThemeKey: EnvironmentKey {
    static let defaultValue: KSSWebThemePayload =
        ThemeCatalog.palette(for: .clayM3, appearance: .dark).webPayload
}

extension EnvironmentValues {
    /// 三个 WKWebView 共享的版本化主题 payload。
    var kssWebTheme: KSSWebThemePayload {
        get { self[KSSWebThemeKey.self] }
        set { self[KSSWebThemeKey.self] = newValue }
    }
}

// MARK: - 无 WebKit 纯状态机（可单测）

/// navigation identity + generation + revision 判定，封闭 stale 回调/异步 completion 竞态。
/// 用 Int 表示 navigation identity（生产里取 `ObjectIdentifier(navigation).hashValue`），
/// 使该 reducer 不依赖 WebKit，便于单测。
struct WebSyncState: Equatable {
    var isLoaded = false
    var activeNavigation: Int?
    var generation = 0
    var contentRevision = 0
    var lastAppliedContentRevision = -1
    var synchronizationRevision = 0

    /// 新导航开始：记录 identity、重置 ready、generation +1。
    mutating func didStartNavigation(_ id: Int?) {
        activeNavigation = id
        isLoaded = false
        generation += 1
    }

    /// didFinish：仅当与当前 activeNavigation 一致（或本次/历史无 identity）才接受。
    /// 返回是否应触发同步。
    @discardableResult
    mutating func didFinish(_ id: Int?) -> Bool {
        if let active = activeNavigation, let id, id != active { return false }
        isLoaded = true
        return true
    }

    /// 内容变更（updateNSView 检测到新数据时调用）。
    mutating func bumpContent() { contentRevision += 1 }

    /// 开一次同步，返回快照 token；completion 回来时用 `isCurrent` 校验。
    mutating func beginSync() -> SyncToken {
        synchronizationRevision += 1
        return SyncToken(generation: generation,
                         synchronization: synchronizationRevision,
                         navigation: activeNavigation,
                         contentRevision: contentRevision)
    }

    func isCurrent(_ token: SyncToken) -> Bool {
        isLoaded
            && token.generation == generation
            && token.synchronization == synchronizationRevision
            && token.navigation == activeNavigation
    }

    /// 是否需要推内容（内容修订 != 已应用修订）。
    var needsContent: Bool { contentRevision != lastAppliedContentRevision }

    mutating func markContentApplied(_ revision: Int) {
        lastAppliedContentRevision = revision
    }
}

struct SyncToken: Equatable {
    let generation: Int
    let synchronization: Int
    let navigation: Int?
    let contentRevision: Int
}

// MARK: - 共享协调器

/// 串行化「主题→内容」更新的 WKWebView 协调器基类。
/// - updateNSView 缓存最新 theme/content，内容变动递增 contentRevision，已加载则 `synchronize`。
/// - 主题与数据走不同 JS API：主题永远先于内容应用；内容未变时允许只推主题（图表从缓存重绘）。
/// - 所有 evaluateJavaScript completion 以 SyncToken 校验当前性，并记录错误供 debug/test。
class BridgedWebCoordinator: NSObject, WKNavigationDelegate {
    private(set) var state = WebSyncState()
    var latestTheme: KSSWebThemePayload?
    private(set) var lastError: String?

    private weak var webView: WKWebView?

    func attach(_ webView: WKWebView) { self.webView = webView }

    var isLoaded: Bool { state.isLoaded }

    /// 内容变更（updateNSView 检测到新数据时调用）。
    func bumpContent() { state.bumpContent() }

    // 子类覆写：主题脚本 / 内容脚本（nil = 无内容表面，如架构图）。
    func themeScript() -> String? {
        guard let latestTheme else { return nil }
        return "window.kssSetTheme(\(latestTheme.json));"
    }
    func contentScript() -> String? { nil }

    // MARK: WKNavigationDelegate
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        state.didStartNavigation(navigation.map { ObjectIdentifier($0).hashValue })
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let id = navigation.map { ObjectIdentifier($0).hashValue }
        guard state.didFinish(id) else { return }
        synchronize()
    }

    /// updateNSView 在缓存最新值后调用。
    func requestSync() {
        guard state.isLoaded else { return }
        synchronize()
    }

    private func synchronize() {
        guard let webView else { return }
        let token = state.beginSync()
        guard state.isCurrent(token) else { return }

        let applyContent: () -> Void = { [weak self] in
            guard let self, self.state.isCurrent(token) else { return }
            if let script = self.contentScript() {
                self.eval(webView, script, token) { ok in
                    if ok { self.state.markContentApplied(token.contentRevision) }
                }
            } else {
                self.state.markContentApplied(token.contentRevision)
            }
        }

        if state.needsContent {
            // theme → content 串行
            if let theme = themeScript() {
                eval(webView, theme, token) { ok in if ok { applyContent() } }
            } else {
                applyContent()
            }
        } else {
            // 内容已最新：只推主题（图表用缓存 bars 重绘）。
            if let theme = themeScript() {
                eval(webView, theme, token) { _ in }
            }
        }
    }

    private func eval(_ webView: WKWebView, _ js: String, _ token: SyncToken,
                      _ done: @escaping (Bool) -> Void) {
        webView.evaluateJavaScript(js) { [weak self] _, error in
            if let error {
                self?.lastError = String(describing: error)
                NSLog("[KSS web] evaluateJavaScript error: %@", String(describing: error))
            }
            guard let self, self.state.isCurrent(token) else { done(false); return }
            done(error == nil)
        }
    }
}
