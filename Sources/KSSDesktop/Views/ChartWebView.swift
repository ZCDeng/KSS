import SwiftUI
import WebKit

/// Hosts the bundled TradingView lightweight-charts candlestick chart inside a
/// WKWebView. Swift owns the data; the web layer only renders. 主题与数据分离：
/// `kssSetTheme` 更新配色并从缓存 bars 重绘，`kssSetData` 只更新数据。
struct ChartWebView: NSViewRepresentable {
    var points: [PricePoint]
    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        webView.setValue(false, forKey: "drawsBackground") // transparent until chart paints

        if let html = Bundle.module.url(forResource: "chart", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.latestTheme = webTheme
        let json = Self.encode(points)
        if json != coord.latestJSON {
            coord.latestJSON = json
            coord.bumpContent()
        }
        webView.underPageBackgroundColor = theme.chartSurfaceNS
        coord.requestSync()
    }

    private static func encode(_ points: [PricePoint]) -> String {
        guard let data = try? JSONEncoder().encode(points),
              let string = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return string
    }

    final class Coordinator: BridgedWebCoordinator {
        var latestJSON = "[]"

        override func contentScript() -> String? {
            "window.kssSetData(\(latestJSON));"
        }
    }
}
