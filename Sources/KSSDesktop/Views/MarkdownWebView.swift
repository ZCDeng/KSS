import SwiftUI
import WebKit

/// Renders a Markdown report into readable styled HTML via the bundled `marked`
/// parser inside a WKWebView. 主题与内容分离：`kssSetTheme` 覆盖配色，`kssSetMarkdown`
/// 只更新正文（切换主题不必重解析 Markdown）。
struct MarkdownWebView: NSViewRepresentable {
    var text: String
    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        webView.setValue(false, forKey: "drawsBackground")
        if let html = Bundle.module.url(forResource: "markdown", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.latestTheme = webTheme
        if text != coord.latestText {
            coord.latestText = text
            coord.bumpContent()
        }
        webView.underPageBackgroundColor = theme.canvasNS
        coord.requestSync()
    }

    final class Coordinator: BridgedWebCoordinator {
        var latestText = ""

        override func contentScript() -> String? {
            let json = (try? JSONEncoder().encode(latestText)).flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
            return "window.kssSetMarkdown(\(json));"
        }
    }
}
