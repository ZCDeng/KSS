import Foundation

struct AppSnapshot: Codable {
    var generatedAt: String
    var projectRoot: String
    var latestDataDate: String?
    var stockCount: Int
    var recommendationDate: String?
    var stocks: [StockSummary]
    var recommendations: [Recommendation]
    var reviews: [DailyReview]
    var backtests: [BacktestReport]
    var tracking: TrackingSummary
    var recommendationTracking: [RecTrackingDay]?
    var bjScan: BJScan?
    var perillaPicks: [PerillaPick]?
    var sectorReviews: [SectorPulse]?
    var sectorRotationHistory: [HotspotRotationHistoryItem]?
    var latestSectorRotation: HotspotRotationSummary?
    var marketStrip: MarketStrip?
    var pythonEnvironment: PythonEnvironment?
    var recentTaskRuns: [TaskRunResult]
}
/// 板块热点轮动：单日龙头 persistence.
struct HotspotLeaderStock: Codable, Hashable, Identifiable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var appearances: Int
    var positions: [String]?
}

/// 板块热点轮动：单个板块（行业/概念/KAIPAN/leader 板）.
struct HotspotBoard: Codable, Hashable, Identifiable {
    var id: String { "\(source)-\(name)" }
    var name: String
    var source: String
    var boardCode: String?
    var pctChange: Double?
    var heatScore: Double?
    var todayRank: Int
    var previousRank: Int?
    var rankJump: Int?
    var top3Appearances: Int
    var streakDays: Int
    var strengthDelta: Double?
    var kaipanStrengthScore: Int?
    var kaipanRank: Int?
    var flowPersistenceScore: Double?
    var classification: String
    var classificationConfidence: String
    var evidenceSources: [String]
    var leaderStocks: [HotspotLeaderStock]?
    var missing: [String]
}

/// 板块热点轮动：单日完整快照.
struct HotspotRotationSnapshot: Codable, Hashable, Identifiable {
    var id: String { tradeDate }
    var tradeDate: String
    var lookbackDays: Int
    var tradingDaysUsed: [String]
    var historyCoverage: Double
    var leaderCoverage: Double
    var missing: [String]
    var industries: [HotspotBoard]
    var concepts: [HotspotBoard]
    var kaipanBoards: [HotspotBoard]
    var leaderBoards: [HotspotBoard]
    var crossSourceSignals: HotspotCrossSourceSignals
}

struct HotspotCrossSourceSignals: Codable, Hashable {
    var mainline: [String]
    var demonBoard: [String]
    var oldHotspotFading: [String]
    var satellite: [String]
}

/// 板块热点轮动：用于总览摘要卡片的轻量结构.
struct HotspotRotationSummary: Codable, Hashable {
    var tradeDate: String?
    var lookbackDays: Int?
    var leaderCoverage: Double?
    var historyCoverage: Double?
    var mainline: [String]
    var demonBoard: [String]
    var oldHotspotFading: [String]
    var topLeaders: [HotspotLeaderStock]
    var boardCount: Int
}

/// 板块热点轮动：日期列表项.
struct HotspotRotationHistoryItem: Codable, Hashable, Identifiable {
    var id: String { tradeDate }
    var tradeDate: String
    var lookbackDays: Int?
    var historyCoverage: Double?
    var leaderCoverage: Double?
    var mainlineCount: Int
    var demonBoardCount: Int
    var oldHotspotFadingCount: Int
    var satelliteCount: Int
}

/// 紫苏叶（供应链护城河评分）选股，数据源 supply_chain.yaml + ChainRegistry。
struct PerillaPick: Codable, Identifiable, Hashable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var chains: String
    var layer: Int
    var role: String
    var moat: String
    var locked: Bool
    var score: Double
    // 行情 / 估值（cs_data + daily_basic 切片）
    var ret1d: Double?
    var ret5d: Double?
    var ret20d: Double?
    var retYear: Double?
    var pe: Double?
    var pb: Double?
    var circMvYi: Double?   // 流通市值（亿元）；缺失时回退总市值
    var mvIsFloat: Bool?    // true=流通市值, false=回退总市值
}

/// 总览第一行市场速览：A500ETF 当日行情 + 北向资金净流入。
struct MarketStrip: Codable, Hashable {
    var date: String?
    var northMoney: Double?    // 万元
    var northDate: String?
    var etfs: [ETFQuote]
    var indices: [IndexQuote]?
    var indexBoard: [IndexQuote]?   // 总览底部 13 个常用指数
}

