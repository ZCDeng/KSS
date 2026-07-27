---
module: data
tags: [intraday, realtime, provider, longbridge, pit-boundary, feasibility]
problem_type: feasibility-research
status: research-only
date: 2026-07-08
---

# 长桥 OpenAPI 补强 KSS 实时数据源 — 可行性评估

> **2026-07-08 探针实测已跑（见文末「验证结果」）。三个未知量全部落地：CN A股实时权限=有（ChinaConnect LV1）、北交所=不覆盖、账户=paper 账户即带权限。另发现一个必须处理的接入硬坑：SDK 默认的 `openapi.longport.cn` 网关本机不可达，必须改指国际网关 `.com`。下方正文的 ❓ 已被实测替换，保留原判做对照。**

## 结论先行

**可行，且正好补上当前那道断点。** 长桥 OpenAPI 是一个带鉴权的云端行情接口，能替 KSS 现在**打不通的东财实时端点**做前向实时采集。它天然落在既有的 `IntradayProvider` 协议槽里，eligibility 结构上封顶 `forward_observed`，不破 PIT 红线。

**但先别写代码。** 有两个 make-or-break 的事实官方文档没写死，必须先跑一次 20 行探针实测，再决定接不接：

1. **沪深 A 股实时行情权限**到底是「开户即免费」还是「延迟/要付费 Lv1」——官方文档只确认了美股/港股 LV1 免费，CN 一栏留白。
2. **北交所（BSE）是否覆盖**——KSS 覆盖北交所标的（紫苏叶供应链那批），文档只列了「沪/深」，没提北交所。

对应项目纪律 `verify-data-source-before-building`：接外部源前先拉真实响应核字段/粒度。这一步不做，后面全是赌。

---

## 为什么这事值得做（当前断点）

KSS 现有取数分两层：

| 源 | 角色 | eligibility | 现状 |
|---|---|---|---|
| Tushare Pro | 历史 PIT（日线/财务/持仓/指数） | `pit_backtest_eligible` | 正常 |
| 东财 / AKShare | 前向实时分时（1m，仅近 5 交易日） | `forward_observed`（结构封顶） | **本机直连/代理均不通，live 采集被阻塞** |

分时数据层（PR #40）代码全实现了——`IntradayProvider` 协议、`collect_intraday.py`、`probe_intraday_provider.py`、`intraday_store` 全在。**卡点不是代码，是东财那个 `push2his.eastmoney.com` 端点在本机（叠了 Clash 系统代理）连不上**，`_bypass_system_proxy` 把域名塞进 NO_PROXY 也没救活。所以实时采集这条链现在是空转。

长桥补的正是这一格：它不是又一个裸 HTTP 抓取端点，而是**带 token 的云网关（HTTPS/gRPC + WebSocket 长连）**，鉴权后走标准出口，不依赖那个不可达的东财推送域名。用户明确说「接受延迟」——KSS 的用途是盘后复盘 + 前向观察，不是高频，延迟 15 分钟也够用。

---

## 长桥 OpenAPI 核实到的能力

来源：`open.longbridge.com/docs` + SDK 文档 + PyPI。**打星号的是已确认，问号的是文档留白、需实测。**

- **市场覆盖**：港股、美股、CN（股票 + ETF + 指数）。A 股代码格式 `688008.SH` / `300750.SZ`。✱
- **数据类型**：实时报价快照、K 线（candlestick）、盘口深度（depth）、经纪队列（broker queue）、逐笔、WebSocket 订阅推送。✱
- **实时 vs 延迟**：美股/港股 LV1 默认免费。**CN A 股行情权限文档未明确**（中文二手源称「免费实时」，低置信，不采信为事实）。❓
- **北交所**：未确认覆盖。❓
- **鉴权**：需**开长桥券商账户** + `app_key`/`app_secret`/`access_token`（legacy）或 OAuth `client_id`。比 Tushare 单 token 重——有 KYC/开户成本。✱
- **Python SDK**：`longport`（PyPI 有 2.0.0）。`QuoteContext` 拉行情、`TradeContext` 交易。✱（装的时候核一下包名，长桥/longport 品牌迁移期文档口径有互相打架的）
- **限流**：单账户 1 条长连、最多订阅 500 标的、拉取 ≤10 次/秒、并发 ≤5。对 KSS 这种几百标的批量拉的量级完全够。✱

