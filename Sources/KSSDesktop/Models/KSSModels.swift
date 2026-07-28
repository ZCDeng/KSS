import Foundation

struct AppSnapshot: Codable {
    var generatedAt: String
    var projectRoot: String
    var latestDataDate: String?
    var stockCount: Int
    var recommendationDate: String?
    /// R6 R5：推荐执行日（数据日的下一交易日，bridge 真值）；日历失败为 nil。
    var recommendationExecutionDate: String?
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

extension AppSnapshot {
    /// 推荐区双日期标注（R6 R5）：「{执行日} 执行 · 基于 {数据日} 收盘数据」。
    /// 执行日缺失（日历失败）退化为单数据日；两者皆缺为 nil。
    var recommendationSubtitle: String? {
        guard let data = recommendationDate, !data.isEmpty else { return nil }
        guard let exec = recommendationExecutionDate, !exec.isEmpty else { return data }
        return "\(exec) 执行 · 基于 \(data) 收盘数据"
    }
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
    /// yupi 旁路灌入结果（bridge `stats.yupi`）；旧缓存无此字段。
    var yupi: RadarYupiStats?

    enum CodingKeys: String, CodingKey {
        case industries, yupi
        case totalSources = "total_sources"
        case failedSources = "failed_sources"
    }
}

/// 资讯雷达 stats.yupi：热议灌入是否成功 + 条数。
struct RadarYupiStats: Codable, Equatable {
    var ok: Bool?
    var skipped: Bool?
    var reason: String?
    var items: Int?
    var error: String?

    /// 顶栏/状态行短文案。
    var badgeText: String {
        if ok == true {
            let n = items ?? 0
            return n > 0 ? "热议 \(n)" : "热议 0"
        }
        if skipped == true {
            let r = (reason ?? error ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if r.contains("not installed") { return "热议未装" }
            if r.lowercased().contains("health") { return "热议离线" }
            return r.isEmpty ? "热议跳过" : "热议跳过"
        }
        if let r = reason, !r.isEmpty { return "热议失败" }
        if let e = error, !e.isEmpty { return "热议失败" }
        return "热议—"
    }

    var isHealthy: Bool { ok == true }
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

/// 全景热点单赛道采样（bridge intel-panorama 入参）。
struct IntelPanoramaTrackInput: Codable {
    var key: String
    var name: String
    var titles: [String]
}

/// 12 赛道全景热点响应。
struct IntelPanoramaResponse: Codable {
    var text: String
    var model: String?
    var generatedAt: String?
    var trackCount: Int?
    var error: String?
    var errorType: String?

    enum CodingKeys: String, CodingKey {
        case text, model, error
        case generatedAt = "generated_at"
        case trackCount = "track_count"
        case errorType = "error_type"
    }
}

/// 资讯雷达正文抓取响应（U4/U5）。
struct IntelArticleResponse: Codable {
    var body: String?
    var title: String?
    var mode: String?
    var error: String?
    var charCount: Int?
    var url: String?
    /// 结构化正文（markdown-lite：## 小标题 / - 列表 / 空行分段；plan 2026-07-22-001）
    var bodyMd: String? = nil
    var extractor: String? = nil
    var cached: Bool? = nil

    enum CodingKeys: String, CodingKey {
        case body, title, mode, error, url, extractor, cached
        case charCount = "char_count"
        case bodyMd = "body_md"
    }
}

/// 资讯雷达改写响应（投研 / 中文改写共用）。
struct IntelRewriteResponse: Codable {
    var itemId: String?
    var trackKey: String?
    var kind: String?
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
        case text, sections, model, status, error, kind
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

/// 详情阅读 Tab（qmreader 风；首 Tab / 默认 = 投研改写）。
/// 中文改写独立 Tab 已下线（plan 2026-07-22-001 R10）：译文并入原文 Tab 按需生成。
enum IntelReaderTab: String, CaseIterable, Identifiable {
    case investment
    case original

    var id: String { rawValue }

    var label: String {
        switch self {
        case .investment: return "投研改写"
        case .original: return "原文"
        }
    }

    /// bridge kind for rewrite tabs
    var rewriteKind: String? {
        switch self {
        case .original: return nil
        case .investment: return "investment"
        }
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
    /// 隔夜美股跑马灯（固定名单顺序；refresh_market_strip 写入）
    var overnightUS: [IndexQuote]?
    /// 第二行三列指数堆叠（含 sparkline）
    var indexStacks: [IndexStackColumn]?
    /// 指标小卡 resolved props（bridge 读时注入）
    var stripMetric: StripMetricProps? = nil
    /// L3 surface 配置摘要
    var surfaceConfig: SurfaceConfigSnapshot? = nil
}

/// 盯盘指标小卡 props（代码 resolve，非 LLM）。
struct StripMetricProps: Codable, Hashable {
    var metricId: String?
    var title: String?
    var value: Double?
    var valueText: String?
    var delta: Double?
    var deltaText: String?
    var sub: String?
    var reason: String?

    enum CodingKeys: String, CodingKey {
        case title, value, delta, sub, reason
        case metricId = "metric_id"
        case valueText = "valueText"
        case deltaText = "deltaText"
    }
}

/// surface 配置摘要（挂在 marketStrip 上）。
struct SurfaceConfigSnapshot: Codable, Hashable {
    var overnightAppend: [SurfaceAppendItem]?
    var stripMetricId: String?
    var degraded: Bool?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case overnightAppend, stripMetricId, degraded, error
    }
}

struct SurfaceAppendItem: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String?
    var kind: String?
    var kindSource: String?
    var probeClose: Double?

    enum CodingKeys: String, CodingKey {
        case code, name, kind
        case kindSource = "kind_source"
        case probeClose = "probe_close"
    }
}

/// surface-get / apply 响应。
struct SurfaceGetResponse: Codable, Hashable {
    var ok: Bool?
    var config: SurfaceConfigBody?
    var candidates: [SurfaceCandidate]?
    var metrics: [SurfaceMetricInfo]?
    var stripMetric: StripMetricProps?
    var error: String?
}

struct SurfaceConfigBody: Codable, Hashable {
    var overnightUs: SurfaceOvernightBody?
    var stripMetric: SurfaceStripMetricBody?

    enum CodingKeys: String, CodingKey {
        case overnightUs = "overnight_us"
        case stripMetric = "strip_metric"
    }
}

struct SurfaceOvernightBody: Codable, Hashable {
    var append: [SurfaceAppendItem]?
}

struct SurfaceStripMetricBody: Codable, Hashable {
    var metricId: String?
    enum CodingKeys: String, CodingKey { case metricId = "metric_id" }
}

struct SurfaceCandidate: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String?
    var kind: String?
}

struct SurfaceMetricInfo: Codable, Hashable, Identifiable {
    var id: String { metricId }
    var metricId: String
    var title: String?
    var description: String?

    enum CodingKeys: String, CodingKey {
        case title, description
        case metricId = "metric_id"
    }
}

struct SurfaceApplyResponse: Codable, Hashable {
    var ok: Bool?
    var error: String?
    var stripMetric: StripMetricProps?
}

/// 一列堆叠（main / growth / hk）
struct IndexStackColumn: Codable, Hashable, Identifiable {
    var id: String
    var items: [IndexStackItem]
}

struct IndexStackItem: Codable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String
    var close: Double
    var pct: Double
    var date: String?
    var source: String?
    /// 当日 1m 收盘点；仅 c 字段
    var sparkline: [SparkPoint]?
}

struct SparkPoint: Codable, Hashable {
    var c: Double
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
    /// 用户 surface 追加项（bridge 读时注入）；默认项为 nil/false
    var isUserAppended: Bool? = nil
    var pending: Bool? = nil
    var kindSource: String? = nil
    var probeClose: Double? = nil
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
    /// 主题代表 ETF 码（篮子首只，R5）：UI 据此挂 Longbridge 实时涨跌。
    var etfCode: String?
}

/// 定时任务（launchd）一项：deploy/launchd/*.plist + launchctl 状态 + 日志末行。
/// 排期结构化字段（bridge `cron-list` 的 `scheduleStruct`，plan 2026-07-12-005 / U6）。
/// 供排期编辑器读初值——避免解析人读 `schedule` 文案的脆弱往返。
struct ScheduleStruct: Codable, Hashable {
    var hour: Int
    var minute: Int
    var weekdays: [Int]?   // daily 形态的工作日子集；nil＝每天
    var weekly: Bool
    var weekday: Int?      // weekly 形态的单一 weekday（launchd 1-7）

    /// 序列化为 bridge `cron-edit-schedule` 期望的 schedule JSON。
    func toScheduleJSON() -> String {
        var payload: [String: Any] = [:]
        if weekly {
            payload["weekly"] = ["weekday": weekday ?? 1, "hour": hour, "minute": minute]
        } else {
            payload["hour"] = hour
            payload["minute"] = minute
            if let weekdays, !weekdays.isEmpty {
                payload["weekdays"] = weekdays
            }
        }
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return json
    }
}

struct ScheduledJob: Codable, Identifiable, Hashable {
    var id: String { label }
    var label: String
    var title: String
    var category: String      // 数据更新 / 扫描选股 / 板块复盘 / 纸交易 / 校验回测 / 盘中快讯 / 系统 / 其他
    var schedule: String      // 人读调度，如「工作日 17:30」
    var scheduleStruct: ScheduleStruct?  // 编辑器初值（U6）；旧 sidecar 未带该字段时为 nil
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
    /// 事件驱动链上游 suffix（plan 2026-07-14-001 / R5）；非链成员为 nil。
    /// schedule 人读文案已由 bridge 拼好触发关系，此字段供样式区分用。
    var triggeredBy: String? = nil

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

/// watchlist-set 的返回（plan 2026-07-12-005 / U15：自选列表写 kss.db）。
struct WatchlistSetResult: Codable, Hashable {
    var ok: Bool
    var symbols: [String]
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
    /// MI 滚动信号（Signal Pack 投影；缺省为 nil）
    var miSignal: MISignal?
    /// 图表 overlay 载荷（与 miSignal 同源 pack）
    var miOverlay: MIOverlay?
    /// 通用指标信号数组（plan 2026-07-12-004 U6，含任意已注册基元库指标；MI 也会在这里出现一份，
    /// 与 miSignal 并存不冲突——additive 字段，不 bump BRIDGE_SCHEMA_VERSION）
    var indicatorSignals: [IndicatorSignal]?
    /// 通用图表 overlay 数组，与 indicatorSignals 同源 pack
    var indicatorOverlays: [IndicatorOverlay]?
}

/// 自选个股详情 · MI 研究级卡片字段
struct MISignal: Codable, Hashable {
    var asof: String?
    var status: String?
    var reason: String?
    var action: String?
    var prevAction: String?
    var position: String?
    var predScore: Double?
    var predBias: String?
    var n: Int?
    var unpinned: Bool?
    var entry: String?
    /// 退出规则名（避免属性名 `exit` 与 Darwin.exit 冲突导致诊断失败）
    var exitRule: String?
    var filter: String?
    var close: Double?
    var mi: Double?
    var miZ: Double?
    var adx: Double?
    var execNote: String?

