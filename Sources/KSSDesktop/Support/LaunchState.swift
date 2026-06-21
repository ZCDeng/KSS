import Foundation

/// 冷启动 gate 的纯状态机：无 UI 副作用、可单测、对一切非法/重复事件幂等。
///
/// 合法序列：`booting → animating → awaitingEntry → entering → entered`，
/// 以及任意非终态可恢复到 `fallback`。唯一进入工作台的路径是用户激活实际按钮
/// （Web 或原生 fallback，归一为 `.userEntry`）；动画、watchdog、画布点击、
/// Escape、循环、重复事件都不能放行。`entered` 是终态。
enum LaunchState: String, Equatable {
    case booting        // 进程刚起，等待 Web 页面 ready
    case animating      // 首轮 timeline 播放中
    case awaitingEntry  // 首轮完成（或 reduced-motion / fallback），按钮常驻可激活
    case entering       // 用户已激活按钮，原生层 cross-fade 中
    case entered        // 终态：启动层移除，工作台接管
    case fallback       // Web 失败/超时，原生静态画面 + 原生按钮
}

/// Web 上报的稳定错误类别（不携带 stack / 路径 / 数据）。
enum LaunchError: String, Equatable {
    case script      // 页面脚本异常
    case resource    // 资源缺失
    case navigation  // 非预期导航 / 加载失败
    case watchdog    // boot 超时未收到 ready
}

/// 驱动 reducer 的事件。Web 消息与原生 watchdog 都归一到这里。
enum LaunchEvent: Equatable {
    case webReady          // 本地页面初始化成功（普通动画路径）
    case reducedMotion     // 本地页面在 prefers-reduced-motion 下就绪（跳过动画）
    case webEntryAvailable // 首轮动画完成，按钮真正可见
    case userEntry         // 用户激活 Web 或原生 fallback 的实际按钮
    case failure(LaunchError)
    case enterComplete     // 原生 cross-fade 结束
}

/// 纯 reducer。`send` 返回是否发生了合法转移（用于触发副作用，如启动 cross-fade）。
struct LaunchReducer: Equatable {
    private(set) var state: LaunchState = .booting

    @discardableResult
    mutating func send(_ event: LaunchEvent) -> Bool {
        switch (state, event) {
        case (.booting, .webReady):
            state = .animating
        case (.booting, .reducedMotion):
            state = .awaitingEntry
        case (.animating, .webEntryAvailable):
            state = .awaitingEntry
        case (.awaitingEntry, .userEntry), (.fallback, .userEntry):
            state = .entering
        case (.booting, .failure), (.animating, .failure), (.awaitingEntry, .failure):
            state = .fallback
        case (.entering, .enterComplete):
            state = .entered
        default:
            return false // 一切非白名单转移：幂等忽略（含 entering/entered 后的 failure、重复 userEntry）
        }
        return true
    }

    /// 是否仍需展示启动层（entering 期间仍在淡出，故只有 entered 才隐藏）。
    var isGateVisible: Bool { state != .entered }
    /// 是否展示「进入」按钮（首轮完成的等待态，或原生 fallback）。
    var showsEntryButton: Bool { state == .awaitingEntry || state == .fallback }
    /// 是否走原生 fallback 画面。
    var isFallback: Bool { state == .fallback }
}

/// 把 WKScriptMessage body（已是 JSON object）映射为合法事件；
/// 未知 type / 非白名单 payload 返回 nil（被幂等忽略）。纯函数，便于单测。
enum LaunchMessageRouter {
    static func event(from body: Any) -> LaunchEvent? {
        guard let dict = body as? [String: Any],
              let type = dict["type"] as? String else { return nil }
        switch type {
        case "ready":
            let reduced = (dict["reducedMotion"] as? Bool) ?? false
            return reduced ? .reducedMotion : .webReady
        case "entryAvailable":
            return .webEntryAvailable
        case "enterRequested":
            return .userEntry
        case "error":
            let category = (dict["category"] as? String).flatMap(LaunchError.init(rawValue:)) ?? .script
            return .failure(category)
        default:
            return nil
        }
    }
}
