import Foundation

/// Longbridge 实时报价合并 / 符号筛选（纯函数，可单测）。
enum RealtimeMerge {
    static let maxSymbolsPerTick = 20
    static let canarySymbol = "000001.SH"

    /// strip / UI 产品码 → Longbridge 请求码。
    /// - `.SH` / `.SZ` / `.HK`：原样大写
    /// - 港股裸指数：`HSI` → `HSI.HK`；`HSTECH` → `HSTECH.HK`（恒生科技指数）
    /// - 其余裸码 / `.BJ` / 美股：不可实时，返回 nil
    static func toLongbridgeSymbol(_ code: String) -> String? {
        let u = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !u.isEmpty else { return nil }
        switch u {
        case "HSI": return "HSI.HK"
        case "HSTECH": return "HSTECH.HK"
        default:
            if u.hasSuffix(".SH") || u.hasSuffix(".SZ") || u.hasSuffix(".HK") {
                return u
            }
            return nil
        }
    }

    /// 是否可走 Longbridge 实时（含港股 `.HK` 与 HSI/HSTECH 别名）。
    static func isLiveableSymbol(_ code: String) -> Bool {
        toLongbridgeSymbol(code) != nil
    }

    /// 采集待刷新符号（**产品码**，UI map key 与 strip 一致）。
    /// 顺序：`priority` → **indexStacks** → ETF → 主指数行 → `extra` → 指数板。
    /// 保证堆叠卡 / 推荐页内标的不被 indexBoard 挤出 20 槽。
    static func harvestSymbols(
        strip: MarketStrip?,
        priority: [String] = [],
        extra: [String] = [],
        maxCount: Int = maxSymbolsPerTick
    ) -> [String] {
        var seen = Set<String>()
        var out: [String] = []

        func add(_ code: String?) {
            guard let raw = code?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return }
            let code = raw.uppercased()
            guard isLiveableSymbol(code), !seen.contains(code) else { return }
            seen.insert(code)
            out.append(code)
        }

        for p in priority { add(p) }
        for c in symbolsFromIndexStacks(strip?.indexStacks) { add(c) }
        for e in strip?.etfs ?? [] { add(e.code) }
        for i in strip?.indices ?? [] { add(i.code) }
        for e in extra { add(e) }
        for i in strip?.indexBoard ?? [] { add(i.code) }

        if out.count > maxCount {
            out = Array(out.prefix(maxCount))
        }
        return out
    }

    /// 堆叠卡产品码（列序 × 层序），供 harvest 与 badge。
    static func symbolsFromIndexStacks(_ stacks: [IndexStackColumn]?) -> [String] {
        guard let stacks else { return [] }
        var out: [String] = []
        for col in stacks {
            for item in col.items {
                out.append(item.code)
            }
        }
        return out
    }

    /// 推荐列表符号（保持 rank 序）。
    static func symbolsFromRecommendations(_ recs: [Recommendation]) -> [String] {
        recs.map(\.symbol)
    }

    /// 主题龙头优先、第二梯队殿后。
    static func symbolsFromThemes(_ themes: [ThemeLeaders]) -> [String] {
        var out: [String] = []
        for t in themes {
            for b in t.boards {
                out.append(contentsOf: b.leaders.map(\.symbol))
            }
        }
        for t in themes {
            for b in t.boards {
                out.append(contentsOf: b.secondTier.map(\.symbol))
            }
        }
        return out
    }

    /// 可选快照收盘价 + quote → 展示用 close/pct/isLive；两者皆无则 nil。
    static func displayPrice(
        snapshotClose: Double?,
        snapshotPct: Double? = nil,
        quote: LongbridgeQuote?
    ) -> (close: Double, pct: Double, isLive: Bool)? {
        if let quote, quote.isLive, let last = quote.lastDone {
            let baseClose = snapshotClose ?? last
            let basePct = snapshotPct ?? 0
            return applyLive(close: baseClose, pct: basePct, quote: quote)
        }
        if let c = snapshotClose {
            return (c, snapshotPct ?? 0, false)
        }
        return nil
    }

    /// 用 live quote 覆盖 close/pct；无有效 lastDone 则保持快照。
    static func applyLive(
        close: Double,
        pct: Double,
        quote: LongbridgeQuote?
    ) -> (close: Double, pct: Double, isLive: Bool) {
        guard let quote, quote.isLive, let last = quote.lastDone else {
            return (close, pct, false)
        }
        if let prev = quote.prevClose, prev > 0 {
            let livePct = (last - prev) / prev * 100.0
            return (last, livePct, true)
        }
        // 有价无昨收：价格用实时，涨跌幅保留快照
        return (last, pct, true)
    }

    /// 展示区是否至少有一个可实时字段命中 map（badge「实时」口径，历史布尔口径，逐步被 worstFreshness 取代）。
    static func hasAnyLive(symbols: [String], quotes: [String: LongbridgeQuote]) -> Bool {
        symbols.contains { code in
            quotes[code.uppercased()]?.isLive == true
        }
    }

    /// 单个标的的新鲜度（KTD1/KTD2）：优先用 quote 自带的 `sourceAsofTs`，缺失/不可解析时
    /// 回退该标的自己的接收时间（`receivedAtBySymbol`，不可用全局时间戳）。
    static func freshness(
        for symbol: String,
        quotes: [String: LongbridgeQuote],
        receivedAtBySymbol: [String: Date],
        now: Date = Date()
    ) -> RealtimeFreshness {
        let code = symbol.uppercased()
        let quote = quotes[code]
        return RealtimeFreshness.status(
            sourceAsofTs: quote?.sourceAsofTs,
            fallbackReceivedAt: receivedAtBySymbol[code],
            now: now
        )
    }

    /// 页面级汇总（页头 badge 用）：展示标的中只要有一个 `.stale` 就诚实降级，
    /// 其次有 `.fresh` 才算「实时」，否则 `.missing`（KTD2：页头是汇总，不是逐标的展示）。
    static func worstFreshness(
        symbols: [String],
        quotes: [String: LongbridgeQuote],
        receivedAtBySymbol: [String: Date],
        now: Date = Date()
    ) -> RealtimeFreshness {
        var sawStale = false
        var sawFresh = false
        for code in symbols {
            guard quotes[code.uppercased()] != nil else { continue }
            switch freshness(for: code, quotes: quotes, receivedAtBySymbol: receivedAtBySymbol, now: now) {
            case .stale: sawStale = true
            case .fresh: sawFresh = true
            case .missing: break
            }
        }
        if sawStale { return .stale }
        if sawFresh { return .fresh }
        return .missing
    }

    /// 从 intraday-bars 收盘序列降采样为 sparkline（≤ maxPoints）。
    static func sparklineCloses(from bars: [OHLCBar], maxPoints: Int = 120) -> [Double] {
        let closes = bars.compactMap(\.close)
        guard !closes.isEmpty else { return [] }
        if closes.count <= maxPoints { return closes }
        let step = max(1, closes.count / maxPoints)
        return Array(stride(from: 0, to: closes.count, by: step).map { closes[$0] }.prefix(maxPoints))
    }
}