    enum CodingKeys: String, CodingKey {
        case asof, status, reason, action, position, n, unpinned, entry, filter, close, mi, adx
        case prevAction = "prev_action"
        case predScore = "pred_score"
        case predBias = "pred_bias"
        case miZ = "mi_z"
        case execNote = "exec_note"
        case exitRule = "exit"
    }
}

/// 透传给 chart.html 的 overlay（markers 等用 AnyCodable 太重 → 用 JSON 字典字串由 bridge 原样传也可）
/// 这里用轻量结构 + 可选 markers 数组。
struct MIOverlay: Codable, Hashable {
    var status: String?
    var reason: String?
    var banner: MIBanner?
    var badge: MIBadge?
    var markers: [MIMarker]?
    var mi: [MIPoint]?
}

struct MIBanner: Codable, Hashable {
    var action: String?
    var reason: String?
    var predScore: Double?
    var unpinned: Bool?

    enum CodingKeys: String, CodingKey {
        case action, reason, unpinned
        case predScore = "pred_score"
    }
}

struct MIBadge: Codable, Hashable {
    var n: Int?
    var entry: String?
    var exitRule: String?
    var filter: String?
    var asof: String?
    var unpinned: Bool?

    enum CodingKeys: String, CodingKey {
        case n, entry, filter, asof, unpinned
        case exitRule = "exit"
    }
}

struct MIMarker: Codable, Hashable {
    var time: String
    var position: String?
    var color: String?
    var shape: String?
    var text: String?
}

struct MIPoint: Codable, Hashable {
    var time: String
    var value: Double
}

/// 通用指标信号（plan 2026-07-12-004 U6）：对齐 bridge `to_signal()` 输出字段名（camelCase）。
/// 覆盖任意注册表条目（预注册基元族 或 MI legacy），不专属某一个指标。
struct IndicatorSignal: Codable, Hashable, Identifiable {
    var id: String { indicatorId ?? "unknown" }
    var indicatorId: String? = nil
    var asof: String? = nil
    var status: String? = nil
    var reason: String? = nil
    var action: String? = nil
    var prevAction: String? = nil
    var position: String? = nil
    var predScore: Double? = nil
    var predBias: String? = nil
    var family: String? = nil
    var unpinned: Bool? = nil
    var ruleSentence: String? = nil
    var execNote: String? = nil
    // 属性名与 bridge to_signal() 的 camelCase JSON 键逐字一致，不需要自定义 CodingKeys。
    // params/paramDelta/tradesPreview/close 等字段暂不解码（v1 展示范围内不需要）。
}

/// 通用图表 overlay（对齐 bridge `to_overlay()` 输出）；markers 复用 MIMarker 形状（字段一致）。
struct IndicatorOverlay: Codable, Hashable, Identifiable {
    var id: String { indicatorId ?? "unknown" }
    var indicatorId: String? = nil
    var status: String? = nil
    var reason: String? = nil
    var markers: [MIMarker]? = nil
    /// 族内主线（ma_fast/rsi/boll_mid 等，字段名随族而变）；解码时取首个非 date 数值字段，
    /// 归一成 {date, value}——chart.html 收到后按同样规则（取首个数值字段）取值，双端一致。
    var series: [IndicatorSeriesPoint]? = nil
    /// S/R 位（plan 2026-07-20-001 KTD1/KTD4）：主图水平线，独立于 markers/series 通道；
    /// additive 字段，不 bump BRIDGE_SCHEMA_VERSION——旧载荷缺此字段解码不受影响。
    var levels: [SRLevel]? = nil
}

/// 支撑/阻力位（bridge `sr_levels.to_levels_overlay()` 投影）。
struct SRLevel: Codable, Hashable {
    var price: Double
    var kind: String  // "support" | "resistance"
    var strength: Double
    var touches: Int
}

/// 会话开场确定性候选建议（plan 2026-07-12-004 U9）：对齐 bridge `indicator-suggest` 输出.
/// ``family == nil`` 表示无候选（基元库已覆盖或均在 NO-GO 记忆内）——Seesaw 空态不显示 chip。
struct IndicatorSuggestion: Codable {
    var family: String?
    var reason: String?
    var suggestedSymbols: [String]?
}

/// 通用指标主线的一个点：动态字段名 → 归一 {date, value}（族内 series 字段名不固定，
/// 如 ma_cross 的 ma_fast/ma_slow、rsi_threshold 的 rsi、boll_atr 的 boll_upper/mid/lower）。
struct IndicatorSeriesPoint: Codable, Hashable {
    var date: String
    var value: Double?

    private struct DynamicKey: CodingKey {
        var stringValue: String
        init?(stringValue: String) { self.stringValue = stringValue }
        var intValue: Int? { nil }
        init?(intValue: Int) { return nil }
    }

    init(date: String, value: Double?) {
        self.date = date
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DynamicKey.self)
        var parsedDate = ""
        var parsedValue: Double?
        for key in container.allKeys {
            if key.stringValue == "date" {
                parsedDate = (try? container.decode(String.self, forKey: key)) ?? ""
            } else if parsedValue == nil {
                parsedValue = try? container.decode(Double.self, forKey: key)
            }
        }
        date = parsedDate
        value = parsedValue
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: DynamicKey.self)
        try container.encode(date, forKey: DynamicKey(stringValue: "date")!)
        if let value {
            try container.encode(value, forKey: DynamicKey(stringValue: "value")!)
        }
    }
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
    /// R6 R8：当日未收盘实时 bar 标记（仅展示层拼接产生；bridge EOD payload 无此键）。
    var provisional: Bool?
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
    case investmentAnalysis = "Investment Analysis"
    case newsDigest = "News"
    case backtests = "Backtests"
    case stocks = "Stocks"
    case runbook = "Runbook"
    case aiChat = "AI Chat"
    case architecture = "Architecture"
    case settings = "Settings"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .dashboard: return "盯盘"
        case .recommendations: return "推荐"
        case .watchlist: return "自选"
        case .themes: return "主题"
        case .trends: return "趋势观察"
        case .runbook: return "任务台"
        case .reviews: return "AI复盘"
        case .investmentAnalysis: return "投资分析"
        case .newsDigest: return "资讯雷达"
        case .backtests: return "AI回测"
        case .stocks: return "股票池"
        case .aiChat: return "Seesaw"
        case .architecture: return "架构"
        case .settings: return "设置"
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
        case .investmentAnalysis: return "doc.text.image"
        case .newsDigest: return "antenna.radiowaves.left.and.right"
        case .backtests: return "chart.xyaxis.line"
        case .stocks: return "list.bullet.rectangle"
        case .aiChat: return "scale.3d"
        case .architecture: return "circle.hexagongrid"
        case .settings: return "gearshape"
        }
    }

    // MARK: 边栏排序（总览永久置顶，其余可拖拽重排）

    /// 永久置顶、不参与排序的 section。
    static let pinned: [WorkspaceSection] = [.dashboard]

    /// 不上侧栏的 section：代码、视图、路由均完整保留。两种排除原因不同——
    /// 暂停类（如曾经的舆情 digest）是"未达预期，等改进方案定了再恢复，从本数组移除即重新显示"；
    /// 任务/架构/Seesaw/设置 属于永久挪走类（改到工具栏/侧边栏页脚），不预期再回到侧边栏导航列表。
    static let hidden: [WorkspaceSection] = [.themes, .runbook, .architecture, .aiChat, .settings]

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

/// A narrowly scoped navigation request into the Seesaw workspace. Provider
/// credentials belong to the chat product surface, not global application
/// settings, so self-checks use this instead of a SettingsCategory case.
enum SeesawDestination: Equatable {
    case conversation
    case models
}

// MARK: - 设置页 Tab（R2-U4 Tab 化；R4 合并为两 tab：凭证与数据源 / 任务与日志）

enum SettingsTab: String, CaseIterable, Identifiable {
    case credentials, operations
    var id: String { rawValue }

    var label: String {
        switch self {
        case .credentials: return "数据源与凭证"
        case .operations: return "任务与日志"
        }
    }

    // 旧四 tab 深链值兼容（R2 时代的调用点语义）：keys/dataSources → credentials，
    // scheduledTasks/logs → operations。
    static let keys: SettingsTab = .credentials
    static let dataSources: SettingsTab = .credentials
    static let scheduledTasks: SettingsTab = .operations
    static let logs: SettingsTab = .operations

    /// 经典两 Tab 深链时的默认 Category（xcom master-detail 落点）。
    var defaultCategory: SettingsCategory {
        switch self {
        case .credentials: return .selfCheck
        case .operations: return .tasks
        }
    }
}

/// xcom 设置左栏分类原子（plan 2026-07-23-003）。顺序即 `allCases` 展示序。
enum SettingsCategory: String, CaseIterable, Identifiable, Hashable {
    case selfCheck
    case tushare
    case longbridge
    case telegram
    case yupi
    case tasks
    case logs

    var id: String { rawValue }

    var label: String {
        switch self {
        case .selfCheck: return "自检"
        case .tushare: return "Tushare"
        case .longbridge: return "Longbridge"
        case .telegram: return "Telegram"
        case .yupi: return "资讯雷达"
        case .tasks: return "任务"
        case .logs: return "日志"
        }
    }

    /// 投影到经典两 Tab。
    var tab: SettingsTab {
        switch self {
        case .selfCheck, .tushare, .longbridge, .telegram, .yupi:
            return .credentials
        case .tasks, .logs:
            return .operations
        }
    }
}

// SettingsCategory ↔ SettingsDataSource 映射见 SettingsView（SettingsDataSource 定义在该文件）。

enum SettingsTabRouting {
    /// 自检 fail 项 → 目标 tab（经典壳）：凭证/数据源类落凭证 tab；任务类落 operations。
    static func targetTab(forSelfCheckItem item: String) -> SettingsTab {
        targetCategory(forSelfCheckItem: item).tab
    }

    /// 自检项 → xcom 左栏 Category（细粒度深链）。
    static func targetCategory(forSelfCheckItem item: String) -> SettingsCategory {
        switch item {
        case "tushare":
            return .tushare
        case "longbridge", "intraday_secrets":
            return .longbridge
        case "telegram":
            return .telegram
        case "llm", "openrouter", "yupi":
            // LLM 具体配置已迁到 Seesaw 的模型页面；Settings 保留自检。
            return item == "yupi" || item == "openrouter" ? .yupi : .selfCheck
        case "scheduled", "cron", "launchd", "jobs":
            return .tasks
        default:
            return .selfCheck
        }
    }

    /// 凭证 tab 状态点——任一数据源未配置，或最近一次连通性测试失败。
    static func dataSourcesNeedsBadge(configured: [Bool], testsOK: [Bool]) -> Bool {
        configured.contains(false) || testsOK.contains(false)
    }

    /// 任务与日志 tab 状态点——任一任务 needsInstall / stale / failed。
    static func scheduledTasksNeedsBadge(jobs: [ScheduledJob]) -> Bool {
        jobs.contains { $0.health == .needsInstall || $0.health == .stale || $0.health == .failed }
    }

    /// 左栏单分类是否需要角标（xcom nav）。
    /// `sourceRaw` 为 `SettingsDataSource.rawValue`（tushare/longbridge/telegram）。
    static func categoryNeedsBadge(
        _ category: SettingsCategory,
        isSourceConfigured: (String) -> Bool,
        testOK: (String) -> Bool?,
        jobs: [ScheduledJob]
    ) -> Bool {
        switch category {
        case .selfCheck, .yupi, .logs:
            return false
        case .tushare, .longbridge, .telegram:
            let raw = category.rawValue
            if !isSourceConfigured(raw) { return true }
            if let ok = testOK(raw), !ok { return true }
            return false
        case .tasks:
            return scheduledTasksNeedsBadge(jobs: jobs)
        }
    }
}

