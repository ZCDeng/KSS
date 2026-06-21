import SwiftUI
import WebKit

/// 专用启动 WebView：隔离的 `WKWebViewConfiguration` + `WKUserContentController`，
/// 唯一命名 handler `kssLaunch`。不复用数据 WebView 的同步 coordinator，
/// 把「启动门禁」与「内容同步」职责分开。
///
/// - 仅加载本地 bundle 文件；外部 URL / popup / 非预期导航一律 cancel。
/// - document-start 注入当前主题 payload，避免首帧闪错色。
/// - boot watchdog：2.5s 内未收到 `ready` → `.failure(.watchdog)`，保证用户能看到原生按钮。
struct KSSLaunchWebView: NSViewRepresentable {
    let payload: KSSWebThemePayload
    let canvas: NSColor
    let onEvent: (LaunchEvent) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onEvent: onEvent) }

    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        // document-start 注入主题（全局变量 + 立即设根背景，防首帧闪色）。
        let themeJSON = payload.json
        let inject = """
        window.__kssLaunchTheme = \(themeJSON);
        try {
          var __c = window.__kssLaunchTheme && window.__kssLaunchTheme.colors;
          if (__c && __c.bg && document.documentElement) {
            document.documentElement.style.background = __c.bg;
          }
        } catch (e) {}
        """
        controller.addUserScript(WKUserScript(source: inject,
                                              injectionTime: .atDocumentStart,
                                              forMainFrameOnly: true))
        controller.add(WeakScriptMessageHandler(context.coordinator), name: "kssLaunch")

        let config = WKWebViewConfiguration()
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.underPageBackgroundColor = canvas // 透明区域露出 canvas 色，消除白闪
        context.coordinator.attach(webView)

        if let url = Bundle.module.url(forResource: "launch", withExtension: "html",
                                       subdirectory: "Launch")
            ?? Bundle.module.url(forResource: "launch", withExtension: "html") {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
            context.coordinator.startWatchdog()
        } else {
            // 资源缺失：立即 fallback。
            context.coordinator.emit(.failure(.resource))
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        webView.underPageBackgroundColor = canvas
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.teardown(webView)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        private let onEvent: (LaunchEvent) -> Void
        private weak var webView: WKWebView?
        private var watchdog: Timer?
        private var ready = false
        private var finished = false

        init(onEvent: @escaping (LaunchEvent) -> Void) {
            self.onEvent = onEvent
        }

        func attach(_ webView: WKWebView) { self.webView = webView }

        /// 主线程派发事件，进入 reducer。
        func emit(_ event: LaunchEvent) {
            if case .webReady = event { ready = true }
            if case .reducedMotion = event { ready = true }
            if Thread.isMainThread { onEvent(event) }
            else { DispatchQueue.main.async { self.onEvent(event) } }
        }

        func startWatchdog() {
            watchdog?.invalidate()
            watchdog = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: false) { [weak self] _ in
                guard let self, !self.ready else { return }
                self.emit(.failure(.watchdog))
            }
        }

        func teardown(_ webView: WKWebView) {
            watchdog?.invalidate(); watchdog = nil
            webView.navigationDelegate = nil
            webView.configuration.userContentController.removeAllUserScripts()
            webView.configuration.userContentController.removeScriptMessageHandler(forName: "kssLaunch")
        }

        // MARK: WKScriptMessageHandler
        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "kssLaunch",
                  let event = LaunchMessageRouter.event(from: message.body) else { return }
            emit(event)
        }

        // MARK: WKNavigationDelegate — 只允许本地 bundle 文件
        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if finished {
                // 首帧加载后任何后续导航都拒绝（页面是纯本地静态）。
                decisionHandler(.cancel); return
            }
            if navigationAction.request.url?.isFileURL == true {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            finished = true
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            emit(.failure(.navigation))
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            emit(.failure(.navigation))
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            emit(.failure(.script))
        }
    }
}

/// 弱引用代理：避免 `WKUserContentController` 强持有 handler 造成 coordinator 泄漏。
private final class WeakScriptMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}
