import Foundation

/// Longbridge 实时报价合并 / 符号筛选（纯函数，可单测）。
enum RealtimeMerge {
    static let maxSymbolsPerTick = 20
    static let canarySymbol = "000001.SH"

    /// 陆股通可拉实时：仅 `.SH` / `.SZ`（排除 `.BJ`、IXIC/HSI 等裸码）。
    static func isLiveableSymbol(_ code: String) -> Bool {
        let u = code.uppercased()
        return u.hasSuffix(".SH") || u.hasSuffix(".SZ")
    }

    /// 采集待刷新符号。
    /// 顺序：`priority`（页内标的）→ ETF → 主指数行 → `extra` → 指数板。
    /// 保证推荐/主题等页内标的不被 indexBoard 挤出 20 槽。
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
        for e in strip?.etfs ?? [] { add(e.code) }
        for i in strip?.indices ?? [] { add(i.code) }
        for e in extra { add(e) }
        for i in strip?.indexBoard ?? [] { add(i.code) }

        if out.count > maxCount {
            out = Array(out.prefix(maxCount))
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

    /// 展示区是否至少有一个可实时字段命中 map（badge「实时」口径）。
    static func hasAnyLive(symbols: [String], quotes: [String: LongbridgeQuote]) -> Bool {
        symbols.contains { code in
            quotes[code]?.isLive == true
        }
    }
}
