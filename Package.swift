// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "KSSDesktop",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "KSSDesktop", targets: ["KSSDesktop"]),
        // A deliberately narrow, signed scheduler entrypoint. It owns the
        // Keychain-to-broker hop for long research jobs without giving Python
        // a credential-bearing environment.
        .executable(name: "KSSResearchSchedulerHelper", targets: ["KSSResearchSchedulerHelper"])
    ],
    targets: [
        .executableTarget(
            name: "KSSDesktop",
            path: "Sources/KSSDesktop",
            resources: [
                .copy("Resources/lightweight-charts.standalone.production.js"),
                .copy("Resources/chart.html"),
                .copy("Resources/marked.min.js"),
                .copy("Resources/markdown.html"),
                .copy("Resources/architecture.html"),
                .copy("Resources/Launch"),
                .copy("Resources/Heatmap"),
                .copy("Resources/logo.png"),
                .copy("Resources/kmark.png"),
                .copy("Resources/DashboardSparkleIcon.png"),
                .copy("Resources/wordmark.png"),
                .copy("Resources/octocat.png"),
                .copy("Resources/Icons"),
                .copy("Resources/HarmonyOS_Sans_SC_Regular.ttf"),
                .copy("Resources/HarmonyOS_Sans_SC_Medium.ttf"),
                .copy("Resources/HarmonyOS_Sans_SC_Bold.ttf"),
                .copy("Resources/HarmonyOS_Sans_SC_Black.ttf"),
                .copy("Resources/chirp-regular-web.ttf"),
                .copy("Resources/chirp-medium-web.ttf"),
                .copy("Resources/chirp-bold-web.ttf"),
                .copy("Resources/chirp-heavy-web.ttf"),
                .copy("Resources/chirp-regular-web.woff"),
                .copy("Resources/chirp-medium-web.woff"),
                .copy("Resources/chirp-bold-web.woff"),
                .copy("Resources/chirp-heavy-web.woff"),
                .copy("Resources/TsangerJinKai02-W02.ttf"),
                .copy("Resources/ChironGoRoundTC-Regular.ttf"),
                .copy("Resources/ChironGoRoundTC-Medium.ttf"),
                .copy("Resources/ChironGoRoundTC-Bold.ttf")
            ]
        ),
        .executableTarget(
            name: "KSSResearchSchedulerHelper",
            path: "Sources/KSSResearchScheduler"
        ),
        .testTarget(
            name: "KSSDesktopTests",
            dependencies: ["KSSDesktop"],
            path: "Tests/KSSDesktopTests"
        )
    ]
)
