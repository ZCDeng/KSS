---
name: kss-indicator-pipeline
description: |
  把「技术/量价指标」从深度研究一路接到 KSS 真 A 股回测、门禁裁决、策略与 Signal Pack、
  内嵌 K 线（lightweight-charts，非 TV.com）标注，以及自选股复盘文字的端到端流水线。
  触发：给指标 / 指标进 KSS / 指标回测 / 写进策略 / TV 图标注 / 自选复盘注入 /
  walk-forward 信号包 / MI 类指标落地 / /kss-indicator-pipeline /
  「研究这个指标能不能进自选」「回测完写进图和复盘」。
  不适用：纯盘面解释（用 kss-review）、只查数据目录（用 kss-orientation）。
---

# KSS 指标落地流水线

把一个指标从「论文/灵感」接到「自选日终真源 + 图 + 复盘」，**禁止跳步**：没有 KSS 真源回测门禁，不写策略、不上图、不灌复盘。

参考实现（已落地）：MI 动量 → `docs/plans/2026-07-11-007-feat-mi-walkforward-signal-pack-plan.md`  
与 `kss/strategies/mi_*.py`、`storage/mi_signals/`、`chart.html` overlay。

---

## 0. 前置（必做）

1. 调 **`get_orientation`**（kss-mcp）或读 [[kss-orientation]]，确认数据根、自选列表、日线路径。
2. 声明假设：标的池（如 kcb50 / 自选）、周期（默认**日线**）、执行口径（默认 **t 收盘信号 → t+1 开盘**）。
3. 定义**可检验成功标准**再动手（例：OOS 相对 buy&hold 或固定参数不显著更差；且解释可复现）。

**硬约束**

- 金融数字只来自回测脚本/工具输出，不手算编造。
- 不给个性化买卖建议；交付是研究级信号与复盘材料。
- 图表面 = **KSS 内嵌 lightweight-charts**，不是 tradingview.com 云端。
- 读写 pack / 规则 / 行情 CSV 走 **`KSS_STATE_ROOT`（`state_root()`）**，禁止只按 `__file__` 指进 `.app/Resources`。

---

## 流水线总览

```
P0 深度研究  →  P1 KSS 真源回测  →  P2 门禁裁决
                                      │
                    ┌─────────────────┴─────────────────┐
                    │ GO                                │ NO-GO
                    ▼                                   ▼
              P3 算法入库 + 规则钉死              文档收口，停
              P4 Signal Pack（日终真源）
              P5 图标注（overlay）
              P6 复盘注入（md + 结构化卡）
              P7 日终 cron + 打包注意
```

每 phase 结束 checkpoint：状态说不清就不要进入下一 phase。

---

## P0 · 深度研究（不写生产代码）

**目标：** 指标定义、变体、文献/实盘口径、失效场景说清楚。

| 产出 | 要求 |
|------|------|
| 定义卡 | 公式、输入字段（OHLCV…）、参数名与合理网格、信号语义（上穿/阈值/Z…） |
| 变体表 | 2–5 个可回测变体（例：SMA vs EWM；零轴 vs Z 分） |
| 执行纪律 | 默认 T+1 开盘；写清是否允许收盘成交假设（KSS 默认不允许暗示） |
| 否决条件 | 何谓「不够进策略」（稀疏交易、仅牛市有效、对参数极度敏感…） |

**动作：** 可用 web/research skill；结果写入 `docs/brainstorms/` 或 plan 的 Research 段，**先不改 `kss/` 生产路径**。

---

## P1 · KSS 真源回测

**目标：** 用仓库内 A 股日线（`cs_data_*.csv` / loader）跑可复现回测，不是合成数据。

1. **池子递进**
   - 先指数/板块子集（如 kcb50）看方向
   - 再落到**当前自选**（`storage/watchlist_symbols.txt` 或项目约定路径）
2. **对照**
   - buy&hold、固定参数 baseline、可选随机/相邻参数敏感性
3. **脚本位置**
   - 探索：`scripts/backtest_<indicator>.py` / `backtest_<indicator>_watchlist.py`
   - 库函数优先落 `kss/features/`、`kss/strategies/`、`kss/backtest/`（薄 CLI）
4. **报告**
   - `storage/reports/` 或 stdout 结构化：收益、回撤、交易次数、持有天数、OOS 切分方式

**数字纪律：** 表格数字 = 脚本输出；引用路径与 asof。

---

## P2 · 门禁裁决（GO / NO-GO）

**在写策略入库前必须显式裁决。** 输出一张表给用户确认（或用户已授权则自决并记录）：

| 维度 | GO 倾向 | NO-GO 倾向 |
|------|---------|------------|
| 经济意义 | 方向符合定义、非纯噪声 | 与随机/BH 无差或更差 |
| 稳健 | 相邻参数不崩、多票不全靠一只 | 单票/单段行情决定一切 |
| 可交易 | 交易次数合理、滑点后仍成立 | 过稀（数年 1 笔）或过密 |
| 可解释 | 规则能一句话说清 | 黑箱堆参 |
| 运维 | 能日终批跑、失败有界 | 只能手调单票 |

- **NO-GO：** 写 `docs/solutions/` 或 plan 结案，**停止** P3+。
- **GO：** 钉死「形态键」（entry/exit/filter 语义），允许滚动的只有 **N / 阈值** 等参数（对齐 MI：形态钉死、参数可 WF）。

---

## P3 · 算法入库 + 规则钉死

**目标：** 可测的库代码 + 每股/默认形态配置。

