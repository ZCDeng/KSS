// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "KSSDesktop",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "KSSDesktop", targets: ["KSSDesktop"])
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
                .copy("Resources/logo.png"),
                .copy("Resources/kmark.png"),
                .copy("Resources/wordmark.png"),
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
                .copy("Resources/chirp-heavy-web.woff")
            ]
        ),
        .testTarget(
            name: "KSSDesktopTests",
            dependencies: ["KSSDesktop"],
            path: "Tests/KSSDesktopTests"
        )
    ]
)
