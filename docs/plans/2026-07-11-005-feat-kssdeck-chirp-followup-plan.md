---
title: KSSDeck Chirp 排版收尾 - Plan
type: feat
date: 2026-07-11
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# KSSDeck Chirp 排版收尾 - Plan

## Goal Capsule

- **Objective:** 补齐 x.com 设计模式(见 `docs/plans/2026-07-11-004-feat-kssdeck-xcom-design-plan.md`)里遗漏的字体收尾——侧边栏统一 Chirp Medium、页面主标题统一 Chirp(二级标题 `SectionHeader` 上一轮已完成,本次不涉及)——并修复排查中发现的一个真实 bug:WebView 中文级联顺序错误导致部分页面中文标题回退到系统字体。
- **Product authority:** 用户本人(KSSDeck 唯一使用者与决策者)。
- **Open blockers:** 无。

## Product Contract

### Summary

侧边栏文字("总览"等导航项、GitHub 链接)在"新版"模式下统一走 Chirp,基准字重降为 Medium,导航项选中态保留 Semibold 差异(经典 8 套主题的侧边栏视觉不受影响;KSSDeck 字标是位图图片,不受影响)。页面主标题(`PageTitle`)和之前被错误认为已完成的两处 `IntelView` 标题补齐接入 Chirp。同时修复一个真实 bug:`xcomChirp` 的 CSS 字体栈里 `-apple-system` 排在 `TsangerJinKai02` 之前,导致 WebView(markdown/architecture)渲染的中文标题被 WebKit 自己的系统字体级联"截胡",永远轮不到 TsangerJinKai02。

### Problem Frame

`docs/plans/2026-07-11-004-...-plan.md` 的 U9 把 ~330 个 `.font(.system(...))` 调用点扫成了主题感知的 `KSSFont.themed(...)`,但漏了两类:(1) 侧边栏本身已经在扫的范围内,但字重沿用了各处原有的 semibold/heavy,没有统一成本次要求的 medium;(2) `KSSFont.title(_:_:design:)` 是一个独立的静态帮助函数(不是 `.font(.system(...))` 字面调用),U9 的正则扫描没覆盖到它,导致 `PageTitle`(全应用的主标题组件)和 `IntelView` 里两处标题从未接入 Chirp。

排查这两处时用 CoreText 直接验证了 `KSSFont.themed` 的级联机制本身没问题(标/题/今 三个汉字都正确解析到 TsangerJinKai02-W02),但发现 WebView 侧的 CSS 字体栈顺序有 bug——`"Chirp", -apple-system, "TsangerJinKai02", sans-serif` 里 `-apple-system` 在 WebKit 里会对不在 Chirp 覆盖范围内的字符(包括中文)先尝试自己内部的系统级联(通常落到苹方),导致 CSS 引擎认为"-apple-system 已经有这个字符的字形"而不再往后找 `TsangerJinKai02`。这解释了用户观察到的"部分页面二级标题中文字体不对"——只发生在 markdown/architecture 这两个走 WebView 渲染的页面,不影响纯原生页面。

### Key Decisions

- **KD1 — 侧边栏字重只在 xcom 模式下变 medium,经典 8 套主题不受影响。** `KSSFont.themed` 需要一个独立于现有 `weight` 参数的新可选参数,专门覆盖 Chirp 路径选用的字重文件,不动经典模式的系统字体回退。与 x.com 设计模式既定的"新版/经典完全共存,经典不受影响"原则一致。
- **KD2 — `PageTitle`/`IntelView` 迁移到 `KSSFont.themed` 时,`weight`/`design` 参数原样传递。** 经典模式行为逐字节不变(同样的 weight + `theme.titleDesign`),只是让 xcom 模式能命中 Chirp 分支——零回归。
- **KD3 — WebView 字体栈修复只调整顺序,不改机制。** `TsangerJinKai02` 挪到 `-apple-system` 之前,不引入新的 CSS/JS 逻辑;`markdown.html`/`architecture.html` 已经动态读 `p.typography.serif/sans` 设置 CSS 变量,顺序改了以后无需其他改动。

### Requirements

