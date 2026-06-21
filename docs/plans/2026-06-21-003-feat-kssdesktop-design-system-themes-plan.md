# KSSDesktop 设计系统主题切换计划

## 目标与范围

为 macOS SwiftUI 的 KSSDesktop（用户所称 KSSDeck）提供 8 套可选设计系统：当前 KSS Clay M3、Trading Terminal、Skeuomorphism、Material 3 Showcase、The Verge、Airbnb、Discord、Binance.US。每一套都必须提供独立的亮色和暗色模式，并由顶部工具栏中的单一主题菜单切换和持久化。

本计划只覆盖客户端视觉系统；不改 Python 数据桥、市场数据、业务模型或用户数据格式。外部参考源仅用于提炼 token，运行时不依赖 `/Users/zcdeng/Downloads/DesignSystems`。

## 已确认的实现边界

| 事实 | 影响 |
| --- | --- |
| 应用是 macOS 14+ 的 SwiftPM SwiftUI 可执行程序，唯一 target 在 `Package.swift:5-29`。 | 主题层使用 SwiftUI/AppKit，不引入 Web 前端依赖。 |
| 当前亮/暗模式通过 `appearanceMode` 持久化，根视图调用 `.preferredColorScheme`（`Sources/KSSDesktop/App/KSSDesktopApp.swift:34-49`）；工具栏只有二元切换按钮（`Sources/KSSDesktop/Views/ContentView.swift:70-87`）。 | 保留现有外观键，并把工具栏升级为“设计系统 + 外观”选择器。 |
| 单一暖纸/clay 调色板、字体和几何 token 均为静态 `KSSTheme`（`Sources/KSSDesktop/Support/Theme.swift:9-84`），卡片 modifier 的默认圆角也直接绑定 `KSSTheme.shapeM`（`:112-150`）。 | 静态颜色和默认几何都不能表达运行时的系统选择；必须改为可观察的语义 token 环境，并在 modifier 内延迟解析默认几何。 |
| 图表、Markdown、架构图是 3 个独立 WKWebView；当前 bridge 只传 `isDark`，各自 hard-code clay 配色（`Views/ChartWebView.swift:25-56`、`Views/MarkdownWebView.swift:23-46`、`Views/ArchitectureView.swift:47-63`）。 | 主题变更需传递 Web palette，不能只换原生壳层。 |
| `chart.html` 以 `let` 声明了多个后续会重新赋值的运行时状态（如 `currentTheme`、`currentTF`、`rawBars`；`Resources/chart.html:82-92,315-382`）。 | JavaScript 的 `let` 可重新赋值，不应为此机械改成 `var`；主题 API 必须用缓存数据重新绘制图表、保持周期和指标状态，并将 bridge 错误暴露给验证。 |
| 7 个参考 showcase 中只有 Material 3 明确提供成对的亮/暗 token（`material3-showcase.html:10-48`）；其他 showcase 只提供一个参考状态。 | 其余 6 套必须设计并做对比度审查后的 counterpart，不能声称参考文件已提供双模式。 |

## 设计决策

### 主题目录

建立一个以语义 role 为中心的 `ThemeCatalog`，而非为每个设计系统分叉一棵 SwiftUI 组件树。每个条目有 `light` 和 `dark` 两组 token，并覆盖：页面/容器层级、主/次/弱文字、边线、主/辅 accent、`onAccent`、accent/status soft fill、主/辅 chart series、chart grid/crosshair、MA 色、卡片几何、阴影层级、标题/正文/数字字体策略。所有颜色 token 均以可审查的 sRGB 原始值表示；透明色须明确其基底。WebView 同样接收受限的 typography、shape、elevation token，避免只换色而保留旧设计的字体、圆角和阴影。

