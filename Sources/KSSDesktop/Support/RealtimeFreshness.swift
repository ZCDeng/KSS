import Foundation

/// 实时行情新鲜度：优先用后端透传的 `source_asof_ts`（Longbridge 报价自身时间戳）判定，
/// 缺失/解析失败时回退到该标的自己的本地接收时间——不可用全局时间戳，否则会被其他
/// 标的的刷新成功掩盖（见 docs/plans/2026-07-13-001-fix-desktop-feedback-polish-plan.md KTD1）。
enum RealtimeFreshness: Equatable {
    case fresh
    case stale
    case missing

    static let staleThresholdSeconds: TimeInterval = 300

    // `source_asof_ts` 出自 Python `dt.astimezone(...).isoformat()`：带微秒时输出小数秒，
    // 不带微秒时不输出——两种格式都要能解析，否则会静默落入回退路径。
    private static let isoWithFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoWithoutFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parseSourceAsofTs(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        return isoWithFraction.date(from: raw) ?? isoWithoutFraction.date(from: raw)
    }

    /// - Parameters:
    ///   - sourceAsofTs: 后端报价自身时间戳（ISO-8601），优先信号。
    ///   - fallbackReceivedAt: 该标的自己本地接收该报价的时间，仅在 `sourceAsofTs` 缺失/不可解析时使用；
    ///     调用方必须传入这个标的自己的时间，不能传全局时间戳。
    static func status(sourceAsofTs: String?, fallbackReceivedAt: Date?, now: Date) -> RealtimeFreshness {
        if let parsed = parseSourceAsofTs(sourceAsofTs) {
            return now.timeIntervalSince(parsed) > staleThresholdSeconds ? .stale : .fresh
        }
        guard let fallback = fallbackReceivedAt else { return .missing }
        return now.timeIntervalSince(fallback) > staleThresholdSeconds ? .stale : .fresh
    }
}
