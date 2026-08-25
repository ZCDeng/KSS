import Foundation

struct HeatmapSnapshot: Codable, Equatable {
    var market: String
    var period: String
    var updatedAt: String
    var tradeDate: String
    var source: String
    var tiles: [HeatmapTile]
    var summary: HeatmapSummary
}

struct HeatmapTile: Codable, Equatable {
    var code: String
    var symbol: String
    var name: String
    var industry: String
    var circMv: Double
    var changePct: Double
    var turnover: Double
    var price: Double
}

struct HeatmapSummary: Codable, Equatable {
    var advanceCount: Int
    var flatCount: Int
    var declineCount: Int
    var turnoverAmount: Double
}

enum HeatmapTape {
    static func canShow(_ snapshot: HeatmapSnapshot) -> Bool {
        snapshot.source == "direct"
            && !snapshot.tradeDate.isEmpty
            && !snapshot.tiles.isEmpty
    }
}

enum HeatmapMessage: Equatable {
    case selectStock(String)
    case refetch(market: String, period: String)

    static func parse(_ body: Any) -> HeatmapMessage? {
        let dict: [String: Any]
        if let object = body as? [String: Any] {
            dict = object
        } else if let text = body as? String,
                  let data = text.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            dict = object
        } else {
            return nil
        }
        let action = (dict["action"] as? String) ?? ""
        if action == "selectStock" {
            let symbol = ((dict["symbol"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
            if symbol.isEmpty || symbol.contains("://") { return nil }
            return .selectStock(symbol)
        }
        if action == "refetch" {
            let market = (dict["market"] as? String) ?? "all"
            let period = (dict["period"] as? String) ?? "day"
            return .refetch(market: market, period: period)
        }
        return nil
    }
}
