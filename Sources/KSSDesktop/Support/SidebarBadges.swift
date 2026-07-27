import Foundation

/// 侧栏导航角标：小圆点（有事提醒）或数字胶囊。
enum SidebarNavBadge: Equatable, Sendable {
    case dot
    case count(Int)

    /// 展示用数字；`dot` 无数字。
    var displayCount: Int? {
        switch self {
        case .dot: return nil
        case .count(let n): return max(0, n)
        }
    }
}

/// 从 store 侧可观测信号推导导航角标映射（纯函数，可单测）。
/// - Parameter selfCheckFailCount: 自检失败项数；>0 时在「盯盘」挂 dot（详情仍靠 banner）。
/// - Parameter recommendationCount: 可选推荐条数；≥1 时在「推荐」挂 count（无数据则不传 / 传 0）。
enum SidebarBadgeMapping {
    static func badges(
        selfCheckFailCount: Int,
        recommendationCount: Int = 0
    ) -> [WorkspaceSection: SidebarNavBadge] {
        var map: [WorkspaceSection: SidebarNavBadge] = [:]
        if selfCheckFailCount > 0 {
            map[.dashboard] = .dot
        }
        if recommendationCount > 0 {
            map[.recommendations] = .count(recommendationCount)
        }
        return map
    }
}