// MARK: - AI 复盘助手聊天模型（#4 U4/U5）

/// Provider-neutral streamed/persisted message content. Unknown block types are
/// retained as metadata instead of making hydration fail, so protocol v1 can
/// evolve without forcing a lock-step desktop release.
struct AgentContentBlock: Codable, Identifiable, Equatable {
    var type: String
    var contentIndex: Int?
    var text: String?
    var signature: String?
    var redacted: Bool?
    var provider: String?
    var model: String?
    var attachmentId: String?
    var mimeType: String?

    var id: String {
        [
            type,
            contentIndex.map(String.init) ?? "-",
            attachmentId ?? "-",
        ].joined(separator: ":")
    }

    enum CodingKeys: String, CodingKey {
        case type, text, signature, redacted, provider, model
        case contentIndex = "content_index"
        case attachmentId = "attachment_id"
        case mimeType = "mime_type"
    }
}

/// An immutable attachment reference. File bytes live in the sidecar's
/// content-addressed store; chat/session JSON only carries this descriptor.
struct AgentAttachment: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var mimeType: String?
    var kind: String?
    var sizeBytes: Int?
    var sha256: String?
    var status: String?
    var extractionStatus: String?
    var extractedChars: Int?
    var error: String?
    var provenance: String?

    enum CodingKeys: String, CodingKey {
        case id, name, filename, kind, status, error, provenance
        case mimeType = "mime_type"
        case sizeBytes = "size_bytes"
        case sha256
        case extractionStatus = "extraction_status"
        case extractedChars = "extracted_chars"
    }

    init(
        id: String,
        name: String,
        mimeType: String? = nil,
        kind: String? = nil,
        sizeBytes: Int? = nil,
        sha256: String? = nil,
        status: String? = nil,
        extractionStatus: String? = nil,
        extractedChars: Int? = nil,
        error: String? = nil,
        provenance: String? = nil
    ) {
        self.id = id
        self.name = name
        self.mimeType = mimeType
        self.kind = kind
        self.sizeBytes = sizeBytes
        self.sha256 = sha256
        self.status = status
        self.extractionStatus = extractionStatus
        self.extractedChars = extractedChars
        self.error = error
        self.provenance = provenance
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decode(String.self, forKey: .name))
            ?? (try? c.decode(String.self, forKey: .filename))
            ?? "附件"
        mimeType = try? c.decode(String.self, forKey: .mimeType)
        kind = try? c.decode(String.self, forKey: .kind)
        sizeBytes = try? c.decode(Int.self, forKey: .sizeBytes)
        sha256 = try? c.decode(String.self, forKey: .sha256)
        status = try? c.decode(String.self, forKey: .status)
        extractionStatus = try? c.decode(String.self, forKey: .extractionStatus)
        extractedChars = try? c.decode(Int.self, forKey: .extractedChars)
        error = try? c.decode(String.self, forKey: .error)
        provenance = try? c.decode(String.self, forKey: .provenance)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(name, forKey: .filename)
        try c.encodeIfPresent(mimeType, forKey: .mimeType)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(sizeBytes, forKey: .sizeBytes)
        try c.encodeIfPresent(sha256, forKey: .sha256)
        try c.encodeIfPresent(status, forKey: .status)
        try c.encodeIfPresent(extractionStatus, forKey: .extractionStatus)
        try c.encodeIfPresent(extractedChars, forKey: .extractedChars)
        try c.encodeIfPresent(error, forKey: .error)
        try c.encodeIfPresent(provenance, forKey: .provenance)
    }

    var isReady: Bool {
        status == nil || status == "ready" || status == "imported"
    }

    var displaySize: String? {
        guard let sizeBytes else { return nil }
        return ByteCountFormatter.string(fromByteCount: Int64(sizeBytes), countStyle: .file)
    }
}

/// 一条聊天消息。会话历史归 KSSStore（不放 view @State，避免 .id(selectedSection) 销毁）。
struct ChatMessage: Identifiable, Equatable {
    enum Role { case user, assistant }
    let id = UUID()
    let role: Role
    var text: String
    /// Provider-returned reasoning only. It is rendered separately from the
    /// answer and must never be synthesized from ordinary assistant text.
    var thinkingBlocks: [AgentContentBlock] = []
    var attachments: [AgentAttachment] = []
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

// MARK: - Agent v1 chat protocol

struct AgentSession: Codable, Identifiable, Equatable {
    var id: String { sessionId }
    var sessionId: String
    var title: String
    var archived: Bool
    var updatedAt: String?
    var messages: [AgentHydratedMessage]?
    var contextUsage: AgentContextUsage?
    var queuedInputs: [AgentQueuedInput]?
    var providerRoute: AgentProviderRoute?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case title, archived
        case updatedAt = "updated_at"
        case messages
        case contextUsage = "context_usage"
        case queuedInputs = "queued_inputs"
        case providerRoute = "provider_route"
    }

    init(sessionId: String, title: String, archived: Bool = false, updatedAt: String? = nil,
         messages: [AgentHydratedMessage]? = nil, contextUsage: AgentContextUsage? = nil,
         queuedInputs: [AgentQueuedInput]? = nil, providerRoute: AgentProviderRoute? = nil) {
        self.sessionId = sessionId
        self.title = title
        self.archived = archived
        self.updatedAt = updatedAt
        self.messages = messages
        self.contextUsage = contextUsage
        self.queuedInputs = queuedInputs
        self.providerRoute = providerRoute
    }
}

struct AgentQueuedInput: Codable, Identifiable, Equatable {
    var id: String
    var clientMessageId: String?
    var sessionId: String?
    var runId: String?
    var mode: String
    var content: String
    var status: String
    var createdAt: Double?
    var appliedAt: Double?
    var sourceQueueId: String?

    enum CodingKeys: String, CodingKey {
        case id, mode, content, input, status
        case clientMessageId = "client_message_id"
        case sessionId = "session_id"
        case runId = "run_id"
        case createdAt = "created_at"
        case appliedAt = "applied_at"
        case sourceQueueId = "source_queue_id"
    }

    init(
        id: String,
        clientMessageId: String? = nil,
        sessionId: String? = nil,
        runId: String? = nil,
        mode: String,
        content: String,
        status: String,
        createdAt: Double? = nil,
        appliedAt: Double? = nil,
        sourceQueueId: String? = nil
    ) {
        self.id = id
        self.clientMessageId = clientMessageId
        self.sessionId = sessionId
        self.runId = runId
        self.mode = mode
        self.content = content
        self.status = status
        self.createdAt = createdAt
        self.appliedAt = appliedAt
        self.sourceQueueId = sourceQueueId
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        clientMessageId = try? c.decode(String.self, forKey: .clientMessageId)
        sessionId = try? c.decode(String.self, forKey: .sessionId)
        runId = try? c.decode(String.self, forKey: .runId)
        mode = (try? c.decode(String.self, forKey: .mode)) ?? "follow_up"
        content = (try? c.decode(String.self, forKey: .content))
            ?? (try? c.decode(String.self, forKey: .input)) ?? ""
        status = (try? c.decode(String.self, forKey: .status)) ?? "pending"
        createdAt = try? c.decode(Double.self, forKey: .createdAt)
        appliedAt = try? c.decode(Double.self, forKey: .appliedAt)
        sourceQueueId = try? c.decode(String.self, forKey: .sourceQueueId)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(clientMessageId, forKey: .clientMessageId)
        try c.encodeIfPresent(sessionId, forKey: .sessionId)
        try c.encodeIfPresent(runId, forKey: .runId)
        try c.encode(mode, forKey: .mode)
        try c.encode(content, forKey: .content)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(appliedAt, forKey: .appliedAt)
        try c.encodeIfPresent(sourceQueueId, forKey: .sourceQueueId)
    }

    var isRestorable: Bool {
        status == "queued" || status == "pending" || status == "restored"
    }
}

struct AgentQueueResponse: Codable, Equatable {
    var ok: Bool?
    var operation: String?
    var item: AgentQueuedInput?
    var queuedInputs: [AgentQueuedInput]?
    var steeringCount: Int?
    var followUpCount: Int?
    var reason: String?

    enum CodingKeys: String, CodingKey {
        case ok, operation, item, reason
        case queuedInputs = "queued_inputs"
        case steeringCount = "steering_count"
        case followUpCount = "follow_up_count"
    }
}

struct AgentQueueAcknowledgement: Identifiable, Equatable {
    var id: String { clientMessageId }
    var clientMessageId: String
    var accepted: Bool
    var operation: String
    var reason: String?
}

struct AgentHydratedMessage: Codable, Equatable, Identifiable {
    var id: String
    var role: String
    var text: String
    var toolCalls: [AgentHydratedToolCall]?
    var evidenceSummary: ChatEvidenceSummary?
    var evidenceDrawer: ChatEvidenceDrawer?
    var contentBlocks: [AgentContentBlock]?
    var attachments: [AgentAttachment]?

    enum CodingKeys: String, CodingKey {
        case id, role, text, content
        case toolCalls = "tool_calls"
        case evidenceSummary, evidenceDrawer
        case evidenceSummarySnake = "evidence_summary"
        case evidenceDrawerSnake = "evidence_drawer"
        case contentBlocks = "content_blocks"
        case attachments
    }

    init(id: String, role: String, text: String,
         toolCalls: [AgentHydratedToolCall]? = nil,
         evidenceSummary: ChatEvidenceSummary? = nil, evidenceDrawer: ChatEvidenceDrawer? = nil,
         contentBlocks: [AgentContentBlock]? = nil, attachments: [AgentAttachment]? = nil) {
        self.id = id
        self.role = role
        self.text = text
        self.toolCalls = toolCalls
        self.evidenceSummary = evidenceSummary
        self.evidenceDrawer = evidenceDrawer
        self.contentBlocks = contentBlocks
        self.attachments = attachments
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        role = (try? c.decode(String.self, forKey: .role)) ?? "assistant"
        contentBlocks = (try? c.decode([AgentContentBlock].self, forKey: .contentBlocks))
            ?? (try? c.decode([AgentContentBlock].self, forKey: .content))
        text = (try? c.decode(String.self, forKey: .text))
            ?? (try? c.decode(String.self, forKey: .content))
            ?? contentBlocks?
                .filter { $0.type == "text" }
                .compactMap(\.text)
                .joined()
            ?? ""
        toolCalls = try? c.decode([AgentHydratedToolCall].self, forKey: .toolCalls)
        evidenceSummary = (try? c.decode(ChatEvidenceSummary.self, forKey: .evidenceSummary))
            ?? (try? c.decode(ChatEvidenceSummary.self, forKey: .evidenceSummarySnake))
        evidenceDrawer = (try? c.decode(ChatEvidenceDrawer.self, forKey: .evidenceDrawer))
            ?? (try? c.decode(ChatEvidenceDrawer.self, forKey: .evidenceDrawerSnake))
        attachments = try? c.decode([AgentAttachment].self, forKey: .attachments)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(role, forKey: .role)
        try c.encode(text, forKey: .text)
        try c.encodeIfPresent(toolCalls, forKey: .toolCalls)
        try c.encodeIfPresent(evidenceSummary, forKey: .evidenceSummary)
        try c.encodeIfPresent(evidenceDrawer, forKey: .evidenceDrawer)
        try c.encodeIfPresent(contentBlocks, forKey: .contentBlocks)
        try c.encodeIfPresent(attachments, forKey: .attachments)
    }
}

