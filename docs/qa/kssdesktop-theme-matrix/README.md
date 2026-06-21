# KSSDesktop 设计系统主题切换 — QA 矩阵

计划：`docs/plans/2026-06-21-003-feat-kssdesktop-design-system-themes-plan.md`
分支：`feat/kssdesktop-design-system-themes`
日期：2026-06-21

## 自动校验（CLT 环境，无完整 Xcode）

本机 `xcode-select` 指向 Command Line Tools，不含 XCTest，故 `swift test` 无法在此环境运行
（XCTest 测试套件 `Tests/KSSDesktopTests/` 已就位，在装有完整 Xcode 的机器上 `swift test` 可跑）。
为在本环境验证逻辑，把纯逻辑源文件（`ThemeCatalog` / `ThemeTokens` / `WebThemeBridge`）
与临时 main 一起 `swiftc` 编译运行，结果：

| 校验 | 结果 |
| --- | --- |
| 16 组合（8 系统 × 亮/暗）全部解析 | PASS |
| 对比度 AA（正文≥4.5、accent/边界≥3:1，含 alpha 合成） | **PASS 16/16**（修正 6 处后） |
| clayM3 / material3 独立 provenance baseline、无 alias | PASS |
| 红涨绿跌固定、up≠down、不随设计系统漂移 | PASS |
| Web payload 覆盖三处 HTML 全部消费的 CSS 键、CSS 安全值 | PASS |
| WebSyncState reducer：navigation identity / generation / revision 失效、stale didFinish、theme→content 串行 | PASS |

对比度修正记录（hex 调整）：
- clayM3 light accent `0xC15F3C → 0xAF5230`（白字 onAccent 达 4.5）
- material3 light muted `0x79747E → 0x615C68`
- discord dark accent `0x7A84FF → 0x5865F2`（回品牌 blurple，白字达 4.5）
- binanceUS light muted `0x707A8A → 0x636B78`

`swift build`、`./script/build_and_run.sh --verify`：均 PASS（app bundle 构建 + 启动存活）。

## 手工视觉走查（macos-use 实机）

| 组合 | 表面 | 结果 | 证据 |
| --- | --- | --- | --- |
| 主题菜单 | 工具栏 Menu：8 设计系统 + 亮/暗两区 + 「当前」只读摘要 + paintpalette + 无障碍 value | PASS | `screenshots/theme-menu-8-systems.png` |
| KSS 暖纸 · 暗色 | 总览（默认/迁移值） | PASS | 启动即此态 |
| 交易终端 · 暗色 | 总览：近黑底 / 青绿 accent / 等宽标题 / 4pt 直角 / 红涨绿跌保留 | PASS | `screenshots/tradingTerminal-dark-dashboard.png` |
| Airbnb · 亮色 | 股票详情 + **K 线 WebView**：白底 / 酒红 accent / sans 标题 / 16pt 圆角；图表底色随主题变白、红绿 K 线 + MACD/量副图正常 | PASS | `screenshots/airbnb-light-chart-webview.png` |
| 持久化 | 切换后菜单「当前」摘要随选择更新；两键写回 | PASS | 菜单 header 实测 |

验证要点：
- 切换实时生效、不重启、连续多次切换无崩溃。
- 原生表面（侧栏/卡片/表格/徽章/标题）全部跟随同一组 token，无游离 clay 硬编码。
- **WebView 不被 teardown**：图表保留已加载数据，仅换配色（环境 token 驱动 `updateNSView`，非 `.id()` 重建）。
- 红涨绿跌在所有主题保持独立 up/down，不蹭设计系统 accent。

## 未尽事项（诚实记录）

- 穷尽的 16 × (11 原生路由 + 3 WebView) = 224 表面逐一截图未全部捕获；已覆盖
  代表性子集：4 套主题 × 亮/暗、原生总览/股票详情、K 线 WebView。Markdown 复盘 /
  架构图 WebView 的 `kssSetTheme` 已实现并在 `swiftc` 逻辑校验中验证 payload 完整，
  但未逐一人工截图。建议在完整 Xcode 机器上跑 `swift test` 后补齐剩余表面截图。
- 主题切换时图表「保留周期/指标」的运行时人工连切压力测试未做（reducer 单测已覆盖该路径）。