**字体统一**
- R1. "新版"(xcom)模式下,侧边栏文字基准字重统一降为 Chirp Medium(替代各处原有的 semibold/heavy);导航项(`navRow`/`collapsedNav`)的选中态保留比未选中态更重一档的字重(Semibold vs Medium),延续现有"颜色 + 图标实心 + 字重"三重选中态信号,不因这次统一而只剩两重信号。经典 8 套主题的侧边栏视觉(含字重)不受影响。KSSDeck 字标(wordmark)实际由位图图片渲染,不受此项影响(见 Scope Boundaries)。
- R2. 页面主标题(`PageTitle` 组件)和 `IntelView` 里另外 2 处仍用旧 `KSSFont.title()` 静态帮助函数的标题,在"新版"模式下统一走 Chirp;经典模式渲染结果不变。

**Bug 修复**
- R3. `xcomChirp` 的 `serif`/`sans` CSS 字体栈顺序修正为 `"Chirp", "TsangerJinKai02", -apple-system, sans-serif`(中文级联字体紧跟在 Chirp 后面,系统泛型字体放最后),使 markdown/architecture 两个 WebView 渲染的中文标题正确落到 TsangerJinKai02 而不是被 `-apple-system` 的内部级联截胡。

### Scope Boundaries

**Outside this scope**
- 不改动卡片正文、chip 标签、数字显示等其他字体粗细——只动侧边栏 + 标题两类。
- 不改变经典 8 套主题的任何视觉(颜色、字体、圆角)。
- 二级标题组件 `SectionHeader` 已经在上一轮正确接入 Chirp,本次不需要改动(仅作为既有正确实现被复用)。
- KSSDeck 字标(`AppHeader.wordmark`)实际渲染路径是 `Image(nsImage:)` 读取已打包的 `Resources/wordmark.png`,只有该图片缺失时才会退到 `Text("KSSDeck").font(...)`;`wordmark.png` 始终存在于构建产物里,所以字标视觉不受本计划任何改动影响。U1 仍会顺手把这条 fallback 路径的 `weight` 迁移到 `KSSFont.themed` + `chirpWeight`(防御性一致,零风险),但不算作"字标已统一 Chirp"的达成项。重新用 Chirp 导出 `wordmark.png` 位图是设计资产工作,不在本计划范围内。

---

## Planning Contract

### Key Technical Decisions

- **KTD1 — `KSSFont.themed` 新增可选 `chirpWeight` 参数,默认 nil 保持现有 ~330 个调用点行为不变。** 签名变为 `themed(_ size:, _ weight: Font.Weight = .regular, chirpWeight: Font.Weight? = nil, theme:, design: Font.Design = .default)`。内部逻辑:classic 模式(`nativeFontFamily == nil`)始终用 `weight` 走 `.system(...)`;xcom 模式下,字重文件选择用 `chirpWeight ?? weight`。侧边栏 9 处调用点补上 `chirpWeight`:`navRow`/`collapsedNav` 的图标 + label 用 `chirpWeight: isOn ? .semibold : .medium`(保留选中态字重差异,见用户决策);`AppHeader`/`SidebarFooter` 其余 7 处无选中态概念,统一 `chirpWeight: .medium`。其余 ~320 个调用点不改。
- **KTD2 — `KSSFont.title(_:_:design:)` 静态函数在迁移后删除。** 迁移前用 `grep -rn "KSSFont\.title("` 确认只有 3 处调用(`Components.swift`、`IntelView.swift` × 2);迁移完成后这 3 处全部改用 `KSSFont.themed`,`KSSFont.title` 无调用方,直接删除该函数(不留死代码)。
- **KTD3 — CSS 字体栈顺序修复是 `ThemeCatalog.swift` 一处 Swift 常量改动,不碰 3 个 HTML 文件。** `markdown.html`/`architecture.html` 的 `kssSetTheme`/`applyThemePayload` 已经把 `p.typography.serif`/`p.typography.sans` 原样写入 `--serif`/`--sans` CSS 变量(见 U8 的 U7 单元),字符串顺序改了以后浏览器侧自动生效,不需要改 HTML/JS。`chart.html` 的图表库自身坐标轴字体沿用系统字体的既有决定不受影响(K 线价格/日期标签是功能性数字显示,与本次品牌字体无关)。

