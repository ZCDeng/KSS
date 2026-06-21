# KSSDeck GSAP 启动页 — QA 矩阵

计划：`docs/plans/2026-06-21-004-feat-kssdeck-gsap-launch-screen-plan.md`
分支：`feat/kssdeck-launch-screen`
日期：2026-06-21

## 自动校验（CLT 环境，无完整 Xcode）

本机 `xcode-select` 指向 Command Line Tools，不含 XCTest，故 `swift test` 无法在此环境运行。
XCTest 套件 `LaunchStateTests.swift` / `LaunchResourceTests.swift` 已就位，装有完整 Xcode 的机器可直接 `swift test`。
为在本环境验证逻辑，把纯 reducer/router 源文件 `LaunchState.swift` 与临时 `main.swift` 一起 `swiftc -O` 编译运行：

| 校验 | 结果 |
| --- | --- |
| 完整正常转移 `booting→animating→awaitingEntry→entering→entered` | PASS |
| `ready` 不提前放行（animating 态 `userEntry` 被忽略） | PASS |
| `entryAvailable` 后才能 Web 进入 | PASS |
| reduced-motion `booting→awaitingEntry`，跳过动画仍需点按钮 | PASS |
| 三处非终态 `failure→fallback`，fallback 按钮可进入 | PASS |
| `entering` 后 `failure` 不回退；重复 `userEntry`/`entryAvailable` 幂等 | PASS |
| `entered` 终态吞掉一切事件 | PASS |
| 消息路由：四类白名单 type 映射正确；未知 type / 缺 type / 裸串 / int → nil | PASS |
| 非法 error `category` 回落 `script`（不暴露任意类别） | PASS |

`swift build`、`./script/build_and_run.sh --verify`：均 PASS（app bundle 构建 + 启动存活）。
三个 Launch 资源已落入 bundle `KSSDesktop_KSSDesktop.bundle/Launch/`（gsap.min.js / launch.html / launch-kss.svg）。

离线与资源契约（shell 等价校验，XCTest 同义）：
- launch.html / launch-kss.svg 唯一 http 引用为 SVG 命名空间 `http://www.w3.org/2000/svg`；无 https、无 cdn/jsdelivr/unpkg/googleapis/@import。
- SVG `<text>` 计数 0；含 `<title>`/`<desc>`/`role="img"`。字标与口号为 HarmonyOS 字体预转换 path。
- gsap.min.js = GSAP 3.12.5 Core，无 MorphSVGPlugin；SHA-256 记于根 `THIRD_PARTY_NOTICES.md`。

## 手工视觉走查（macos-use 实机）

| 组合 | 验证点 | 结果 | 证据 |
| --- | --- | --- | --- |
| Airbnb · 暗色 | 冷启动播 `△ ○ × □ → KSS → Let's join the war!`；首轮后「进入工作台」常驻 + LOADING WORKSPACE；按钮 = accent 粉、白字 | PASS | 暗底 / 粉按钮截图 |
| Airbnb · 暗色 | 点「进入工作台」→ cross-fade → 工作台接管，数据已并行加载完成（指数/推荐全在） | PASS | 进入后 dashboard 截图 |
| Material 3 · 亮色 | 冷启动重新出现 gate（证明仅冷启动触发）；浅紫 canvas / 深墨字标 / M3 紫 accent 按钮白字，首帧无闪错色 | PASS | 亮底截图 |
| Material 3 · 亮色 | **画布空白点击 (400,300) 不进入**——启动层完整保留 | PASS | 点击后启动层仍在截图 |
| Material 3 · 亮色 | 点真按钮 → 进入工作台（亮色路径同样可入） | PASS | 进入后 dashboard 截图 |

验证要点：
- 启动层全窗盖住工作台；reducer 唯一入口是用户激活实际按钮（Web 或原生 fallback），画布点击已实测不放行。
- 启动页配色随当前 `ThemeController` 设计系统 + 亮暗 mode（暗/亮 + 两套设计系统实测），首帧 `underPageBackgroundColor` = canvas，无错色闪烁。
- `store.loadSnapshot()` 与动画并行、只在根 `.task` 调一次；进入即见已加载好的 dashboard。
- 四符号为中性几何（无 PlayStation 名称/配色/logo/手柄轮廓）。

## 未尽事项（诚实记录）

- `swift test` 需完整 Xcode；本环境已用 `swiftc` 验证 reducer/router 全部分支，资源契约用 shell 等价校验，但 XCTest 套件本身未在本机执行。
- reduced-motion 与 watchdog/资源缺失 fallback 两条 **运行时**路径未实机走查（需改系统「减弱动效」设置或破坏 bundle 资源，侵入性高）；其状态转移由 reducer 单测覆盖，原生 `LaunchFallbackView` 编译通过。建议在完整 Xcode 机器上跑 `swift test` 并补这两条运行时截图。
- Web Inspector 的「无 console error / 无网络请求 / 单一背景 timeline / 按钮不失焦」与 Instruments 循环 CPU 检查未做（动画运行平稳、无可见抖动，但未取仪器读数）。
