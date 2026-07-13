import Foundation

/// 堆叠卡会话分时数据（R2-U7 KTD7）：持久态含昨收锚点 + 单调扩大的最大偏离，
/// 供 Y 轴范围计算脱离"当前已加载了多少个 bar"这个易变量。
struct SparklineSeries: Equatable {
    var points: [Double] = []
    var prevClose: Double?
    /// 已见数据（含历次 dayHigh/dayLow）相对 prevClose 的最大绝对偏离，只增不减（同一 tradeDate 内）。
    var maxDeviation: Double = 0
    /// 会话日 YYYY-MM-DD；变化即视为跨交易日，触发整体重置。
    var tradeDate: String?
}

/// Y 轴范围计算 + 跨刷新合并（纯函数，可单测）。
enum SparklineYAxis {
    /// 平盘日最小半幅：昨收的 0.5%，避免微小波动被放大成剧烈锯齿。
    static let minHalfSpanFraction = 0.005

    /// 合并一次新抓取结果进已有序列。
    /// - tradeDate 与已存不同 → 整体重置（不携带跨日的旧极值/旧昨收）。
    /// - 否则：`points` 更新为最新切片（渲染用），`maxDeviation` 只增不减。
    static func merge(
        existing: SparklineSeries?,
        newPoints: [Double],
        newPrevClose: Double?,
        newDayHigh: Double?,
        newDayLow: Double?,
        newTradeDate: String?
    ) -> SparklineSeries {
        var base = existing ?? SparklineSeries()
        if let newTradeDate, let oldTradeDate = base.tradeDate, newTradeDate != oldTradeDate {
            base = SparklineSeries()
        }
        base.points = newPoints
        if let newPrevClose, newPrevClose > 0 { base.prevClose = newPrevClose }
        if let newTradeDate { base.tradeDate = newTradeDate }

        guard let prevClose = base.prevClose, prevClose > 0 else {
            return base
        }
        var deviation = base.maxDeviation
        if let h = newDayHigh { deviation = max(deviation, abs(h - prevClose)) }
        if let l = newDayLow { deviation = max(deviation, abs(prevClose - l)) }
        for p in newPoints { deviation = max(deviation, abs(p - prevClose)) }
        base.maxDeviation = deviation
        return base
    }

    /// Y 轴范围：昨收 ± max(已见最大偏离, 最小半幅)。prevClose 缺失/非正时返回 nil
    /// （渲染层据此回退旧 min/max 自适应模式，不崩溃）。
    static func range(for series: SparklineSeries) -> (yMin: Double, yMax: Double)? {
        guard let prevClose = series.prevClose, prevClose > 0 else { return nil }
        let minHalf = prevClose * minHalfSpanFraction
        let halfSpan = max(series.maxDeviation, minHalf)
        return (prevClose - halfSpan, prevClose + halfSpan)
    }
}
