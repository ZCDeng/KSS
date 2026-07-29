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
        XCTAssertTrue(html.contains("--brand: #1B365D"))
        XCTAssertTrue(html.contains("ChironGoRoundTC-Regular.ttf"))
        XCTAssertTrue(html.contains("Chiron GoRound TC"))
        XCTAssertTrue(html.contains("window.kssSetTheme"))
        XCTAssertTrue(html.contains("window.kssSetMarkdown"))
        XCTAssertTrue(html.contains("window.kssSetHTML"))
        XCTAssertFalse(html.contains("background: #f5f4ed"))
        XCTAssertFalse(html.lowercased().contains("https://"))
        XCTAssertFalse(html.lowercased().contains("gstatic.com"))
        XCTAssertFalse(html.lowercased().contains("fonts.googleapis.com"))
    }

    func testChironGoRoundTCFontResourceIsBundled() throws {
        for name in ["ChironGoRoundTC-Regular", "ChironGoRoundTC-Medium", "ChironGoRoundTC-Bold"] {
            let url = try XCTUnwrap(
                Bundle.module.url(forResource: name, withExtension: "ttf"),
                "\(name).ttf 必须打进 Bundle.module"
            )
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            let size = try XCTUnwrap(attrs[.size] as? NSNumber)
            XCTAssertGreaterThan(size.intValue, 1_000_000, name)
        }
    }

    func testEditorialContentThemeUsesChironGoRoundTC() {
        let xcom = ThemeCatalog.palette(for: .xcom, appearance: .light).webPayload
        let editorial = xcom.asEditorialContentTheme()
        XCTAssertEqual(editorial.colors, xcom.colors)
        XCTAssertTrue(editorial.typography.serif.contains("Chiron GoRound TC"))
        XCTAssertTrue(editorial.typography.sans.contains("Chiron GoRound TC"))
        XCTAssertFalse(editorial.typography.serif.contains("Chirp"))
        XCTAssertFalse(editorial.typography.serif.contains("LXGW"))
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
}