| ID | 视觉意图 | 亮/暗实现约束 |
| --- | --- | --- |
| `clayM3` | 当前 KSS 暖纸/clay 的 M3-inspired tonal elevation、圆角与字体组合 | 由现有 `Theme.swift` token 迁入 catalog；这是旧用户缺失 `designSystemId` 时的迁移默认，必须保持当前视觉连续性。 |
| `tradingTerminal` | 高密度等宽、近黑与青绿 | 两种模式均保留紧凑数字排版；深色为默认呈现，亮色不得牺牲行情可读性。 |
| `skeuomorphism` | 橙色、实体层次、较明显的高光/阴影 | 阴影是有限 token，不为每个组件复制样式。 |
| `material3` | `material3-showcase.html` 的标准紫色 Material 3 色彩、type/shape/elevation 语义 | 使用 showcase 成对亮/暗 token；与 `clayM3` 独立，不能回落或混用其 clay 色板。 |
| `theVerge` | 深灰、酸性 mint、紫色边线和硬朗边界 | 亮色需保留高对比与品牌重音，但不可用低对比荧光作正文。 |
| `airbnb` | 通透中性底、酒红/珊瑚强调 | 保留充足留白与柔和边界；暗色不能把卡片层级压平。 |
| `discord` | 石墨底、blurple、社区式分层容器 | 深色参考色须配套可读亮色。 |
| `binanceUS` | 交易所黄、石墨文字、数据密度 | 黄色仅作为 accent，不能承担白底普通文字的对比责任。 |

市场语义固定：A 股“上涨=红、下跌=绿”在所有主题中都保持为独立的 `up/down` token，不与每套设计系统的 success/error 或品牌色混用（现有约定见 `Theme.swift:35-40`）。

### 用户交互与状态

顶部工具栏保留一个“主题”入口，但改为 `Menu`：

1. 首行只读显示当前“设计系统 · 亮/暗色”。
2. `设计系统` 区列出 8 个带勾选状态的条目。
3. `外观` 区提供明确的“亮色”和“暗色”两个带勾选状态的条目，不再使用会让操作结果不明确的二元切换按钮。
4. 菜单按钮显示 `paintpalette` 与当前值的 VoiceOver label/value；所有选中状态由文本和勾选表达，不依赖颜色。
5. 使用两个独立的持久化值：新 `designSystemId` 与既有 `appearanceMode`。缺失/非法设计系统回退 `clayM3`，缺失/非法外观回退 `dark`；旧版 `appearanceMode == "system"` 被识别为遗留值，并与缺失/非法外观一样在首次读取时规范化、持久化为 `dark`。这是移除“跟随系统”第三态后的有意行为迁移，不承诺保留旧值在系统亮色时的实际外观；它只保证旧安装保留当前暖纸/clay 设计系统。

不加入“跟随系统”模式：当前需求是每个系统的亮/暗两种可选状态，额外第三态会把验证矩阵从 16 组合扩大且无明确产品要求。

## 可测试验收标准

1. 工具栏菜单可在不重启应用的情况下选择全部 8 套设计系统及亮/暗色，任意顺序切换均不会崩溃；共 16 个有效组合。
2. `designSystemId` 与 `appearanceMode` 在退出并重启后保持；未知或缺失设计系统分别回退为 `clayM3`，未知、缺失或遗留 `system` 外观均在首次读取后回退并持久化为 `dark`。
3. 所有原生页面（侧栏、卡片、表格、按钮、toast、文本与 window tint）在选择变化后立即使用同一组语义 token；当前 clay 色值只能作为 `clayM3` catalog 条目存在，不保留游离硬编码色。
4. `ChartWebView`、`MarkdownWebView`、`LocalHTMLView` 也收到相同版本化 `id/mode/colors/typography/shape/elevation` payload，其背景、正文、边线和 accent 与原生界面一致。
5. 切换图表主题不丢失已加载数据、选定周期或指标可见状态；修正后 console 无 `Assignment to constant variable` 错误。
6. 每套亮/暗 token 对满足普通正文与背景 WCAG AA `>= 4.5:1`，UI/图形边界 `>= 3:1`；红绿行情还保留涨跌文字、箭头或图标提示。
7. 不添加第三方包、远程字体或设计系统 HTML 运行时依赖；继续使用系统字体、`.monospaced` 和现有 `HarmonyOS_Sans_SC_Bold.ttf`（`App/KSSDesktopApp.swift:15-27`）。
8. Web bridge 在首次加载、刷新/导航期间和运行中切换时均先应用主题、再更新内容；其 JavaScript completion handler 无错误，图表主题切换无需等待新的数据推送。
9. `swift test`、`swift build` 以及 `./script/build_and_run.sh --verify` 成功；手工视觉检查覆盖全部 16 个组合 × 11 个原生 `WorkspaceSection` 路由及 3 个 WebView 表面（K 线、Markdown 复盘、架构图），并将截图和检查结果保存到 `docs/qa/kssdesktop-theme-matrix/`。

