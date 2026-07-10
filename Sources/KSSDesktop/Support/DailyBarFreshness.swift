import Foundation

/// 日线底稿新鲜度：比较票的末数据日与「应有日线日」referenceTradeDate。
enum DailyBarFreshness {
    enum Status: Equatable {
        case fresh
        case stale
        case missing
    }

    /// 支持 `YYYY-MM-DD` / `YYYYMMDD`；无法解析返回 nil。
    static func normalizeDate(_ raw: String?) -> String? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
            return nil
        }
        let digits = raw.filter(\.isNumber)
        guard digits.count >= 8 else { return nil }
        let y = digits.prefix(4)
        let m = digits.dropFirst(4).prefix(2)
        let d = digits.dropFirst(6).prefix(2)
        return "\(y)-\(m)-\(d)"
    }

    /// bar 缺失 → missing；bar &lt; reference → stale；否则 fresh。
    static func status(barDate: String?, referenceTradeDate: String?) -> Status {
        guard let bar = normalizeDate(barDate) else { return .missing }
        guard let ref = normalizeDate(referenceTradeDate) else { return .fresh }
        if bar < ref { return .stale }
        return .fresh
    }

    /// 列表紧凑：`07-09` / `陈旧·06-26` / `无日线`
    static func compactLabel(barDate: String?, referenceTradeDate: String?) -> String {
        switch status(barDate: barDate, referenceTradeDate: referenceTradeDate) {
        case .missing:
            return "无日线"
        case .stale:
            return "陈旧·\(shortMD(barDate))"
        case .fresh:
            return shortMD(barDate)
        }
    }

    /// 详情：`日线截至 2026-07-09` / `日线陈旧 · 截至 2026-06-26`
    static func detailLabel(barDate: String?, referenceTradeDate: String?) -> String {
        switch status(barDate: barDate, referenceTradeDate: referenceTradeDate) {
        case .missing:
            return "无日线"
        case .stale:
            if let n = normalizeDate(barDate) {
                return "日线陈旧 · 截至 \(n)"
            }
            return "日线陈旧"
        case .fresh:
            if let n = normalizeDate(barDate) {
                return "日线截至 \(n)"
            }
            return "日线"
        }
    }

    private static func shortMD(_ raw: String?) -> String {
        guard let n = normalizeDate(raw), n.count >= 10 else { return "—" }
        // YYYY-MM-DD → MM-DD
        return String(n.dropFirst(5))
    }
}