struct AgentHydratedToolCall: Codable, Equatable {
    var id: String
    var name: String
}

// MARK: - Provider catalog / attachment protocol

struct AgentModelDescriptor: Codable, Identifiable, Equatable {
    var id: String
    var name: String?
    var providerId: String?
    var contextWindow: Int?
    var maxOutputTokens: Int?
    var supportsThinking: Bool?
    var supportsImages: Bool?
    var supportsTools: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name
        case modelId = "model_id"
        case providerId = "provider_id"
        case contextWindow = "context_window"
        case maxOutputTokens = "max_output_tokens"
        case supportsThinking = "supports_thinking"
        case supportsImages = "supports_images"
        case supportsTools = "supports_tools"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id))
            ?? (try? c.decode(String.self, forKey: .modelId))
            ?? "unknown"
        name = try? c.decode(String.self, forKey: .name)
        providerId = try? c.decode(String.self, forKey: .providerId)
        contextWindow = try? c.decode(Int.self, forKey: .contextWindow)
        maxOutputTokens = try? c.decode(Int.self, forKey: .maxOutputTokens)
        supportsThinking = try? c.decode(Bool.self, forKey: .supportsThinking)
        supportsImages = try? c.decode(Bool.self, forKey: .supportsImages)
        supportsTools = try? c.decode(Bool.self, forKey: .supportsTools)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .modelId)
        try c.encodeIfPresent(name, forKey: .name)
        try c.encodeIfPresent(providerId, forKey: .providerId)
        try c.encodeIfPresent(contextWindow, forKey: .contextWindow)
        try c.encodeIfPresent(maxOutputTokens, forKey: .maxOutputTokens)
        try c.encodeIfPresent(supportsThinking, forKey: .supportsThinking)
        try c.encodeIfPresent(supportsImages, forKey: .supportsImages)
        try c.encodeIfPresent(supportsTools, forKey: .supportsTools)
    }
}

struct AgentProviderDescriptor: Codable, Identifiable, Equatable {
    var id: String
    var name: String?
    var authenticated: Bool?
    var authKind: String?
    var baseURL: String?
    var models: [AgentModelDescriptor]?

    enum CodingKeys: String, CodingKey {
        case id, name, authenticated, models
        case authKind = "auth_kind"
        case baseURL = "base_url"
    }
}

struct AgentProviderRoute: Codable, Equatable {
    var providerId: String?
    var modelId: String?
    var baseURL: String?
    var thinkingLevel: String?
    var contextWindow: Int?
    var maxOutputTokens: Int?
    var supportsImages: Bool?
    var supportsTools: Bool?
    var supportsThinking: Bool?

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case modelId = "model_id"
        case baseURL = "base_url"
        case thinkingLevel = "thinking_level"
        case contextWindow = "context_window"
        case maxOutputTokens = "max_output_tokens"
        case supportsImages = "supports_images"
        case supportsTools = "supports_tools"
        case supportsThinking = "supports_thinking"
    }

    init(
        providerId: String? = nil,
        modelId: String? = nil,
        baseURL: String? = nil,
        thinkingLevel: String? = nil,
        contextWindow: Int? = nil,
        maxOutputTokens: Int? = nil,
        supportsImages: Bool? = nil,
        supportsTools: Bool? = nil,
        supportsThinking: Bool? = nil
    ) {
        self.providerId = providerId
        self.modelId = modelId
        self.baseURL = baseURL
        self.thinkingLevel = thinkingLevel
        self.contextWindow = contextWindow
        self.maxOutputTokens = maxOutputTokens
        self.supportsImages = supportsImages
        self.supportsTools = supportsTools
        self.supportsThinking = supportsThinking
    }
}

enum SeesawProviderReadiness: Equatable {
    case missingCredential
    case missingRoute
    case brokerLoading
    case configuredUntested
    case ready
    case failed(String)

    var isReadyForComposer: Bool {
        switch self {
        case .configuredUntested, .ready: return true
        default: return false
        }
    }
}

struct AgentProvidersResponse: Codable, Equatable {
    var providers: [AgentProviderDescriptor]
    var primary: AgentProviderRoute?
    var fallback: AgentProviderRoute?
    var status: String?
    var source: String?
    var ok: Bool?
    var latencyMs: Double?
    var hint: String?
    var candidates: [DataSourceCandidateProbe]?
    var error: String?

    init(
        providers: [AgentProviderDescriptor] = [],
        primary: AgentProviderRoute? = nil,
        fallback: AgentProviderRoute? = nil,
        status: String? = nil,
        source: String? = nil,
        ok: Bool? = nil,
        latencyMs: Double? = nil,
        hint: String? = nil,
        candidates: [DataSourceCandidateProbe]? = nil,
        error: String? = nil
    ) {
        self.providers = providers
        self.primary = primary
        self.fallback = fallback
        self.status = status
        self.source = source
        self.ok = ok
        self.latencyMs = latencyMs
        self.hint = hint
        self.candidates = candidates
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case providers, primary, fallback, status, source, ok, hint, candidates, error
        case latencyMs = "latency_ms"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        providers = (try? c.decode([AgentProviderDescriptor].self, forKey: .providers)) ?? []
        primary = try? c.decode(AgentProviderRoute.self, forKey: .primary)
        fallback = try? c.decode(AgentProviderRoute.self, forKey: .fallback)
        status = try? c.decode(String.self, forKey: .status)
        source = try? c.decode(String.self, forKey: .source)
        ok = try? c.decode(Bool.self, forKey: .ok)
        latencyMs = try? c.decode(Double.self, forKey: .latencyMs)
        hint = try? c.decode(String.self, forKey: .hint)
        candidates = try? c.decode([DataSourceCandidateProbe].self, forKey: .candidates)
        error = try? c.decode(String.self, forKey: .error)
    }
}

/// Compact, display-safe projection of a persisted `live_context` event.
/// Quote rows remain in the sidecar transcript/evidence ledger; Swift only
/// needs freshness, scope and coverage information for the Right Rail.
struct AgentLiveMarketContext: Decodable, Equatable, Identifiable {
    let kind: String?
    let snapshotID: String?
    let symbols: [String]
    let rows: [AgentLiveMarketRow]
    let sourceAsOf: String?
    let retrievedAt: String?
    let warnings: [String]
    let errors: [AgentLiveMarketContextError]
    let eligibility: String?
    let provenance: String?

    var id: String {
        "\(retrievedAt ?? "unknown")::\(symbols.joined(separator: ","))"
    }

    var coverageText: String {
        if errors.isEmpty { return "覆盖 \(symbols.count) 个标的" }
        return "覆盖 \(symbols.count) 个标的 · \(errors.count) 项未完整返回"
    }

    enum CodingKeys: String, CodingKey {
        case kind, symbols, rows, warnings, errors, eligibility, provenance
        case snapshotID = "snapshot_id"
        case sourceAsOf = "source_asof_ts"
        case retrievedAt = "retrieved_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try? c.decode(String.self, forKey: .kind)
        snapshotID = try? c.decode(String.self, forKey: .snapshotID)
        symbols = (try? c.decode([String].self, forKey: .symbols)) ?? []
        rows = (try? c.decode([AgentLiveMarketRow].self, forKey: .rows)) ?? []
        sourceAsOf = try? c.decode(String.self, forKey: .sourceAsOf)
        retrievedAt = try? c.decode(String.self, forKey: .retrievedAt)
        warnings = (try? c.decode([String].self, forKey: .warnings)) ?? []
        errors = (try? c.decode([AgentLiveMarketContextError].self, forKey: .errors)) ?? []
        eligibility = try? c.decode(String.self, forKey: .eligibility)
        provenance = try? c.decode(String.self, forKey: .provenance)
    }
}

struct AgentLiveMarketRow: Decodable, Equatable, Identifiable {
    let symbol: String
    let quote: LongbridgeQuote?
    let routedProvider: String?
    let eligibility: String?

    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol, quote, eligibility
        case routedProvider = "routed_provider"
    }
}

struct AgentLiveMarketContextError: Decodable, Equatable, Identifiable {
    let symbol: String
    let quoteError: String?
    let snapshotError: String?

    var id: String { symbol }

    enum CodingKeys: String, CodingKey {
        case symbol
        case quoteError = "quote_error"
        case snapshotError = "snapshot_error"
    }
}

struct AgentAttachmentsResponse: Codable, Equatable {
    var attachments: [AgentAttachment]
    var attachment: AgentAttachment?
    var error: String?

    init(
        attachments: [AgentAttachment] = [],
        attachment: AgentAttachment? = nil,
        error: String? = nil
    ) {
        self.attachments = attachments
        self.attachment = attachment
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case attachments, attachment, error
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        attachments = (try? c.decode([AgentAttachment].self, forKey: .attachments)) ?? []
        attachment = try? c.decode(AgentAttachment.self, forKey: .attachment)
        error = try? c.decode(String.self, forKey: .error)
    }

    var allAttachments: [AgentAttachment] {
        if !attachments.isEmpty { return attachments }
        return attachment.map { [$0] } ?? []
    }
}

struct AgentSkill: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var description: String?
    var enabled: Bool?
    var pinned: Bool?
    var category: String?
    var version: String?
    var source: String?
    var upstreamCommit: String?
    var contentHash: String?
    var trust: String?
    var requiredTools: [String]?
    var allowedProfiles: [String]?
    var protected: Bool?
    var active: Bool?
    var shadowedBy: String?
    var available: Bool?
    var missingRequiredTools: [String]?

    enum CodingKeys: String, CodingKey {
        case id, name, description, enabled, pinned, category, version, source
        case trust, protected, active, available
        case upstreamCommit = "upstream_commit"
        case contentHash = "content_hash"
        case requiredTools = "required_tools"
        case allowedProfiles = "allowed_profiles"
        case shadowedBy = "shadowed_by"
        case missingRequiredTools = "missing_required_tools"
    }
}

struct AgentSkillDiagnostic: Codable, Identifiable, Equatable {
    var id: String { "\(code):\(path ?? message)" }
    var code: String
    var message: String
    var path: String?
}

struct AgentMemoryCandidate: Codable, Identifiable, Equatable {
    var id: String
    var text: String
    var source: String?
    var status: String?
}

struct AgentMemoryRecord: Codable, Identifiable, Equatable {
    var id: String
    var text: String
    var source: String?
    var archived: Bool?
    var kind: String?
    var sourceSession: String?
    var sourceEntry: String?
    var tags: [String]?
    var status: String?
    var createdAt: Double?
    var expiresAt: Double?
    var reviewRequired: Bool?
    var score: Double?
    var injectionText: String?

    enum CodingKeys: String, CodingKey {
        case id, text, content, source, archived, kind, tags, status, score
        case sourceSession = "source_session"
        case sourceEntry = "source_entry"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case reviewRequired = "review_required"
        case injectionText = "injection_text"
    }

