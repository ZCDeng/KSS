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
    var pythonEnvironment: PythonEnvironment?
    var recentTaskRuns: [TaskRunResult]
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

struct StockDetail: Codable {
    var symbol: String
    var name: String
    var industry: String
    var concept: String
    var latest: StockSummary?
    var history: [PricePoint]
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
        }
    }

    var lane: String {
        switch self {
        case .previewPicks, .generatePicks, .paperSummary, .logmvBacktest, .radarArchiveAnalysis:
            return "轻量"
        case .formalDailyPicks, .formalPaperSummary, .formalDailyReview, .formalSectorReview, .formalEtfRadarBacktest:
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
    case runbook = "Runbook"
    case reviews = "Reviews"
    case backtests = "Backtests"
    case stocks = "Stocks"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .dashboard: return "总览"
        case .recommendations: return "每日推荐"
        case .watchlist: return "自选"
        case .runbook: return "任务"
        case .reviews: return "复盘"
        case .backtests: return "回测"
        case .stocks: return "股票池"
        }
    }

    var symbol: String {
        switch self {
        case .dashboard: return "gauge.with.dots.needle.50percent"
        case .recommendations: return "target"
        case .watchlist: return "star"
        case .runbook: return "terminal"
        case .reviews: return "doc.text.magnifyingglass"
        case .backtests: return "chart.xyaxis.line"
        case .stocks: return "list.bullet.rectangle"
        }
    }
}