struct ETFQuote: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String
    var close: Double
    var pct: Double
}

struct IndexQuote: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String
    var close: Double
    var pct: Double
    var date: String?
}

/// 板块脉冲（每日一份）：etf_radar 切片，资金申赎 + 强势确认分级。
struct SectorPulse: Codable, Hashable, Identifiable {
    var id: String { tradeDate }
    var tradeDate: String
    var dataDate: String
    var stale: Bool
    var note: String
    var regimeInRegime: Bool?
    var regimeMom20: Double?
    var regimeMom20Th: Double?
    var themes: [SectorTheme]
    var commentary: String?   // 投顾点评 Markdown（概念轮动 / 七大主题 / 加减仓建议 等）
}

struct SectorTheme: Codable, Identifiable, Hashable {
    var id: String { name }
    var name: String
    var flow1d: Double?
    var flow5d: Double?
    var past5Ret: Double?
    var grade: String
    var divergence: Bool
    var accel: Bool
    var rank5d: Int?
    var nFunds: Int?
}

/// 定时任务（launchd）一项：deploy/launchd/*.plist + launchctl 状态 + 日志末行。
struct ScheduledJob: Codable, Identifiable, Hashable {
    var id: String { label }
    var label: String
    var title: String
    var schedule: String      // 人读调度，如「工作日 17:30」
    var script: String
    var enabled: Bool         // 是否启用（未被 launchctl disable）
    var loaded: Bool          // 是否已 bootstrap 到 gui 域
    var lastStatus: String    // success / failed / unknown
    var lastRunAt: String?    // 上次运行时间（日志 mtime）
    var lastLine: String?     // 上次运行日志末行摘要
}

/// cron-rerun / cron-enable / cron-disable 的返回。
struct CronActionResult: Codable, Hashable {
    var ok: Bool
    var error: String?
    var job: ScheduledJob?
}

/// 十五五科技主题 → 板块龙头/第二梯队（数据源 themes_15th_5y.yaml + 热点轮动归档）。
struct ThemeLeaders: Codable, Hashable, Identifiable {
    var id: String { name }
    var name: String
    var boardNames: [String]
    var boardCount: Int
    var boards: [ThemeBoard]
    var leaderBoardCount: Int
}

struct ThemeBoard: Codable, Hashable, Identifiable {
    var id: String { board }
    var board: String
    var classification: String?
    var leaders: [HotspotLeaderStock]      // 龙一/龙二
    var secondTier: [HotspotLeaderStock]   // 龙三/龙四/龙五
}

struct RecTrackingDay: Codable, Identifiable, Hashable {
    var id: String { date }
    var date: String
    var nPicks: Int
    var ret1d: Double?
    var ret5d: Double?
    var ret20d: Double?
    var picks: [RecTrackingPick]
}

struct RecTrackingPick: Codable, Identifiable, Hashable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var ret1d: Double?
    var ret5d: Double?
    var ret20d: Double?
}

struct BJScan: Codable, Hashable {
    var scanDate: String?
    var total: Int
    var passed: Int
    var top: [BJScanItem]
}

struct BJScanItem: Codable, Identifiable, Hashable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var industry: String
    var score: Double?
    var ret20d: Double?
    var close: Double?
    var tag: String
}

struct StockSummary: Codable, Identifiable, Hashable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var industry: String
    var concept: String
    var latestDate: String
    var close: Double?
    var pctChange: Double?
    var turnoverRate: Double?
    var amount: Double?
    var pe: Double?
    var pb: Double?
    var totalMv: Double?
    var ma5: Double?
    var ma20: Double?
    var high20: Double?
    var low20: Double?
}

struct Recommendation: Codable, Identifiable, Hashable {
    var id: String { "\(date)-\(symbol)" }
    var date: String
    var symbol: String
    var name: String
    var industry: String
    var rank: Int
    var weight: Double
    var factorValue: Double?
    var latestClose: Double?
    var trackingReturn: Double?
    var status: String
}

struct DailyReview: Codable, Identifiable, Hashable {
    var id: String { path }
    var date: String
    var title: String
    var excerpt: String
    var path: String
    var focusSymbols: [String]
}