| 层 | 路径约定（按指标替换名） |
|----|--------------------------|
| 特征 | `kss/features/…` 或既有 `technical.py` |
| 规则引擎 | `kss/strategies/<name>_signal.py`（仓位、买卖、pred） |
| WF（可选） | `kss/backtest/<name>_walk_forward.py` — 只估允许滚动的参数 |
| 配置 | `storage/<name>_rules.yaml`：`defaults` + `symbols`；缺省 → `unpinned=true` |
| 测试 | `kss/tests/test_<name>_*.py` — 可复现、边界、unpinned |

形态键一经钉死，日终任务**不得**日更 entry/exit/filter 脸。

---

## P4 · Signal Pack（图与复盘唯一真源）

**目标：** 日终一份 pack，UI 禁止另算一套点。

1. **schema（最低字段）**  
   `schema_version`, `symbol`, `asof`, `status∈{ok,skipped,error,stale}`, `reason`,  
   生效参数, `entry`/`exit`/`filter`, `unpinned`, `action`, `prev_action`,  
   `pred_score`, `trades`, `trades_preview`, 指标序列（如 `mi_series`）, `param_delta`
2. **I/O**  
   - 写：`{state}/storage/<name>_signals/{asof|latest}/{symbol}.json`  
   - 读：必须 `state_root()` / `KSS_STATE_ROOT`，见 MI 踩坑
3. **投影**（同源）  
   - `to_<name>_signal(pack)` → 桌面卡片 / 复盘结构  
   - `to_<name>_overlay(pack, history_dates=…)` → 图；**markers 时间必须落在 history 窗口**  
   - `format_<name>_section(pack)` → Markdown 研究级段
4. **CLI**  
   `scripts/run_<name>_signal_pack.py` + `scripts/run_<name>_signal_pack_daily.sh`  
5. **bridge**  
   `stock_detail` 挂 `miSignal`/`miOverlay` 同类字段；异常吞掉但 stderr 可诊断，勿静默假成功。

---

## P5 · 内嵌图标注（「TV 图表指标」在 KSS 的含义）

**不是** TradingView.com Pine 云同步；是 **App 内 lightweight-charts**。

| 元素 | 实现要点 |
|------|----------|
| 买卖点 | `setMarkers`；time = 交易日 `YYYY-MM-DD`；与 history 对齐 |
| 当前动作 | **Swift 图区外** `*ChartBanner`（图例与 WebView 之间），禁止叠 OHLC/TF 钮 |
| 参数 | 横幅或 badge 展示 N/规则/asof；**HTML 内 badge 勿 `right` 压 TF** |
| 副图 | 独立 `priceScaleId`；与 MACD/OBV 留边；仅日线；切 1m/5m 清标注 |
| 注入 | 大 JSON 优先 **base64**（`kssSet*OverlayB64`）；主题 `chart.remove` 后重绑 `lastOverlay` |

改完需 **重打包** 才进 `/Applications`：`script/sign_and_build.sh` → notarize → `ditto` 安装。  
**切勿**对已签名 `Resources` 跑 Python 写 `__pycache__`（会毁 codesign）。Bridge 应设 `PYTHONDONTWRITEBYTECODE=1` + `PYTHONPYCACHEPREFIX` 到 state。

---

## P6 · 自选复盘文字

**双通道，缺一不可：**

1. **Markdown 日复盘**  
   `daily_review`（或同类）注入 `format_*_section`；键用 **ts_code**（`688017.SH`），并兼容裸代码回退。
2. **桌面结构化**  
   - 详情 `StockReviewCard` / 专用卡读 `*Signal`  
   - 勿只靠刮 md；bridge 直出字段  
   - 「复盘结论」标题下应能看到指标段（用户心智：复盘结论 = 结论 + 信号，不只 headline）

复盘页全文 md 能看到、自选结论卡看不到 = **未完成 P6**。

---

## P7 · 日终自动化与验收

1. **`config/cron_jobs.yaml` + launchd**  
   交易日收盘后（例 17:15）跑 pack 脚本；`StandardOutPath` 与漏跑判定一致。
2. **漏跑**  
   日志 mtime ≥ 最近一次应跑时刻 → 非 stale；新任务首日可手跑一次写日志。
3. **验收清单（v1 门禁）**

- [ ] 固定输入重跑 pack diff 空（动作/参数/点位）
- [ ] 自选详情：`*Signal` 非 null；图有标记或空态 reason 一致
- [ ] 复盘 md + 结论卡同源动作
- [ ] unpinned 票有明示，不静默
- [ ] 单票失败不拖垮整池
- [ ] 安装包：`codesign --verify --deep --strict` 通过；能启动

---

## Agent 执行协议

1. **先 P0–P2**，门禁未过不改 App UI。  
2. GO 后按 P3→P4→P5→P6→P7 顺序；可并行写测试与投影函数。  
3. 每 phase 用 3–8 行 checkpoint 汇报：做了什么、产物路径、是否阻塞。  
4. 复用 MI 骨架时：**复制模式勿硬编码 mi 名到通用层**；新指标新目录/字段名。  
5. 用户说「只研究不落地」→ 停在 P2。用户说「进自选图和复盘」→ 必须跑完 P4–P6。

---

## 反模式（本会话踩过，禁止再犯）

| 反模式 | 后果 |
|--------|------|
| pack 按 `__file__` 读 Resources | App 内 signal/overlay 全 null |
| 图内绝对定位横幅/badge | 压 OHLC / 压 TF 钮 |
| 复盘只写 md 不写 bridge 字段 | 复盘页有、自选结论无 |
| 签名后在 bundle 内跑 import | `__pycache__` 毁封印，无法打开 |
| markers 日期不在 history 窗 | LWC 静默不画点 |
| 形态与 N 一起日更 | 不可解释、不可钉死 |

更细的 MI 对照与路径表见同目录 `references/worked-example-mi.md`。
