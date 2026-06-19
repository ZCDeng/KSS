import SwiftUI
import WebKit

/// Shows the project's interactive architecture diagram (the self-contained
/// docs/kss_architecture_interactive.html, bundled into the app) in a WKWebView.
struct ArchitectureView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                PageTitle("架构全景", subtitle: "KSS 系统架构 · 交互版（点击节点高亮数据流）")
                Spacer()
                if let url = URL(string: "https://github.com/ZCDeng/KSS") {
                    Link(destination: url) {
                        Label("GitHub", systemImage: "arrow.up.forward.square")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .tint(KSSTheme.accent)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 18)

            LocalHTMLView(resource: "architecture")
                .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
    }
}

/// Loads a bundled, self-contained HTML resource into a WKWebView and syncs its
/// `html.dark` class to the app's color scheme.
struct LocalHTMLView: NSViewRepresentable {
    var resource: String
    @Environment(\.colorScheme) private var colorScheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        if let html = Bundle.module.url(forResource: resource, withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.isDark = colorScheme == .dark
        if context.coordinator.isLoaded { context.coordinator.applyTheme(webView) }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var isLoaded = false
        var isDark = true

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoaded = true
            applyTheme(webView)
        }

        func applyTheme(_ webView: WKWebView) {
            webView.evaluateJavaScript("document.documentElement.classList.toggle('dark', \(isDark));", completionHandler: nil)
        }
    }
}