    init(
        id: String, text: String, source: String? = nil, archived: Bool? = nil,
        kind: String? = nil, sourceSession: String? = nil, sourceEntry: String? = nil,
        tags: [String]? = nil, status: String? = nil, createdAt: Double? = nil,
        expiresAt: Double? = nil, reviewRequired: Bool? = nil, score: Double? = nil,
        injectionText: String? = nil
    ) {
        self.id = id
        self.text = text
        self.source = source
        self.archived = archived
        self.kind = kind
        self.sourceSession = sourceSession
        self.sourceEntry = sourceEntry
        self.tags = tags
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.reviewRequired = reviewRequired
        self.score = score
        self.injectionText = injectionText
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        text = (try? c.decode(String.self, forKey: .text))
            ?? (try? c.decode(String.self, forKey: .content)) ?? ""
        source = try? c.decode(String.self, forKey: .source)
        archived = try? c.decode(Bool.self, forKey: .archived)
        kind = try? c.decode(String.self, forKey: .kind)
        sourceSession = try? c.decode(String.self, forKey: .sourceSession)
        sourceEntry = try? c.decode(String.self, forKey: .sourceEntry)
        tags = try? c.decode([String].self, forKey: .tags)
        status = try? c.decode(String.self, forKey: .status)
        createdAt = try? c.decode(Double.self, forKey: .createdAt)
        expiresAt = try? c.decode(Double.self, forKey: .expiresAt)
        reviewRequired = try? c.decode(Bool.self, forKey: .reviewRequired)
        score = try? c.decode(Double.self, forKey: .score)
        injectionText = try? c.decode(String.self, forKey: .injectionText)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(text, forKey: .text)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encodeIfPresent(archived, forKey: .archived)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(sourceSession, forKey: .sourceSession)
        try c.encodeIfPresent(sourceEntry, forKey: .sourceEntry)
        try c.encodeIfPresent(tags, forKey: .tags)
        try c.encodeIfPresent(status, forKey: .status)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(expiresAt, forKey: .expiresAt)
        try c.encodeIfPresent(reviewRequired, forKey: .reviewRequired)
        try c.encodeIfPresent(score, forKey: .score)
        try c.encodeIfPresent(injectionText, forKey: .injectionText)
    }
}

struct AgentSourceRecall: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var source: String?
    var excerpt: String?
    var kind: String?
    var content: String?
    var sourceSession: String?
    var sourceEntry: String?
    var tags: [String]?
    var createdAt: Double?
    var expiresAt: Double?
    var reviewRequired: Bool?
    var score: Double?
    var injectionText: String?

    enum CodingKeys: String, CodingKey {
        case id, title, source, excerpt, kind, content, text, tags, score
        case sourceSession = "source_session"
        case sourceEntry = "source_entry"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case reviewRequired = "review_required"
        case injectionText = "injection_text"
    }

    init(
        id: String, title: String, source: String? = nil, excerpt: String? = nil,
        kind: String? = nil, content: String? = nil, sourceSession: String? = nil,
        sourceEntry: String? = nil, tags: [String]? = nil, createdAt: Double? = nil,
        expiresAt: Double? = nil, reviewRequired: Bool? = nil, score: Double? = nil,
        injectionText: String? = nil
    ) {
        self.id = id
        self.title = title
        self.source = source
        self.excerpt = excerpt
        self.kind = kind
        self.content = content
        self.sourceSession = sourceSession
        self.sourceEntry = sourceEntry
        self.tags = tags
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.reviewRequired = reviewRequired
        self.score = score
        self.injectionText = injectionText
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try? c.decode(String.self, forKey: .kind)
        reviewRequired = try? c.decode(Bool.self, forKey: .reviewRequired)
        title = (try? c.decode(String.self, forKey: .title))
            ?? [kind ?? "记忆", reviewRequired == true ? "待复核" : nil]
                .compactMap { $0 }.joined(separator: " · ")
        sourceSession = try? c.decode(String.self, forKey: .sourceSession)
        sourceEntry = try? c.decode(String.self, forKey: .sourceEntry)
        source = (try? c.decode(String.self, forKey: .source))
            ?? [sourceSession, sourceEntry].compactMap { $0 }.joined(separator: " · ")
        content = (try? c.decode(String.self, forKey: .content))
            ?? (try? c.decode(String.self, forKey: .text))
        injectionText = try? c.decode(String.self, forKey: .injectionText)
        excerpt = (try? c.decode(String.self, forKey: .excerpt))
            ?? injectionText ?? content
        tags = try? c.decode([String].self, forKey: .tags)
        createdAt = try? c.decode(Double.self, forKey: .createdAt)
        expiresAt = try? c.decode(Double.self, forKey: .expiresAt)
        score = try? c.decode(Double.self, forKey: .score)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encodeIfPresent(excerpt, forKey: .excerpt)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(content, forKey: .content)
        try c.encodeIfPresent(sourceSession, forKey: .sourceSession)
        try c.encodeIfPresent(sourceEntry, forKey: .sourceEntry)
        try c.encodeIfPresent(tags, forKey: .tags)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(expiresAt, forKey: .expiresAt)
        try c.encodeIfPresent(reviewRequired, forKey: .reviewRequired)
        try c.encodeIfPresent(score, forKey: .score)
        try c.encodeIfPresent(injectionText, forKey: .injectionText)
    }
}

struct AgentContextUsage: Codable, Equatable {
    var used: Int?
    var limit: Int?
    var percent: Double?
    var label: String?
    var estimated: Bool? = nil

    enum CodingKeys: String, CodingKey {
        case used, limit, percent, label, estimated
    }

    var displayText: String {
        if let label, !label.isEmpty { return label }
        if let percent { return "上下文 \(Int(percent.rounded()))%" }
        if let used, let limit, limit > 0 { return "上下文 \(used)/\(limit)" }
        return "上下文 —"
    }
}

struct AgentUsage: Decodable, Equatable {
    var inputTokens: Int?
    var outputTokens: Int?
    var totalTokens: Int?
    var cachedInputTokens: Int?
    var reasoningTokens: Int?

    enum CodingKeys: String, CodingKey {
        case inputTokens = "input_tokens"
        case promptTokens = "prompt_tokens"
        case outputTokens = "output_tokens"
        case completionTokens = "completion_tokens"
        case totalTokens = "total_tokens"
        case cachedInputTokens = "cached_input_tokens"
        case cacheReadTokens = "cache_read_tokens"
        case reasoningTokens = "reasoning_tokens"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        inputTokens = (try? c.decode(Int.self, forKey: .inputTokens))
            ?? (try? c.decode(Int.self, forKey: .promptTokens))
        outputTokens = (try? c.decode(Int.self, forKey: .outputTokens))
            ?? (try? c.decode(Int.self, forKey: .completionTokens))
        totalTokens = try? c.decode(Int.self, forKey: .totalTokens)
        cachedInputTokens = (try? c.decode(Int.self, forKey: .cachedInputTokens))
            ?? (try? c.decode(Int.self, forKey: .cacheReadTokens))
        reasoningTokens = try? c.decode(Int.self, forKey: .reasoningTokens)
    }
}

struct AgentNumberGuard: Codable, Equatable {
    var unverified: [String]?
}

struct AgentCommandAck: Codable, Equatable {
    var ok: Bool?
    var error: String?
}

struct AgentSessionListResponse: Codable, Equatable {
    var sessions: [AgentSession]
    var selectedSessionId: String?

    enum CodingKeys: String, CodingKey {
        case sessions
        case selectedSessionId = "selected_session_id"
    }
}

struct AgentSkillsResponse: Codable, Equatable {
    var skills: [AgentSkill]
    var diagnostics: [AgentSkillDiagnostic]?
}

struct AgentMemoriesResponse: Codable, Equatable {
    var memories: [AgentMemoryRecord]
    var candidates: [AgentMemoryCandidate]?
    var recalls: [AgentSourceRecall]?
}

// MARK: - Deep Research protocol

/// `agent-research` intentionally remains tolerant: the Python research service may
/// add richer budget/usage/snapshot fields without forcing an app release.
struct ResearchGoalSummary: Decodable, Equatable, Identifiable {
    var goalId: String
    var sessionId: String?
    var profileId: String
    var executionMode: String
    var objective: String
    var status: String
    var progress: Double?
    var terminalReason: String?
    var createdAt: String?
    var updatedAt: String?
    var origin: String
    var cadence: String?

    var id: String { goalId }

    enum CodingKeys: String, CodingKey {
        case goalId = "goal_id"
        case legacyId = "id"
        case sessionId = "session_id"
        case profileId = "profile_id"
        case executionMode = "execution_mode"
        case objective, status, progress
        case terminalReason = "terminal_reason"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case origin, cadence
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        goalId = (try? c.decode(String.self, forKey: .goalId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? ""
        sessionId = try? c.decode(String.self, forKey: .sessionId)
        profileId = (try? c.decode(String.self, forKey: .profileId)) ?? "investment-weekly-v3"
        executionMode = (try? c.decode(String.self, forKey: .executionMode)) ?? "single"
        objective = (try? c.decode(String.self, forKey: .objective)) ?? "未命名研究"
        status = (try? c.decode(String.self, forKey: .status)) ?? "created"
        progress = try? c.decode(Double.self, forKey: .progress)
        terminalReason = try? c.decode(String.self, forKey: .terminalReason)
        createdAt = try? c.decode(String.self, forKey: .createdAt)
        updatedAt = try? c.decode(String.self, forKey: .updatedAt)
        origin = (try? c.decode(String.self, forKey: .origin)) ?? "manual"
        cadence = try? c.decode(String.self, forKey: .cadence)
    }

    init(goalId: String, sessionId: String? = nil, profileId: String = "investment-weekly-v3",
         executionMode: String = "single",
         objective: String, status: String, progress: Double? = nil,
         terminalReason: String? = nil, createdAt: String? = nil, updatedAt: String? = nil,
         origin: String = "manual", cadence: String? = nil) {
        self.goalId = goalId
        self.sessionId = sessionId
        self.profileId = profileId
        self.executionMode = executionMode
        self.objective = objective
        self.status = status
        self.progress = progress
        self.terminalReason = terminalReason
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.origin = origin
        self.cadence = cadence
    }
}

struct InvestmentAnalysisReportSummary: Decodable, Equatable, Identifiable {
    var goalId: String
    var profileId: String
    var cadence: String?
    var dateStart: String?
    var dateEnd: String?
    var asOf: String?
    var title: String
    var goalStatus: String
    var auditStatus: String?
    var isDraft: Bool
    var artifactId: String?
    var objectHash: String?
    var createdAt: String?

    var id: String { goalId }

