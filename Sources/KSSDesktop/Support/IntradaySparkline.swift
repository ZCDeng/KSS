import SwiftUI

/// 迷你分时折线：输入收盘价序列，相对第一点着色（红涨绿跌）。
/// 点数不足时不渲染占位（避免堆叠卡底部空虚线框）。
///
/// R2-U7（KTD7）：`anchor` 非 nil 时启用锚定模式——Y 轴固定用 `SparklineYAxis.range` 算出的
/// 范围（昨收 ± 单调扩大的最大偏离），不再逐帧按当前 `points` 的 min/max 重新缩放，并画一条
/// 昨收虚线；着色也改用「现价 vs 昨收」而非「现价 vs 序列首点」，更贴近涨跌语义。`anchor` 缺省
/// （nil）时保留原 min/max 自适应模式，供无昨收锚点的旧调用方（如快照静态兜底）使用。
struct IntradaySparkline: View {
    @Environment(\.kssTheme) private var theme
    var points: [Double]
    var height: CGFloat = 36
    /// 无数据时是否画虚线框（默认否，紧凑卡布局用）
    var showEmptyPlaceholder: Bool = false
    var anchor: (yMin: Double, yMax: Double, prevClose: Double)? = nil

    private var direction: Double {
        if let anchor {
            guard let last = points.last else { return 0 }
            return last - anchor.prevClose
        }
        guard let first = points.first, let last = points.last, first != 0 else { return 0 }
        return last - first
    }

    var body: some View {
        Group {
            if points.count < 2 {
                if showEmptyPlaceholder {
                    GeometryReader { geo in
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(theme.hairline, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                            .frame(width: geo.size.width, height: geo.size.height)
                    }
                    .frame(height: height)
                }
            } else {
                GeometryReader { geo in
                    let w = geo.size.width
                    let h = geo.size.height
                    let minV = anchor?.yMin ?? points.min() ?? 0
                    let maxV = anchor?.yMax ?? points.max() ?? 1
                    let span = max(maxV - minV, 1e-9)
                    let color = theme.signColor(direction)
                    let yFor: (Double) -> CGFloat = { v in h * (1 - CGFloat((v - minV) / span)) }
                    ZStack {
                        if let anchor {
                            Path { path in
                                let py = yFor(anchor.prevClose)
                                path.move(to: CGPoint(x: 0, y: py))
                                path.addLine(to: CGPoint(x: w, y: py))
                            }
                            .stroke(theme.textSecondary.opacity(0.5),
                                    style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                        }
                        Path { path in
                            for (i, v) in points.enumerated() {
                                let x = w * CGFloat(i) / CGFloat(points.count - 1)
                                let py = yFor(v)
                                if i == 0 { path.move(to: CGPoint(x: x, y: py)) }
                                else { path.addLine(to: CGPoint(x: x, y: py)) }
                            }
                        }
                        .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineJoin: .round))
                    }
                }
                .frame(height: height)
            }
        }
    }
}
