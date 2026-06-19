import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if let icon = Bundle.main.url(forResource: "AppIcon", withExtension: "icns").flatMap({ NSImage(contentsOf: $0) }) {
            NSApp.applicationIconImage = icon
        }
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct KSSDesktopApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = KSSStore()

    var body: some Scene {
        WindowGroup("KSS Desktop", id: "main") {
            ContentView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .tint(KSSTheme.accent)
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
