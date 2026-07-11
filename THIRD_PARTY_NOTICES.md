# 第三方组件声明

本仓库捆绑以下第三方组件。仅打包必要的离线文件，运行时不发起任何网络请求、不引入 npm / Swift Package 依赖，也不做运行时许可校验。

## GSAP (GreenSock Animation Platform) — Core

- 用途：KSSDeck 冷启动启动页（`Sources/KSSDesktop/Resources/Launch/launch.html`）的动画。仅使用 GSAP Core 的 timeline / transform / opacity / stroke-dashoffset；**不**使用 MorphSVGPlugin 或任何 Club 插件。
- 版本：3.12.5
- 文件：`Sources/KSSDesktop/Resources/Launch/gsap.min.js`
- 上游：https://gsap.com/ （分发副本取自 https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js）
- 许可证：GreenSock Standard "No Charge" License — https://gsap.com/standard-license
- SHA-256：`28033e449a31ebcc396e5be8b13b63152bf03094288fb5867034321927bce087`

## HarmonyOS Sans SC

- 用途：x.com 新版设计（`KSSUIGeneration.xcom`）下，Chirp（无中文字形）级联到中文字形的兜底字体；同时用于启动页字标转 SVG（见下）。
- 版本：Regular / Medium / Bold / Black 四档
- 文件：`Sources/KSSDesktop/Resources/HarmonyOS_Sans_SC_{Regular,Medium,Bold,Black}.ttf`
- 上游：https://developer.huawei.com/consumer/cn/doc/design-guides/typography-0000001157868583
- 许可证：HarmonyOS Sans Fonts License Agreement（Huawei Device Co., Ltd. 授权，允许原样打包/嵌入/分发，禁止修改字体本身或单独分发字体文件）

## 启动页字形与符号

`Sources/KSSDesktop/Resources/Launch/launch-kss.svg` 与 `launch.html` 内联的 `KSS` 字标、口号 `Let's join the war!` 均在资源制作阶段由仓库已捆绑的 HarmonyOS Sans SC Bold 字体预转换为静态 SVG path（不在运行时转换、不引用系统字体）。四个几何符号（△ ○ × □）为无品牌抽象图形，不含任何 PlayStation 名称、配色、logo 或官方资产。
