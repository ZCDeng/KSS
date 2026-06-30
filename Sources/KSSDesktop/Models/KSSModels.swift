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

    // bridge 有两种 leaderStocks 形态：摘要卡 topLeaders 用 symbol/appearances，
    // 归档全快照 leaderBoards[].leaderStocks 是原始 code/count。两者都要能解码，
    // 否则进「复盘」加载全快照时 keyNotFound → "Bridge returned invalid JSON"。
    enum CodingKeys: String, CodingKey {
        case symbol, code, name, appearances, count, positions
    }

    init(symbol: String, name: String, appearances: Int, positions: [String]?) {
        self.symbol = symbol
        self.name = name
        self.appearances = appearances
        self.positions = positions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        symbol = (try? c.decode(String.self, forKey: .symbol))
            ?? (try? c.decode(String.self, forKey: .code)) ?? ""
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        appearances = (try? c.decode(Int.self, forKey: .appearances))
            ?? (try? c.decode(Int.self, forKey: .count)) ?? 0
        positions = try? c.decode([String].self, forKey: .positions)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(symbol, forKey: .symbol)
        try c.encode(name, forKey: .name)
        try c.encode(appearances, forKey: .appearances)
        try c.encodeIfPresent(positions, forKey: .positions)
    }
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

// MARK: - 舆情热点 digest（news-digest）

/// 舆情热点：bridge `news-digest [DATE] [SCENE]` 顶层响应。
/// available=false 时 selected=nil（空态）；index 供日期/场景选择列表。
struct NewsDigestResponse: Codable, Hashable {
    var available: Bool
    var index: [NewsDigestIndexEntry]
    var selected: NewsDigest?
}

/// 舆情热点：可选档案条目（日期 + 场景），newest first。
struct NewsDigestIndexEntry: Codable, Hashable, Identifiable {
    var id: String { "\(date)-\(scene)" }
    var date: String
    var scene: String
}

/// 舆情热点：选中档（某日某场景的完整 digest）。
struct NewsDigest: Codable, Hashable {
    var date: String
    var scene: String
    var generatedAt: String?
    var directions: [NewsDirection]
    var catalysts: [NewsCatalyst]
    var quarantinedCount: Int?
    var llmStatus: String?
    var withMapping: Bool?
    var partial: Bool?
    var sources: [String: Int]?

    enum CodingKeys: String, CodingKey {
        case date, scene, generatedAt, directions, catalysts, partial, sources
        case quarantinedCount = "quarantined_count"
        case llmStatus = "llm_status"
        case withMapping = "with_mapping"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = (try? c.decode(String.self, forKey: .date)) ?? ""
        scene = (try? c.decode(String.self, forKey: .scene)) ?? ""
        generatedAt = try? c.decode(String.self, forKey: .generatedAt)
        directions = (try? c.decode([NewsDirection].self, forKey: .directions)) ?? []
        catalysts = (try? c.decode([NewsCatalyst].self, forKey: .catalysts)) ?? []
        quarantinedCount = try? c.decode(Int.self, forKey: .quarantinedCount)
        llmStatus = try? c.decode(String.self, forKey: .llmStatus)
        withMapping = try? c.decode(Bool.self, forKey: .withMapping)
        partial = try? c.decode(Bool.self, forKey: .partial)
        sources = try? c.decode([String: Int].self, forKey: .sources)
    }
}

/// 舆情热点：单个集中方向（题材/板块）。
struct NewsDirection: Codable, Hashable, Identifiable {
    var id: String { label }
    var label: String
    var sentiment: String
    var sentimentSource: String?
    var heatLine: String?
    var independentConfirmations: Int?
    var distinctSources: [String]?
    var heatScore: Double?
    var theme: String?
    var mapping: String?
    var degradeReason: String?
    var stocks: [NewsStock]?
    var sourcePosts: [NewsSourcePost]?
    var signalQuality: NewsSignalQuality?

    enum CodingKeys: String, CodingKey {
        case label, sentiment, theme, mapping, stocks
        case sentimentSource = "sentiment_source"
        case heatLine = "heat_line"
        case independentConfirmations = "independent_confirmations"
        case distinctSources = "distinct_sources"
        case heatScore = "heat_score"
        case degradeReason = "degrade_reason"
        case sourcePosts = "source_posts"
        case signalQuality = "signal_quality"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = (try? c.decode(String.self, forKey: .label)) ?? ""
        sentiment = (try? c.decode(String.self, forKey: .sentiment)) ?? ""
        sentimentSource = try? c.decode(String.self, forKey: .sentimentSource)
        heatLine = try? c.decode(String.self, forKey: .heatLine)
        independentConfirmations = try? c.decode(Int.self, forKey: .independentConfirmations)
        distinctSources = try? c.decode([String].self, forKey: .distinctSources)
        heatScore = try? c.decode(Double.self, forKey: .heatScore)
        theme = try? c.decode(String.self, forKey: .theme)
        mapping = try? c.decode(String.self, forKey: .mapping)
        degradeReason = try? c.decode(String.self, forKey: .degradeReason)
        stocks = try? c.decode([NewsStock].self, forKey: .stocks)
        sourcePosts = try? c.decode([NewsSourcePost].self, forKey: .sourcePosts)
        signalQuality = try? c.decode(NewsSignalQuality.self, forKey: .signalQuality)
    }
}