    enum CodingKeys: String, CodingKey {
        case goalId = "goal_id", profileId = "profile_id", cadence
        case dateStart = "date_start", dateEnd = "date_end", asOf = "as_of"
        case title, goalStatus = "goal_status", auditStatus = "audit_status"
        case isDraft = "is_draft", artifactId = "artifact_id"
        case objectHash = "object_hash", createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        goalId = (try? c.decode(String.self, forKey: .goalId)) ?? ""
        profileId = (try? c.decode(String.self, forKey: .profileId)) ?? ""
        cadence = try? c.decode(String.self, forKey: .cadence)
        dateStart = try? c.decode(String.self, forKey: .dateStart)
        dateEnd = try? c.decode(String.self, forKey: .dateEnd)
        asOf = try? c.decode(String.self, forKey: .asOf)
        title = (try? c.decode(String.self, forKey: .title)) ?? "投资分析"
        goalStatus = (try? c.decode(String.self, forKey: .goalStatus)) ?? "draft"
        auditStatus = try? c.decode(String.self, forKey: .auditStatus)
        isDraft = (try? c.decode(Bool.self, forKey: .isDraft)) ?? true
        artifactId = try? c.decode(String.self, forKey: .artifactId)
        objectHash = try? c.decode(String.self, forKey: .objectHash)
        createdAt = try? c.decode(String.self, forKey: .createdAt)
    }
}

struct ResearchCriterion: Decodable, Equatable, Identifiable {
    var criterionId: String
    var title: String
    var status: String?
    var detail: String?

    var id: String { criterionId }

    enum CodingKeys: String, CodingKey {
        case criterionId = "criterion_id"
        case legacyId = "id"
        case title, label, status, detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decode(String.self, forKey: .title))
            ?? (try? c.decode(String.self, forKey: .label))
            ?? "验收条件"
        criterionId = (try? c.decode(String.self, forKey: .criterionId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "criterion:\(title)"
        status = try? c.decode(String.self, forKey: .status)
        detail = try? c.decode(String.self, forKey: .detail)
    }
}

struct ResearchTask: Decodable, Equatable, Identifiable {
    var taskId: String
    var title: String
    var status: String
    var agentId: String?
    var attempt: Int?
    var detail: String?

    var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case legacyId = "id"
        case agentId = "agent_id"
        case title, objective, status, attempt, detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decode(String.self, forKey: .title))
            ?? (try? c.decode(String.self, forKey: .objective))
            ?? "研究任务"
        taskId = (try? c.decode(String.self, forKey: .taskId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "task:\(title)"
        status = (try? c.decode(String.self, forKey: .status)) ?? "pending"
        agentId = try? c.decode(String.self, forKey: .agentId)
        attempt = try? c.decode(Int.self, forKey: .attempt)
        detail = try? c.decode(String.self, forKey: .detail)
    }
}

struct ResearchAgentSummary: Decodable, Equatable, Identifiable {
    var agentId: String
    var title: String
    var role: String?
    var focus: String?
    var tasks: Int?
    var succeeded: Int?

    var id: String { agentId }

    enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case legacyId = "id"
        case title, name, role, focus, tasks, succeeded
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        agentId = (try? c.decode(String.self, forKey: .agentId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? ""
        title = (try? c.decode(String.self, forKey: .title))
            ?? (try? c.decode(String.self, forKey: .name))
            ?? agentId
        role = try? c.decode(String.self, forKey: .role)
        focus = try? c.decode(String.self, forKey: .focus)
        tasks = try? c.decode(Int.self, forKey: .tasks)
        succeeded = try? c.decode(Int.self, forKey: .succeeded)
    }
}

struct ResearchEvidence: Decodable, Equatable, Identifiable {
    var evidenceId: String
    var title: String
    var source: String?
    var url: String?
    var snippet: String?
    var status: String?
    var createdAt: String?
    var sourceTier: String?
    var dataAsOf: String?
    var evidenceHash: String?
    var verified: Bool?

    var id: String { evidenceId }

    enum CodingKeys: String, CodingKey {
        case evidenceId = "evidence_id"
        case legacyId = "id"
        case title, source, url, snippet, status
        case sourceTool = "source_tool"
        case uri, caveat
        case sourceTier = "source_tier"
        case dataAsOf = "data_as_of"
        case evidenceHash = "hash"
        case verified
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = (try? c.decode(String.self, forKey: .source))
            ?? (try? c.decode(String.self, forKey: .sourceTool))
        url = (try? c.decode(String.self, forKey: .url))
            ?? (try? c.decode(String.self, forKey: .uri))
        title = (try? c.decode(String.self, forKey: .title))
            ?? source
            ?? "研究证据"
        evidenceId = (try? c.decode(String.self, forKey: .evidenceId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "evidence:\(source ?? ""):\(url ?? ""):\(title)"
        snippet = (try? c.decode(String.self, forKey: .snippet))
            ?? (try? c.decode(String.self, forKey: .caveat))
        status = try? c.decode(String.self, forKey: .status)
        sourceTier = try? c.decode(String.self, forKey: .sourceTier)
        dataAsOf = try? c.decode(String.self, forKey: .dataAsOf)
        evidenceHash = try? c.decode(String.self, forKey: .evidenceHash)
        verified = try? c.decode(Bool.self, forKey: .verified)
        createdAt = try? c.decode(String.self, forKey: .createdAt)
    }
}

struct ResearchArtifact: Decodable, Equatable, Identifiable {
    var artifactId: String
    var kind: String
    var logicalName: String
    var mediaType: String?
    var sizeBytes: Int?
    var sha256: String?
    var relativePath: String?
    var createdAt: String?
    var auditStatus: String?
    var isDraft: Bool?
    /// Optional inline preview returned by newer sidecars. It is never interpreted
    /// as a URL and is rendered in a network-disabled WKWebView.
    var content: String?

    var id: String { artifactId }

    enum CodingKeys: String, CodingKey {
        case artifactId = "artifact_id"
        case legacyId = "id"
        case kind
        case logicalName = "logical_name"
        case legacyName = "name"
        case mediaType = "media_type"
        case sizeBytes = "size_bytes"
        case sha256
        case relativePath = "relative_path"
        case createdAt = "created_at"
        case auditStatus = "audit_status"
        case isDraft = "draft"
        case content
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "artifact"
        logicalName = (try? c.decode(String.self, forKey: .logicalName))
            ?? (try? c.decode(String.self, forKey: .legacyName))
            ?? "研究产物"
        artifactId = (try? c.decode(String.self, forKey: .artifactId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "artifact:\(kind):\(logicalName)"
        mediaType = try? c.decode(String.self, forKey: .mediaType)
        sizeBytes = try? c.decode(Int.self, forKey: .sizeBytes)
        sha256 = try? c.decode(String.self, forKey: .sha256)
        relativePath = try? c.decode(String.self, forKey: .relativePath)
        createdAt = try? c.decode(String.self, forKey: .createdAt)
        auditStatus = try? c.decode(String.self, forKey: .auditStatus)
        isDraft = try? c.decode(Bool.self, forKey: .isDraft)
        content = try? c.decode(String.self, forKey: .content)
    }
}

struct ResearchAuditEntry: Decodable, Equatable, Identifiable {
    var eventId: String
    var type: String
    var timestamp: String?
    var status: String?
    var message: String?

    var id: String { eventId }

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case legacyId = "id"
        case type, event, timestamp, status, message, detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decode(String.self, forKey: .type))
            ?? (try? c.decode(String.self, forKey: .event))
            ?? "research_event"
        timestamp = try? c.decode(String.self, forKey: .timestamp)
        eventId = (try? c.decode(String.self, forKey: .eventId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "audit:\(type):\(timestamp ?? "")"
        status = try? c.decode(String.self, forKey: .status)
        message = (try? c.decode(String.self, forKey: .message))
            ?? (try? c.decode(String.self, forKey: .detail))
    }
}

struct ResearchSnapshot: Decodable, Equatable {
    var snapshotId: String?
    var profileId: String?
    var asOf: String?
    var createdAt: String?
    var refreshOf: String?

    enum CodingKeys: String, CodingKey {
        case snapshotId = "snapshot_id"
        case profileId = "profile_id"
        case asOf = "as_of"
        case createdAt = "created_at"
        case refreshOf = "refresh_of"
    }
}

struct ResearchGoalDetail: Decodable, Equatable, Identifiable {
    var goalId: String
    var sessionId: String?
    var profileId: String
    var executionMode: String
    var objective: String
    var status: String
    var progress: Double?
    var terminalReason: String?
    var createdAt: String?
    var updatedAt: String?
    var origin: String
    var cadence: String?
    var criteria: [ResearchCriterion]
    var tasks: [ResearchTask]
    var evidence: [ResearchEvidence]
    var audit: [ResearchAuditEntry]
    var artifacts: [ResearchArtifact]
    var events: [ResearchEvent]
    var researchAgents: [ResearchAgentSummary]
    var snapshot: ResearchSnapshot?
    var budget: [String: Int]
    var usage: [String: Int]

    var id: String { goalId }
    var summary: ResearchGoalSummary {
        ResearchGoalSummary(
            goalId: goalId, sessionId: sessionId, profileId: profileId,
            executionMode: executionMode,
            objective: objective, status: status, progress: progress,
            terminalReason: terminalReason, createdAt: createdAt, updatedAt: updatedAt,
            origin: origin, cadence: cadence)
    }