struct BacktestReport: Codable, Identifiable, Hashable {
    var id: String { path }
    var title: String
    var path: String
    var updatedAt: String
    var metrics: [ReportMetric]
    var excerpt: String
}

struct ReportDetail: Codable, Hashable {
    var title: String
    var path: String
    var updatedAt: String
    var text: String
}

struct ReportMetric: Codable, Hashable {
    var name: String
    var value: String
}

struct TrackingSummary: Codable, Hashable {
    var nDaysLogged: Int
    var nDaysWithReturns: Int
    var sampleStart: String?
    var sampleEnd: String?
    var annualized: Double?
    var sharpe: Double?
    var maxDrawdown: Double?
    var winRate: Double?
    var avgDailyReturn: Double?
    var message: String?
}

/// 股票池导入解析结果：名称/代码 → ts_code。
struct ResolvedStock: Codable, Identifiable, Hashable {
    var id: String { query + code }
    var query: String
    var code: String
    var name: String
    var ok: Bool
    var inPool: Bool
    var kind: String?   // stock / fund
}

struct StockDetail: Codable {
    var symbol: String
    var name: String
    var industry: String
    var concept: String
    var latest: StockSummary?
    var history: [PricePoint]
    var reviewConclusion: StockReview?
}

/// 个股复盘结论（从 daily_review 抽取）：标题 / 快照 / 预期区间 / 建议。
struct StockReview: Codable, Hashable {
    var date: String
    var headline: String
    var snapshot: String
    var expectation: String
    var suggestions: [String]
}

struct PricePoint: Codable, Identifiable {
    var id: String { date }
    var date: String
    var open: Double?
    var high: Double?
    var low: Double?
    var close: Double
    var pctChange: Double?
    var volume: Double?
    var amount: Double?
}

struct TaskRunResult: Codable, Identifiable, Hashable {
    var id: String { "\(taskId)-\(startedAt)" }
    var taskId: String
    var title: String
    var startedAt: String
    var finishedAt: String
    var status: String
    var exitCode: Int
    var summary: String
    var stdout: String
    var stderr: String
    var artifacts: [String]
}

struct PythonEnvironment: Codable, Hashable {
    var selected: String?
    var usable: Bool
    var requiredModules: [String]
    var candidates: [PythonCandidate]
}

struct PythonCandidate: Codable, Hashable {
    var path: String
    var usable: Bool
    var missingModules: [String]
    var error: String
}

enum KSSTask: String, CaseIterable, Identifiable {
    case previewPicks = "daily-picks-preview"
    case generatePicks = "daily-picks"
    case paperSummary = "paper-summary"
    case logmvBacktest = "logmv-backtest"
    case radarArchiveAnalysis = "radar-archive-analysis"
    case formalDailyPicks = "formal-daily-picks"
    case formalPaperSummary = "formal-paper-summary"
    case formalDailyReview = "formal-daily-review"
    case formalSectorReview = "formal-sector-review"
    case formalEtfRadarBacktest = "formal-etf-radar-backtest"
    case refreshBjDaily = "refresh-bj-daily"
    case refreshDailyBasic = "refresh-daily-basic"
    case refreshMarketStrip = "refresh-market-strip"
    case refreshSectorRotation = "refresh-sector-rotation"
    case updateCsData = "update-cs-data"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .previewPicks: return "预览推荐"
        case .generatePicks: return "保存每日推荐"
        case .paperSummary: return "纸交易跟踪"
        case .logmvBacktest: return "log_mv 轻量回测"
        case .radarArchiveAnalysis: return "雷达归档分析"
        case .formalDailyPicks: return "正式每日选股"
        case .formalPaperSummary: return "正式纸交易汇总"
        case .formalDailyReview: return "正式每日复盘"
        case .formalSectorReview: return "正式板块复盘"
        case .formalEtfRadarBacktest: return "正式 ETF 回测"
        case .refreshBjDaily: return "刷新北证日线"
        case .refreshDailyBasic: return "刷新流通市值/估值"
        case .refreshMarketStrip: return "刷新市场速览"
        case .refreshSectorRotation: return "刷新板块热点轮动"
        case .updateCsData: return "同步股票池日线"
        }
    }

    var systemImage: String {
        switch self {
        case .previewPicks: return "eye"
        case .generatePicks: return "square.and.arrow.down"
        case .paperSummary: return "chart.line.uptrend.xyaxis"
        case .logmvBacktest: return "function"
        case .radarArchiveAnalysis: return "chart.bar.xaxis"
        case .formalDailyPicks: return "checkmark.seal"
        case .formalPaperSummary: return "doc.text.magnifyingglass"
        case .formalDailyReview: return "text.page.badge.magnifyingglass"
        case .formalSectorReview: return "chart.bar.xaxis"
        case .formalEtfRadarBacktest: return "waveform.path.ecg"
        case .refreshBjDaily: return "arrow.triangle.2.circlepath"
        case .refreshDailyBasic: return "yensign.circle"
        case .refreshMarketStrip: return "chart.bar.doc.horizontal"
        case .refreshSectorRotation: return "flame"
        case .updateCsData: return "arrow.triangle.2.circlepath.circle"
        }
    }

    var lane: String {
        switch self {
        case .previewPicks, .generatePicks, .paperSummary, .logmvBacktest, .radarArchiveAnalysis:
            return "轻量"
        case .formalDailyPicks, .formalPaperSummary, .formalDailyReview, .formalSectorReview, .formalEtfRadarBacktest, .refreshBjDaily, .refreshDailyBasic, .refreshMarketStrip, .refreshSectorRotation, .updateCsData:
            return "正式"
        }
    }

    var arguments: [String] {
        switch self {
        case .logmvBacktest:
            return ["--lookback", "160"]
        default:
            return []
        }
    }
}