/// 舆情热点：方向下挂的个股（龙头 / 二梯队）。
struct NewsStock: Codable, Hashable, Identifiable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var board: String?
    var tier: String?
    var theme: String?
    var sourcePosts: [NewsSourcePost]?

    enum CodingKeys: String, CodingKey {
        case symbol, name, board, tier, theme
        case sourcePosts = "source_posts"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        symbol = (try? c.decode(String.self, forKey: .symbol)) ?? ""
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        board = try? c.decode(String.self, forKey: .board)
        tier = try? c.decode(String.self, forKey: .tier)
        theme = try? c.decode(String.self, forKey: .theme)
        sourcePosts = try? c.decode([NewsSourcePost].self, forKey: .sourcePosts)
    }
}

/// 舆情热点：重大催化事件。
struct NewsCatalyst: Codable, Hashable, Identifiable {
    var id: String { "\(type)-\(title)" }
    var type: String
    var title: String
    var source: String?
    var url: String?
    var attachStocks: Bool?

    enum CodingKeys: String, CodingKey {
        case type, title, source, url
        case attachStocks = "attach_stocks"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decode(String.self, forKey: .type)) ?? ""
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        source = try? c.decode(String.self, forKey: .source)
        url = try? c.decode(String.self, forKey: .url)
        attachStocks = try? c.decode(Bool.self, forKey: .attachStocks)
    }
}

/// 舆情热点：信号质量（独立源 / 真实催化 / 映射）。
struct NewsSignalQuality: Codable, Hashable {
    var independentSources: Int?
    var hasRealCatalyst: Bool?
    var mapping: String?

    enum CodingKeys: String, CodingKey {
        case mapping
        case independentSources = "independent_sources"
        case hasRealCatalyst = "has_real_catalyst"
    }
}

/// 舆情热点：信息源帖（平台 + 标题 + 链接）。
struct NewsSourcePost: Codable, Hashable, Identifiable {
    var id: String { "\(source ?? "")-\(title)-\(url ?? "")" }
    var source: String?
    var title: String
    var url: String?

    enum CodingKeys: String, CodingKey { case source, title, url }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try? c.decode(String.self, forKey: .source)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        url = try? c.decode(String.self, forKey: .url)
    }
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
    var tier: String?   // core=核心垄断/双寡头 · main=国产替代主线(三家寡头深链)
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

// MARK: - 趋势页（日历）模型（bridge trends-month / trends-day 输出）

/// 北向净额（亿元，方向）。
struct TrendNorth: Codable, Hashable {
    var money: Double
    var unit: String
    var dir: String          // in / out / flat
}

/// 推荐后续 T+N 表现（百分比；缺/停牌为 nil）。
struct TrendFwd: Codable, Hashable {
    var t1: Double?
    var t5: Double?
    var t20: Double?
    var asof: String?        // 实际落点 trade_date，防按行偏移张冠李戴
}

/// 各类是否有数据，驱动热力格三态/标记。
struct TrendFlags: Codable, Hashable {
    var north: Bool = false
    var etf: Bool = false
    var sector: Bool = false
    var recs: Bool = false
}

struct TrendEtf: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String
    var pct: Double?
}

struct TrendSectorTheme: Codable, Hashable, Identifiable {
    var id: String { name }
    var name: String
    var grade: String
    var past5Ret: Double?
}

struct TrendRec: Codable, Hashable, Identifiable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var fwd: TrendFwd
}

/// 月度格子（驱动热力格底色 + 板块点 + 推荐微条）。
struct TrendDayCell: Codable, Hashable, Identifiable {
    var id: String { date }
    var date: String
    var isTrading: Bool = true
    var heat: Double?
    var inflowScore: Double?     // 增量资金合成强度(-1..1)，驱动顶部增量资金热力图
    var inflowDir: String?       // in / out / flat
    var sectorHeat: Double?
    var recAvgFwd: Double?
    var north: TrendNorth?
    var sectorCount: Int = 0
    var topSector: String?       // 当天最强主题名，日历格子直观显示
    var recCount: Int = 0
    var flags: TrendFlags = TrendFlags()
    var hasData: Bool = false
}

