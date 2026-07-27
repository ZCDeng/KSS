import Foundation

/// SwiftPM 为 `Bundle.module` 生成的生产 fallback 会写入构建机绝对路径。
/// 已安装 App 必须优先读取 `Contents/Resources` 内随签名包交付的资源 bundle。
enum KSSResources {
    static let bundle: Bundle = resolveBundle(
        resourceRoot: Bundle.main.resourceURL,
        executableRoot: Bundle.main.executableURL?.deletingLastPathComponent(),
        // Do not eagerly evaluate Bundle.module here. In a signed app SwiftPM's
        // generated fallback may point at the build machine, even though the
        // resource bundle was packaged correctly beside the executable.
        fallback: { Bundle.module }
    )

    static func resolveBundle(
        resourceRoot: URL?,
        executableRoot: URL?,
        fallback: () -> Bundle
    ) -> Bundle {
        let name = "KSSDesktop_KSSDesktop.bundle"
        for root in [resourceRoot, executableRoot].compactMap({ $0 }) {
            if let bundle = Bundle(url: root.appending(path: name)) {
                return bundle
            }
        }
        return fallback()
    }
}