## 实施步骤

1. **建立纯值主题领域模型与目录。**
   - 新增 `Sources/KSSDesktop/Support/ThemeCatalog.swift`，定义 `KSSDesignSystem`（8 个稳定 raw id，含 `clayM3` 与 `material3`）、`KSSAppearance`（`light`/`dark`）、sRGB `ThemeColor`、`KSSThemeTokens`、几何/阴影/字体策略与 `ThemeCatalog.palette(for:appearance:)`。明确 required role 列表及每个 role 的文本/背景或图形使用面。
   - 同文件定义版本化的 `KSSWebThemePayload: Codable`（`version`、`id`、`mode`、`colors`、`typography`、`shape`、`elevation`）；`colors` 为 hex/sRGB 原始值，键名与三个 HTML 使用的 CSS custom properties 一一对应，包含 `accentSoft`、`onAccent`、`up/down`、`upFill/downFill`、series、grid、crosshair 和 SVG 节点/选中态。`typography` 仅允许 `serif`/`sans`/`mono` 等受限语义 font id，`shape` 仅允许受限的圆角数值，`elevation` 仅允许明确的 opacity/radius/y 数值；三个 HTML 必须消费其对应 CSS variables，禁止自由拼接 CSS。
   - `clayM3` 以现有 `Sources/KSSDesktop/Support/Theme.swift` 的暖纸/clay token 为唯一视觉来源并迁入 catalog；`material3` 使用 `/Users/zcdeng/Downloads/DesignSystems/material3-showcase.html:10-48` 的成对紫色 token。其余 6 套外部参考按上述约束设计 counterpart，并把所有颜色作为可审查的本地 Swift 常量；不得混用 `clayM3` 与 `material3` 的 token。在测试 target 保存不从生产 catalog 派生的 role-level expected token baseline：`clayM3` 对应迁移前 `Theme.swift` 的规范化映射，`material3` 对应该 showcase 的本地转录映射；逐 role 断言来源基线、ID 不同且两者不得经 alias/fallback 互相派生。该 fixture 仅用于测试，不构成运行时外部依赖。
   - 添加纯函数校验（必填 role、颜色格式、正文/背景和 UI 对比度），使 token 变更可单测而不是仅凭肉眼。

2. **引入可观察、可迁移的主题控制器和 SwiftUI 环境。**
   - 新增 `Sources/KSSDesktop/Support/ThemeController.swift`：以 `ObservableObject` + `@Published` 实现，构造器注入 `UserDefaults`（默认 `.standard`），负责读写命名空间明确的 `designSystemId`、兼容既有 `appearanceMode`、把缺失/非法 `designSystemId` 规范化为 `clayM3`、把遗留 `system` 与缺失/非法外观规范化并持久化为 `dark`、暴露当前 `colorScheme`、原生 tokens 与 `KSSWebThemePayload`。测试 suite 必须在 teardown 清理。
   - 在 `Sources/KSSDesktop/App/KSSDesktopApp.swift:31-52` 创建并注入单一 `@StateObject`，以它驱动 `.preferredColorScheme`、`.tint(...)` 和自定义 `EnvironmentValues` token；移除根视图独立的 `@AppStorage("appearanceMode")` 分叉状态。
   - 以 token 环境更新 SwiftUI body，确保主题选择发布后整个视图树重新计算；不再以全局静态 palette 作为设计系统选择的唯一来源。

