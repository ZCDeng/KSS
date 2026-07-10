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
