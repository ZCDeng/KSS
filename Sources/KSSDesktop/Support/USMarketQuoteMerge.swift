import Foundation

struct USMarketHeaderStatus: Equatable {
    var text: String
    var systemImage: String
    var isActive: Bool
}

/// 隔夜美股的独立合并规则。失败行不会清空上一条完整快照；降级状态仍显式
/// 展示，且价格与昨收始终来自同一条 provider 结果。
enum USMarketQuoteMerge {
    static func merge(
        previous: [String: USMarketQuote],
        incoming: [USMarketQuote]
    ) -> [String: USMarketQuote] {
        var result = previous
        for quote in incoming {
            let key = quote.code.uppercased()
            if quote.hasUsablePrice || result[key] == nil {
                result[key] = quote
                continue
            }
            guard var retained = result[key], retained.hasUsablePrice else {
                result[key] = quote
                continue
            }
            retained.status = quote.status == "static" ? "static" : "stale"
            retained.marketPhase = quote.marketPhase ?? retained.marketPhase
            retained.receivedAt = quote.receivedAt ?? retained.receivedAt
            retained.error = quote.error
            result[key] = retained
        }
        return result
    }

    static func coverage(
        quotes: [String: USMarketQuote],
        orderedCodes: [String]
    ) -> USMarketCoverage {
        let values = orderedCodes.compactMap { quotes[$0.uppercased()] }
        return USMarketCoverage(
            live: values.count { $0.status == "live" },
            delayed: values.count { $0.status == "delayed" },
            stale: values.count { $0.status == "stale" },
            static: values.count { $0.status == "static" },
            unavailable: values.count { $0.status == "unavailable" }
        )
    }

    static func summary(_ coverage: USMarketCoverage?) -> String {
        guard let coverage else { return "等待美股行情" }
        var parts: [String] = []
        if let count = coverage.live, count > 0 { parts.append("\(count) 实时") }
        if let count = coverage.delayed, count > 0 { parts.append("\(count) 延迟") }
        if let count = coverage.stale, count > 0 { parts.append("\(count) 过期") }
        if let count = coverage.static, count > 0 { parts.append("\(count) 静态") }
        if let count = coverage.unavailable, count > 0 {
            parts.append("\(count) 不可用")
        }
        return parts.isEmpty ? "暂无可用行情" : parts.joined(separator: " · ")
    }

    /// 卡片只承载价格与涨跌；行情阶段和新鲜度集中放在分区标题，避免每张卡
    /// 重复出现“静态 · yFinance”一类实现细节。
    static func headerStatus(
        _ coverage: USMarketCoverage?,
        phase: String?
    ) -> USMarketHeaderStatus {
        switch phase {
        case "regular":
            guard let coverage else {
                return .init(
                    text: "盘中 · 等待行情",
                    systemImage: "clock.arrow.circlepath",
                    isActive: false
                )
            }
            var parts = ["盘中"]
            if let live = coverage.live, live > 0 { parts.append("\(live) 实时") }
            if let delayed = coverage.delayed, delayed > 0 { parts.append("\(delayed) 延迟") }
            if let stale = coverage.stale, stale > 0 { parts.append("\(stale) 过期") }
            if let unavailable = coverage.unavailable, unavailable > 0 {
                parts.append("\(unavailable) 未覆盖")
            }
            if parts.count == 1 { parts.append("收盘数据") }
            let active = (coverage.live ?? 0) > 0
            return .init(
                text: parts.joined(separator: " · "),
                systemImage: active
                    ? "dot.radiowaves.left.and.right"
                    : "clock.arrow.circlepath",
                isActive: active
            )
        case "pre":
            return .init(text: "盘前 · 上一收盘", systemImage: "sunrise", isActive: false)
        case "post":
            return .init(text: "盘后 · 收盘数据", systemImage: "moon.stars", isActive: false)
        case "closed":
            return .init(text: "休市 · 收盘数据", systemImage: "pause.circle", isActive: false)
        default:
            return .init(
                text: "行情状态待确认",
                systemImage: "clock.arrow.circlepath",
                isActive: false
            )
        }
    }
}
