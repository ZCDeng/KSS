import AppKit
import Foundation

/// 覆盖产物打开器：接受会话内 `.pdf`，交给系统默认应用（Preview 等）。
/// 与 `ExternalReportOpener` 的 MarkEdit / `.md` 门分开；不改那条校验。
enum ArtifactOpener {

    enum OpenError: LocalizedError, Equatable {
        case pathResolution(String)
        case appLaunch(String)

        var errorDescription: String? {
            switch self {
            case .pathResolution(let detail):
                return "无法打开文件：\(detail)"
            case .appLaunch(let detail):
                return "默认应用打开失败：\(detail)"
            }
        }
    }

    /// 纯路径解析（无 NSWorkspace 耦合）。顺序与 `ExternalReportOpener` 相同，
    /// 唯一差别：后缀必须是 `.pdf`。
    static func resolveArtifactURL(relativePath: String, under stateRoot: URL) throws -> URL {
        let trimmed = relativePath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw OpenError.pathResolution("产物路径为空")
        }
        guard !(trimmed as NSString).isAbsolutePath else {
            throw OpenError.pathResolution("产物路径必须相对于状态根")
        }
        guard !(trimmed as NSString).pathComponents.contains("..") else {
            throw OpenError.pathResolution("产物路径不得包含 .. 分量")
        }
        let base = stateRoot.resolvingSymlinksInPath().standardizedFileURL
        let candidate = base.appending(path: trimmed)
            .resolvingSymlinksInPath().standardizedFileURL
        guard isContained(candidate, in: base) else {
            throw OpenError.pathResolution("产物路径逃逸了状态根")
        }
        guard candidate.pathExtension.lowercased() == "pdf" else {
            throw OpenError.pathResolution("覆盖产物打开器只接受 PDF")
        }
        let values = try? candidate.resourceValues(forKeys: [.isRegularFileKey])
        guard let isRegular = values?.isRegularFile else {
            throw OpenError.pathResolution("产物不存在：\(relativePath)")
        }
        guard isRegular else {
            throw OpenError.pathResolution("产物路径不是常规文件")
        }
        return candidate
    }

    private static func isContained(_ candidate: URL, in base: URL) -> Bool {
        let baseComps = base.pathComponents
        let candComps = candidate.pathComponents
        guard candComps.count >= baseComps.count else { return false }
        return Array(candComps.prefix(baseComps.count)) == baseComps
    }

    /// `openFile` 可注入，避免单测真的拉起 Preview。
    static func open(
        relativePath: String,
        under stateRoot: URL,
        openFile: (URL) throws -> Void = { url in
            guard NSWorkspace.shared.open(url) else {
                throw OpenError.appLaunch("NSWorkspace.open 返回 false")
            }
        },
        completion: @escaping (OpenError?) -> Void
    ) {
        let fileURL: URL
        do {
            fileURL = try resolveArtifactURL(relativePath: relativePath, under: stateRoot)
        } catch let err as OpenError {
            completion(err); return
        } catch {
            completion(.pathResolution(error.localizedDescription)); return
        }
        do {
            try openFile(fileURL)
            completion(nil)
        } catch let err as OpenError {
            completion(err)
        } catch {
            completion(.appLaunch(error.localizedDescription))
        }
    }
}