---

## 架构契合度（干净得出奇）

接入点已经现成，不用改协议：

- **协议槽**：`kss/data/intraday_client.py` 的 `IntradayProvider` Protocol。照着 `EastmoneyAkshareProvider` 再写一个 `LongbridgeProvider`，实现 `fetch_bars` / `capability` / `supported_*` 即可。数据层契约照旧：**失败不抛，返回带 `error` 的 `FetchResult`**。
- **PIT 红线守卫**：在 `FORWARD_ONLY_PROVIDERS` 里加 `"longbridge"`。`classify_eligibility()` 是确定性纯函数，加进去之后**无论响应多完整，eligibility 恒 `forward_observed`**——realtime 券商推送本来就不是 PIT 源，这个封顶是对的，不是妥协。
- **切换点只有两处**：`collect_intraday.py:710` 和 `probe_intraday_provider.py:308` 都硬编码 `EastmoneyAkshareProvider()`。加个 `--provider` 参数或 env 开关就能让 `LongbridgeProvider` 平替，甚至东财/长桥双源互为 fallback。

一句话：**这不是新盖一层，是往一个已经建好、目前空转的插座里插一个能通电的 provider。**

---

## 风险与硬门（Gate）

| # | 风险 | 影响 | 拆法 |
|---|---|---|---|
| G1 | CN A 股实时权限可能延迟/要付费 | 用户说接受延迟 → 即便延迟也 OK；但要知道是不是要花钱 | 探针实测：拉 `688008.SH` 快照，比对本地时钟看延迟 |
| G2 | 北交所可能不覆盖 | 覆盖不全 → 长桥只替沪/深/科创/创业，北交所仍缺 | 探针拉一个北交所代码（如 `830799.BJ`），看是否 404/空 |
| G3 | 要开长桥券商账户 | 非纯 API key，有 KYC + 可能要入金 | 决策：用户是否愿意/已有长桥账户 |
| G4 | 长连推送 = 新运维模型 | 现在是 cron 拉取，WebSocket 是常驻长连 | 一期先只用**拉取式**（quote/candlestick），push 二期再说 |
| G5 | K 线历史深度有限 | 长桥不是 PIT 回填源 | 明确分工：**历史仍归 Tushare，长桥只做前向实时**，不碰回测回填 |

---

## 建议路径（先探针，后接线）

**第 0 步（阻塞，先做）— 20 行探针，1 小时内出结论：**

```python
# scratch 探针，不进 repo。目标：核 G1 延迟 / G2 北交所 / 本机代理下可达性
from longport.openapi import QuoteContext, Config
config = Config.from_env()  # 填 app_key/app_secret/access_token
ctx = QuoteContext(config)
print(ctx.quote(["688008.SH", "300750.SZ", "830799.BJ"]))  # 看返回 + 时间戳 + 北交所是否有值
```

跑通看三件事：(a) 叠着系统代理能不能连上长桥网关；(b) A 股返回时间戳离现在多久（实时还是延迟）；(c) 北交所代码有没有数据。

**第 1 步（探针绿灯后）**：`LongbridgeProvider(IntradayProvider)` + `FORWARD_ONLY_PROVIDERS` 加 `"longbridge"` + `--provider` 开关。约 1 个工作单元（U1 级别，参照当初东财 provider 的量）。

**第 2 步**：接进 `collect_intraday` / `probe_intraday_provider`，东财与长桥互为 fallback。

**第 3 步（可选）**：WebSocket 实时快照，喂 `macro/snapshot` 盘中横幅。

**明确不做**：不拿长桥做历史回填、不给它任何 PIT 准入、一期不上交易接口（`TradeContext` 碰都不碰）。

---

## 验证结果（2026-07-08 探针实测）