3. **把共享原生组件改为语义 token 消费者。**
   - 重构 `Sources/KSSDesktop/Support/Theme.swift:9-150`：保留动画和通用 helper，但将静态颜色/几何访问替换为环境中的 `KSSThemeTokens`；把 `signColor` 放到 token/palette 实例上。`KSSCard` 的默认 radius 改为可选值，在 modifier body 内从环境 token 解析，不能以函数默认参数冻结 M3 几何。
   - 更新 `KSSCard`、`PageTitle`、badge、排序控件等 `Support/Components.swift` 使用环境 token；卡片样式继续是 `elevated/filled/outlined` 语义，而非主题特例。
   - 机械迁移所有引用 `KSSTheme` 的 SwiftUI 视图（现已覆盖 `SidebarView`、Dashboard、股票、热点、主题、趋势、复盘、任务等），按设计系统的 typography/shape/elevation token 替换颜色、圆角和硬编码阴影；保留业务内容、布局、导航与数据调用不变。
   - 在迁移前后执行颜色字面量审计（`Color(...)`、`.white`、`.black`、`NSColor(...)`、渐变与 alpha）：每一项必须迁移为语义 token，或在代码旁明确为不随主题变化的 invariant，并列出实际背景和对比度依据。字体采用 token-backed helper；若某类文本因系统字体限制不迁移，计划和验收必须降级为“配色/shape/elevation”而非完整字体系统。

4. **实现顶部工具栏主题菜单。**
   - 在 `Sources/KSSDesktop/Views/ContentView.swift:3-9,70-87` 用注入的 `ThemeController` 替代本地 `appearanceMode`；将当前主题按钮改成上述两段式 `Menu`，加入键盘可达的 `Button`、checkmark、`help` 和 accessibility value。
   - 维持刷新按钮位置、加载 spinner、sidebar 收缩和现有最小窗口大小（`ContentView.swift:40-108`）；选择主题或外观不能触发 store reload、导航重置或侧栏顺序变更。

5. **统一三类 WebView 的 palette bridge。**
   - `Sources/KSSDesktop/Views/ChartWebView.swift`、`MarkdownWebView.swift`、`ArchitectureView.swift` 从环境取得 `KSSWebThemePayload`，向 HTML 发送 JSON-safe payload；`underPageBackgroundColor` 从 palette 而不是 `isDark` 的固定白/深灰计算。
   - 用独立的 `window.kssSetTheme(theme)` API 更新 CSS custom properties，数据 API 只更新数据：`kssSetData` / `kssSetMarkdown` 不再是唯一的主题通道。必要时对 payload 使用 `JSONEncoder`，不经字符串拼接注入颜色值。
   - 每个 coordinator 持有 `{ isLoaded, activeNavigationID, navigationGeneration, synchronizationRevision, contentRevision, lastAppliedContentRevision, latestTheme, latestContent }`，并以单一 `synchronize` 串行所有更新；`updateNSView` 总是先缓存最新 theme/content，内容变动递增 `contentRevision`，已加载时调用该函数。`didStartProvisionalNavigation` 以 `WKNavigation` identity 写入 `activeNavigationID`、重置 ready 状态并递增 generation；`didFinish` 仅处理仍等于 `activeNavigationID` 的 navigation。每次 `synchronize` 均递增 `synchronizationRevision`，并让 theme completion 与 content completion 捕获 `{ navigationID, generation, synchronizationRevision, contentRevision }`；每个 completion 在发送下一步或写入 `lastAppliedContentRevision` 前都必须确认四者仍是当前值。只有 `contentRevision == lastAppliedContentRevision` 时才允许运行中 theme-only push（Chart 仍从缓存 bars 重绘）；否则同步 theme→content。刷新/导航前后的最新状态以该缓存为准，所有 `evaluateJavaScript` completion handler 记录并在 debug/test 路径可断言地报告错误。
   - 将 `LocalHTMLView` 的同步接口从仅 `html.dark` 改为 palette payload，并删除 `Resources/architecture.html:7-13,57-62,308-312` 中与应用主题竞争的 localStorage/内部切换按钮；保留架构图原有交互 chips。

6. **重构三个 HTML 资源为 palette 驱动。**
   - `Resources/chart.html:7-13,78-83,376-429`：由 `kssSetTheme(payload)` 更新 CSS vars 与 Lightweight Charts options；不因 `let` 声明而改写状态类型。主题变更后从 `rawBars` 重建/更新图表，保持 `currentTF` 和 `indState`，且不依赖后续 `kssSetData` 才显示新主题。
   - `Resources/markdown.html:7-14,74-76`：用 `kssSetTheme` 覆盖背景、文本、code/table/blockquote token；切换主题不必重新解析 Markdown。
   - `Resources/architecture.html:15-35`：改为相同的语义 CSS vars，确保 SVG 填充、边线、节点、选中态和流动动画均使用当前系统，而不是旧 clay 常量。

