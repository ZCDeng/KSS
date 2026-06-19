---
title: SwiftUI on macOS：让自定义设计系统盖过系统默认（侧栏/列表/sheet/webview）
module: Sources/KSSDesktop
tags: [swiftui, macos, design-system, navigationsplitview, list-selection, sheet, webview, theming, kss-desktop]
problem_type: ui-patterns
date: 2026-06-19
related: [project_retrospective.md]
---

# SwiftUI on macOS：让自定义设计系统盖过系统默认

KSS Desktop 用一套暖纸 + clay（赭土）设计系统。多处 SwiftUI/AppKit 默认控件会用
系统外观（蓝色选中、半透明材质、固定尺寸）顶掉这套设计，且 `.tint()`、
`.navigationSplitViewColumnWidth` 等"正规"修饰符在 macOS 上不生效。下面是本轮逐个
踩平的坑与可复用的修法，避免下次再各试一遍。

## Context

把一个 SwiftUI macOS app 套上自定义设计系统时，反复遇到"修饰符看着对、实际不生效"
的情况：侧栏折不窄、列表选中是系统蓝、亮色下侧栏透出桌面、图表"放大"开不大。根因都是
**系统容器自带的外观/尺寸策略，优先级高于声明式修饰符**，只能绕开默认容器、自己控。

## Guidance

### 1. NavigationSplitView 侧栏缩不到图标栏 → 改自定义 HStack

`NavigationSplitView` 的侧栏列有一个系统最小宽（约 180pt）。把
`.navigationSplitViewColumnWidth(min:ideal:max:)` 全设成 64 也被忽略——列宽不变，
图标只是在宽列里居中，做不出"折叠成图标栏"。

修法：弃用 `NavigationSplitView`，改自定义 `HStack { sidebar.frame(width:) | Divider | detail }`，
自己管侧栏宽度（折叠 64 / 展开 224）；detail 仍包一层 `NavigationStack` 以保留窗口工具栏
（主题/刷新按钮）。

```swift
HStack(spacing: 0) {
    SidebarView(collapsed: collapsed, onToggleCollapse: { withAnimation { collapsed.toggle() } })
        .frame(width: collapsed ? 64 : 224)
        .frame(maxHeight: .infinity)
        .background(KSSTheme.canvas)
    Divider().overlay(KSSTheme.hairline)
    NavigationStack { detail.toolbar { /* 主题 / 刷新 */ } }
}
```

### 2. List 选中态是系统蓝，`.tint()` 盖不住 → 自管选中 + listRowBackground

`List(selection:)`（含 `.listStyle(.sidebar)`）的选中高亮走系统 accent（蓝），
SwiftUI 的 `.tint(_:)` 在 macOS 上改不动它。

修法：去掉 `selection:` 绑定，行改成 `Button` 手动选中，选中行用
`listRowBackground` 铺自定义色；需要的话把选中行文字也换成强调色。

```swift
List(items) { item in
    let isOn = item.id == selectedID
    Button { selectedID = item.id } label: {
        RowView(item).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .listRowBackground(isOn ? KSSTheme.accent.opacity(0.16) : Color.clear)
}
```

侧栏导航（强选中）可用纯自定义 `Button` 列（铺满 clay + 白字）；数据列表（弱选中）
用 `accent.opacity(0.16)` 淡底更耐看，红涨绿跌等行内色不受影响。

### 3. 亮色主题下侧栏透出桌面成蓝灰渐变 → 去材质 + 实色底

脱离 `NavigationSplitView` 后，`List(.sidebar)` 仍自带 `NSVisualEffect` 半透明材质；
亮色主题下透出桌面壁纸，变成蓝灰渐变，与暖纸底不符（暗色因材质偏暗未暴露）。

修法：`List` 加 `.scrollContentBackground(.hidden)` 去材质，容器铺满高度 + 实色底覆盖
窗口 vibrancy。

```swift
List { ... }.listStyle(.sidebar).scrollContentBackground(.hidden)
// 容器：
.frame(maxWidth: .infinity, maxHeight: .infinity).background(KSSTheme.canvas)
```

### 4. `.sheet` 开不大（"放大"过小）→ 改铺满容器的 overlay

`.sheet` 受父窗口约束、且不认 `idealWidth`（基本按 `minWidth` 开），做不出"动态匹配
窗口尺寸最大化"。

修法：用铺满容器的 `.overlay` 取代 sheet——`frame(maxWidth/maxHeight: .infinity)`，
随窗口尺寸动态最大化、缩放跟随。状态从子视图上抛一层用闭包触发。

```swift
.overlay {
    if showFullscreen, let detail {
        FullscreenView(detail: detail) { showFullscreen = false }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
```

### 5. ScrollView 里嵌 WebView 测高麻烦 → Markdown 用原生渲染

详情面板在 `ScrollView` 里要展示 Markdown 文本（投顾点评等）。`WKWebView` 在 ScrollView
内无法自适应高度。少量结构化文本（`## 段标题` + `**强调**`）直接用原生渲染更稳：按段切分，
段标题用 bold accent，正文用 `AttributedString(markdown:)`。整页 Markdown 文档（复盘/回测
全文）才用 WebView，并在 HTML 里 `max-width + margin:0 auto` 控制行宽与居中。

```swift
Text((try? AttributedString(markdown: line,
      options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(line))
```

### 6. 适配双主题的颜色：NSColor 外观闭包

主题 token 用 `NSColor(name: nil) { appearance in ... }` 按 `bestMatch(.aqua/.darkAqua)`
返回亮/暗色，配 `@AppStorage("appearanceMode")` + `.preferredColorScheme`。图表/Markdown
等 WebView 内的配色另走 JS（`document.documentElement.classList.toggle('dark', isDark)` +
两套 CSS 变量），由 Swift 在重新 `setData` 时下发 `isDark`。

## Why This Matters

这些不是孤立 bug，而是同一类问题：**SwiftUI 的系统容器（NavigationSplitView / List /
sheet）自带外观与尺寸策略，声明式修饰符改不动**。识别出"修饰符不生效 = 该弃用默认容器"
后，修法是统一的——退到自管布局（HStack / Button 列 / overlay）+ 实色底 + 原生渲染。
先认清这点，能省掉一轮轮"换个修饰符再试"的试错。

布局层面同时遵循 Material 3 响应式栅格（内容封顶居中、统一边距/沟槽、断点决定列数），
见全局记忆 `m3-responsive-layout-default`。

## When to Apply

- 给 SwiftUI macOS app 套**任何**非系统默认的设计系统（自定义选中色、底色、侧栏形态）时。
- 现象触发：某个 `.tint` / `.frame` / `.navigationSplitViewColumnWidth` / `.sheet` 尺寸
  "设了没反应"——优先怀疑系统容器在接管，而不是参数写错。
- 桌面端 vibrancy/材质透出非预期颜色时，先 `.scrollContentBackground(.hidden)` + 实色底。

## Examples（本轮落点）

| 坑 | 文件 | 修法提交 |
|---|---|---|
| 侧栏折不窄 | `Views/ContentView.swift` / `SidebarView.swift` | 自定义 HStack + 固定宽 |
| 列表选中蓝 | `StockBrowserView` / `ReviewsView` / `BacktestsView` | Button 行 + clay listRowBackground |
| 亮色侧栏透桌面 | `SidebarView.swift` | scrollContentBackground(.hidden) + canvas 底 |
| 放大 sheet 过小 | `StockBrowserView.swift` | 铺满 overlay + onZoom 闭包 |
| 投顾点评渲染 | `ReviewsView.swift`（CommentaryView） | AttributedString 原生 Markdown |
