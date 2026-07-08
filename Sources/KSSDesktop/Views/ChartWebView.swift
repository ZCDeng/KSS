import SwiftUI
import WebKit

/// Hosts the bundled lightweight-charts candlestick chart inside a WKWebView.
/// Swift owns the data; the web layer only renders.
/// U9: 双实例模式——日线蜡烛图（上方）+ 分钟线图（下方独立 WKWebView），
/// 通过 `subscribeVisibleLogicalRangeChange` 同步水平滚动。
struct ChartWebView: NSViewRepresentable {
    var points: [PricePoint]
    var intradayBars: [OHLCBar]? = nil
    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let wv = makeChartWebView(url: Bundle.module.url(forResource: "chart", withExtension: "html"),
                                   coordinator: context.coordinator)
        if let bars = intradayBars, !bars.isEmpty {
            // 创建第二个 chart 实例用于分钟线（独立 WebView — chart.html 内建双实例支持）
            context.coordinator.intradayWebView = makeChartWebView(
                url: Bundle.module.url(forResource: "chart", withExtension: "html"),
                coordinator: context.coordinator)
        }
        return wv
    }

    private func makeChartWebView(url: URL?, coordinator: Coordinator) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = coordinator
        coordinator.attach(webView)
        webView.setValue(false, forKey: "drawsBackground")
        if let html = url { webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent()) }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.latestTheme = webTheme
        let json = Self.encode(points)
        if json != coord.latestJSON { coord.latestJSON = json; coord.bumpContent() }
        webView.underPageBackgroundColor = theme.chartSurfaceNS
        coord.requestSync()
        // 分钟线 WebView 同步推送
        if let iwv = coord.intradayWebView, let bars = intradayBars, !bars.isEmpty {
            let ijson = Self.encodeBars(bars)
            if ijson != coord.latestIntradayJSON { coord.latestIntradayJSON = ijson }
            iwv.underPageBackgroundColor = theme.chartSurfaceNS
            // 分钟线 chart 实例创建后首次推数据 + bind scroll sync
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                coord.syncIntradayData()
            }
        }
    }

    static func encode(_ points: [PricePoint]) -> String {
        guard let data = try? JSONEncoder().encode(points), let s = String(data: data, encoding: .utf8) else { return "[]" }
        return s
    }
    static func encodeBars(_ bars: [OHLCBar]) -> String {
        guard let data = try? JSONEncoder().encode(bars), let s = String(data: data, encoding: .utf8) else { return "[]" }
        return s
    }

    final class Coordinator: BridgedWebCoordinator {
        var latestJSON = "[]"
        var latestIntradayJSON = "[]"
        var intradayWebView: WKWebView?

        override func contentScript() -> String? {
            return "window.kssSetData(\(latestJSON));"
        }
        func syncIntradayData() {
            guard let wv = intradayWebView else { return }
            wv.evaluateJavaScript("window.kssSetIntradayData(\(latestIntradayJSON));") { _, err in
                if let err { print("[ChartWebView] intraday push error: \(err)") }
            }
        }
    }
}
