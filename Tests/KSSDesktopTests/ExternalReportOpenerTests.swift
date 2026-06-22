import XCTest
@testable import KSSDesktop

/// `ExternalReportOpener.resolveReportURL` 的路径安全边界。向量刻意镜像 Python
/// `_resolve_markdown_path`（scripts/kss_app_bridge.py:375-388）的拒绝集，外加 Swift 侧
/// 加固（符号链接逃逸、非常规文件）。两侧若漂移，本文件应红。
/// 注：本机 CLT 无 XCTest，需完整 Xcode `swift test`。
final class ExternalReportOpenerTests: XCTestCase {

    private var root: URL!

    override func setUpWithError() throws {
        // 真实临时 stateRoot；自身可能含 /private 符号链接，故解析后再比较。
        root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appending(path: "kss-markedit-test-\(UUID().uuidString)", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        if let root { try? FileManager.default.removeItem(at: root) }
    }

    private func write(_ relative: String, _ body: String = "# report") throws -> URL {
        let url = root.appending(path: relative)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try body.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private func assertRejects(_ relative: String,
                               _ message: String,
                               file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try ExternalReportOpener.resolveReportURL(relativePath: relative,
                                                                       under: root),
                             message, file: file, line: line) { err in
            guard case ExternalReportOpener.OpenError.pathResolution = err else {
                return XCTFail("期望 pathResolution，得到 \(err)", file: file, line: line)
            }
        }
    }

    // MARK: 镜像 Python 拒绝集

    func testRelativeMarkdownUnderRootSucceeds() throws {
        let created = try write("daily_review/2026-06-23.md")
        let resolved = try ExternalReportOpener.resolveReportURL(
            relativePath: "daily_review/2026-06-23.md", under: root)
        XCTAssertEqual(resolved.resolvingSymlinksInPath().standardizedFileURL,
                       created.resolvingSymlinksInPath().standardizedFileURL)
    }

    func testAbsolutePathFails() {
        assertRejects("/etc/passwd", "绝对路径应拒绝")
    }

    func testDotDotEscapeFails() throws {
        // 在 root 外放一个真实 .md，再用 ../ 试图够到它。
        let outside = root.deletingLastPathComponent()
            .appending(path: "escapee-\(UUID().uuidString).md")
        try "# x".write(to: outside, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: outside) }
        assertRejects("../\(outside.lastPathComponent)", "../ 逃逸应拒绝")
    }

    func testNonMarkdownFails() throws {
        _ = try write("notes.txt")
        assertRejects("notes.txt", "非 .md 应拒绝")
    }

    func testMissingFileFails() {
        assertRejects("daily_review/nope.md", "缺失文件应拒绝")
    }

    // MARK: Swift 侧加固

    func testSymlinkEscapeFails() throws {
        // root 外的真实 .md 目标 + root 内指向它的 .md 符号链接 → 规范化后逃逸 → 拒绝。
        let outsideDir = root.deletingLastPathComponent()
            .appending(path: "outside-\(UUID().uuidString)", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: outsideDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: outsideDir) }
        let target = outsideDir.appending(path: "secret.md")
        try "# secret".write(to: target, atomically: true, encoding: .utf8)

        let link = root.appending(path: "link.md")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)

        assertRejects("link.md", "指向 root 外的 .md 符号链接应拒绝")
    }

    func testDirectoryNamedMarkdownFails() throws {
        let dir = root.appending(path: "folder.md", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        assertRejects("folder.md", "名为 foo.md 的目录应拒绝（非常规文件）")
    }

    func testEmptyPathFails() {
        assertRejects("   ", "空/空白路径应拒绝")
    }

    func testUppercaseSuffixAccepted() throws {
        // 后缀小写化比较：.MD 应通过后缀闸（对应 Python `suffix.lower()`）。
        let created = try write("REPORT.MD")
        let resolved = try ExternalReportOpener.resolveReportURL(
            relativePath: "REPORT.MD", under: root)
        XCTAssertEqual(resolved.lastPathComponent, created.lastPathComponent)
    }

    // MARK: open() 的 MarkEdit 缺失分支（注入 locate，不真启动）

    func testOpenReportsMarkEditNotFound() throws {
        _ = try write("daily_review/2026-06-23.md")
        let exp = expectation(description: "completion")
        var captured: ExternalReportOpener.OpenError?
        ExternalReportOpener.open(relativePath: "daily_review/2026-06-23.md",
                                  under: root,
                                  locate: { nil }) { err in
            captured = err; exp.fulfill()
        }
        wait(for: [exp], timeout: 2)
        XCTAssertEqual(captured, .markEditNotFound)
    }

    func testOpenSurfacesResolutionErrorBeforeLocatingApp() {
        let exp = expectation(description: "completion")
        var captured: ExternalReportOpener.OpenError?
        var locateCalled = false
        ExternalReportOpener.open(relativePath: "/etc/passwd",
                                  under: root,
                                  locate: { locateCalled = true; return nil }) { err in
            captured = err; exp.fulfill()
        }
        wait(for: [exp], timeout: 2)
        XCTAssertFalse(locateCalled, "路径校验失败时不应再去定位 MarkEdit")
        guard case .pathResolution = captured else {
            return XCTFail("期望 pathResolution，得到 \(String(describing: captured))")
        }
    }
}
