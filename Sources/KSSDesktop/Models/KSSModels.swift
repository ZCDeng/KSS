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
struct NewsDigestResponse: Codable {
    var available: Bool
    var index: [NewsDigestIndexEntry]
    var selected: NewsDigest?
    var tracks: [IntelTrack]?     // U2 multi-track grouping (U1 extended bridge)
    var generatedAt: String?      // intel-radar: "YYYY-MM-DD HH:MM" 生成时间
    var recentDays: Int?          // intel-radar: 抓取天数范围
    var stats: RadarStats?        // intel-radar: 源统计
}

/// intel-radar 源统计。
struct RadarStats: Codable {
    var industries: Int?
    var totalSources: Int?
    var failedSources: Int?

    enum CodingKeys: String, CodingKey {
        case industries
        case totalSources = "total_sources"
        case failedSources = "failed_sources"
    }
}

/// 舆情热点：可选档案条目（日期 + 场景），newest first。
struct NewsDigestIndexEntry: Codable, Hashable, Identifiable {
    var id: String { "\(date)-\(scene)" }
    var date: String
    var scene: String
}

/// 资讯雷达单赛道 AI digest 响应（plan 2026-07-09-001 + 2026-07-10 pool mode）。
struct IntelDigestResponse: Codable {
    var text: String
    var model: String?
    var generatedAt: String?
    var prompt: String?
    var itemCount: Int?
    /// 错误时 text 为空、error 非空
    var error: String?
    var errorType: String?
    /// 缓存命中（已存在沉淀）→ true
    var fromCache: Bool?
    var cachedPath: String?
    /// items 为空跳过
    var skipped: Bool?
    /// ``pool`` = 改写池聚合；``list`` = 旧列表提炼（plan 2026-07-10）
    var mode: String?

    enum CodingKeys: String, CodingKey {
        case text
        case model
        case generatedAt = "generated_at"
        case prompt
        case itemCount = "item_count"
        case error
        case errorType = "error_type"
        case fromCache = "from_cache"
        case cachedPath = "cached_path"
        case skipped
        case mode
    }

    var isPoolMode: Bool { mode == "pool" }
}

/// 资讯雷达正文抓取响应（U4/U5）。
struct IntelArticleResponse: Codable {
    var body: String?
    var title: String?
    var mode: String?
    var error: String?
    var charCount: Int?
    var url: String?

    enum CodingKeys: String, CodingKey {
        case body, title, mode, error, url
        case charCount = "char_count"
    }
}

/// 资讯雷达投研改写响应。
struct IntelRewriteResponse: Codable {
    var itemId: String?
    var trackKey: String?
    var status: String?
    var text: String?
    var sections: [String: String]?
    var model: String?
    var generatedAt: String?
    var bodyText: String?
    var bodyMode: String?
    var bodyCharCount: Int?
    var error: String?
    var errorType: String?
    var fromCache: Bool?

    enum CodingKeys: String, CodingKey {
        case text, sections, model, status, error
        case itemId = "item_id"
        case trackKey = "track_key"
        case generatedAt = "generated_at"
        case bodyText = "body_text"
        case bodyMode = "body_mode"
        case bodyCharCount = "body_char_count"
        case errorType = "error_type"
        case fromCache = "from_cache"
    }
}

/// Top-K rewrite worker summary.
struct IntelRewriteRunResponse: Codable {
    var day: String?
    var k: Int?
    var tracks: Int?
    var attempted: Int?
    var readyNew: Int?
    var failed: Int?
    var skipped: Int?
    var stoppedReason: String?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case day, k, tracks, attempted, failed, skipped, error
        case readyNew = "ready_new"
        case stoppedReason = "stopped_reason"
    }
}

/// 资讯雷达 digest 写入沉淀库响应。
struct IntelDigestSaveResponse: Codable {
    var ok: Bool
    var savedPath: String?
    var error: String?
    var errorType: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case savedPath = "saved_path"
        case error
        case errorType = "error_type"
    }
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
    var instHolding: String?   // 机构持仓动态(机构占比+增减+北向)；缓存未命中=空
    var instRatio: Double?     // 机构类合计持仓占比 %；缺=null
    var usPeerTicker: String?  // 对标美股代码(如 LRCX)；空=无对标
    var usPeerName: String?    // 对标美股名
    var usPeerPe: Double?      // 对标美股 PE(缓存)；缺=null
}

// MARK: - 紫苏叶个股富化（机构持仓 / PE 分位 / 美股对标，bridge perilla-enrichment）

/// 顶层富化 payload。``status`` ∈ {ok, not_in_perilla_list, unavailable, invalid_symbol}。
struct PerillaEnrichment: Codable, Hashable {
    var symbol: String
    var name: String?
    var tier: String?
    var status: String
    var institutional: PerillaInstitutional?
    var valuationPe: PerillaPE?
    var usPeer: PerillaUsPeer?

