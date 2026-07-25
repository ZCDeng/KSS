import SwiftUI
import WebKit

/// Shows the project's interactive architecture diagram (the self-contained
/// docs/kss_architecture_interactive.html, bundled into the app) in a WKWebView.
struct ArchitectureView: View {
    @Environment(\.kssTheme) private var theme
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                PageTitle("架构全景", subtitle: "KSS 系统架构 · 交互版（点击节点高亮数据流）")
                Spacer()
                if let url = URL(string: "https://github.com/ZCDeng/KSS") {
                    Link(destination: url) {
                        Label("GitHub", systemImage: "arrow.up.forward.square")
                            .font(KSSFont.themed(13, .semibold, theme: theme))
                    }
                    .tint(theme.accent)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 18)

            LocalHTMLView(resource: "architecture")
                .background(theme.canvas)
        }
        .background(theme.canvas)
    }
}

/// Loads a bundled, self-contained HTML resource into a WKWebView and pushes the
/// app's full palette payload via `window.kssSetTheme`（不再只 toggle `.dark` class）。
struct LocalHTMLView: NSViewRepresentable {
    var resource: String
    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        if let html = KSSResources.bundle.url(forResource: resource, withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.latestTheme = webTheme
        webView.underPageBackgroundColor = theme.canvasNS
        context.coordinator.requestSync()
    }

    /// 架构图无独立内容表面：只推主题（contentScript 继承默认 nil）。
    final class Coordinator: BridgedWebCoordinator {}
}