    enum CodingKeys: String, CodingKey {
        case goalId = "goal_id"
        case legacyId = "id"
        case sessionId = "session_id"
        case profileId = "profile_id"
        case executionMode = "execution_mode"
        case objective, status, progress
        case terminalReason = "terminal_reason"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case origin, cadence
        case criteria, tasks, evidence, audit, artifacts, events, snapshot
        case researchAgents = "research_agents"
        case budget, usage
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        goalId = (try? c.decode(String.self, forKey: .goalId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? ""
        sessionId = try? c.decode(String.self, forKey: .sessionId)
        profileId = (try? c.decode(String.self, forKey: .profileId)) ?? "investment-weekly-v3"
        executionMode = (try? c.decode(String.self, forKey: .executionMode)) ?? "single"
        objective = (try? c.decode(String.self, forKey: .objective)) ?? "未命名研究"
        status = (try? c.decode(String.self, forKey: .status)) ?? "created"
        progress = try? c.decode(Double.self, forKey: .progress)
        terminalReason = try? c.decode(String.self, forKey: .terminalReason)
        createdAt = try? c.decode(String.self, forKey: .createdAt)
        updatedAt = try? c.decode(String.self, forKey: .updatedAt)
        origin = (try? c.decode(String.self, forKey: .origin)) ?? "manual"
        cadence = try? c.decode(String.self, forKey: .cadence)
        criteria = (try? c.decode([ResearchCriterion].self, forKey: .criteria)) ?? []
        tasks = (try? c.decode([ResearchTask].self, forKey: .tasks)) ?? []
        evidence = (try? c.decode([ResearchEvidence].self, forKey: .evidence)) ?? []
        audit = (try? c.decode([ResearchAuditEntry].self, forKey: .audit)) ?? []
        artifacts = (try? c.decode([ResearchArtifact].self, forKey: .artifacts)) ?? []
        events = (try? c.decode([ResearchEvent].self, forKey: .events)) ?? []
        researchAgents = (try? c.decode([ResearchAgentSummary].self, forKey: .researchAgents)) ?? []
        snapshot = try? c.decode(ResearchSnapshot.self, forKey: .snapshot)
        budget = (try? c.decode([String: Int].self, forKey: .budget)) ?? [:]
        usage = (try? c.decode([String: Int].self, forKey: .usage)) ?? [:]
    }
}

struct ResearchProfileSummary: Decodable, Equatable, Identifiable {
    var profileId: String
    var name: String
    var description: String?
    var id: String { profileId }

    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case legacyId = "id"
        case name, title, description
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        profileId = (try? c.decode(String.self, forKey: .profileId))
            ?? (try? c.decode(String.self, forKey: .legacyId))
            ?? "investment-weekly-v3"
        name = (try? c.decode(String.self, forKey: .name))
            ?? (try? c.decode(String.self, forKey: .title))
            ?? profileId
        description = try? c.decode(String.self, forKey: .description)
    }
}

struct ResearchResponse: Decodable, Equatable {
    var goals: [ResearchGoalSummary]
    var goal: ResearchGoalDetail?
    var profiles: [ResearchProfileSummary]?
    var error: String?
    var reports: [InvestmentAnalysisReportSummary]
    var nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case goals, goal, detail, profiles, error, reports
        case nextCursor = "next_cursor"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        goals = (try? c.decode([ResearchGoalSummary].self, forKey: .goals)) ?? []
        goal = (try? c.decode(ResearchGoalDetail.self, forKey: .detail))
            ?? (try? c.decode(ResearchGoalDetail.self, forKey: .goal))
        profiles = try? c.decode([ResearchProfileSummary].self, forKey: .profiles)
        error = try? c.decode(String.self, forKey: .error)
        reports = (try? c.decode([InvestmentAnalysisReportSummary].self, forKey: .reports)) ?? []
        nextCursor = try? c.decode(String.self, forKey: .nextCursor)
    }
}

struct ResearchArtifactResponse: Decodable, Equatable {
    var artifacts: [ResearchArtifact]
    var artifact: ResearchArtifact?
    var content: String?
    var destination: String?
    var published: Bool?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case artifacts, artifact, content, destination, published, error
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        artifacts = (try? c.decode([ResearchArtifact].self, forKey: .artifacts)) ?? []
        artifact = try? c.decode(ResearchArtifact.self, forKey: .artifact)
        content = try? c.decode(String.self, forKey: .content)
        destination = try? c.decode(String.self, forKey: .destination)
        published = try? c.decode(Bool.self, forKey: .published)
        error = try? c.decode(String.self, forKey: .error)
    }
}

struct ResearchEventPayload: Codable, Equatable {
    var title: String?
    var message: String?
    var detail: String?
    var reason: String?
    var progress: Double?
}

struct ResearchEvent: Decodable, Equatable, Identifiable {
    var protocolVersion: Int?
    var goalId: String
    var eventId: String
    var sequence: Int
    var timestamp: String
    var type: String
    var taskId: String?
    var attemptId: String?
    var runId: String?
    var status: String?
    var payload: ResearchEventPayload?
    /// Non-durable hydration frame sent before durable replay. Its sequence is
    /// only a replay cursor and must not participate in event deduplication.
    var snapshot: ResearchGoalDetail?

    var id: String { eventId }
    var displayMessage: String {
        payload?.message ?? payload?.detail ?? payload?.title ?? status ?? type
    }

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case goalId = "goal_id"
        case legacyGoal = "goal"
        case eventId = "event_id"
        case sequence, timestamp, type
        case legacyType = "event"
        case taskId = "task_id"
        case attemptId = "attempt_id"
        case runId = "run_id"
        case status, payload, snapshot
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        protocolVersion = try? c.decode(Int.self, forKey: .protocolVersion)
        goalId = (try? c.decode(String.self, forKey: .goalId))
            ?? (try? c.decode(String.self, forKey: .legacyGoal))
            ?? ""
        sequence = (try? c.decode(Int.self, forKey: .sequence)) ?? 0
        eventId = (try? c.decode(String.self, forKey: .eventId))
            ?? "\(goalId):\(sequence)"
        timestamp = (try? c.decode(String.self, forKey: .timestamp)) ?? ""
        type = (try? c.decode(String.self, forKey: .type))
            ?? (try? c.decode(String.self, forKey: .legacyType))
            ?? "research_event"
        taskId = try? c.decode(String.self, forKey: .taskId)
        attemptId = try? c.decode(String.self, forKey: .attemptId)
        runId = try? c.decode(String.self, forKey: .runId)
        status = try? c.decode(String.self, forKey: .status)
        payload = try? c.decode(ResearchEventPayload.self, forKey: .payload)
        snapshot = try? c.decode(ResearchGoalDetail.self, forKey: .snapshot)
    }
}

struct ResearchCandidate: Codable, Equatable {
    var objective: String
    var profileId: String?
    var sessionId: String?

    enum CodingKeys: String, CodingKey {
        case objective
        case profileId = "profile_id"
        case sessionId = "session_id"
    }
}

struct AgentFrame: Decodable, Equatable {
    let protocolVersion: Int?
    let sessionId: String?
    let runId: String?
    let sequence: Int?
    let type: String
    let messageId: String?
    let text: String?
    let delta: String?
    let name: String?
    let tool: String?
    let command: String?
    let effect: String?
    let argsText: String?
    let callId: String?
    let reason: String?
    let error: String?
    let model: String?
    let provider: String?
    let contentIndex: Int?
    let signature: String?
    let redacted: Bool?
    let contentBlocks: [AgentContentBlock]?
    let attachment: AgentAttachment?
    let attachments: [AgentAttachment]?
    let providerRoute: AgentProviderRoute?
    let usage: AgentUsage?
    let existingRunId: String?
    let isError: Bool?
    let terminationReason: String?
    let numberGuard: AgentNumberGuard?
    let contextUsage: AgentContextUsage?
    let memoryCandidate: AgentMemoryCandidate?
    let memories: [AgentMemoryRecord]?
    let recall: AgentSourceRecall?
    let recalls: [AgentSourceRecall]?
    let evidenceSummary: ChatEvidenceSummary?
    let evidenceDrawer: ChatEvidenceDrawer?
    let operation: String?
    let item: AgentQueuedInput?
    let queuedInputs: [AgentQueuedInput]?
    let steeringCount: Int?
    let followUpCount: Int?
    let researchCandidate: ResearchCandidate?
    let liveContexts: [AgentLiveMarketContext]?

    enum CodingKeys: String, CodingKey {
        case type, sequence, text, delta, name, tool, command, effect, reason, error, model, provider, usage, numberGuard
        case signature, redacted, attachment, attachments
        case operation, item
        case numberGuardSnake = "number_guard"
        case protocolVersion = "protocol_version"
        case sessionId = "session_id"
        case runId = "run_id"
        case existingRunId = "existing_run_id"
        case isError = "is_error"
        case isErrorCamel = "isError"
        case terminationReason = "termination_reason"
        case terminationReasonCamel = "terminationReason"
        case messageId = "message_id"
        case argsText = "argsText"
        case argsTextSnake = "args_text"
        case callId = "call_id"
        case contextUsage = "context_usage"
        case memoryCandidate = "memory_candidate"
        case memories, recall, recalls
        case sourceRecall = "source_recall"
        case evidenceSummary, evidenceDrawer
        case evidenceSummarySnake = "evidence_summary"
        case evidenceDrawerSnake = "evidence_drawer"
        case queuedInputs = "queued_inputs"
        case steeringCount = "steering_count"
        case followUpCount = "follow_up_count"
        case researchCandidate = "research_candidate"
        case liveContexts = "items"
        case contentIndex = "content_index"
        case contentIndexCamel = "contentIndex"
        case contentBlocks = "content_blocks"
        case providerRoute = "provider_route"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        protocolVersion = try? c.decode(Int.self, forKey: .protocolVersion)
        sessionId = try? c.decode(String.self, forKey: .sessionId)
        runId = try? c.decode(String.self, forKey: .runId)
        sequence = try? c.decode(Int.self, forKey: .sequence)
        type = try c.decode(String.self, forKey: .type)
        messageId = try? c.decode(String.self, forKey: .messageId)
        text = try? c.decode(String.self, forKey: .text)
        delta = try? c.decode(String.self, forKey: .delta)
        name = try? c.decode(String.self, forKey: .name)
        tool = try? c.decode(String.self, forKey: .tool)
        command = try? c.decode(String.self, forKey: .command)
        effect = try? c.decode(String.self, forKey: .effect)
        argsText = (try? c.decode(String.self, forKey: .argsText))
            ?? (try? c.decode(String.self, forKey: .argsTextSnake))
        callId = try? c.decode(String.self, forKey: .callId)
        reason = try? c.decode(String.self, forKey: .reason)
        error = try? c.decode(String.self, forKey: .error)
        model = try? c.decode(String.self, forKey: .model)
        provider = try? c.decode(String.self, forKey: .provider)
        contentIndex = (try? c.decode(Int.self, forKey: .contentIndex))
            ?? (try? c.decode(Int.self, forKey: .contentIndexCamel))
        signature = try? c.decode(String.self, forKey: .signature)
        redacted = try? c.decode(Bool.self, forKey: .redacted)
        contentBlocks = try? c.decode([AgentContentBlock].self, forKey: .contentBlocks)
        attachment = try? c.decode(AgentAttachment.self, forKey: .attachment)
        attachments = try? c.decode([AgentAttachment].self, forKey: .attachments)
        providerRoute = try? c.decode(AgentProviderRoute.self, forKey: .providerRoute)
        usage = try? c.decode(AgentUsage.self, forKey: .usage)
        existingRunId = try? c.decode(String.self, forKey: .existingRunId)
        isError = (try? c.decode(Bool.self, forKey: .isError))
            ?? (try? c.decode(Bool.self, forKey: .isErrorCamel))
        terminationReason = (try? c.decode(String.self, forKey: .terminationReason))
            ?? (try? c.decode(String.self, forKey: .terminationReasonCamel))
        numberGuard = (try? c.decode(AgentNumberGuard.self, forKey: .numberGuard))
            ?? (try? c.decode(AgentNumberGuard.self, forKey: .numberGuardSnake))
        contextUsage = try? c.decode(AgentContextUsage.self, forKey: .contextUsage)
        memoryCandidate = try? c.decode(AgentMemoryCandidate.self, forKey: .memoryCandidate)
        memories = try? c.decode([AgentMemoryRecord].self, forKey: .memories)
        recall = (try? c.decode(AgentSourceRecall.self, forKey: .recall))
            ?? (try? c.decode(AgentSourceRecall.self, forKey: .sourceRecall))
        recalls = try? c.decode([AgentSourceRecall].self, forKey: .recalls)
        evidenceSummary = (try? c.decode(ChatEvidenceSummary.self, forKey: .evidenceSummary))
            ?? (try? c.decode(ChatEvidenceSummary.self, forKey: .evidenceSummarySnake))
        evidenceDrawer = (try? c.decode(ChatEvidenceDrawer.self, forKey: .evidenceDrawer))
            ?? (try? c.decode(ChatEvidenceDrawer.self, forKey: .evidenceDrawerSnake))
        operation = try? c.decode(String.self, forKey: .operation)
        item = try? c.decode(AgentQueuedInput.self, forKey: .item)
        queuedInputs = try? c.decode([AgentQueuedInput].self, forKey: .queuedInputs)
        steeringCount = try? c.decode(Int.self, forKey: .steeringCount)
        followUpCount = try? c.decode(Int.self, forKey: .followUpCount)
        researchCandidate = try? c.decode(ResearchCandidate.self, forKey: .researchCandidate)
        liveContexts = try? c.decode([AgentLiveMarketContext].self, forKey: .liveContexts)
    }