    enum CodingKeys: String, CodingKey {
        case symbol, name, tier, status, institutional
        case valuationPe = "valuation_pe"
        case usPeer = "us_peer"
    }
}

/// 机构持仓块；整块失败时仅有 status，成功时挂 top10 / northbound。
struct PerillaInstitutional: Codable, Hashable {
    var status: String?
    var top10: PerillaTop10?
    var northbound: PerillaNorthbound?
}

struct PerillaTop10: Codable, Hashable {
    var status: String
    var latestPeriod: String?
    var nHolders: Int?
    var nIncreasing: Int?
    var nDecreasing: Int?
    var netDirection: String?
    var top10Ratio: Double?   // 前十大流通股东合计占比 %
    var instRatio: Double?    // 机构类合计占比 %

    enum CodingKeys: String, CodingKey {
        case status
        case latestPeriod = "latest_period"
        case nHolders = "n_holders"
        case nIncreasing = "n_increasing"
        case nDecreasing = "n_decreasing"
        case netDirection = "net_direction"
        case top10Ratio = "top10_ratio"
        case instRatio = "inst_ratio"
    }
}

struct PerillaNorthbound: Codable, Hashable {
    var status: String
    var latestPeriod: String?
    var holdRatio: Double?
    var qoqChange: Double?
    var direction: String?

    enum CodingKeys: String, CodingKey {
        case status, direction
        case latestPeriod = "latest_period"
        case holdRatio = "hold_ratio"
        case qoqChange = "qoq_change"
    }
}

struct PerillaPE: Codable, Hashable {
    var status: String
    var peTtm: Double?
    var percentile: Double?
    var nPoints: Int?
    var asOf: String?

    enum CodingKeys: String, CodingKey {
        case status, percentile
        case peTtm = "pe_ttm"
        case nPoints = "n_points"
        case asOf = "as_of"
    }
}

/// 美股对标块；``status`` ∈ {ok, no_peer, unavailable}。
struct PerillaUsPeer: Codable, Hashable {
    var status: String
    var ticker: String?
    var name: String?
    var peerPe: Double?
    var peerMarketCapUsd: Double?
    var peRatioAOverPeer: Double?
    var peerToAMcapMultiple: Double?
    var mcapMultipleStatus: String?

    enum CodingKeys: String, CodingKey {
        case status, ticker, name
        case peerPe = "peer_pe"
        case peerMarketCapUsd = "peer_market_cap_usd"
        case peRatioAOverPeer = "pe_ratio_a_over_peer"
        case peerToAMcapMultiple = "peer_to_a_mcap_multiple"
        case mcapMultipleStatus = "mcap_multiple_status"
    }
}

/// 总览第一行市场速览：A500ETF 当日行情 + 北向资金净流入。
struct MarketStrip: Codable, Hashable {
    var date: String?
    var northMoney: Double?
    var northDate: String?
    var etfs: [ETFQuote]
    var indices: [IndexQuote]?
    var indexBoard: [IndexQuote]?
    var limitBoard: LimitBoard?      // U4 短线情绪（连板梯队/封板率）
    var turnoverTop: [TurnoverTop]?  // U5a 成交额 TOP20
    var globalIndices: [GlobalIndex]? // U5b 全球隔夜指数
}

struct LimitBoard: Codable, Hashable {
    var maxBoard: Int?
    var tiers: [LimitTier]?
    var total: Int?
    var sealRate: Double?
    var breakRate: Double?
}

struct LimitTier: Codable, Hashable {
    var level: Int
    var count: Int
}

struct TurnoverTop: Codable, Hashable, Identifiable {
    var id: String { code ?? "" }
    var code: String?
    var name: String?
    var close: Double?
    var pctChange: Double?
    var volume: Double?
    var amount: Double?
}

struct GlobalIndex: Codable, Hashable {
    var code: String?
    var name: String?
    var close: Double?
    var pctChange: Double?
    var date: String?
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

/// cron-sync 的返回。
struct CronSyncResponse: Codable, Hashable {
    var ok: Bool
    var error: String?
    var notices: [String]?
    var jobs: [ScheduledJob]?
    var categoryOrder: [String]?
    var plan: CronSyncPlan?

