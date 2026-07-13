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

## Python 依赖闭包

打包应用首启 `uv sync --frozen --no-dev` 从 [`pyproject.toml`](pyproject.toml) / [`uv.lock`](uv.lock) 安装到 `~/Library/Application Support/KSS/venv`（bundle 模式）——不随 `.app` 分发源码或 wheel，运行时向 PyPI 拉取官方发行版。以下 58 项许可证从各包 `*.dist-info/METADATA` 的 `License-Expression`（PEP 639）/`Classifier: License ::` 字段核对，取自 `.venv`（`uv sync` 后的真实安装态），非人工转述。

许可证均为宽松式（MIT / BSD / Apache-2.0 / PSF / ISC / Zlib / 0BSD / CC0-1.0 / HPND 系）——**无 GPL / AGPL / LGPL**，闭源分发无冲突。两项含 **MPL-2.0**（弱 copyleft，仅约束修改 MPL 源文件本身，本仓库未修改任一上游文件，二进制分发无义务）单独标注。

| 包 | 版本 | 许可证 |
|---|---|---|
| akshare | 1.18.64 | MIT |
| beautifulsoup4 | 4.15.0 | MIT |
| bs4 | 0.0.2 | MIT |
| certifi | 2026.6.17 | **MPL-2.0** |
| cffi | 2.0.0 | MIT |
| charset-normalizer | 3.4.7 | MIT |
| click | 8.4.1 | BSD-3-Clause |
| contourpy | 1.3.3 | BSD-3-Clause |
| curl-cffi | 0.15.0 | MIT |
| cycler | 0.12.1 | BSD-3-Clause |
| decorator | 5.3.1 | BSD-2-Clause |
| et-xmlfile | 2.0.0 | MIT |
| fastmcp | 3.3.1 | Apache-2.0 |
| fonttools | 4.63.0 | MIT |
| html5lib | 1.1 | MIT |
| idna | 3.18 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD-3-Clause |
| joblib | 1.5.3 | BSD-3-Clause |
| jsonpath | 0.82.2 | MIT |
| kiwisolver | 1.5.0 | BSD-3-Clause |
| lightgbm | 4.6.0 | MIT |
| longbridge | 4.3.3 | Apache-2.0 OR MIT |
| lxml | 6.1.1 | BSD-3-Clause |
| markdown-it-py | 4.2.0 | MIT |
| markupsafe | 3.0.3 | BSD-3-Clause |
| matplotlib | 3.11.0 | PSF-2.0 |
| mdurl | 0.1.2 | MIT |
| mini-racer | 0.14.1 | ISC |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openai | 2.43.0 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.3 | BSD-3-Clause |
| patsy | 1.0.2 | BSD-2-Clause |
| pillow | 12.2.0 | MIT-CMU |
| pyarrow | 24.0.0 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pygments | 2.20.0 | BSD-2-Clause |
| pyparsing | 3.3.2 | MIT |
| python-dateutil | 2.9.0.post0 | BSD-3-Clause OR Apache-2.0 |
| pyyaml | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| scipy | 1.17.1 | BSD-3-Clause |
| simplejson | 4.1.1 | MIT OR AFL-2.1 |
| six | 1.17.0 | MIT |
| soupsieve | 2.8.4 | MIT |
| statsmodels | 0.14.6 | BSD-3-Clause |
| tabulate | 0.10.0 | MIT |
| tqdm | 4.68.3 | **MPL-2.0** AND MIT |
| tushare | 1.4.29 | BSD-3-Clause |
| typing-extensions | 4.15.0 | PSF-2.0 |
| urllib3 | 2.7.0 | MIT |
| webencodings | 0.5.1 | BSD-3-Clause |
| websocket-client | 1.9.0 | Apache-2.0 |
| xgboost | 3.2.0 | Apache-2.0 |
| xlrd | 2.0.2 | BSD-3-Clause |
| yfinance | 1.5.1 | Apache-2.0 |

注：`duckdb` 计划在 Phase C（DuckDB 查询层，U16）引入后补充本表——当前 `pyproject.toml` 尚未依赖它，此处不预先声明未安装的包。