### Assumptions

- 本环境无显示器,WebView 渲染结果的最终确认(markdown/architecture 页面中文标题实际显示 TsangerJinKai02)依赖 CSS 字体栈顺序的既有 WebKit 行为推断 + 单元里能做的间接验证,不是像素级截图核对——建议后续在有屏幕的机器上过一遍 AI 复盘/架构图页面确认。

---

## Implementation Units

### U1. `KSSFont.themed` 新增 `chirpWeight` 覆盖参数 + 侧边栏迁移到 Medium

- **Goal:** 让侧边栏在 xcom 模式下基准字重降为 Chirp Medium、导航项选中态保留 Semibold 差异,经典模式字重完全不变。
- **Requirements:** R1
- **Dependencies:** 无
- **Files:** `Sources/KSSDesktop/Support/Theme.swift`、`Sources/KSSDesktop/Views/SidebarView.swift`
- **Approach:** `KSSFont.themed` 签名加一个默认 nil 的 `chirpWeight: Font.Weight?` 参数;内部 `weightSuffix` 调用改成 `weightSuffix(chirpWeight ?? weight)`,`.system(...)` 回退分支继续只用 `weight`(不受 `chirpWeight` 影响,经典模式零回归)。`SidebarView.swift` 里现有 9 处 `KSSFont.themed(...)` 调用全部补上 `chirpWeight`:`navRow`(图标 + label 两处)、`collapsedNav`(图标一处)传 `chirpWeight: isOn ? .semibold : .medium`,保留选中态比未选中态更重一档,延续"颜色 + 图标实心 + 字重"三重选中态信号;`AppHeader` 的 toggleButton + wordmark fallback、`SidebarFooter` 的 3 处(无选中态概念)统一传 `chirpWeight: .medium`。原有 `weight` 参数(含 `navRow` 现有的 `isXcom && isOn ? .bold : .semibold` 表达式)原样保留不删除,只影响经典模式。`AppHeader.wordmark` 那一处是防御性迁移(该 `Text` 分支在 `wordmark.png` 存在时永远不会执行,详见 Scope Boundaries),不计入本单元的可见效果验收。
- **Test scenarios:**
  - 经典模式(`nativeFontFamily == nil`):侧边栏 9 处渲染字重与改动前逐一比对一致(semibold/heavy/bold-三元不变,`chirpWeight` 完全不生效)。
  - xcom 模式:`navRow`/`collapsedNav` 选中项渲染 Chirp Semibold、未选中项渲染 Chirp Medium,两者可辨;`AppHeader`/`SidebarFooter` 其余 7 处统一渲染 Chirp Medium。
  - 一个不传 `chirpWeight` 的 `KSSFont.themed` 调用(如 `SectionHeader`)在 xcom 模式下行为不变(用 `weight` 作为 Chirp 字重选择),验证默认值不破坏已有 ~320 处调用点。
- **Verification:** `swift build` 通过;xcom 模式下手动核对侧边栏未选中项为 medium 字重、选中项为 semibold 字重,经典模式(至少一套主题)回归对比改动前截图/记忆一致。

### U2. `PageTitle` / `IntelView` 标题迁移到 `KSSFont.themed`

- **Goal:** 页面主标题和 `IntelView` 里两处标题接入 Chirp,经典模式渲染逐字节不变。
- **Requirements:** R2
- **Dependencies:** 无(不依赖 U1 的 `chirpWeight` 参数,这里 `weight`/`chirpWeight` 用同一个值)
- **Files:** `Sources/KSSDesktop/Support/Components.swift`(`PageTitle`)、`Sources/KSSDesktop/Views/IntelView.swift`(2 处)、`Sources/KSSDesktop/Support/Theme.swift`(删除 `KSSFont.title`)
- **Approach:** 3 处 `KSSFont.title(size, weight, design: theme.titleDesign)` 原样改写为 `KSSFont.themed(size, weight, theme: theme, design: theme.titleDesign)`,`weight`/`design` 参数不变。确认 `grep -rn "KSSFont\.title("` 改动后归零,删除 `Theme.swift` 里的 `KSSFont.title` 静态函数定义。
- **Test scenarios:**
  - 经典模式:3 处标题渲染(字号/字重/design)与改动前完全一致(回归)。
  - xcom 模式:`PageTitle`(如"今日看盘")和 `IntelView` 两处标题渲染 Chirp,中文字符正确级联到仓耳今楷(参照 U1 CoreText 验证方式)。
  - 编译期检查:`KSSFont.title` 删除后 `swift build` 无未解析引用错误。
