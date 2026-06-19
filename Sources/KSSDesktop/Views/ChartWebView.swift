import SwiftUI
import WebKit

/// Hosts the bundled TradingView lightweight-charts candlestick chart inside a
/// WKWebView. Swift owns the data; the web layer only renders. OHLC + volume +
/// MA5/MA20 are pushed in as JSON whenever `points` changes.
struct ChartWebView: NSViewRepresentable {
    var points: [PricePoint]

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground") // transparent until chart paints
        webView.underPageBackgroundColor = NSColor(red: 0x1E/255, green: 0x22/255, blue: 0x2D/255, alpha: 1)

        if let html = Bundle.module.url(forResource: "chart", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let json = Self.encode(points)
        context.coordinator.latestJSON = json
        if context.coordinator.isLoaded {
            context.coordinator.push(into: webView)
        }
    }

    private static func encode(_ points: [PricePoint]) -> String {
        guard let data = try? JSONEncoder().encode(points),
              let string = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return string
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var isLoaded = false
        var latestJSON = "[]"

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoaded = true
            push(into: webView)
        }

        func push(into webView: WKWebView) {
            webView.evaluateJavaScript("window.kssSetData(\(latestJSON));", completionHandler: nil)
        }
    }
}
