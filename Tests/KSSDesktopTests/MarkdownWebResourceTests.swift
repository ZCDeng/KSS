import XCTest
@testable import KSSDesktop

final class MarkdownWebResourceTests: XCTestCase {
    private func markdownHTML() throws -> String {
        let url = try XCTUnwrap(
            Bundle.module.url(forResource: "markdown", withExtension: "html"),
            "markdown.html 必须打进 Bundle.module"
        )
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func decodeArtifact(_ json: String) throws -> ResearchArtifact {
        let data = try XCTUnwrap(json.data(using: .utf8))
        return try JSONDecoder().decode(ResearchArtifact.self, from: data)
    }

    func testMarkdownShellBundlesKamiReaderAndBridgeAPIs() throws {
        let html = try markdownHTML()
        XCTAssertTrue(html.contains("data-reader=\"kami\""))
        XCTAssertTrue(html.contains("html[data-reader=\"kami\"]"))
        XCTAssertTrue(html.contains("return \"kami\""))
        XCTAssertTrue(html.contains("demo-kami-print"))
        XCTAssertTrue(html.contains("--brand: #1B365D"))
        XCTAssertTrue(html.contains("setProperty(\"--brand\", \"#1B365D\")"))
        XCTAssertTrue(html.contains("LXGWWenKaiMonoTC-Regular.ttf"))
        XCTAssertTrue(html.contains("LXGW WenKai Mono TC"))
        XCTAssertTrue(html.contains("window.kssSetTheme"))
        XCTAssertTrue(html.contains("window.kssSetMarkdown"))
        XCTAssertTrue(html.contains("window.kssSetHTML"))
        XCTAssertTrue(html.contains("kssMarkdown"))
        XCTAssertTrue(html.contains("readerForPayload"))
        XCTAssertFalse(html.contains("background: #f5f4ed"))
        // 纯离线：不得外链加载字体
        XCTAssertFalse(html.lowercased().contains("https://"))
        XCTAssertFalse(html.lowercased().contains("http://"))
        XCTAssertFalse(html.lowercased().contains("gstatic.com"))
        XCTAssertFalse(html.lowercased().contains("fonts.googleapis.com"))
    }

    func testLXGWWenKaiMonoTCFontResourceIsBundled() throws {
        for name in ["LXGWWenKaiMonoTC-Regular", "LXGWWenKaiMonoTC-Medium"] {
            let url = try XCTUnwrap(
                Bundle.module.url(forResource: name, withExtension: "ttf"),
                "\(name).ttf 必须打进 Bundle.module"
            )
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            let size = try XCTUnwrap(attrs[.size] as? NSNumber)
            XCTAssertGreaterThan(size.intValue, 1_000_000, name)
        }
    }

    func testEditorialContentThemeUsesLXGWWenKaiMonoTC() {
        let xcom = ThemeCatalog.palette(for: .xcom, appearance: .light).webPayload
        XCTAssertEqual(xcom.id, "xcom")
        let editorial = xcom.asEditorialContentTheme()
        XCTAssertEqual(editorial.id, "xcom")
        XCTAssertEqual(editorial.mode, xcom.mode)
        XCTAssertEqual(editorial.colors, xcom.colors)
        XCTAssertTrue(editorial.typography.serif.contains("LXGW WenKai Mono TC"))
        XCTAssertTrue(editorial.typography.sans.contains("LXGW WenKai Mono TC"))
        XCTAssertFalse(editorial.typography.serif.contains("Chirp"))
        XCTAssertFalse(editorial.typography.sans.contains("Chirp"))
    }

    func testArtifactPreviewPrefersHTMLBodyFragmentAndMarkdown() throws {
        let htmlArtifact = try decodeArtifact("""
        {
          "artifact_id": "a1",
          "kind": "report",
          "logical_name": "weekly.html",
          "media_type": "text/html",
          "relative_path": "out/weekly.html",
          "content": "<!doctype html><html><body><h1>周报</h1><p>正文</p></body></html>"
        }
        """)
        let htmlSpec = ResearchArtifactPreviewSupport.renderSpec(
            artifact: htmlArtifact,
            loadedContent: htmlArtifact.content
        )
        XCTAssertEqual(htmlSpec.kind, .htmlFragment)
        XCTAssertTrue(htmlSpec.text.contains("<h1>周报</h1>"))
        XCTAssertFalse(htmlSpec.text.lowercased().contains("<html"))

        let mdArtifact = try decodeArtifact("""
        {
          "artifact_id": "a2",
          "kind": "note",
          "logical_name": "note.md",
          "media_type": "text/markdown",
          "relative_path": "out/note.md",
          "content": "# 标题\\n\\n段落"
        }
        """)
        let mdSpec = ResearchArtifactPreviewSupport.renderSpec(
            artifact: mdArtifact,
            loadedContent: mdArtifact.content
        )
        XCTAssertEqual(mdSpec.kind, .markdown)
        XCTAssertEqual(mdSpec.text, "# 标题\n\n段落")
    }

    func testHtmlBodyFragmentFallsBackForBareSnippet() {
        let snippet = "<section><p>片段</p></section>"
        XCTAssertEqual(ResearchArtifactPreviewSupport.htmlBodyFragment(snippet), snippet)
    }

    func testDemoInvestmentReportHTMLIsLoadableAsFragment() throws {
        let demoURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("storage/demo/kami_reader_smoke_report.html")
        let raw = try String(contentsOf: demoURL, encoding: .utf8)
        let fragment = ResearchArtifactPreviewSupport.htmlBodyFragment(raw)
        XCTAssertTrue(fragment.contains("科创板半导体") || fragment.contains("Kami"))
        XCTAssertFalse(fragment.lowercased().contains("<!doctype"))
        let artifact = try decodeArtifact("""
        {
          "artifact_id": "demo-weekly",
          "kind": "report_html",
          "logical_name": "kami_reader_smoke_report.html",
          "media_type": "text/html",
          "relative_path": "demo/kami_reader_smoke_report.html",
          "content": \(String(data: try JSONEncoder().encode(raw), encoding: .utf8) ?? "\"\"")
        }
        """)
        let spec = ResearchArtifactPreviewSupport.renderSpec(
            artifact: artifact,
            loadedContent: artifact.content
        )
        XCTAssertEqual(spec.kind, .htmlFragment)
        XCTAssertTrue(spec.text.contains("LXGW") || spec.text.contains("墨蓝") || spec.text.contains("半导体"))
    }
}
