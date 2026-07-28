import AppKit
import SwiftUI
import WebKit

/// Renders Markdown / HTML report content inside a themed WKWebView shell.
///
/// 主题与内容分离：`kssSetTheme` 推配色；`kssSetMarkdown` / `kssSetHTML` 只更新正文。
/// 阅读皮固定 **Kami 白底打印版节奏**（demo-kami-print）：衬线标题 + 主题正文/配色，
/// **不**强制羊皮纸暖底，避免与 xcom chrome 色差过大。
/// 不引入第三方 JSBridge / SwiftUI-WebView——`BridgedWebCoordinator` 已覆盖
/// 离线资源、theme→content 串行、高度回传、外链拦截。
/// 见 `docs/plans/2026-07-29-001-research-content-webview-kami-integration.md`。
struct MarkdownWebView: NSViewRepresentable {
    enum ContentKind: Equatable {
        case markdown
        case htmlFragment
    }

    var text: String
    var kind: ContentKind = .markdown
    /// 嵌在外层 ScrollView 时开启：按内容高度自适应，避免双滚动条。
    var fitsContent: Bool = false
    var minHeight: CGFloat = 120

    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator { Coordinator(fitsContent: fitsContent) }

    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(WeakMarkdownMessageHandler(context.coordinator), name: "kssMarkdown")

        let config = WKWebViewConfiguration()
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        context.coordinator.attachRepresented(webView)
        webView.setValue(false, forKey: "drawsBackground")
        webView.allowsMagnification = true
        if let html = KSSResources.bundle.url(forResource: "markdown", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let coord = context.coordinator
        coord.fitsContent = fitsContent
        coord.minHeight = minHeight
        coord.attachRepresented(webView)
        // print 模板节奏：配色/正文 sans 跟 chrome；仅标题 serif 用仓耳今楷。
        coord.latestTheme = webTheme.asEditorialContentTheme()
        let fingerprint = "\(kind == .htmlFragment ? "html" : "md")\u{1e}\(text)"
        if fingerprint != coord.latestFingerprint {
            coord.latestFingerprint = fingerprint
            coord.latestText = text
            coord.latestKind = kind
            coord.bumpContent()
        }
        webView.underPageBackgroundColor = theme.canvasNS
        coord.requestSync()
        coord.applyIntrinsicHeightIfNeeded()
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.teardown(webView)
    }

    final class Coordinator: BridgedWebCoordinator, WKScriptMessageHandler {
        var latestText = ""
        var latestKind: ContentKind = .markdown
        var latestFingerprint = ""
        var fitsContent = false
        var minHeight: CGFloat = 120

        private weak var representedWebView: WKWebView?
        private var reportedHeight: CGFloat = 0
        private var heightConstraint: NSLayoutConstraint?

        init(fitsContent: Bool) {
            self.fitsContent = fitsContent
            super.init()
        }

        func attachRepresented(_ webView: WKWebView) {
            attach(webView)
            representedWebView = webView
        }

        override func contentScript() -> String? {
            let json = (try? JSONEncoder().encode(latestText))
                .flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
            switch latestKind {
            case .markdown:
                return "window.kssSetMarkdown(\(json));"
            case .htmlFragment:
                return "window.kssSetHTML(\(json));"
            }
        }

        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "kssMarkdown" else { return }
            let body = message.body
            let value: CGFloat?
            if let dict = body as? [String: Any] {
                if let n = dict["value"] as? NSNumber { value = CGFloat(truncating: n) }
                else if let d = dict["value"] as? Double { value = CGFloat(d) }
                else { value = nil }
            } else if let n = body as? NSNumber {
                value = CGFloat(truncating: n)
            } else {
                value = nil
            }
            guard let value, value.isFinite, value > 0 else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                let next = max(self.minHeight, ceil(value))
                guard abs(next - self.reportedHeight) > 0.5 else { return }
                self.reportedHeight = next
                self.applyIntrinsicHeightIfNeeded()
            }
        }

        func applyIntrinsicHeightIfNeeded() {
            guard let webView = representedWebView else { return }
            guard fitsContent else {
                heightConstraint?.isActive = false
                return
            }
            let height = max(minHeight, reportedHeight)
            if let heightConstraint {
                heightConstraint.constant = height
                heightConstraint.isActive = true
            } else {
                let constraint = webView.heightAnchor.constraint(equalToConstant: height)
                constraint.identifier = "kss.md.height"
                constraint.priority = .required
                constraint.isActive = true
                heightConstraint = constraint
            }
        }

        func teardown(_ webView: WKWebView) {
            heightConstraint?.isActive = false
            heightConstraint = nil
            webView.navigationDelegate = nil
            webView.configuration.userContentController
                .removeScriptMessageHandler(forName: "kssMarkdown")
            representedWebView = nil
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }
            // 允许初始 file 文档与 about:blank；外链丢给系统浏览器。
            if navigationAction.navigationType == .other
                || url.isFileURL
                || url.absoluteString == "about:blank" {
                decisionHandler(.allow)
                return
            }
            if let scheme = url.scheme?.lowercased(), ["http", "https", "mailto"].contains(scheme) {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.cancel)
        }
    }
}

/// 避免 WKUserContentController 对 handler 的强引用环。
private final class WeakMarkdownMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}
