# 研究报告：KSS 内容页 WebView 集成 × Kami 排版

**日期**: 2026-07-29  
**状态**: 结论已定 · 采用现有桥 · 不引入第三方  
**关联**: `MarkdownWebView` / `BridgedWebCoordinator` / commit `5ee5854c`

---

## 1. 问题定义

KSS 有大量**报告/资讯长文**（AI 复盘、AI 回测、资讯雷达长文、投资分析 HTML、研究产物），SwiftUI `Text` / `AttributedString` 对 Markdown 层级、表格、引用、中文衬线不友好。

目标：

1. 长文走 **WKWebView + 离线阅读壳**
2. 排版语言对齐 **Kami（紙）**：[kami.tw93.fun](https://kami.tw93.fun/) / [tw93/kami](https://github.com/tw93/kami)
3. 评估是否引入：
   - `Lision/WKWebViewJavascriptBridge`
   - 各类 SwiftUI-WebView 封装

---

## 2. 库调研结论

### 2.1 Lision/WKWebViewJavascriptBridge（~1.3k★, MIT）

| 维度 | 事实 |
|------|------|
| 定位 | marcuswestin 桥的 Swift 重构；handler 注册 + 双向 callback |
| 平台 | **Package.swift 仅声明 iOS 9+**，非 macOS 一等公民 |
| 机制 | `WKScriptMessageHandler` + 注入 setup JS |
| 适用 | 混合 App：H5 调原生支付/分享，需要命名 handler 与 responseCallback |
| KSS 匹配度 | **低** |

KSS 内容面实际协议只有：

| 方向 | API | 用途 |
|------|-----|------|
| Swift → JS | `kssSetTheme` / `kssSetMarkdown` / `kssSetHTML` | 主题 + 正文 |
| JS → Swift | `webkit.messageHandlers.kssMarkdown` | 内容高度 |
| 导航 | `decidePolicyFor` | 外链系统浏览器 |

无复杂 callback 编排。再包一层 Bridge 只会：

- 与现有 `BridgedWebCoordinator`（generation / stale token / theme→content 串行）**双轨**
- 增加注入脚本与 handler 名冲突面
- 无助于 Kami 视觉

**裁决：不引入。**

### 2.2 SwiftUI-WebView 封装族

代表性：`kylehickinson/SwiftUI-WebView`（~361★）、`globulus/swiftui-webview` 等。  
用户提到的 `duimik/SwiftUI-WebView`、`lucas-moraes/SwiftUIWebView` 在 GitHub 上**无稳定主仓**（404 / 不可检索）。

| 维度 | 事实 |
|------|------|
| 定位 | URL 导航壳：`canGoBack` / title / progress / Cookie |
| 适用 | 内嵌浏览器、OAuth、文档站点 |
| KSS 匹配度 | **低** |

KSS 需要的是：

- `loadFileURL` 离线 `markdown.html`
- 主题 JSON 与内容 JSON **分通道**、可单测的同步状态机
- `fitsContent` 高度回传（嵌在 ScrollView）
- 禁止任意网络导航

通用 wrapper 不提供这些，还要再包一层 = 白加依赖。

**裁决：不引入。继续 `NSViewRepresentable` + `BridgedWebCoordinator`。**

### 2.3 Kami 是什么（以及不是什么）

Kami **不是**独立 macOS App，也不是 WebView 库。它是：

> 面向 AI 交付物的**文档设计系统**：暖纸底、单强调色油墨蓝、衬线层级、编辑向留白。

官方 token（`tokens.json`）：

| Token | 值 | 角色 |
|-------|-----|------|
| `--parchment` | `#f5f4ed` | 页面底 |
| `--ivory` | `#faf9f5` | 抬升面 |
| `--brand` | `#1B365D` | 唯一彩色强调 |
| `--near-black` | `#141413` | 标题墨色 |
| CN 衬线 | TsangerJinKai02 | 标题层级 |

KSS 已在 `5ee5854c` 落地离线子集：

- `Resources/markdown.html` → `data-reader=kami` 编辑皮
- `TsangerJinKai02-W02.ttf` 打进 bundle
- `clayM3` → `readerForPayload` → kami
- 长文页：`ReviewsView` / `BacktestsView` / `IntelView` / `ResearchArtifactPreview` / 投资分析 HTML

**缺口（用户「看不到 Kami」的根因）**：

1. 偏好里 `uiGenerationId=xcom` / `designSystemId=xcom` → 旧逻辑把阅读皮切到 **xcom 线程皮**，不是 Kami  
2. xcom 的 typography 是 Chirp，即使用 kami 布局也会「不像纸」  
3. 舆情热点等结构化卡仍是 SwiftUI（正确：不是长文）

---

## 3. 推荐架构（选定）

```
┌─────────────────────────────────────────────────────┐
│ SwiftUI 壳（列表 / 元数据 / 操作）                     │
│  theme.canvas · 侧栏 · 工具栏（可 xcom / clay / …）   │
└───────────────────────┬─────────────────────────────┘
                        │ 长文 / HTML 报告
                        ▼
┌─────────────────────────────────────────────────────┐
│ MarkdownWebView (NSViewRepresentable)                 │
│  · BridgedWebCoordinator：theme → content 串行        │
│  · 内容面强制 editorial：id=clayM3 + 衬线 typography  │
│  · loadFileURL(markdown.html) 纯离线                  │
└───────────────────────┬─────────────────────────────┘
                        │ evaluateJavaScript
                        ▼
┌─────────────────────────────────────────────────────┐
│ markdown.html                                         │
│  data-reader=kami（固定）                             │
│  kssSetTheme / kssSetMarkdown / kssSetHTML            │
│  height → kssMarkdown message handler                 │
└─────────────────────────────────────────────────────┘
```

**原则**

| 原则 | 说明 |
|------|------|
| 零新依赖 | 不引入 Bridge / SwiftUI-WebView |
| 壳与纸分离 | UI chrome 可跟 xcom；**纸面长文固定 Kami 阅读皮** |
| 离线 | 禁止 CDN；字体与 HTML 在 bundle |
| 主题色可跟 | colors 仍来自当前 palette（暗色 chrome 下长文不刺眼） |
| 字面固定编辑向 | 内容 WebView 强制 `TsangerJinKai02` 标题栈 |

**页面覆盖**

| 页面 | 长文渲染 | 策略 |
|------|----------|------|
| AI 复盘 | `MarkdownWebView` | 固定 Kami |
| AI 回测 | `MarkdownWebView` | 固定 Kami |
| 资讯雷达长文 | `MarkdownWebView` | 固定 Kami |
| 研究产物 / 投资分析 HTML | `ResearchArtifactPreview` → MD/HTML 壳 | 固定 Kami |
| 舆情热点卡 | SwiftUI 结构列表 | **保持原生**（非长文） |
| 板块点评短 MD | `CommentaryView` | 保持原生（嵌套 ScrollView 测高） |

---

## 4. 本轮落地（相对调研的最小补丁）

1. `markdown.html`：默认 `data-reader=kami`；`readerForPayload` **恒返回 kami**（内容壳不再跟 xcom/classic 切换）  
2. `MarkdownWebView`：推送主题前 `asEditorialContentTheme()`——`id=clayM3` + 强制衬线 typography  
3. 单测同步断言  
4. （可选后续）把 light 强调色对齐官方 ink-blue `#1B365D`；暗色保持暖黑体系  

**明确不做**

- SPM 拉 Bridge / SwiftUI-WebView  
- 把 Kami skill 整站 HTML 嵌进 App  
- 舆情热点卡片 WebView 化  

---

## 5. 验收

1. 设置保持「新版 x.com」时，打开 **AI 复盘 / AI 回测 / 资讯长文**：窄栏 + 衬线标题 + 编辑向 h2 左边线  
2. 字体为仓耳今楷（标题），非 Chirp 线程感  
3. `swift test --filter MarkdownWebResourceTests` 通过  
4. 无新增 SPM 依赖  

---

## 6. 决策摘要

| 选项 | 决策 |
|------|------|
| WKWebViewJavascriptBridge | **拒绝**（iOS 向、协议过重、与 BridgedWebCoordinator 重复） |
| SwiftUI-WebView 封装 | **拒绝**（导航壳，不匹配离线内容同步） |
| 自研 MarkdownWebView + Kami 阅读皮 | **采用并强化**（内容面固定 Kami，chrome 可 xcom） |
