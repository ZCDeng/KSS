import XCTest
@testable import KSSDesktop

/// 启动资源契约：三资源可寻址、纯离线（无 CDN / 远端引用）、SVG 无障碍且无 <text>、
/// 主题注入 payload 可 JSON round-trip 且覆盖全部已支持系统 × 亮暗。
final class LaunchResourceTests: XCTestCase {

    private func launchURL(_ name: String, _ ext: String) -> URL? {
        Bundle.module.url(forResource: name, withExtension: ext, subdirectory: "Launch")
            ?? Bundle.module.url(forResource: name, withExtension: ext)
    }

    private func launchText(_ name: String, _ ext: String) throws -> String {
        let url = try XCTUnwrap(launchURL(name, ext), "缺资源 \(name).\(ext)")
        return try String(contentsOf: url, encoding: .utf8)
    }

    func testThreeLaunchResourcesResolvable() throws {
        for (n, e) in [("launch", "html"), ("launch-kss", "svg"), ("gsap", "min.js")] {
            // gsap.min.js 的扩展名解析：先试组合，再退回 "js"。
            let url = launchURL(n, e) ?? launchURL(n, "js")
            XCTAssertNotNil(url, "Bundle.module 应能解析 \(n).\(e)")
        }
    }

    func testGSAPIsPinnedCoreWithoutClubPlugins() throws {
        let url = try XCTUnwrap(launchURL("gsap", "min.js") ?? launchURL("gsap", "js"))
        let js = try String(contentsOf: url, encoding: .utf8)
        XCTAssertTrue(js.contains("GSAP 3.12.5"), "GSAP 须为锁定的 3.12.5")
        XCTAssertFalse(js.contains("MorphSVGPlugin"), "不得含 Club 插件 MorphSVGPlugin")
    }

    func testHTMLAndSVGAreFullyOffline() throws {
        for (n, e) in [("launch", "html"), ("launch-kss", "svg")] {
            let text = try launchText(n, e).lowercased()
            XCTAssertFalse(text.contains("https://"), "\(n).\(e) 不得含 https 远端引用")
            // http 仅允许 SVG 命名空间 URI（不发起网络）。
            let httpHits = text.components(separatedBy: "http://")
                .dropFirst()
                .filter { !$0.hasPrefix("www.w3.org/") }
            XCTAssertTrue(httpHits.isEmpty, "\(n).\(e) 只允许 w3.org 命名空间，发现其它 http 引用")
            for token in ["cdn", "@import", "unpkg", "jsdelivr", "googleapis", "//fonts."] {
                XCTAssertFalse(text.contains(token), "\(n).\(e) 不得含外部引用 token: \(token)")
            }
        }
    }

    func testSVGIsAccessibleAndHasNoTextElement() throws {
        let svg = try launchText("launch-kss", "svg")
        XCTAssertTrue(svg.contains("<title"), "SVG 须含 <title> 无障碍标签")
        XCTAssertTrue(svg.contains("<desc"), "SVG 须含 <desc> 无障碍描述")
        XCTAssertTrue(svg.contains("role=\"img\""))
        XCTAssertFalse(svg.contains("<text"), "禁止 <text>：字形须为预转换 path")
    }

    func testHTMLInlinesPathsAndUsesOnlyLocalScript() throws {
        let html = try launchText("launch", "html")
        XCTAssertTrue(html.contains("<path"), "字形/符号须内联为 path")
        XCTAssertFalse(html.contains("<text"), "HTML 内联 SVG 也不得用 <text>")
        XCTAssertTrue(html.contains("src=\"gsap.min.js\""), "仅引用本地相对 gsap.min.js")
        XCTAssertTrue(html.contains("kssLaunch"), "须经唯一 message handler 通信")
        XCTAssertTrue(html.contains("type=\"button\""), "进入须是真实 <button type=button>")
    }

    func testThemePayloadRoundTripsForAllSupportedThemes() throws {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let payload = ThemeCatalog.palette(for: system, appearance: appearance).webPayload
                let data = try XCTUnwrap(payload.json.data(using: .utf8))
                let decoded = try JSONDecoder().decode(KSSWebThemePayload.self, from: data)
                XCTAssertEqual(decoded, payload, "\(system).\(appearance) payload 须 JSON round-trip")
                // 启动页消费的键必须存在且非空。
                for key in ["bg", "ink", "muted", "accent", "onAccent"] {
                    let v = try XCTUnwrap(payload.colors[key], "\(system).\(appearance) 缺启动页键 \(key)")
                    XCTAssertFalse(v.isEmpty)
                }
            }
        }
    }
}