7. **补齐无依赖的测试面与迁移测试。**
   - 修改 `Package.swift:5-29` 新增依赖 `KSSDesktop` 的 `testTarget`，新建 `Tests/KSSDesktopTests/ThemeCatalogTests.swift` 和 `ThemeControllerTests.swift`。
   - 覆盖 8 × 2 的完整性、所有 required role、`clayM3` 与 `material3` 分别匹配独立 provenance baseline 且无 alias/fallback、旧安装默认 `clayM3` 迁移、所有 HTML 的 color/typography/shape/elevation CSS 变量映射、原始和 alpha 合成后的对比度、市场 `up/down` 区分、未知/缺失偏好回退、遗留 `appearanceMode == "system" → dark` 迁移和两键持久化；通过独立 `UserDefaults(suiteName:)` 避免污染用户真实偏好并在 teardown 清理。
   - 将 navigation/generation/revision 判定抽为无 WebKit 副作用的同步状态/reducer，`WKWebView.evaluateJavaScript` 只作执行适配层；对该 reducer、payload 编码/解码、以及 coordinator 的初始加载/导航/主题变更/普通内容更新状态机做单测（含快速主题切换与 reload 的 navigation identity、generation、synchronization/content revision 失效，特别是新导航开始后的 stale `didFinish`/JavaScript completion，以及 theme→content 串行化）。另对 Chart “无新数据主题重绘且保留周期/指标”的纯函数路径做单测；WKWebView 最终像素一致性留给手工验证，避免引入测试依赖。

8. **构建、视觉验证与交付记录。**
   - 独立运行 `swift test`、`swift build`、`./script/build_and_run.sh --verify`；不为把测试塞进现有打包/启动脚本而修改该脚本。
   - 手工走查完整 16 × (11 原生路由 + 3 WebView) 矩阵：每个设计系统及亮/暗模式均检查全部 `WorkspaceSection` 路由、股票图、Markdown、架构图；重点核对 `clayM3` 迁移默认与 `material3` showcase 紫色 token 的视觉隔离。把矩阵 checklist、截图、选择值、复现步骤与 bridge console 结果写入 `docs/qa/kssdesktop-theme-matrix/`。验证重启持久化以及连续快速切换时图表的周期/指标保持。
   - 仅在验证记录里注明参考设计系统的本地来源和人工派生的 counterpart；不复制 showcase 的无效 CSS 字段或远程字体。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 继续使用当前 appearance-adaptive `KSSTheme` 静态颜色只能适配亮/暗，不能代表用户选定的设计系统，导致部分视图保持 clay。 | 使用可观察控制器 + Environment token；对所有 `KSSTheme` 引用及颜色字面量做 repo-wide 检查。 |
| 三个 WKWebView 仅换 dark class，或只接收颜色而保留旧字体/圆角/阴影，出现“原生已切换、网页仍 clay”的分裂界面。 | 统一含受限 visual token 的 JSON bridge，并把 WebView 验收列为必经项目。 |
| 参考文件缺少六套的 counterpart，简单反相造成低对比或品牌失真。 | 采用语义 role 设计，单测对比度并做视觉 QA；Material 3 是唯一直接成对来源。 |
| 黄色/荧光强调色在亮色正文中不达标，或红绿色盲用户无法理解行情。 | Accent 仅用于互动/装饰；正文使用高对比 ink；状态始终同时带文字/图标。 |
| 图表主题更新未从缓存 bars 重绘而显示旧 palette、导航时旧 completion 覆盖新页面、丢失 timeframe/indicator state，或 bridge 错误被吞掉。 | 主题 API 与数据 API 分离，coordinator 以 navigation generation 缓存并串行 theme→data 同步；测试并手工检查状态保持和 completion handler 错误。 |
| 工作树已有大量 CSV 与运行产物改动。 | 只写本计划列出的 Swift/HTML/test 文件及 `docs/qa/kssdesktop-theme-matrix/` 证据；不清理或覆盖无关改动。 |

## 验证顺序

