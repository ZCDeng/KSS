import SwiftUI
import WebKit

/// Shows the project's interactive architecture diagram (the self-contained
/// docs/kss_architecture_interactive.html, bundled into the app) in a WKWebView.
struct ArchitectureView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                PageTitle("架构全景", subtitle: "KSS 系统架构 · 交互版（点击节点高亮数据流）")
                Spacer()
                if let url = URL(string: "https://github.com/ZCDeng/KSS") {
                    Link(destination: url) {
                        Label("GitHub", systemImage: "arrow.up.forward.square")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .tint(KSSTheme.accent)
                }
            }
            .padding(16)

            LocalHTMLView(resource: "architecture")
                .background(Color.white)
        }
        .background(KSSTheme.canvas)
    }
}

/// Loads a bundled, self-contained HTML resource (no data injection) into a WKWebView.
struct LocalHTMLView: NSViewRepresentable {
    var resource: String

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        if let html = Bundle.module.url(forResource: resource, withExtension: "html") {
            webView.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}
}
