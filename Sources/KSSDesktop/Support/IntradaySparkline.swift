import SwiftUI

/// 迷你分时折线：输入收盘价序列，相对第一点着色（红涨绿跌）。
struct IntradaySparkline: View {
    @Environment(\.kssTheme) private var theme
    var points: [Double]
    var height: CGFloat = 36

    private var direction: Double {
        guard let first = points.first, let last = points.last, first != 0 else { return 0 }
        return last - first
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            if points.count < 2 {
                RoundedRectangle(cornerRadius: 3)
                    .stroke(theme.hairline, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .frame(height: h)
            } else {
                let minV = points.min() ?? 0
                let maxV = points.max() ?? 1
                let span = max(maxV - minV, 1e-9)
                let color = theme.signColor(direction)
                Path { path in
                    for (i, v) in points.enumerated() {
                        let x = w * CGFloat(i) / CGFloat(points.count - 1)
                        let y = h * (1 - CGFloat((v - minV) / span))
                        if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                        else { path.addLine(to: CGPoint(x: x, y: y)) }
                    }
                }
                .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineJoin: .round))
            }
        }
        .frame(height: height)
    }
}
