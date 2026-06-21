import SwiftUI

/// 拥有 reducer 的可观察包装。`entering` 时调度一次 180–220ms cross-fade 完成事件。
final class LaunchModel: ObservableObject {
    @Published private(set) var reducer = LaunchReducer()

    func send(_ event: LaunchEvent) {
        let changed = reducer.send(event)
        guard changed else { return }
        if reducer.state == .entering {
            // 原生层淡出后置入终态，移除启动层。
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
                self?.send(.enterComplete)
            }
        }
    }
}

/// 冷启动 gate：在工作台之上叠加启动层；仅当用户激活实际按钮、reducer 抵达
/// `entering` 后才做 cross-fade 并移除。gate 只在新进程出现，不读写「已看过」偏好。
struct LaunchGateView<Content: View>: View {
    @EnvironmentObject private var theme: ThemeController
    @Environment(\.kssTheme) private var tokens
    @StateObject private var model = LaunchModel()
    private let content: () -> Content

    init(@ViewBuilder content: @escaping () -> Content) {
        self.content = content
    }

    var body: some View {
        ZStack {
            content()
            if model.reducer.isGateVisible {
                launchLayer
                    .opacity(model.reducer.state == .entering ? 0 : 1)
                    .animation(.easeInOut(duration: 0.2), value: model.reducer.state)
                    .allowsHitTesting(model.reducer.state != .entering)
                    .transition(.identity)
            }
        }
    }

    @ViewBuilder private var launchLayer: some View {
        ZStack {
            tokens.canvas.ignoresSafeArea()
            if model.reducer.isFallback {
                LaunchFallbackView { model.send(.userEntry) }
            } else {
                KSSLaunchWebView(payload: theme.webPayload, canvas: tokens.canvasNS) { event in
                    model.send(event)
                }
                .ignoresSafeArea()
            }
        }
    }
}

/// 原生 fallback：复用当前 theme token 的静态 `△ ○ × □ / KSS / 口号` + 「进入」按钮。
/// 按钮只触发 reducer 的用户入口；不自动进入。
struct LaunchFallbackView: View {
    @Environment(\.kssTheme) private var theme
    let onEnter: () -> Void

    var body: some View {
        VStack(spacing: 30) {
            HStack(spacing: 24) {
                LaunchSymbol(kind: .triangle)
                LaunchSymbol(kind: .circle)
                LaunchSymbol(kind: .cross)
                LaunchSymbol(kind: .square)
            }
            .frame(height: 34)

            Text("KSS")
                .font(.system(size: 88, weight: .bold, design: theme.titleDesign))
                .foregroundStyle(theme.textPrimary)

            Text("Let's join the war!")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(theme.textSecondary)

            Button(action: onEnter) {
                Text("进入工作台")
                    .font(.system(size: 15, weight: .semibold))
                    .padding(.horizontal, 40)
                    .padding(.vertical, 12)
                    .background(theme.accent, in: Capsule())
                    .foregroundStyle(theme.onAccent)
            }
            .buttonStyle(.plain)
            .padding(.top, 6)
            .accessibilityLabel("进入工作台")
        }
        .padding(40)
    }
}

/// fallback 用的中性几何符号（与启动 SVG 对应，无品牌资产）。
private struct LaunchSymbol: View {
    enum Kind { case triangle, circle, cross, square }
    let kind: Kind
    @Environment(\.kssTheme) private var theme

    var body: some View {
        let line: CGFloat = 3
        let stroke = theme.textPrimary
        return Canvas { ctx, size in
            let r = min(size.width, size.height) / 2 - line
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            var path = Path()
            switch kind {
            case .triangle:
                path.move(to: CGPoint(x: c.x, y: c.y - r))
                path.addLine(to: CGPoint(x: c.x + r * 0.87, y: c.y + r * 0.5))
                path.addLine(to: CGPoint(x: c.x - r * 0.87, y: c.y + r * 0.5))
                path.closeSubpath()
            case .circle:
                path.addEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
            case .cross:
                path.move(to: CGPoint(x: c.x - r, y: c.y - r))
                path.addLine(to: CGPoint(x: c.x + r, y: c.y + r))
                path.move(to: CGPoint(x: c.x + r, y: c.y - r))
                path.addLine(to: CGPoint(x: c.x - r, y: c.y + r))
            case .square:
                path.addRect(CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
            }
            ctx.stroke(path, with: .color(stroke),
                       style: StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round))
        }
        .frame(width: 34, height: 34)
    }
}
