import SwiftUI
import WebKit

/// Local heatmap canvas. Swift owns the tape; the page only renders.
struct HeatmapWebView: NSViewRepresentable {
    var snapshot: HeatmapSnapshot
    var onMessage: (HeatmapMessage) -> Void

    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator {
        Coordinator(onMessage: onMessage)
    }

    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(WeakHeatmapMessageHandler(context.coordinator), name: "kssHeatmap")

        let config = WKWebViewConfiguration()
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        webView.setValue(false, forKey: "drawsBackground")

        if let html = KSSResources.bundle.url(
            forResource: "heatmap",
            withExtension: "html",
            subdirectory: "Heatmap"
        ) ?? KSSResources.bundle.url(forResource: "heatmap", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.onMessage = onMessage
        coord.latestTheme = webTheme
        if HeatmapTape.canShow(snapshot) {
            let json = Self.encode(snapshot)
            if json != coord.latestJSON {
                coord.latestJSON = json
                coord.bumpContent()
            }
        }
        webView.underPageBackgroundColor = theme.canvasNS
        coord.requestSync()
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.teardown(webView)
    }

    private static func encode(_ snapshot: HeatmapSnapshot) -> String {
        guard let data = try? JSONEncoder().encode(snapshot),
              let string = String(data: data, encoding: .utf8) else {
            return ""
        }
        return string
    }

    final class Coordinator: BridgedWebCoordinator, WKScriptMessageHandler {
        var latestJSON = ""
        var onMessage: (HeatmapMessage) -> Void

        init(onMessage: @escaping (HeatmapMessage) -> Void) {
            self.onMessage = onMessage
            super.init()
        }

        override var skipsUnchangedTheme: Bool { true }

        override func contentScript() -> String? {
            guard !latestJSON.isEmpty, let data = latestJSON.data(using: .utf8) else { return nil }
            let b64 = data.base64EncodedString()
            return "window.kssSetHeatmapB64 && window.kssSetHeatmapB64('\(b64)');"
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if navigationAction.request.url?.isFileURL == true {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
            }
        }

        func userContentController(
            _ controller: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard message.name == "kssHeatmap",
                  let parsed = HeatmapMessage.parse(message.body) else { return }
            if Thread.isMainThread {
                onMessage(parsed)
            } else {
                DispatchQueue.main.async { [weak self] in self?.onMessage(parsed) }
            }
        }

        func teardown(_ webView: WKWebView) {
            webView.configuration.userContentController
                .removeScriptMessageHandler(forName: "kssHeatmap")
        }
    }
}

private final class WeakHeatmapMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(
        _ controller: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        target?.userContentController(controller, didReceive: message)
    }
}
