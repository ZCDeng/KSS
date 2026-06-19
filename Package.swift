// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "KSSDesktop",
    platforms: [
        .macOS(.v13)
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
                .copy("Resources/logo.png")
            ]
        )
    ]
)
