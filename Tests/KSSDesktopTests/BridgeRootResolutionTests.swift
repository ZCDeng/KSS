import XCTest
@testable import KSSDesktop

/// 已安装 App 的代码与数据根契约：
/// 代码可来自 bundle Resources，但安装期 breadcrumb 指向的有效状态根必须继续生效。
final class BridgeRootResolutionTests: XCTestCase {

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appending(path: "kss-root-resolution-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testBundleModeKeepsValidBreadcrumbStateRoot() throws {
        let base = try temporaryDirectory()
        let bundleResources = base.appending(path: "KSSDesktop.app/Contents/Resources")
        let sharedState = base.appending(path: "shared-state")
        let appSupport = base.appending(path: "Application Support/KSS")
        try FileManager.default.createDirectory(at: bundleResources, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: sharedState, withIntermediateDirectories: true)

        let selected = BridgeClient.selectStateRoot(
            envState: nil,
            breadcrumbState: sharedState.path,
            isDevMode: false,
            projectRoot: bundleResources,
            appSupportRoot: appSupport
        )

        XCTAssertEqual(selected, sharedState.standardizedFileURL)
        XCTAssertNotEqual(selected, bundleResources, "bundle 代码根不得替代可变数据状态根")
        XCTAssertNotEqual(selected, appSupport, "有效 breadcrumb 不得被静默丢弃")
    }

    func testExplicitEnvironmentOverrideHasHighestPriority() throws {
        let base = try temporaryDirectory()
        let envState = base.appending(path: "explicit-state")
        let breadcrumbState = base.appending(path: "breadcrumb-state")
        try FileManager.default.createDirectory(at: breadcrumbState, withIntermediateDirectories: true)

        let selected = BridgeClient.selectStateRoot(
            envState: envState.path,
            breadcrumbState: breadcrumbState.path,
            isDevMode: false,
            projectRoot: base,
            appSupportRoot: base.appending(path: "fallback")
        )

        XCTAssertEqual(selected, envState.standardizedFileURL)
    }

    func testMissingOrRelativeBreadcrumbFallsBackSafely() throws {
        let base = try temporaryDirectory()
        let appSupport = base.appending(path: "Application Support/KSS")

        for invalid in ["relative/state", base.appending(path: "missing").path] {
            let selected = BridgeClient.selectStateRoot(
                envState: nil,
                breadcrumbState: invalid,
                isDevMode: false,
                projectRoot: base.appending(path: "bundle"),
                appSupportRoot: appSupport
            )
            XCTAssertEqual(selected, appSupport)
        }
    }

    func testDevModeWithoutOverridesUsesProjectRoot() throws {
        let base = try temporaryDirectory()
        let project = base.appending(path: "project")

        let selected = BridgeClient.selectStateRoot(
            envState: nil,
            breadcrumbState: nil,
            isDevMode: true,
            projectRoot: project,
            appSupportRoot: base.appending(path: "fallback")
        )

        XCTAssertEqual(selected, project)
    }

    func testBundleModeRejectsGitCheckoutBreadcrumb() throws {
        let base = try temporaryDirectory()
        let checkout = base.appending(path: "KSS")
        let appSupport = base.appending(path: "Application Support/KSS")
        try FileManager.default.createDirectory(at: checkout, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: checkout.appending(path: ".git").path, contents: Data())

        let selected = BridgeClient.selectStateRoot(
            envState: nil,
            breadcrumbState: checkout.path,
            isDevMode: false,
            projectRoot: base.appending(path: "KSSDesktop.app/Contents/Resources"),
            appSupportRoot: appSupport
        )

        XCTAssertEqual(selected, appSupport)
        XCTAssertNotEqual(selected, checkout, "安装版不得把 git 工作副本当 sidecar 状态根")
    }

    func testPackagedBreadcrumbKeepsCheckoutProjectRoot() throws {
        let base = try temporaryDirectory()
        let checkout = base.appending(path: "KSS")
        let resources = base.appending(path: "KSSDesktop.app/Contents/Resources")
        try FileManager.default.createDirectory(
            at: checkout.appending(path: "scripts"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        FileManager.default.createFile(
            atPath: checkout.appending(path: "scripts/kss_app_bridge.py").path, contents: Data())

        let preserved = BridgeClient.packagedBreadcrumbProjectRoot(
            codeRoot: resources,
            existingProjectRoot: checkout.path
        )
        XCTAssertEqual(preserved, checkout.standardizedFileURL)
    }

    func testPackagedBreadcrumbDoesNotPersistBundleProjectRoot() throws {
        let resources = URL(fileURLWithPath: "/Applications/KSSDesktop.app/Contents/Resources")
        XCTAssertNil(BridgeClient.packagedBreadcrumbProjectRoot(
            codeRoot: resources,
            existingProjectRoot: resources.path
        ))
        XCTAssertNil(BridgeClient.packagedBreadcrumbProjectRoot(
            codeRoot: resources,
            existingProjectRoot: nil
        ))
    }

    func testDevModeKeepsGitCheckoutBreadcrumb() throws {
        let base = try temporaryDirectory()
        let checkout = base.appending(path: "KSS")
        try FileManager.default.createDirectory(at: checkout, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: checkout.appending(path: ".git").path, contents: Data())

        let selected = BridgeClient.selectStateRoot(
            envState: nil,
            breadcrumbState: checkout.path,
            isDevMode: true,
            projectRoot: checkout,
            appSupportRoot: base.appending(path: "fallback")
        )

        XCTAssertEqual(selected, checkout.standardizedFileURL)
    }
}
