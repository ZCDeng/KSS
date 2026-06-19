import SwiftUI
import WebKit

/// Renders a Markdown report into readable styled HTML (Discord theme) via the
/// bundled `marked` parser inside a WKWebView. Tables, headings, code, and
/// blockquotes all render properly instead of raw monospace text.
struct MarkdownWebView: NSViewRepresentable {
    var text: String
    @Environment(\.colorScheme) private var colorScheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        if let html = Bundle.module.url(forResource: "markdown", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.latestText = text
        context.coordinator.isDark = colorScheme == .dark
        webView.underPageBackgroundColor = colorScheme == .dark
            ? NSColor(srgbRed: 0x1F/255, green: 0x1F/255, blue: 0x1D/255, alpha: 1)
            : NSColor.white
        if context.coordinator.isLoaded {
            context.coordinator.push(into: webView)
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var isLoaded = false
        var latestText = ""
        var isDark = true

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoaded = true
            push(into: webView)
        }

        func push(into webView: WKWebView) {
            let json = (try? JSONEncoder().encode(latestText)).flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
            webView.evaluateJavaScript("window.kssSetMarkdown(\(json), \(isDark));", completionHandler: nil)
        }
    }
}
