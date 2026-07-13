import Foundation

/// 两条独立刷新 timer 的启停判定（纯函数，可单测）。R2-U6 KTD6：quote timer 维持既有
/// 交易时段门控不动；sparkline 新增独立盘后 timer，不受 authFailed 影响（盘后走 local
/// 降级不依赖 Longbridge 鉴权），交易时段内不单独跑（随 quote tick 顺带刷新）。
enum RealtimeTimerDecision {
    struct Result: Equatable {
        var quoteTimerOn: Bool
        var sparklineTimerOn: Bool
    }

    static func evaluate(
        scenePhaseActive: Bool,
        isTradingSession: Bool,
        isTradeDay: Bool,
        authFailed: Bool
    ) -> Result {
        guard scenePhaseActive else { return Result(quoteTimerOn: false, sparklineTimerOn: false) }
        let quoteOn = isTradingSession && !authFailed
        // 交易时段内 sparkline 随 quote tick 顺带刷新，不需要独立 timer；
        // 盘后交易日才启用独立 5 分钟 tick；非交易日整天暂停；与 authFailed 无关。
        let sparklineOn = isTradeDay && !isTradingSession
        return Result(quoteTimerOn: quoteOn, sparklineTimerOn: sparklineOn)
    }
}
