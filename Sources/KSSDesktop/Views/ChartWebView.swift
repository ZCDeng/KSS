import SwiftUI
import WebKit

/// Hosts the bundled TradingView lightweight-charts candlestick chart inside a
/// WKWebView. Swift owns the data; the web layer only renders. 主题与数据分离：
/// `kssSetTheme` 更新配色并从缓存 bars 重绘，`kssSetData` 只更新数据。
struct ChartWebView: NSViewRepresentable {
    var points: [PricePoint]
    /// U3 日内模式（F008）：非空时渲染分钟 K 线（走 chart.html 的 kssSetIntradayData 路径）。
    var intradayBars: [OHLCBar]? = nil
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
        if let bars = intradayBars {
            // 日内模式：推分钟 bar 序列（F006 全序列）。
            let json = Self.encodeBars(bars)
            if json != coord.latestIntradayJSON || !coord.isIntraday {
                coord.latestIntradayJSON = json
                coord.isIntraday = true
                coord.bumpContent()
            }
        } else {
            let json = Self.encode(points)
            if json != coord.latestJSON || coord.isIntraday {
                coord.latestJSON = json
                coord.isIntraday = false
                coord.bumpContent()
            }
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

    private static func encodeBars(_ bars: [OHLCBar]) -> String {
        guard let data = try? JSONEncoder().encode(bars),
              let string = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return string
    }

    final class Coordinator: BridgedWebCoordinator {
        var latestJSON = "[]"
        var latestIntradayJSON = "[]"
        var isIntraday = false

        override func contentScript() -> String? {
            if isIntraday {
                return "window.kssSetIntradayData(\(latestIntradayJSON));"
            }
            return "window.kssSetData(\(latestJSON));"
        }
    }
}
