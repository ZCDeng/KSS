import SwiftUI

// 原在 HotspotRotationView.swift；删该页时提取为共享文件（ThemesView 仍在用）。

/// 简易流式标签（板块名长度不一，比 LazyVGrid 自适应更紧凑）。
struct FlowChips: View {
    @Environment(\.kssTheme) private var theme
    var items: [String]
    var tint: Color

    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            ForEach(items, id: \.self) { name in
                Text(name)
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(theme.textBody)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(tint.opacity(0.10), in: Capsule())
                    .overlay(Capsule().stroke(tint.opacity(0.22), lineWidth: 0.5))
                    .lineLimit(1).fixedSize()
            }
        }
    }
}

/// 轻量流式布局（macOS 13+ Layout 协议）：标签从左到右排满换行。
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > maxW, x > 0 {
                x = 0; y += lineH + lineSpacing; lineH = 0
            }
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
        return CGSize(width: maxW == .infinity ? x : maxW, height: y + lineH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        let maxW = bounds.width
        var x: CGFloat = bounds.minX, y: CGFloat = bounds.minY, lineH: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x - bounds.minX + s.width > maxW, x > bounds.minX {
                x = bounds.minX; y += lineH + lineSpacing; lineH = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
    }
}
