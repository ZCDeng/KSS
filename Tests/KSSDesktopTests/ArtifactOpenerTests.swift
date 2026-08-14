import XCTest
@testable import KSSDesktop

final class ArtifactOpenerTests: XCTestCase {

    private var root: URL!

    override func setUpWithError() throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appending(path: "kss-pdf-open-\(UUID().uuidString)", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        if let root { try? FileManager.default.removeItem(at: root) }
    }

    private func writePDF(_ relative: String) throws -> URL {
        let url = root.appending(path: relative)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data("%PDF-1.4\n".utf8).write(to: url)
        return url
    }

    private func assertRejects(_ relative: String, _ message: String,
                               file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(
            try ArtifactOpener.resolveArtifactURL(relativePath: relative, under: root),
            message, file: file, line: line
        ) { err in
            guard case ArtifactOpener.OpenError.pathResolution = err else {
                return XCTFail("期望 pathResolution，得到 \(err)", file: file, line: line)
            }
        }
    }

    func testRelativePDFUnderRootSucceeds() throws {
        let created = try writePDF("equity_research/demo.pdf")
        let resolved = try ArtifactOpener.resolveArtifactURL(
            relativePath: "equity_research/demo.pdf", under: root)
        XCTAssertEqual(resolved.resolvingSymlinksInPath().standardizedFileURL,
                       created.resolvingSymlinksInPath().standardizedFileURL)
    }

    func testMarkdownRejectedByPDFOpener() throws {
        let url = root.appending(path: "note.md")
        try "# x".write(to: url, atomically: true, encoding: .utf8)
        assertRejects("note.md", "PDF 打开器不得接受 .md")
    }

    func testMarkEditOpenerStillRejectsPDF() throws {
        _ = try writePDF("report.pdf")
        XCTAssertThrowsError(
            try ExternalReportOpener.resolveReportURL(relativePath: "report.pdf", under: root)
        ) { err in
            guard case ExternalReportOpener.OpenError.pathResolution = err else {
                return XCTFail("MarkEdit 门应拒绝 .pdf，得到 \(err)")
            }
        }
    }

    func testAbsoluteAndDotDotRejected() {
        assertRejects("/etc/passwd.pdf", "绝对路径应拒绝")
        assertRejects("../escape.pdf", "../ 逃逸应拒绝")
    }

    func testOpenUsesInjectedDefaultAppAndDoesNotNeedMarkEdit() throws {
        _ = try writePDF("equity_research/demo.pdf")
        let exp = expectation(description: "completion")
        var opened: URL?
        var captured: ArtifactOpener.OpenError?
        ArtifactOpener.open(
            relativePath: "equity_research/demo.pdf",
            under: root,
            openFile: { url in opened = url }
        ) { err in
            captured = err
            exp.fulfill()
        }
        wait(for: [exp], timeout: 2)
        XCTAssertNil(captured)
        XCTAssertEqual(opened?.pathExtension.lowercased(), "pdf")
    }
}