用户提供 paper trading 账户凭据（`ac: lb_papertrading`），跑 `longport` SDK（PyPI 已确认为 `longport`，非 `longbridge`）实测。探针脚本在 scratchpad，未进 repo。

### 硬坑 G0（新发现，必须处理）：默认网关本机不可达
SDK 默认打 `https://openapi.longport.cn`，本机（叠 Clash 系统代理，7890）**直连和走代理都 000 失败**——和东财那个坑同一性质。国际网关 `https://openapi.longportapp.com` 直连正常（401=已触达）。**解法：三个 env 覆盖，接入时必须写死：**

```
LONGPORT_HTTP_URL=https://openapi.longportapp.com
LONGPORT_QUOTE_WS_URL=wss://openapi-quote.longportapp.com/v2
LONGPORT_TRADE_WS_URL=wss://openapi-trade.longportapp.com/v2
```

不设这个，KSS 会吃到和东财一模一样的「端点不可达」失败。设了就通。

### G1 — CN A股实时权限：✓ 有（超预期）
SDK 启动打印的权限表明确写：**`CN  ChinaConnect LV1 Real-time Quotes`**。实测拉到快照 + 1m K线：

| 标的 | 返回 | last | 板块 |
|---|---|---|---|
| 688008.SH 澜起科技 | ✓ | 253.20 | 科创 |
| 300750.SZ 宁德时代 | ✓ | 372.49 | 创业 |
| 600519.SH 贵州茅台 | ✓ | 1188.80 | 沪主板 |
| 000001.SZ 平安银行 | ✓ | 10.47 | 深主板 |
| 588000.SH 科创50ETF | ✓ | 2.126 | ETF |
| 000688.SH 科创50指数 | ✓ | 2001.59 | 指数 |

1m candlestick 也正常返回 5 根。**科创/创业/沪深主板/ETF/指数全通。**

**延迟大小暂未测准**：跑的时候是收盘后（约 00:14），返回时间戳 `2026-07-07 23:00:00`，市场早已收盘（15:00 收），这个「滞后 73.9 分钟」是收盘态假象，不是盘中真延迟。**权限表写的是 Real-time；真实盘中新鲜度要盘中（9:30–15:00）重跑一次探针钉死。** 但即便按用户「接受延迟」的底线，这条已经够用。

⚠️ 注意 entitlement 是 **ChinaConnect**（沪深港通口径）LV1，不是本地 A 股 LV1。覆盖面 = 陆股通标的池。主流科创/创业大票都在池内（实测已验证），但**非陆股通标的（小盘/次新/ST 等）可能拉不到**——接入时对 KSS 全池扫一遍覆盖率。

### G2 — 北交所（BSE）：✗ 不覆盖（确认）
`830799.BJ`、`920819.BJ` 两个北交所代码**均无返回**。ChinaConnect 口径本就不含北交所。**KSS 的北交所标的（紫苏叶供应链那批）拿不到长桥实时**——这部分仍归 Tushare/东财，长桥只补沪深科创创业。

### G3 — 账户：✓ paper 账户即带 CN 权限
无需为 CN LV1 单独付费开通（至少此账户层级已带）。开户成本这一项在此凭据上已不是阻塞。

### 结论修订
G1/G3 比预期好（实时权限现成、paper 账户就有），G2 坐实缺口（北交所无），并多出一个必须写进接入代码的 G0（国际网关覆盖）。**接入判断从「先探针」推进到「可以写 provider」**——探针已绿，路线不变，`LongbridgeProvider` 里把 G0 的三个 env 固化、eligibility 进 `FORWARD_ONLY_PROVIDERS` 封顶、北交所标的路由回东财即可。唯一待补的实测：盘中重跑一次量真延迟。

---

## 一句话给决策

值得做，卡点正好被它解掉，接入面干净。**但下一个动作不是写 provider，是先花 1 小时跑探针把 G1/G2/G3 三个未知量测掉**——尤其是「要不要开户、A 股实时到底免不免费、北交所覆不覆盖」。这三个答案出来之前，写代码就是赌文档没写的东西。