- **Verification:** `swift build` 通过;`grep -rn "KSSFont\.title("` 无匹配;xcom 模式下手动核对 3 处标题视觉。

### U3. 修复 WebView 中文字体级联顺序

- **Goal:** markdown/architecture 两个 WebView 渲染的中文标题正确落到 TsangerJinKai02,不被 `-apple-system` 的内部系统级联截胡。
- **Requirements:** R3
- **Dependencies:** 无
- **Files:** `Sources/KSSDesktop/Support/ThemeCatalog.swift`(`xcomChirp` 预设)
- **Approach:** `xcomChirp` 的 `serif`/`sans` 字符串从 `"\"Chirp\", -apple-system, \"TsangerJinKai02\", sans-serif"` 改为 `"\"Chirp\", \"TsangerJinKai02\", -apple-system, sans-serif"`(两个字段都改,顺序一致)。不改 `mono`(等宽场景本来就该走系统等宽,与本次无关)。
- **Test scenarios:**
  - 字符串顺序变更后 `swift build` 通过(纯常量改动,无逻辑分支)。
  - 无显示器环境下的间接核实:在 `markdown.html`/`architecture.html` 的 `kssSetTheme` 里,主题切到 xcom 后跑一次 `document.fonts.check('16px "TsangerJinKai02"')`,确认 `@font-face` 声明的字体文件确实加载成功(排除路径编码/格式声明错误这类会让 fallback 静默生效的失败模式)。这只能证明字体文件可用,不能证明级联顺序本身生效——那部分仍需人工视觉核对。
  - 人工核对(需要在有屏幕的机器上做):打开一份 AI 复盘或架构图页面,切到 xcom 模式,确认中文标题字形是仓耳今楷而不是系统苹方/宋体。
- **Verification:** `swift build` 通过;`document.fonts.check` 返回 true;`ThemeTokens.swift` 里 `webPayload.typography` 相关既有测试(如 `ThemeCatalogTests.testWebPayloadCoversAllConsumedCSSVars`)不受字符串内容影响,仍然通过;最终视觉确认待有屏幕环境补做。

---

## Verification Contract

| 验证项 | 命令/方式 | 适用单元 |
|---|---|---|
| 编译通过 | `swift build` | 全部 |
| 死代码检查 | `grep -rn "KSSFont\.title("` 无匹配 | U2 |
| 经典模式回归 | 至少一套经典主题下侧边栏 + 标题视觉与改动前一致 | U1, U2 |
| xcom 模式视觉核对 | 侧边栏字重、标题字体、WebView 中文标题字形人工核对(本机无显示器,建议在有屏幕的机器上补做) | U1, U2, U3 |

## Definition of Done

- 侧边栏在 xcom 模式下**文字**基准字重为 Chirp Medium,导航项选中态保留 Semibold 差异(字标是位图图片,不在此列——见 Scope Boundaries),经典模式字重不变(U1)。
- `PageTitle` 与 `IntelView` 两处标题在 xcom 模式下渲染 Chirp,经典模式不变;`KSSFont.title` 已删除且无残留调用(U2)。
- `xcomChirp` 字体栈顺序修正,`TsangerJinKai02` 排在 `-apple-system` 之前;`document.fonts.check` 确认字体文件加载成功(U3)。
- `swift build` 全程无错误。
- **待补(非阻塞):** 本环境无显示器,侧边栏字重、标题字体、WebView 中文标题字形的最终视觉确认尚未完成,需要在有屏幕的机器上补做(见 Assumptions / Verification Contract)。
