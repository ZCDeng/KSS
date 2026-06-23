# KSSDeck GSAP 动态启动页计划（复核修订版）

## 结论

为 KSSDeck 增加一个只在进程冷启动时出现、必须由用户点击“进入”才会放行工作台的启动 gate。动效仅借鉴 [GSAP ConvertToPath demo](https://demos.gsap.com/demo/converttopath/) 的视觉语言：描边路径、轮廓收拢和有节奏的文字落点；不复制 demo 源码、结构或交互。

首轮按 `△ ○ × □ → KSS → Let's join the war!` 播放；首轮完成后展示并常驻“进入”按钮，背景继续重复这三个阶段。数据快照在启动层背后并行加载，动画、watchdog、Escape 和画布点击都不能自动进入工作台。

GSAP 的 `MorphSVGPlugin.convertToPath()` 只适合将 SVG 基础图形转为 path，不是文字转路径机制。[官方 API](https://gsap.com/docs/v3/Plugins/MorphSVGPlugin/static.convertToPath/) 因此本期不引入或依赖 MorphSVGPlugin：四个符号、`KSS` 与口号均在资源制作阶段转为静态 SVG path，运行时只使用固定版本的 GSAP Core。

## 复核发现与修订

| 原计划问题 | 复核后的处理 |
| --- | --- |
| 把 demo 当作可复用实现的风险较高。 | 明确为“仅参考视觉风格”；不抓取、不复制 demo 源码，也不承诺像素级复刻。 |
| `ready` 被定义为直接转入 `awaitingEntry`，与“首轮结束后才显示按钮”冲突。 | 状态改为 `ready → animating → awaitingEntry`；仅 `entryAvailable`（首轮完成）进入等待状态。 |
| 假定主题控制器和 XCTest target 尚不存在。 | 现有 `ThemeController`、`kssTheme`/`kssWebTheme` 环境与 `KSSDesktopTests` target 已可复用，不再重复创建。 |
| 仅按 light/dark 固定 clay 色描述启动页。 | 启动层消费当前 `ThemeController` 的语义 token / Web payload，兼容已实现的设计系统与亮暗模式。 |
| “只有 `enterRequested` 可以进入”遗漏原生 fallback 按钮。 | 唯一合法入口改为“用户激活 Web 或原生 fallback 中的实际按钮”；两者都归一为 `.userEntry`。 |

## 已确认事实

| 事实 | 设计影响 |
| --- | --- |
| App 根部已有 `@StateObject` 的 `KSSStore` 与 `ThemeController`，并把 `kssTheme`、`kssWebTheme`、色彩方案和 tint 注入 `ContentView`（`Sources/KSSDesktop/App/KSSDesktopApp.swift:31-47`）。 | 用 `LaunchGateView` 替换根部直接展示的 `ContentView`，但保留同一批 environment 注入及根 `.task { await store.loadSnapshot() }`。 |
| `ThemeController` 已把当前 palette 暴露为原生 `KSSThemeTokens` 与 JSON-safe `KSSWebThemePayload`（`Sources/KSSDesktop/Support/ThemeController.swift:7-30`、`ThemeTokens.swift:8-205`）。 | 原生 fallback 用 `theme.canvas` 等 token；Web 启动页在 document-start 接收编码后的 payload，不能硬编码 clay 色或自行猜测系统外观。 |
| 现有三个 WKWebView 从 `Bundle.module` 加载本地 HTML；资源已由 SwiftPM `.copy(...)` 打包（`Package.swift:14-32`、`Views/ChartWebView.swift:7-56`）。 | 启动页沿用离线 bundle + `WKNavigationDelegate` 模式，不使用 CDN、npm、远端字体或运行时网络。 |
| `KSSDesktopTests` 已存在并依赖可执行 target（`Package.swift:29-33`）。 | 只新增启动状态机、资源和主题注入契约测试；不修改 manifest 来重复添加 target。 |
| `ContentView` 对快照缺失已有 loading/error 路径，`store.loadSnapshot()` 在 App 根任务启动（`KSSDesktopApp.swift:45-46`）。 | 启动 gate 不能等待数据成功，也不能把加载迁移到“进入”按钮回调。 |

## 交互、状态与视觉

### 状态机

将 `LaunchState` 实现为无 UI 副作用的 reducer，状态为 `booting → animating → awaitingEntry → entering → entered`，以及可恢复的 `fallback`。

| 事件 | 合法转移 | 行为 |
| --- | --- | --- |
| 本地 Web 页面 `ready` | `booting → animating` | 首轮 timeline 开始；不显示入口，也不放行工作台。 |
| 本地 Web 页面 `entryAvailable` | `animating → awaitingEntry` | 首轮动画完成，显示并保持 Web “进入”按钮。 |
| 用户点击/键盘激活 Web “进入”按钮 | `awaitingEntry → entering` | 发送 `enterRequested`；原生层在 180–220ms 内淡出启动层。 |
| `prefers-reduced-motion` | `booting → awaitingEntry` | 不创建 tween，直接显示静态 `KSS`、口号和按钮。 |
| Web 导航/脚本/资源错误或 boot watchdog 超时 | `booting`/`animating`/`awaitingEntry → fallback` | 换为原生静态画面及原生“进入”按钮；不自动进入。 |
| 用户激活原生 fallback 按钮 | `fallback → entering` | 使用同一个 `.userEntry` 完成路径。 |

任何重复 `enterRequested`、循环结束、非白名单 message、画布点击、Escape、定时器和后续 navigation 回调均幂等忽略，不能改变为 `entering` 或 `entered`。`entered` 是终态。

### Web 与原生消息边界

注册唯一命名 handler `kssLaunch`，只接受经过 JSON 解码和枚举验证的 `{ type: "ready" | "entryAvailable" | "enterRequested" | "error" }`。`error` 只带稳定错误类别（`script`、`resource`、`navigation`），不得把 stack、文件路径或数据快照送回 UI/日志。Web 按钮必须是实际的 `<button type="button">`，只能在 click 或浏览器标准的键盘 button activation 时发送 `enterRequested`。

原生侧只允许本地 bundle 文件加载；`WKNavigationDelegate` 对外部 URL、popup 和非预期 navigation 均 cancel。WebView content-process termination、provisional/committed navigation failure、资源缺失和 2.5 秒内未收到 `ready` 都进入 fallback。watchdog 的作用仅是保证用户能看到原生“进入”按钮。

### 视觉 timeline

启动层为当前主题的全窗 canvas，图形以单一主视觉组呈现；“进入”在独立 DOM/SwiftUI 层，不属于重复 timeline。

1. **0–120ms，建立画布。** 当前 palette canvas 和低优先级状态文案淡入。
2. **120–720ms，四键显影。** `△ ○ × □` 以错开的 stroke-dashoffset、opacity 和短距离 transform 成形、聚拢。它们是无品牌抽象几何，不使用 PlayStation 名称、颜色、logo、手柄轮廓或官方资产。
3. **680–1,260ms，收拢为 KSS。** 符号与预转换 `KSS` path 用 transform、遮罩和 opacity 交接；KSS 完成描边后填充为当前前景色。
4. **1,260–1,860ms，口号落点。** `KSS` 收束至上方，预转换的 `Let's join the war!` 从基线展开并做 1.00→1.02→1.00 settle。
5. **1,860ms 后，等待并循环。** `entryAvailable` 发出，“进入”在 180–220ms 内淡入，保持位置、焦点和可点击性；主视觉组每 2.5–2.8 秒循环回到第二步。按钮不重建、不移动、不失焦。

`prefers-reduced-motion: reduce` 直接显示静态 `KSS`、口号和按钮，不创建 GSAP tween；用户仍必须激活按钮。

## 实施计划

1. **冻结启动资源与第三方边界。**
   - 新增 `Sources/KSSDesktop/Resources/Launch/launch.html`、`launch-kss.svg` 和固定版本 `gsap.min.js`，在 `Package.swift` 以一个明确的 `.copy("Resources/Launch")` 打入 bundle；通过 `Bundle.module` 和本地 `loadFileURL` 解析。
   - 制作 `launch-kss.svg`：四个符号、`KSS`、口号均为预转换 path，保留稳定的 group/id；提交 SVG `<title>`/`<desc>`，不引用系统字体、现有 PNG、外部 CSS、图片或 URL。保留可编辑的源文件只作为设计资产，不由运行时加载。
   - 在根 `THIRD_PARTY_NOTICES.md`（或已有同类 notice 文件）记录 GSAP 版本、上游 URL、许可证和 SHA-256；不得新增 npm、Swift Package 或网络权限。

2. **实现离线 HTML、主题注入与 GSAP Core timeline。**
   - `launch.html` 只用 GSAP Core 的 timeline、transform、opacity、clip/mask 与 stroke-dashoffset；不调用 `MorphSVGPlugin`，不把 SVG `<text>` 当作可运行时转 path 的内容。
   - 原生端从现有 `KSSWebThemePayload` 以 `JSONEncoder` 生成 document-start user script，写入受控的 theme object/CSS variables；HTML 不拼接外部输入。native under-page background 同时使用 `@Environment(\.kssTheme)` 的 `canvas`，防止 WebView 首帧闪错色。
   - HTML 成功初始化后发送 `ready`；首轮结束且按钮真正可见时发送一次 `entryAvailable`；后续循环绝不再次发送；错误时显示 Web 静态内容并发 `error`，由原生侧决定 fallback。

3. **实现专用启动 WebView 和 reducer。**
   - 新增 `Sources/KSSDesktop/Support/LaunchState.swift`，定义 `LaunchState`、事件和 `.userEntry` completion reason；把消息处理、watchdog 与 reducer 转移放在这里或小型 coordinator，确保可单测及幂等。
   - 新增 `Sources/KSSDesktop/Views/KSSLaunchWebView.swift`，以 `NSViewRepresentable` 创建隔离的 `WKWebViewConfiguration`、`WKUserContentController` 和唯一 `kssLaunch` handler。它不复用数据 WebView 的同步 coordinator，避免把启动门禁和内容同步职责混在一起。
   - 新增原生 `LaunchFallbackView`：复用当前 theme token，提供静态 `KSS`、口号和符合本计划语义的“进入”按钮；fallback 按钮只调用 reducer 的用户入口事件。

4. **接入根视图且不改变业务加载。**
   - 在 `KSSDesktopApp` 的 `WindowGroup` 中用 `LaunchGateView` 包裹现有 `ContentView`，继续注入同一个 store/theme/environment，保留 root `.task { await store.loadSnapshot() }`，保证冷启动只触发一次加载。
   - `LaunchGateView` 同时承载既有工作台和启动层；只有 reducer 到 `entering` 后才执行 180–220ms cross-fade，随后移除启动层。加载慢、bridge 失败或无快照时，点击“进入”后仍交由既有 `ContentView` loading/error UI。
   - gate 仅在新进程启动出现；切换 section、刷新、打开 modal 或返回 dashboard 都不重播，也不读写“已看过”偏好。

5. **补回归测试和资源契约。**
   - 在现有 `Tests/KSSDesktopTests` 新增 `LaunchStateTests.swift`，覆盖完整正常转移、`ready` 不提前放行、`entryAvailable` 后才可 Web enter、reduced motion、fallback button、重复 enter、watchdog、navigation/error、未知 payload 和终态幂等。
   - 新增资源契约测试：三个 Launch 资源可从 `Bundle.module` 找到；HTML/SVG 不含外部 `http(s)`、CDN、`@import` 或远端图片；SVG 含无障碍标签且不含 `<text>`；主题注入 payload 可 JSON round-trip，并覆盖 `ThemeCatalog` 的全部已支持系统与亮暗 mode。
   - 把 WebKit 的帧动画和鼠标/键盘路径留给端到端手工验证，不伪造像素级 unit test。

6. **构建和真实应用验收。**
   - 运行 `swift test`、`swift build`、`./script/build_and_run.sh --verify`。最后一项仅证明 app 存活，不能取代视觉/交互验收。
   - 在每个当前可选设计系统的 light/dark 下验证：首次 timeline、后续循环、reduced motion、Web 按钮鼠标点击、Tab + Space/Return、画布/Escape 不进入、缺资源或脚本错误 fallback、数据慢于动画、以及进入后既有 loading/error 页面。
   - 用 WKWebView 的 Web Inspector 验证无 console error、无网络请求、只有一个背景 timeline、按钮不失焦/不重建；用 Instruments 或 Activity Monitor 检查循环没有无界 CPU 占用或明显帧率抖动。

## 验收标准

1. 冷启动先显示 `△ ○ × □ → KSS → Let's join the war!`，首轮约 1.86 秒；首轮后主视觉持续循环。
2. “进入”只在首轮结束（或 reduced-motion / fallback）时出现，并且只有用户激活实际按钮才进入既有工作台。
3. 动画为本地 bundle 内固定 GSAP Core + 预转换 SVG path；无 CDN、DNS/HTTP 请求、外部字体或 PlayStation 品牌资产。
4. 启动层遵循当前 `ThemeController` 所选设计系统和亮暗 mode，首帧无错误配色闪烁；主文本和按钮满足 AA 对比度。
5. `store.loadSnapshot()` 与动画并行且只调用一次；点击进入时即使无数据也能进入现有 loading/error UI。
6. `swift test`、`swift build` 和存活验证通过；上述人工交互矩阵均有记录，特别是无自动放行、无焦点陷阱和 fallback 可用。

## 不在范围内

- 不将 GSAP 带入工作台、图表、侧栏或页面转场。
- 不复制参考 demo 的源码或具体实现，不新增 MorphSVGPlugin、CDN、npm、Swift Package、远端字体或运行时许可校验。
- 不改数据 bridge、模型、工作台路由、主题目录、用户数据或持久化偏好。
- 不加入自动进入、画布点击进入、Escape 进入或“永久跳过启动页”开关。

## 停止条件

仅当所有测试、构建、离线检查和人工交互矩阵通过，且任何非按钮路径都无法进入 dashboard 时，实施才可交付。