1. 先运行目录/迁移/对比度单元测试，再执行 `swift build`。
2. 用 `./script/build_and_run.sh --verify` 启动实际应用，确认 app bundle/启动路径未因 SwiftPM test target 受影响。
3. 在工具栏逐项走 8 套设计系统 × 亮/暗模式，检查全部 11 个原生路由即时更新且刷新/导航状态稳定；确认旧安装默认进入 `clayM3`，主动选择后 `material3` 使用 showcase 紫色 token。
4. 按 16 × (11 原生路由 + 3 WebView) 矩阵逐项打开 K 线、Markdown、架构图，确认 color、typography、shape、elevation 一致；图表在主题变化后保持数据、周期和指标。
5. 重启应用复核最后选择；最后检查 completion handler/console 无 theme/bridge 错误，并将视觉 QA 证据落盘。

## 停止条件

当且仅当完整 16 × (11 原生路由 + 3 WebView) 矩阵通过主题目录测试、构建验证、WebView 同步检查、持久化复核和对比度标准，并且无已知 theme/bridge JavaScript 错误时，实施工作才可结束；与该功能无关的既有工作树问题不构成阻塞。

## 2026-06-21 审查修订记录

- 修正了把 JavaScript `let` 误判为不可重新赋值的描述；验收改为验证缓存数据重绘、状态保持和 bridge 错误可见性。
- 补充了可序列化、版本化的原生/WebView token 协议及其 required role。
- 补充 WebView 生命周期状态机、颜色字面量审计、`KSSCard` 默认几何解析和字体范围约束。
- 将视觉检查最终统一为 16 个主题/外观组合 ×（11 个原生 `WorkspaceSection` 路由 + 3 个 WebView 表面），补充 QA 证据路径；保留 `swift test` 为独立校验，不扩大打包脚本职责。
- 二次 reviewer-only 后，补充 WebView 非颜色视觉 token、navigation generation 串行化、`system → dark` 迁移和全部原生路由视觉矩阵。
- 三次 reviewer-only 后，以 `WKNavigation` identity 和 synchronization revision 封闭 stale 回调/异步 completion 的竞态，并把该场景列入状态机单测。
- 将当前暖纸/clay 视觉拆为独立 `clayM3` 主题；`material3` 专指 showcase 的成对紫色 M3 token。主题数更新为 8 套、16 个组合，旧安装缺失 `designSystemId` 时保持 `clayM3`。
- 明确 `appearanceMode == "system"` 强制规范化为 `dark` 是移除第三态后的有意行为迁移，并将 WebView revision 判定提升为可独立单测的无 WebKit reducer。

## RALPLAN 一致性 ADR（2026-06-21）

**决策背景。** 当前暖纸/clay 界面必须作为可切换主题继续存在，同时 supplied Material 3 showcase 的紫色成对 token 必须成为另一套独立主题；两者不能被“Material 3”这个描述混为同一来源。主题切换还必须覆盖 11 个原生路由和 3 个 WebView 的异步同步路径。

**备选方案。**

- 把当前界面直接并入 `material3`：拒绝，因为会覆盖 showcase 紫色 token 的明确来源，并破坏旧用户默认外观。
- 保留 `system` 跟随系统第三态：拒绝，因为需求定义每套主题仅亮/暗两种状态，会使持久化与验收矩阵额外分支。
- 以 palette checksum 替代 role-level 来源基线：拒绝，因为哈希不能定位语义 role 漂移，评审与测试诊断能力较差。

**决策。** 保留 8 个稳定 ID 和 16 个组合；`clayM3` 是默认迁移主题，`material3` 仅对应 showcase 的紫色 token；以独立 provenance baseline 和无 alias/fallback 测试证明两者隔离。遗留 `system` 在首次读取时持久化为 `dark`，这是有意行为迁移。WebView 使用版本化完整 token payload，并以无 WebKit reducer 验证导航与异步 completion 竞态。

**后果与交付约束。** 实施需维护测试专用 baseline，并对旧 `system` 用户接受一次明确的外观迁移；不增加依赖或运行时 showcase 文件。Planner、Architect、Critic 已按顺序复核并一致批准；本次仅完成计划复核，不授权实施。

**可用执行角色。** `executor` 负责 Swift/HTML 与测试实现，`verifier` 负责 16 × 14 QA 矩阵与构建证据，`designer` 只在视觉 token 细化或截图判定出现分歧时介入。
