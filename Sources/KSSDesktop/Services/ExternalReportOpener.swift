import AppKit
import Foundation

/// 外部编辑桥：把当前选中的 `.md` 报告交给 MarkEdit.app 打开（不嵌编辑器、不入 Python 桥）。
///
/// 路径校验是**安全边界**，刻意在 Swift 侧复刻 Python 的同款模型；二者须保持一致，
/// 由 `Tests/KSSDesktopTests/ExternalReportOpenerTests` 的 parity 向量守护防漂移。
// source-of-truth: scripts/kss_app_bridge.py:375-388 (_resolve_markdown_path)
enum ExternalReportOpener {

    enum OpenError: LocalizedError, Equatable {
        /// 路径校验失败（绝对 / 含 .. / 逃逸 / 非 .md / 非常规文件 / 缺失）。
        case pathResolution(String)
        /// 未找到 MarkEdit.app。
        case markEditNotFound
        /// 路径校验通过、定位到 MarkEdit，但 NSWorkspace 启动失败（沙箱拒绝 / 应用崩溃等）。
        case appLaunch(String)

        var errorDescription: String? {
            switch self {
            case .pathResolution(let detail):
                return "无法打开报告：\(detail)"
            case .markEditNotFound:
                return "MarkEdit 未安装，请安装后重试。"
            case .appLaunch(let detail):
                return "MarkEdit 打开失败：\(detail)"
            }
        }
    }

    /// MarkEdit 候选 bundle id。实装时以 `osascript -e 'id of app "MarkEdit"'` 核实，
    /// 必要时补充新渠道；多候选即「跨渠道兜底」。
    static let markEditBundleIDs = ["app.cyan.markedit", "com.markedit.MarkEdit"]

    /// 纯路径解析（无 NSWorkspace 耦合，可对真实临时目录单测）。顺序固定，复刻 Python：
    /// 1 绝对 → 2 拼接 + 符号链接规范化 → 3 包含 → 4 后缀 → 5 常规文件 → 6 存在。
    /// 成功返回规范化后的绝对 file URL；失败抛 `.pathResolution`。
    static func resolveReportURL(relativePath: String, under stateRoot: URL) throws -> URL {
        let trimmed = relativePath.trimmingCharacters(in: .whitespacesAndNewlines)
        // 1. 拒绝空串与绝对路径（对应 Python `raw.is_absolute()`）。
        guard !trimmed.isEmpty else {
            throw OpenError.pathResolution("报告路径为空")
        }
        guard !(trimmed as NSString).isAbsolutePath else {
            throw OpenError.pathResolution("报告路径必须相对于状态根")
        }
        // 1b. 拒绝任何 `..` 路径分量。报告路径来自 `_collect_reports` 的 `relative_to(STATE_ROOT)`
        //     干净相对路径，绝不含 `..`。显式拒绝消除一处相对 Python 物理 `.resolve()` 的解析漂移：
        //     `standardizedFileURL` 对 `..` 做**词法**折叠（如 `symlinkDir/../x` → `x`），而 Python
        //     物理跟随符号链接后再上跳，二者结果可不同。拒绝 `..` 让两侧在此类输入上都收敛到 reject。
        guard !(trimmed as NSString).pathComponents.contains("..") else {
            throw OpenError.pathResolution("报告路径不得包含 .. 分量")
        }
        // 2. 在 stateRoot 下拼接，对两侧做符号链接规范化（对应 Python `.resolve()`，跟随符号链接）。
        let base = stateRoot.resolvingSymlinksInPath().standardizedFileURL
        let candidate = base.appending(path: trimmed)
            .resolvingSymlinksInPath().standardizedFileURL
        // 3. 拒绝规范化后逃逸 stateRoot 的路径（按规范 URL 的路径分量前缀判定，非裸字符串）。
        guard isContained(candidate, in: base) else {
            throw OpenError.pathResolution("报告路径逃逸了状态根")
        }
        // 4. 拒绝非 .md（小写后缀比较，对应 Python `suffix.lower()`）。
        guard candidate.pathExtension.lowercased() == "md" else {
            throw OpenError.pathResolution("报告路径必须指向 markdown 文件")
        }
        // 5/6. 拒绝缺失文件与非常规文件（目录 / FIFO / 设备等）。Python 仅查存在；
        //      交给外部 app 前此处加固为「必须是常规文件」。
        let values = try? candidate.resourceValues(forKeys: [.isRegularFileKey])
        guard let isRegular = values?.isRegularFile else {
            throw OpenError.pathResolution("报告不存在：\(relativePath)")
        }
        guard isRegular else {
            throw OpenError.pathResolution("报告路径不是常规文件")
        }
        return candidate
    }

    /// 规范 URL 的路径分量包含判定：candidate 的路径分量须以 base 的为前缀
    /// （避免 `…/state-evil` 误判为在 `…/state` 内的裸字符串前缀陷阱）。
    private static func isContained(_ candidate: URL, in base: URL) -> Bool {
        let baseComps = base.pathComponents
        let candComps = candidate.pathComponents
        guard candComps.count >= baseComps.count else { return false }
        return Array(candComps.prefix(baseComps.count)) == baseComps
    }

    /// 定位 MarkEdit.app（按候选 bundle id），无则返回 nil。
    static func locateMarkEdit() -> URL? {
        for id in markEditBundleIDs {
            if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) {
                return url
            }
        }
        return nil
    }

    /// 解析路径 → 定位 MarkEdit → 打开。`completion` 可能在主线程外触发，调用方自行 hop。
    /// `completion(nil)` = 成功；非 nil = `OpenError`。`locate` 可注入便于测试。
    static func open(relativePath: String,
                     under stateRoot: URL,
                     locate: () -> URL? = locateMarkEdit,
                     completion: @escaping (OpenError?) -> Void) {
        let fileURL: URL
        do {
            fileURL = try resolveReportURL(relativePath: relativePath, under: stateRoot)
        } catch let err as OpenError {
            completion(err); return
        } catch {
            completion(.pathResolution(error.localizedDescription)); return
        }
        guard let markEdit = locate() else {
            completion(.markEditNotFound); return
        }
        let config = NSWorkspace.OpenConfiguration()
        NSWorkspace.shared.open([fileURL], withApplicationAt: markEdit,
                                configuration: config) { _, error in
            completion(error.map { .appLaunch($0.localizedDescription) })
        }
    }
}
