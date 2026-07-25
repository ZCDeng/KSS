import XCTest
@testable import KSSDesktop

final class KSSResourcesTests: XCTestCase {

    func testInstalledResourceBundleWinsOverBuildFallback() throws {
        let base = FileManager.default.temporaryDirectory
            .appending(path: "kss-resources-\(UUID().uuidString)")
        let resources = base.appending(path: "KSSDesktop.app/Contents/Resources")
        let embedded = resources.appending(path: "KSSDesktop_KSSDesktop.bundle")
        try FileManager.default.createDirectory(at: embedded, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: base) }

        let plist: [String: Any] = [
            "CFBundleIdentifier": "com.zcdeng.KSSDesktop.resources.test",
            "CFBundleName": "KSSDesktopResources",
            "CFBundlePackageType": "BNDL",
        ]
        let plistData = try PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )
        try plistData.write(to: embedded.appending(path: "Info.plist"))

        let resolved = KSSResources.resolveBundle(
            resourceRoot: resources,
            executableRoot: nil,
            fallback: Bundle.module
        )

        XCTAssertEqual(resolved.bundleURL.standardizedFileURL, embedded.standardizedFileURL)
        XCTAssertFalse(resolved.bundleURL.path.contains("/.build/"))
    }

    func testMissingEmbeddedBundleUsesFallback() {
        let resolved = KSSResources.resolveBundle(
            resourceRoot: FileManager.default.temporaryDirectory
                .appending(path: "missing-\(UUID().uuidString)"),
            executableRoot: nil,
            fallback: Bundle.module
        )
        XCTAssertEqual(resolved.bundleURL, Bundle.module.bundleURL)
    }
}
