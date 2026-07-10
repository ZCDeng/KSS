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

    /// 注册打包进 app 的字体（SwiftPM 资源在 Bundle.module，需运行时注册才能被 Font.custom 使用）。
    private static func registerBundledFonts() {
        for name in ["HarmonyOS_Sans_SC_Bold", "chirp-regular-web", "chirp-medium-web", "chirp-bold-web", "chirp-heavy-web", "仓耳今楷02-W02"] {
            guard let url = Bundle.module.url(forResource: name, withExtension: "ttf") else {
                NSLog("[KSS] 字体缺失，未注册: \(name).ttf")
                continue
            }
            var error: Unmanaged<CFError>?
            if !CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) {
                NSLog("[KSS] 字体注册失败 \(name): \(String(describing: error?.takeRetainedValue()))")
            }
        }
        // CTFontManagerRegisterFontsForURL 同步返回成功，但字体真正能被 PostScript 名查询到是异步生效的
        // （已实测确认的 macOS 行为：注册调用刚返回时按名查找会静默落到 Helvetica，约 1s 后才稳定可查）。
        // 不等它就绪的话，App 首帧渲染的标题/侧边栏会永久卡在 Helvetica——SwiftUI 算出的 Font 值不会
        // 因为字体后来可用了就自动重算。这里在显示窗口前同步轮询到全部就绪，上限 500ms 防止极端情况卡启动。
        waitForFontsAvailable(["Chirp-Regular", "Chirp-Medium", "Chirp-Bold", "Chirp-Heavy"], timeoutMs: 500)
    }

    private static func waitForFontsAvailable(_ postScriptNames: [String], timeoutMs: Int) {
        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000)
        var pending = Set(postScriptNames)
        while !pending.isEmpty && Date() < deadline {
            for name in pending {
                let descriptor = CTFontDescriptorCreateWithNameAndSize(name as CFString, 12)
                let font = CTFontCreateWithFontDescriptor(descriptor, 12, nil)
                if (CTFontCopyPostScriptName(font) as String) == name {
                    pending.remove(name)
                }
            }
            if !pending.isEmpty { usleep(10_000) }
        }
        if !pending.isEmpty {
            NSLog("[KSS] 字体就绪等待超时，未确认可查询: \(pending.sorted().joined(separator: ", "))")
        }
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