enum WorkspaceSection: String, CaseIterable, Identifiable {
    case dashboard = "Dashboard"
    case recommendations = "Daily Picks"
    case watchlist = "Watchlist"
    case hotspot = "Hotspot"
    case themes = "Themes"
    case reviews = "Reviews"
    case backtests = "Backtests"
    case stocks = "Stocks"
    case runbook = "Runbook"
    case architecture = "Architecture"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .dashboard: return "总览"
        case .recommendations: return "推荐"
        case .watchlist: return "自选"
        case .hotspot: return "热点"
        case .themes: return "主题"
        case .runbook: return "任务"
        case .reviews: return "复盘"
        case .backtests: return "回测"
        case .stocks: return "股票池"
        case .architecture: return "架构"
        }
    }

    var symbol: String {
        switch self {
        case .dashboard: return "gauge.with.dots.needle.50percent"
        case .recommendations: return "target"
        case .watchlist: return "star"
        case .hotspot: return "flame"
        case .themes: return "square.grid.2x2"
        case .runbook: return "terminal"
        case .reviews: return "doc.text.magnifyingglass"
        case .backtests: return "chart.xyaxis.line"
        case .stocks: return "list.bullet.rectangle"
        case .architecture: return "circle.hexagongrid"
        }
    }

    // MARK: 边栏排序（总览永久置顶，其余可拖拽重排）

    /// 永久置顶、不参与排序的 section。
    static let pinned: [WorkspaceSection] = [.dashboard]

    /// 可被用户拖拽重排的 section（enum 原序，去掉置顶项）。
    static var reorderable: [WorkspaceSection] {
        allCases.filter { !pinned.contains($0) }
    }

    /// 把已存顺序（rawValue 逗号串）解析成完整有序 section 列表。
    /// 规则：置顶项永远在前；其余按存储顺序排；存储里缺失的 reorderable 项
    /// 按 enum 原序追加末尾（向前兼容未来新增 section）；非法/置顶/重复 rawValue 忽略。
    static func ordered(from saved: String) -> [WorkspaceSection] {
        let savedRaw = saved
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        var result: [WorkspaceSection] = []
        var seen = Set<WorkspaceSection>()
        for raw in savedRaw {
            guard let section = WorkspaceSection(rawValue: raw),
                  !pinned.contains(section),
                  !seen.contains(section) else { continue }
            result.append(section)
            seen.insert(section)
        }
        // 追加存储里没有的 reorderable 项（含未来新增）
        for section in reorderable where !seen.contains(section) {
            result.append(section)
        }
        return pinned + result
    }

    /// 把有序 section 列表编码成存储串（只存非置顶项）。
    static func encode(_ sections: [WorkspaceSection]) -> String {
        sections
            .filter { !pinned.contains($0) }
            .map { $0.rawValue }
            .joined(separator: ",")
    }
}
