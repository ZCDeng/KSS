import AppKit
import SwiftUI
import WebKit

/// Renders Markdown / HTML report content inside a themed WKWebView shell.
///
/// - `fitsContent == false`：自滚主阅读器（复盘/回测主栏），占满父级剩余高度
/// - `fitsContent == true`：嵌在外层 ScrollView；高度跟内容走 + **滚轮转发给祖先 NSScrollView**
///
/// 见 `docs/plans/2026-07-29-001-research-content-webview-kami-integration.md`。
struct MarkdownWebView: View {
    enum ContentKind: Equatable {
        case markdown
        case htmlFragment
    }

    var text: String
    var kind: ContentKind = .markdown
    /// 嵌在外层 ScrollView 时开启：按内容高度自适应 + 滚轮外传。
    var fitsContent: Bool = false
    var minHeight: CGFloat = 120

    @State private var contentHeight: CGFloat = 0
    /// 上次驱动高度重置的内容指纹，避免同文反复把高度打回 minHeight。
    @State private var heightFingerprint = ""

    var body: some View {
        Representable(
            text: text,
            kind: kind,
            fitsContent: fitsContent,
            minHeight: minHeight,
            contentHeight: $contentHeight
        )
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(
            minHeight: fitsContent ? minHeight : nil,
            maxHeight: fitsContent ? nil : .infinity
        )
        // 嵌套模式：SwiftUI 用明确高度驱动外层 ScrollView 内容尺寸
        .frame(height: fitsContent ? max(minHeight, contentHeight) : nil, alignment: .top)
        .onChange(of: text) { _, newText in
            // 换文时先回落 minHeight，防止旧文过大高度撑出大片空白
            guard fitsContent else { return }
            let next = "\(kind == .htmlFragment ? "html" : "md")\u{1e}\(newText)"
            guard next != heightFingerprint else { return }
            heightFingerprint = next
            contentHeight = minHeight
        }
        .onAppear {
            heightFingerprint = "\(kind == .htmlFragment ? "html" : "md")\u{1e}\(text)"
            if fitsContent, contentHeight < minHeight {
                contentHeight = minHeight
            }
        }
    }
}

// MARK: - NSViewRepresentable

private struct Representable: NSViewRepresentable {
    var text: String
    var kind: MarkdownWebView.ContentKind
    var fitsContent: Bool
    var minHeight: CGFloat
    @Binding var contentHeight: CGFloat

    @Environment(\.kssTheme) private var theme
    @Environment(\.kssWebTheme) private var webTheme

    func makeCoordinator() -> Coordinator {
        Coordinator(fitsContent: fitsContent, minHeight: minHeight, contentHeight: $contentHeight)
    }