    struct CronSyncPlan: Codable, Hashable {
        var install: [String]
        var update: [String]
        var stale: [String]
        var aligned: [String]
    }
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
        case .dashboard: return "今日看盘"
        case .recommendations: return "推荐"
        case .watchlist: return "自选"
        case .themes: return "主题"
        case .trends: return "趋势观察"
        case .runbook: return "任务"
        case .reviews: return "AI复盘"
        case .newsDigest: return "资讯雷达"
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
        case .newsDigest: return "antenna.radiowaves.left.and.right"
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
    static let hidden: [WorkspaceSection] = []

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
    /// U4: 分钟 K 线附件（intraday-snapshot 工具返回后渲染 K 线 bubble）
    var chartAttachment: ChartAttachment? = nil
}

/// U4: 聊天气泡内的分钟 K 线附件（R8: Seesaw 工具返回含 bar 数据时渲染 R6 组件）。
struct ChartAttachment: Codable, Equatable {
    var symbol: String?
    var intervalMinutes: Int?
    var bars: [OHLCBar]
    var sourceAsofTs: String?
    var eligibility: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case intervalMinutes = "interval_minutes"
        case bars
        case sourceAsofTs = "source_asof_ts"
        case eligibility
    }
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

// MARK: - U2 资讯雷达模型（bridge news-digest U1 扩展多赛道字段）

/// 赛道（多赛道分组新闻源）。
struct IntelTrack: Codable, Equatable {
    var key: String
    var name: String
    var accent: String?
    var items: [IntelItem]?
    var total: Int?
}

/// 赛道内单条资讯。
struct IntelItem: Codable, Equatable, Identifiable {
    var id: String { url ?? title }
    var title: String
    var url: String?
    var time: String?
    var source: String?
    var summary: String?
}

/// Dashboard 资讯摘要条带（U3 轻量数据）。
struct IntelSummary: Codable, Equatable {
    var updatedTrackCount: Int
    var recentTitles: [String]
}

// MARK: - Longbridge 实时（U1）—— bridge longbridge-quote / intraday-snapshot / intraday-bars / trading-hours 输出

/// 实时快照（bridge `longbridge-quote`）。R1/R12——数字为 bridge 真值字段，直接渲染不经 LLM。
/// 覆盖失败/非陆股通时 `error` 非空、数值字段 nil，UI 据此回退存量 + 标注"非实时"。
struct LongbridgeQuote: Codable, Hashable {
    var symbol: String?
    var lastDone: Double?
    var prevClose: Double?
    var open: Double?
    var high: Double?
    var low: Double?
    var volume: Double?
    var turnover: Double?
    var tradeStatus: String?
    var sourceAsofTs: String?
    var eligibility: String?
    var routedProvider: String?
    var manifestStale: Bool?
    var error: String?
    var hint: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case lastDone = "last_done"
        case prevClose = "prev_close"
        case open, high, low, volume, turnover
        case tradeStatus = "trade_status"
        case sourceAsofTs = "source_asof_ts"
        case eligibility
        case routedProvider = "routed_provider"
        case manifestStale = "manifest_stale"
        case error, hint
    }

    /// 是否拿到实时（无 error 且有价）。UI 据此决定展示实时 vs 回退存量。
    var isLive: Bool { error == nil && lastDone != nil }
}

/// 单根 OHLCV bar（intraday-snapshot 的 `bar` / intraday-bars 的 `bars[]` 元素）。
struct OHLCBar: Codable, Hashable {
    var timestamp: String?
    var open: Double?
    var high: Double?
    var low: Double?
    var close: Double?
    var volume: Double?
    var turnover: Double?
}

/// 最新分钟 bar 快照（bridge `intraday-snapshot`）。R2。
struct IntradaySnapshot: Codable, Hashable {
    var symbol: String?
    var intervalMinutes: Int?
    var bar: OHLCBar?
    var sourceAsofTs: String?
    var eligibility: String?
    var routedProvider: String?
    var manifestStale: Bool?
    var error: String?
    var hint: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case intervalMinutes = "interval_minutes"
        case bar
        case sourceAsofTs = "source_asof_ts"
        case eligibility
        case routedProvider = "routed_provider"
        case manifestStale = "manifest_stale"
        case error, hint
    }
}

/// 完整日内 bar 序列（bridge `intraday-bars`，K 线图渲染消费）。R2/R6/F006。
struct IntradayBars: Codable, Hashable {
    var symbol: String?
    var intervalMinutes: Int?
    var bars: [OHLCBar]
    var sourceAsofTs: String?
    var eligibility: String?
    var routedProvider: String?
    var manifestStale: Bool?
    var error: String?
    var hint: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case intervalMinutes = "interval_minutes"
        case bars
        case sourceAsofTs = "source_asof_ts"
        case eligibility
        case routedProvider = "routed_provider"
        case manifestStale = "manifest_stale"
        case error, hint
    }

    /// K 线可渲染（无 error 且有序列）。R15 empty/error 状态据此判定。
    var isRenderable: Bool { error == nil && !bars.isEmpty }
}

/// 交易时段查询（bridge `trading-hours`，门控实时拉取 / 定时器）。R13/F007。
struct TradingHours: Codable, Hashable {
    var isTradeDay: Bool
    var isTradingSession: Bool
    var sessionEnd: String?
    var now: String?

    enum CodingKeys: String, CodingKey {
        case isTradeDay = "is_trade_day"
        case isTradingSession = "is_trading_session"
        case sessionEnd = "session_end"
        case now
    }
}
