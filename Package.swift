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
                .copy("Resources/HarmonyOS_Sans_SC_Bold.ttf"),
                .copy("Resources/chirp-regular-web.ttf"),
                .copy("Resources/chirp-medium-web.ttf"),
                .copy("Resources/chirp-bold-web.ttf"),
                .copy("Resources/chirp-heavy-web.ttf"),
                .copy("Resources/chirp-regular-web.woff"),
                .copy("Resources/chirp-medium-web.woff"),
                .copy("Resources/chirp-bold-web.woff"),
                .copy("Resources/chirp-heavy-web.woff"),
                .copy("Resources/仓耳今楷02-W02.ttf")
            ]
        ),
        .testTarget(
            name: "KSSDesktopTests",
            dependencies: ["KSSDesktop"],
            path: "Tests/KSSDesktopTests"
        )
    ]
)