struct TrendMonth: Codable, Hashable {
    var month: String
    var days: [TrendDayCell]
}

/// 单日完整明细。
struct TrendDayDetail: Codable, Hashable {
    var date: String
    var found: Bool = false
    var isTrading: Bool = true
    var north: TrendNorth?
    var etfs: [TrendEtf]?
    var sectorTop: [TrendSectorTheme] = []
    var sectorCount: Int = 0
    var recs: [TrendRec] = []
    var recCount: Int = 0
    var recAvgFwd: Double?
    var heat: Double?
    var sectorHeat: Double?
    var flags: TrendFlags = TrendFlags()
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
    var category: String      // 数据更新 / 扫描选股 / 板块复盘 / 纸交易 / 校验回测 / 盘中快讯 / 系统 / 其他
    var schedule: String      // 人读调度，如「工作日 17:30」
    var script: String
    var enabled: Bool         // 是否启用（未被 launchctl disable）
    var needsInstall: Bool?   // 清单有但 ~/Library/LaunchAgents 未装 → 需同步（U4/R4）
    var loaded: Bool          // 是否已 bootstrap 到 gui 域
    var running: Bool         // 当前是否有进程在跑
    var lastStatus: String    // success / failed / unknown
    var lastRunAt: String?    // 上次运行时间（日志 mtime）
    var lastLine: String?     // 上次运行日志末行摘要
    var stale: Bool           // 应跑未跑（关机漏跑）
    var missedCycles: Int     // 漏跑了几个预定周期
    var expectedAt: String?   // 最近一次本该触发的时刻
    var nextRunAt: String?    // 下次预定触发时刻

    /// 综合健康态（监控用）：运行中 > 需同步 > 停用 > 漏跑 > 失败 > 正常。
    enum Health { case running, needsInstall, stale, failed, disabled, ok }
    var health: Health {
        if running { return .running }
        if needsInstall == true { return .needsInstall }
        if !enabled { return .disabled }
        if stale { return .stale }
        if lastStatus == "failed" { return .failed }
        return .ok
    }
}

/// cron-list 响应：任务列表 + 清单派生的分类排序（U4 下发，U5 任务页读 categoryOrder）。
struct CronListResponse: Codable, Hashable {
    var jobs: [ScheduledJob]
    var categoryOrder: [String]
}

/// cron-rerun / cron-enable / cron-disable 的返回。
struct CronActionResult: Codable, Hashable {
    var ok: Bool
    var error: String?
    var job: ScheduledJob?
}

/// cron-catchup / cron-rerun-many 批量结果。
struct CronBatchResult: Codable, Hashable {
    var ok: Bool
    var count: Int                  // 实际触发的任务数
    var ran: [CronBatchItem]
    var skipped: [String]

