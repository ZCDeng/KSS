import AppKit
import CoreText
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.registerBundledFonts()
        NSApp.setActivationPolicy(.regular)
        if let icon = Bundle.main.url(forResource: "AppIcon", withExtension: "icns").flatMap({ NSImage(contentsOf: $0) }) {
            NSApp.applicationIconImage = icon
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    /// 注册打包进 app 的字体（SwiftPM 资源 bundle 需运行时注册才能被 Font.custom 使用）。
    private static func registerBundledFonts() {
        for name in [
            "HarmonyOS_Sans_SC_Regular", "HarmonyOS_Sans_SC_Medium", "HarmonyOS_Sans_SC_Bold", "HarmonyOS_Sans_SC_Black",
            "chirp-regular-web", "chirp-medium-web", "chirp-bold-web", "chirp-heavy-web",
            "TsangerJinKai02-W02",
            "ChironGoRoundTC-Regular", "ChironGoRoundTC-Medium", "ChironGoRoundTC-Bold",
        ] {
            guard let url = KSSResources.bundle.url(forResource: name, withExtension: "ttf") else {
                NSLog("[KSS] 字体缺失，未注册: \(name).ttf")
                continue
            }
            var error: Unmanaged<CFError>?
            if !CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) {
                NSLog("[KSS] 字体注册失败 \(name): \(String(describing: error?.takeRetainedValue()))")
            }
        }
        // CTFontManagerRegisterFontsForURL 同步返回成功，但字体真正"可被按名解析"和"可被当作级联
        // fallback 参与字形替换"是两件不同的事，且后者就绪得更晚（实测：单纯按名解析约几百毫秒内
        // 就绪，但级联替换要到近 2s 后才稳定生效——期间同一段代码反复跑,前面全部落到 Helvetica/
        // PingFang SC 兜底，之后才正确解析到目标字体）。不等到级联本身就绪的话，App 首帧渲染的标题/
        // 侧边栏会永久卡在错误字体——SwiftUI 算出的 Font 值不会因为字体后来可用了就自动重算。这里在
        // 显示窗口前同步轮询「实际级联行为」本身（而不是单纯查字体名是否存在），上限 3s 防止极端情况卡启动。
        // HarmonyOS Sans SC 的 Regular 档 PostScript 名不带粗细后缀（就是 "HarmonyOS_Sans_SC" 本身），
        // 其余档位为 "HarmonyOS_Sans_SC_<Medium|Bold|Black>"——与 KSSFont.themed() 的取名规则一致。
        waitForCascadeReady(
            baseName: "Chirp-Regular",
            cjkName: "HarmonyOS_Sans_SC",
            testCharacter: "字",
            timeoutMs: 3000
        )
    }

    /// 轮询直到「Chirp 级联到 HarmonyOS Sans SC」这个具体行为在当前进程里真正生效——
    /// 用一个真实汉字测试解析结果，而不是只检查字体名是否能被找到（两者就绪时机不同）。
    private static func waitForCascadeReady(baseName: String, cjkName: String, testCharacter: Character, timeoutMs: Int) {
        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000)
        let size: CGFloat = 16
        let testChar = String(testCharacter) as CFString
        let range = CFRangeMake(0, CFStringGetLength(testChar))
        while Date() < deadline {
            let baseDescriptor = CTFontDescriptorCreateWithNameAndSize(baseName as CFString, size)
            let cjkDescriptor = CTFontDescriptorCreateWithNameAndSize(cjkName as CFString, size)
            let cascaded = CTFontDescriptorCreateCopyWithAttributes(
                baseDescriptor, [kCTFontCascadeListAttribute: [cjkDescriptor]] as CFDictionary
            )
            let ctFont = CTFontCreateWithFontDescriptor(cascaded, size, nil)
            let resolvedFont = CTFontCreateForString(ctFont, testChar, range)
            if (CTFontCopyPostScriptName(resolvedFont) as String) == cjkName {
                return
            }
            usleep(20_000)
        }
        NSLog("[KSS] 中文字体级联就绪等待超时（\(timeoutMs)ms），可能仍会回退到系统字体")
    }
}

@main
struct KSSDesktopApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = KSSStore()
    @StateObject private var theme = ThemeController()

    var body: some Scene {
        WindowGroup("KSS Desktop", id: "main") {
            LaunchGateView {
                ContentView()
            }
                .environmentObject(store)
                .environmentObject(theme)
                .environment(\.kssTheme, theme.tokens)
                .environment(\.kssWebTheme, theme.webPayload)
                .preferredColorScheme(theme.colorScheme)
                .tint(theme.tokens.accent)
                .task {
                    await store.loadSnapshot()
                    await store.runSelfCheck()
                }
        }
        .commands {
            CommandMenu("KSS") {
                Button("Refresh") {
                    Task { await store.loadSnapshot() }
                }
                .keyboardShortcut("r", modifiers: [.command])
            }
        }
    }
}
