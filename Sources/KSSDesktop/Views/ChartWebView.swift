import SwiftUI
import WebKit

/// Hosts the bundled TradingView lightweight-charts candlestick chart inside a
/// WKWebView. Swift owns the data; the web layer only renders. 主题与数据分离：
/// `kssSetTheme` 更新配色并从缓存 bars 重绘，`kssSetData` 只更新数据。
///
/// 周期钮在 chart.html 内（1分/5分/日/周/月/年）：分钟请求经 `kssChart` 消息回 Swift。
struct ChartWebView: NSViewRepresentable {
    var points: [PricePoint]
    /// 日内模式：非空时渲染分钟 K（kssSetIntradayData）。
    var intradayBars: [OHLCBar]? = nil
    /// 当前周期高亮：daily / m1 / m5（HTML 的 D/1m/5m）。
    var activeMode: ChartDataMode = .daily
    /// 状态条文案（加载中 / 错误）；空则不改 legend。
    var statusText: String? = nil
    /// MI Signal Pack overlay（JSON 字串，null 清除）。
    var miOverlayJSON: String? = nil
    /// 通用多指标 overlay 数组（JSON 字串，与 miOverlayJSON 并存不冲突；"null"/"[]" 视为空）。
    var indicatorOverlaysJSON: String? = nil
    /// 用户点 HTML TF 条时回调（含 1m/5m/日线结构）。
    var onSelectMode: ((ChartDataMode) -> Void)? = nil

    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator {
        Coordinator(onSelectMode: onSelectMode)
    }

    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(WeakChartMessageHandler(context.coordinator), name: "kssChart")

        let config = WKWebViewConfiguration()
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        webView.setValue(false, forKey: "drawsBackground")

        if let html = Bundle.module.url(forResource: "chart", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.onSelectMode = onSelectMode
        coord.latestTheme = webTheme

        let modeKey = Self.tfKey(activeMode)
        if modeKey != coord.latestTFKey {
            coord.latestTFKey = modeKey
            coord.pendingTFScript = "window.kssSetTFMode && window.kssSetTFMode('\(modeKey)');"
            coord.bumpContent()
        }

        let status = statusText ?? ""
        if status != coord.latestStatus {
            coord.latestStatus = status
            if !status.isEmpty {
                let escaped = status
                    .replacingOccurrences(of: "\\", with: "\\\\")
                    .replacingOccurrences(of: "'", with: "\\'")
                coord.pendingStatusScript = "window.kssSetStatus && window.kssSetStatus('\(escaped)');"
                coord.bumpContent()
            }
        }

        if let bars = intradayBars {
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
        let ov = miOverlayJSON ?? "null"
        if ov != coord.latestMiOverlayJSON {
            coord.latestMiOverlayJSON = ov
            coord.bumpContent()
        }
        let indOv = indicatorOverlaysJSON ?? "[]"
        if indOv != coord.latestIndicatorOverlaysJSON {
            coord.latestIndicatorOverlaysJSON = indOv
            coord.bumpContent()
        }
        webView.underPageBackgroundColor = theme.chartSurfaceNS
        coord.requestSync()
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.teardown(webView)
    }

    private static func tfKey(_ mode: ChartDataMode) -> String {
        switch mode {
        case .daily: return "D"
        case .m1: return "1m"
        case .m5: return "5m"
        }
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

    final class Coordinator: BridgedWebCoordinator, WKScriptMessageHandler {
        var latestJSON = "[]"
        var latestIntradayJSON = "[]"
        var latestMiOverlayJSON = "null"
        var latestIndicatorOverlaysJSON = "[]"
        var isIntraday = false
        var latestTFKey = "D"
        var latestStatus = ""
        var pendingTFScript: String?
        var pendingStatusScript: String?
        var onSelectMode: ((ChartDataMode) -> Void)?

        init(onSelectMode: ((ChartDataMode) -> Void)?) {
            self.onSelectMode = onSelectMode
            super.init()
        }

        override func contentScript() -> String? {
            var parts: [String] = []
            if let tf = pendingTFScript {
                parts.append(tf)
                pendingTFScript = nil
            }
            if isIntraday {
                parts.append("window.kssSetIntradayData(\(latestIntradayJSON));")
            } else {
                parts.append("window.kssSetData(\(latestJSON));")
            }
            // base64 注入：避免引号/大 JSON 破坏 evaluateJavaScript；HTML 侧 atob+parse
            if latestMiOverlayJSON == "null" {
                parts.append("window.kssSetMiOverlay && window.kssSetMiOverlay(null);")
            } else if let data = latestMiOverlayJSON.data(using: .utf8) {
                let b64 = data.base64EncodedString()
                parts.append(
                    "window.kssSetMiOverlayB64 && window.kssSetMiOverlayB64('\(b64)');"
                )
            } else {
                parts.append("window.kssSetMiOverlay && window.kssSetMiOverlay(null);")
            }
            // 通用多指标 overlay：同款 base64 注入，避免大 JSON 数组破坏 evaluateJavaScript。
            if latestIndicatorOverlaysJSON == "[]" || latestIndicatorOverlaysJSON == "null" {
                parts.append("window.kssSetIndicatorOverlays && window.kssSetIndicatorOverlays([]);")
            } else if let data = latestIndicatorOverlaysJSON.data(using: .utf8) {
                let b64 = data.base64EncodedString()
                parts.append(
                    "window.kssSetIndicatorOverlaysB64 && window.kssSetIndicatorOverlaysB64('\(b64)');"
                )
            } else {
                parts.append("window.kssSetIndicatorOverlays && window.kssSetIndicatorOverlays([]);")
            }
            if let st = pendingStatusScript {
                parts.append(st)
                pendingStatusScript = nil
            }
            return parts.joined(separator: "\n")
        }

        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "kssChart" else { return }
            let body = message.body
            var tf: String?
            if let dict = body as? [String: Any] {
                tf = dict["tf"] as? String
            } else if let str = body as? String {
                tf = str
            }
            guard let tf else { return }
            let mode: ChartDataMode
            switch tf {
            case "1m": mode = .m1
            case "5m": mode = .m5
            default: mode = .daily
            }
            if Thread.isMainThread {
                onSelectMode?(mode)
            } else {
                DispatchQueue.main.async { [weak self] in self?.onSelectMode?(mode) }
            }
        }

        func teardown(_ webView: WKWebView) {
            webView.configuration.userContentController
                .removeScriptMessageHandler(forName: "kssChart")
        }
    }
}

/// 避免 WKUserContentController 强引用 Coordinator 形成环。
private final class WeakChartMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}
