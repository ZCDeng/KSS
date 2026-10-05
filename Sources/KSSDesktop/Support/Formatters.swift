import Foundation

enum KSSFormat {
    static func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%+.2f%%", value * 100)
    }

    /// 比率类指标（胜率、波动率、权重）不是涨跌，不带正号。
    static func ratioPercent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.2f%%", value * 100)
    }

    static func pctPoints(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%+.2f%%", value)
    }

    static func number(_ value: Double?, digits: Int = 2) -> String {
        guard let value else { return "-" }
        return String(format: "%.\(digits)f", value)
    }

    /// Tushare 日线 `amount` 的单位是千元，先换算成元再紧凑显示。
    static func amountFromThousandYuan(_ value: Double?) -> String {
        compactMoney(value.map { $0 * 1_000 })
    }

    static func compactMoney(_ value: Double?) -> String {
        guard let value else { return "-" }
        if abs(value) >= 100_000_000 {
            return String(format: "%.2f亿", value / 100_000_000)
        }
        if abs(value) >= 10_000_000 {
            return String(format: "%.0f万", value / 10_000)
        }
        if abs(value) >= 10_000 {
            return String(format: "%.1f万", value / 10_000)
        }
        return String(format: "%.0f", value)
    }
}