    struct CronBatchItem: Codable, Hashable, Identifiable {
        var id: String { label }
        var label: String
        var title: String
        var ok: Bool
        var error: String?
    }
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
    var latestOpen: Double?
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
    case themes = "Themes"
    case trends = "Trends"
    case reviews = "Reviews"
    case newsDigest = "News"
    case backtests = "Backtests"
    case stocks = "Stocks"
    case runbook = "Runbook"
    case aiChat = "AI Chat"
    case architecture = "Architecture"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .dashboard: return "今日总览"
        case .recommendations: return "推荐"
        case .watchlist: return "自选"
        case .themes: return "主题"
        case .trends: return "趋势观察"
        case .runbook: return "任务"
        case .reviews: return "AI复盘"
        case .newsDigest: return "舆情热点"
        case .backtests: return "AI回测"
        case .stocks: return "股票池"
        case .aiChat: return "Seesaw"
        case .architecture: return "架构"
        }
    }

    var symbol: String {
        switch self {
        case .dashboard: return "gauge.with.dots.needle.50percent"
        case .recommendations: return "target"
        case .watchlist: return "star"
        case .themes: return "square.grid.2x2"
        case .trends: return "calendar"
        case .runbook: return "terminal"
        case .reviews: return "doc.text.magnifyingglass"
        case .newsDigest: return "newspaper"
        case .backtests: return "chart.xyaxis.line"
        case .stocks: return "list.bullet.rectangle"
        case .aiChat: return "scale.3d"
        case .architecture: return "circle.hexagongrid"
        }
    }

    // MARK: 边栏排序（总览永久置顶，其余可拖拽重排）

    /// 永久置顶、不参与排序的 section。
    static let pinned: [WorkspaceSection] = [.dashboard]

    /// 暂时隐藏的 section：代码保留、不上侧栏。舆情 digest 未达预期,等改进方案定了再恢复
    /// （从本数组移除即重新显示）。enum case / NewsDigestView / ContentView 路由均完整保留。
    static let hidden: [WorkspaceSection] = [.newsDigest]

    /// 可被用户拖拽重排的 section（enum 原序，去掉置顶项与隐藏项）。
    static var reorderable: [WorkspaceSection] {
        allCases.filter { !pinned.contains($0) && !hidden.contains($0) }
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
                  !hidden.contains(section),
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

// MARK: - AI 复盘助手聊天模型（#4 U4/U5）

/// 一条聊天消息。会话历史归 KSSStore（不放 view @State，避免 .id(selectedSection) 销毁）。
struct ChatMessage: Identifiable, Equatable {
    enum Role { case user, assistant }
    let id = UUID()
    let role: Role
    var text: String
    var evidenceSummary: ChatEvidenceSummary = .empty
    var evidenceDrawer: ChatEvidenceDrawer = ChatEvidenceDrawer()
    /// 助手本轮自产数字是否还「未核实」（流式中为 true，done 守卫过转 false）。R7/KTD-5。
    var numbersUnverified: Bool = false
    /// 终态错误气泡样式（step-limit / 断连 等）。
    var isError: Bool = false
}

struct ChatEvidenceSummary: Codable, Equatable {
    var kssTruthCount: Int = 0
    var externalSourceCount: Int = 0
    var injectionWarningCount: Int = 0
    var conflictCount: Int = 0
    var provider: String?

    static let empty = ChatEvidenceSummary()

    var hasEvidence: Bool {
        kssTruthCount > 0 || externalSourceCount > 0 || injectionWarningCount > 0 || conflictCount > 0
    }

    mutating func merge(_ other: ChatEvidenceSummary) {
        kssTruthCount += other.kssTruthCount
        externalSourceCount += other.externalSourceCount
        injectionWarningCount += other.injectionWarningCount
        conflictCount += other.conflictCount
        if let provider = other.provider, !provider.isEmpty {
            self.provider = provider
        }
    }
}

struct ChatEvidenceDrawer: Codable, Equatable {
    var kssTruth: [ChatKSSTruthEvidence] = []
    var externalSources: [ChatExternalSourceEvidence] = []
    var warnings: [ChatEvidenceWarning] = []

    mutating func merge(_ other: ChatEvidenceDrawer) {
        kssTruth.append(contentsOf: other.kssTruth)
        externalSources.append(contentsOf: other.externalSources)
        warnings.append(contentsOf: other.warnings)
    }
}

struct ChatKSSTruthEvidence: Codable, Identifiable, Equatable {
    var label: String
    var tool: String
    var fields: [String]
    var provenance: String

    var id: String { "\(tool)-\(label)-\(fields.joined(separator: ","))" }
}

struct ChatExternalSourceEvidence: Codable, Identifiable, Equatable {
    var title: String
    var url: String
    var sourceTier: String
    var retrievedAt: String
    var cacheStatus: String
    var excerpt: String
    var usedFor: String?

    var id: String { "\(url)-\(retrievedAt)-\(title)" }
}

struct ChatEvidenceWarning: Codable, Identifiable, Equatable {
    var type: String
    var severity: String
    var message: String

    var id: String { "\(type)-\(severity)-\(message)" }
}

/// 流式帧（sidecar chat-turn handler 回的 newline-delimited JSON）。U3 协议。
struct ChatFrame: Decodable {
    let type: String
    let text: String?
    let name: String?            // tool_call / tool_done 的工具名
    let reason: String?          // done 的原因（stop / max_steps / timeout / error）
    let error: String?
    let callId: String?          // confirm_required
    let tool: String?
    let command: String?
    let effect: String?          // 人话效果（U5 modal 标题）
    let argsText: String?        // 写参数格式化串（Swift modal body）
    let numberGuard: NumberGuard?
    let evidenceSummary: ChatEvidenceSummary?
    let evidenceDrawer: ChatEvidenceDrawer?

    struct NumberGuard: Decodable { let unverified: [String]? }

    enum CodingKeys: String, CodingKey {
        case type, text, name, reason, error, tool, command, effect, argsText
        case callId = "call_id"
        case numberGuard, evidenceSummary, evidenceDrawer
    }
}

/// 待人工确认的写操作（人在环内闸，U5）。modal 显 effect + args。
struct PendingWriteConfirm: Identifiable {
    let id = UUID()
    let callId: String
    let tool: String
    let command: String
    let effect: String
    let argsText: String
    let contextLine: String      // loop 最近一句作上下文
}