    var duplicateReason: String? {
        let values = [type, terminationReason, reason, error]
            .compactMap { $0?.lowercased() }
        if values.contains(where: { $0.contains("duplicate_completed") }) {
            return "duplicate_completed"
        }
        if values.contains(where: {
            $0.contains("already_running") || $0.contains("already has active run")
        }) {
            return "already_running"
        }
        return nil
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

    /// yupi 灌入条目 source 前缀为「热议·」（见 yupi_ingest.SOURCE_PREFIX）。
    var isYupiHot: Bool {
        (source ?? "").hasPrefix("热议")
    }
}

/// 资讯雷达 yupi 监控词（bridge `intel-keywords-get`）。
struct IntelKeywordsResponse: Codable, Equatable {
    var tracks: [String: [String]]?
    var defaults: [String: [String]]?
    var userOverride: [String: [String]]?

    enum CodingKeys: String, CodingKey {
        case tracks, defaults
        case userOverride = "user_override"
    }
}

struct IntelKeywordsSetResponse: Codable, Equatable {
    var ok: Bool?
    var tracks: [String: [String]]?
}

/// KSS 托管 yupi 运行时状态（bridge `yupi-status`）。
struct YupiRuntimeStatus: Codable, Equatable {
    var baseUrl: String?
    var port: Int?
    var model: String?
    var installed: Bool?
    var healthOk: Bool?
    var hasOpenrouterKey: Bool?
    var openrouterKeySource: String?
    var node: String?
    var nodeOk: Bool?
    var gitRef: String?
    var gitHead: String?
    var launchdLoaded: Bool?

    enum CodingKeys: String, CodingKey {
        case port, model, installed, node
        case baseUrl = "base_url"
        case healthOk = "health_ok"
        case hasOpenrouterKey = "has_openrouter_key"
        case openrouterKeySource = "openrouter_key_source"
        case nodeOk = "node_ok"
        case gitRef = "git_ref"
        case gitHead = "git_head"
        case launchdLoaded = "launchd_loaded"
    }
}

struct YupiEnsureResponse: Codable, Equatable {
    var ok: Bool?
    var baseUrl: String?
    var port: Int?
    var model: String?
    var action: String?
    var error: String?
    var hasOpenrouterKey: Bool?
    /// 嵌套 install 失败时 bridge 可能带回
    var install: YupiInstallSlice?

    enum CodingKeys: String, CodingKey {
        case ok, port, model, action, error, install
        case baseUrl = "base_url"
        case hasOpenrouterKey = "has_openrouter_key"
    }
}

struct YupiInstallSlice: Codable, Equatable {
    var ok: Bool?
    var error: String?
}

/// Dashboard 资讯摘要条带（U3 轻量数据）。
struct IntelSummary: Codable, Equatable {
    var updatedTrackCount: Int
    var recentTitles: [String]
}

// MARK: - Longbridge 实时（U1）—— bridge longbridge-quote / intraday-snapshot / intraday-bars / trading-hours 输出

/// 实时快照（bridge `longbridge-quote`）。R1/R12——数字为 bridge 真值字段，直接渲染不经 LLM。
/// 覆盖失败/非陆股通时 `error` 非空、数值字段 nil，UI 据此回退存量 + 标注"非实时"。
/// `longbridge-quotes` 批量响应（R5）：quotes 逐标与单标命令同 shape（含逐标 error 行）。
struct LongbridgeQuotesResponse: Codable {
    var quotes: [LongbridgeQuote]
    var count: Int?
}

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

/// 独立美股行情桥接结果。它不复用 A 股 `RealtimeMerge`，避免把美股权限、
/// 交易阶段和延迟口径混入 ChinaConnect 的实时语义。
struct USMarketQuotesResponse: Codable, Equatable {
    var quotes: [USMarketQuote]
    var count: Int?
    var marketPhase: String?
    var receivedAt: String?
    var coverage: USMarketCoverage?

    enum CodingKeys: String, CodingKey {
        case quotes, count, coverage
        case marketPhase = "market_phase"
        case receivedAt = "received_at"
    }
}

struct USMarketCoverage: Codable, Equatable {
    var live: Int?
    var delayed: Int?
    var stale: Int?
    var `static`: Int?
    var unavailable: Int?
}

struct USMarketQuote: Codable, Equatable, Hashable, Identifiable {
    var code: String
    var name: String
    var last: Double?
    var prevClose: Double?
    var pct: Double?
    var source: String?
    var sourceAsOf: String?
    var receivedAt: String?
    var marketPhase: String?
    var status: String
    var error: String?

    var id: String { code }

    enum CodingKeys: String, CodingKey {
        case code, name, last, pct, source, status, error
        case prevClose = "prev_close"
        case sourceAsOf = "source_as_of"
        case receivedAt = "received_at"
        case marketPhase = "market_phase"
    }

    var hasUsablePrice: Bool {
        last != nil && pct != nil && status != "unavailable"
    }
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

    init(
        timestamp: String? = nil,
        open: Double? = nil,
        high: Double? = nil,
        low: Double? = nil,
        close: Double? = nil,
        volume: Double? = nil,
        turnover: Double? = nil
    ) {
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.turnover = turnover
    }

    enum CodingKeys: String, CodingKey {
        case timestamp, time, open, high, low, close, volume, turnover, amount
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Longbridge: timestamp；东财/部分 cache: time
        timestamp = try c.decodeIfPresent(String.self, forKey: .timestamp)
            ?? c.decodeIfPresent(String.self, forKey: .time)
        open = try Self.decodeFlexibleDouble(c, .open)
        high = try Self.decodeFlexibleDouble(c, .high)
        low = try Self.decodeFlexibleDouble(c, .low)
        close = try Self.decodeFlexibleDouble(c, .close)
        volume = try Self.decodeFlexibleDouble(c, .volume)
        turnover = try Self.decodeFlexibleDouble(c, .turnover)
            ?? Self.decodeFlexibleDouble(c, .amount)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(timestamp, forKey: .timestamp)
        try c.encodeIfPresent(open, forKey: .open)
        try c.encodeIfPresent(high, forKey: .high)
        try c.encodeIfPresent(low, forKey: .low)
        try c.encodeIfPresent(close, forKey: .close)
        try c.encodeIfPresent(volume, forKey: .volume)
        try c.encodeIfPresent(turnover, forKey: .turnover)
    }

    private static func decodeFlexibleDouble(
        _ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys
    ) throws -> Double? {
        if let v = try? c.decodeIfPresent(Double.self, forKey: key) { return v }
        if let i = try? c.decodeIfPresent(Int.self, forKey: key) { return Double(i) }
        if let s = try? c.decodeIfPresent(String.self, forKey: key), let v = Double(s) { return v }
        return nil
    }
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
    /// live | local | live_partial（非交易时段降级）
    var source: String?
    /// 会话日 YYYY-MM-DD
    var sessionDate: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case intervalMinutes = "interval_minutes"
        case bars
        case sourceAsofTs = "source_asof_ts"
        case eligibility
        case routedProvider = "routed_provider"
        case manifestStale = "manifest_stale"
        case error, hint
        case source
        case sessionDate = "session_date"
    }

    /// K 线可渲染：有序列即可（local 可能仍带 hint；error 仅在 bars 空时阻断）。
    var isRenderable: Bool { !bars.isEmpty }

    /// 图上/状态条来源文案。
    var sourceLabel: String? {
        switch source {
        case "local":
            if let d = sessionDate, !d.isEmpty { return "本地 · \(d)" }
            return "本地会话"
        case "live_partial":
            return "源 · 部分"
        case "live":
            return "源"
        default:
            return nil
        }
    }
}

// MARK: - 启动自检（plan 2026-07-12-005 / U8，bridge `self-check`）

/// 单项自检结果。status: ok / warn / fail。
struct SelfCheckItem: Codable, Hashable, Identifiable {
    var id: String { item }
    var item: String        // venv / storage / tushare / longbridge / telegram / llm
    var status: String      // "ok" | "warn" | "fail"
    var detail: String
    var fixHint: String?
    var fixAction: String?  // "reinit_runtime" | "open_settings" | nil

    var isOK: Bool { status == "ok" }
    var isWarn: Bool { status == "warn" }
    var isFail: Bool { status == "fail" }

    /// 人读条目名（横幅/设置页共用）。
    var displayName: String {
        switch item {
        case "venv": return "运行时"
        case "storage": return "数据目录"
        case "tushare": return "Tushare"
        case "longbridge": return "Longbridge"
        case "telegram": return "Telegram"
        case "llm": return "LLM 端点"
        case "sidecar": return "后台服务"
        case "kss_db": return "统一库"
        case "duckdb_ext": return "查询扩展"
        case "intraday_secrets": return "分时采集凭证"
        default: return item
        }
    }
}

struct SelfCheckResponse: Codable, Hashable {
    var items: [SelfCheckItem]
    var generatedAt: String
}

// MARK: - 日志分区（plan 2026-07-12-005 / U7，bridge `log-list` / `log-tail`）

/// 单个日志文件（含轮转代）。
struct LogFileEntry: Codable, Hashable, Identifiable {
    var id: String { name }
    var name: String     // 相对 storage/logs/ 的路径，如 "sidecar.log" 或 "cron/scanner.log"
    var size: Int
    var mtime: String

    var sizeLabel: String {
        if size < 1024 { return "\(size)B" }
        if size < 1024 * 1024 { return String(format: "%.0fKB", Double(size) / 1024) }
        return String(format: "%.1fMB", Double(size) / (1024 * 1024))
    }
}

struct LogListResponse: Codable, Hashable {
    var logs: [LogFileEntry]
}

struct LogTailResponse: Codable, Hashable {
    var name: String
    var lines: [String]
    var totalMatched: Int
    var error: String?
}

// MARK: - 数据源连通性测试（plan 2026-07-12-005 / U4，bridge `datasource-test`）

/// 单候选（主/备）探测结果。
struct DataSourceCandidateProbe: Codable, Hashable, Identifiable {
    var role: String        // "primary" | "fallback"
    var model: String?
    var ok: Bool
    var latencyMs: Double?
    var error: String?
    var hint: String?

    var id: String { role }

    enum CodingKeys: String, CodingKey {
        case role, model, ok
        case latencyMs = "latency_ms"
        case error, hint
    }
}

/// 数据源连通性测试结果（bridge `datasource-test <source>`）。R7。
struct DataSourceTestResult: Codable, Hashable {
    var source: String
    var ok: Bool
    var latencyMs: Double?
    var error: String?
    var hint: String?
    /// 仅 LLM 源非空：主/备各一条。
    var candidates: [DataSourceCandidateProbe]?

    enum CodingKeys: String, CodingKey {
        case source, ok
        case latencyMs = "latency_ms"
        case error, hint, candidates
    }
}

/// 交易时段查询（bridge `trading-hours`，门控实时拉取 / 定时器）。R13/F007。
struct TradingHours: Codable, Hashable {
    var isTradeDay: Bool
    var isTradingSession: Bool
    var sessionEnd: String?
    var now: String?
    /// 应有日线日 YYYY-MM-DD（日线新鲜度锚点；缺省则不标陈旧）
    var referenceTradeDate: String?

    enum CodingKeys: String, CodingKey {
        case isTradeDay = "is_trade_day"
        case isTradingSession = "is_trading_session"
        case sessionEnd = "session_end"
        case now
        case referenceTradeDate = "reference_trade_date"
    }
}