    func makeNSView(context: Context) -> ScrollForwardingWebView {
        let controller = WKUserContentController()
        controller.add(WeakMarkdownMessageHandler(context.coordinator), name: "kssMarkdown")

        let config = WKWebViewConfiguration()
        config.userContentController = controller

        let webView = ScrollForwardingWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.forwardScrollToSuperview = fitsContent
        context.coordinator.attachRepresented(webView)
        webView.setValue(false, forKey: "drawsBackground")
        webView.allowsMagnification = false
        Self.configureInternalScrolling(webView, fitsContent: fitsContent)
        if let html = KSSResources.bundle.url(forResource: "markdown", withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: ScrollForwardingWebView, context: Context) {
        let coord = context.coordinator
        coord.fitsContent = fitsContent
        coord.minHeight = minHeight
        coord.contentHeight = $contentHeight
        webView.forwardScrollToSuperview = fitsContent
        coord.attachRepresented(webView)
        Self.configureInternalScrolling(webView, fitsContent: fitsContent)
        // Kami print 节奏 + Chiron GoRound TC 内容皮；配色跟 chrome。
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
    }

    /// 只调 **WKWebView 子树** 里的 NSScrollView；绝不用 `enclosingScrollView`
    ///（在 SwiftUI ScrollView 嵌套时会误指外层，关掉后整页无法滚）。
    fileprivate static func configureInternalScrolling(_ webView: WKWebView, fitsContent: Bool) {
        func apply(to scroll: NSScrollView) {
            if fitsContent {
                scroll.hasVerticalScroller = false
                scroll.hasHorizontalScroller = false
                scroll.verticalScrollElasticity = .none
                scroll.horizontalScrollElasticity = .none
                scroll.scrollerStyle = .overlay
            } else {
                scroll.hasVerticalScroller = true
                scroll.verticalScrollElasticity = .allowed
            }
        }
        var stack: [NSView] = webView.subviews
        while let view = stack.popLast() {
            if let scroll = view as? NSScrollView {
                apply(to: scroll)
            }
            stack.append(contentsOf: view.subviews)
        }
    }

    static func dismantleNSView(_ webView: ScrollForwardingWebView, coordinator: Coordinator) {
        coordinator.teardown(webView)
    }

    final class Coordinator: BridgedWebCoordinator, WKScriptMessageHandler {
        var latestText = ""
        var latestKind: MarkdownWebView.ContentKind = .markdown
        var latestFingerprint = ""
        var fitsContent = false
        var minHeight: CGFloat = 120
        var contentHeight: Binding<CGFloat>

        private weak var representedWebView: ScrollForwardingWebView?

        init(fitsContent: Bool, minHeight: CGFloat, contentHeight: Binding<CGFloat>) {
            self.fitsContent = fitsContent
            self.minHeight = minHeight
            self.contentHeight = contentHeight
            super.init()
        }

        func attachRepresented(_ webView: ScrollForwardingWebView) {
            attach(webView)
            representedWebView = webView
        }

        /// 嵌套：文档不自滚；主阅读器：交给 WK 内置 NSScrollView。
        private var overflowScript: String {
            if fitsContent {
                return """
                document.documentElement.style.height='auto';
                document.documentElement.style.overflow='hidden';
                if(document.body){document.body.style.height='auto';document.body.style.overflow='hidden';document.body.style.overscrollBehavior='none';}
                """
            }
            return """
            document.documentElement.style.height='';
            document.documentElement.style.overflow='';
            if(document.body){document.body.style.height='';document.body.style.overflow='';document.body.style.overscrollBehavior='';}
            """
        }

        override func themeScript() -> String? {
            let base = super.themeScript() ?? ""
            return base + overflowScript
        }

        override func contentScript() -> String? {
            let json = (try? JSONEncoder().encode(latestText))
                .flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
            let set: String
            switch latestKind {
            case .markdown:
                set = "window.kssSetMarkdown(\(json));"
            case .htmlFragment:
                set = "window.kssSetHTML(\(json));"
            }
            return set + overflowScript
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
                guard let self, self.fitsContent else { return }
                // body padding 余量，避免末行被裁
                let next = max(self.minHeight, ceil(value) + 8)
                guard abs(next - self.contentHeight.wrappedValue) > 0.5 else { return }
                self.contentHeight.wrappedValue = next
            }
        }

        func teardown(_ webView: WKWebView) {
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

        override func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            super.webView(webView, didFinish: navigation)
            webView.evaluateJavaScript(overflowScript, completionHandler: nil)
            Representable.configureInternalScrolling(webView, fitsContent: fitsContent)
        }
    }
}

// MARK: - Scroll forwarding

/// macOS 上 WKWebView 嵌在 SwiftUI ScrollView 时会吞掉 scrollWheel。
/// fitsContent 模式：不调用 super，把事件交给**祖先** NSScrollView（从 superview 往上找）。
///
/// 注意：绝不能用 `webView.enclosingScrollView`——那常是 WK 内部滚动层，或误伤外层后整页锁死。
final class ScrollForwardingWebView: WKWebView {
    /// true = 嵌套在外层 ScrollView，转发滚轮；false = 自滚主阅读器。
    var forwardScrollToSuperview = false

    override func scrollWheel(with event: NSEvent) {
        guard forwardScrollToSuperview else {
            super.scrollWheel(with: event)
            return
        }
        if let outer = ancestorScrollView() {
            outer.scrollWheel(with: event)
            return
        }
        // SwiftUI 偶发不把 NSScrollView 放在 superview 链上：沿 responder 链再找一次
        var responder: NSResponder? = nextResponder
        while let current = responder {
            if let scroll = current as? NSScrollView {
                scroll.scrollWheel(with: event)
                return
            }
            responder = current.nextResponder
        }
        nextResponder?.scrollWheel(with: event)
    }

    /// 从父视图向上找第一个 NSScrollView（跳过 WK 内部滚动层）。
    private func ancestorScrollView() -> NSScrollView? {
        var view: NSView? = superview
        while let current = view {
            if let scroll = current as? NSScrollView {
                return scroll
            }
            view = current.superview
        }
        return nil
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
