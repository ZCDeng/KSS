---
title: 收盘后板块复盘部署指南
tags: [deployment, sector-review, cron, monitoring, tushare]
problem_type: operations
module: scripts/sector_review
created: 2026-05-13
---

# 收盘后板块复盘部署指南

> 每个交易日 17:30 收盘后，自动拉取全市场行业 / 概念资金流 + 北向，输出
> 「热度 / 资金持续性 / 轮动信号」三类启发式排序，标注科创板池子持仓重叠，
> 推送到 Telegram. 与早盘 9:05 选股推送形成「盘前选股 + 盘后复盘」闭环.

## TL;DR

```bash
# 1. 配置文件（默认权重适合大多数场景，可调）
cat storage/sector_review_config.json

# 2. 部署 cron（每个交易日 17:30 跑）—— 已在 crontab.txt 同步
crontab -l | grep sector_review
# 30 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_sector_review_daily.sh >> /tmp/kss_sector_review.log 2>&1

# 3. 手动测试
bash scripts/run_sector_review_daily.sh --dry-run                 # 仅 print
bash scripts/run_sector_review_daily.sh --date 2026-05-12 --channel telegram  # 真实推送指定日
```

## 1. 数据流与依赖

```
17:30 cron
  ↓
run_sector_review_daily.sh        # .env 加载 → Python 绝对路径
  ↓
scripts/sector_review.py
  ↓
4 个 Tushare API（kss.data.tushare_client）
  ├── moneyflow_ind_dc       # 东财行业资金流（~85 行，含 pct_change/net_amount_rate/buy_elg_amount_rate）
  ├── moneyflow_cnt_ths      # 同花顺概念资金流（~380 概念）
  ├── sw_daily               # 申万指数日线（仅作市场基准，不参与评分）
  └── moneyflow_hsgt         # 北向汇总（单行）
  ↓
3 个评分函数（kss.sector.scorer）
  ├── compute_heat_score      # 加权 min-max 归一化
  ├── compute_flow_persistence # N 日累计 + 连续净流入天数
  └── compute_rotation_signal  # 排名跃升 + 今日净流入
  ↓
KCB 池叠加（kss.sector.kcb_overlay）
  ├── industry_to_codes  → 申万行业名（来自 stock_names.csv）
  └── concept_to_codes   → 同花顺概念名
  ↓
Markdown 5 段（kss.sector.formatter）
  ↓
kss.notifications.manager.send_to_channels  # 复用 paper_trade 同款多通道
  ↓
Telegram bot（自建 server，与 paper_trade 共用 .env 凭据）
```

## 2. 配置文件

`storage/sector_review_config.json`：

```json
{
  "industry_heat_weights": {
    "pct_change": 0.5,            // 涨幅权重
    "net_amount_rate": 0.3,        // 主力净流入率权重
    "buy_elg_amount_rate": 0.2     // 大单买入率权重
  },
  "concept_heat_weights": {
    "pct_change": 0.6,             // 概念只有 2 维（无大单买入率）
    "net_amount": 0.4
  },
  "top_n_industry": 5,             // 行业热度 Top N
  "top_n_concept": 5,              // 概念 Top N
  "top_n_flow": 3,                 // 资金涌入 Top N
  "top_n_rotation": 5,             // 轮动信号 Top N
  "persistence_days": 3,           // 资金持续性回看天数
  "rotation_lookback_days": 3,     // 轮动对照 N 日前数据
  "rotation_rank_jump_threshold": 50  // 排名跃升触发阈值（500 板块全市场用 50 较合理）
}
```

权重不需要和为 1（最终 score 数值不影响排序）.

## 3. 已知限制（v1）

### 3.1 KCB 池行业维度命中率 ≈ 0

**现象**：行业 Top 强势 / 资金涌入两张表的「KCB 池」列大概率全是 `—`.

**原因**：`stock_names.csv` 的 `industry` 字段来自申万一级行业分类（例如「半导体」「软件服务」），
而 `moneyflow_ind_dc` 用的是东方财富的细分行业分类（例如「半导体设备」「证券Ⅱ」），
两套命名空间命名不一致，无法直接 string-equal 匹配.

**v1 决策**：不做跨源映射 —— 任何 fuzzy match 或映射表都会引入新的维护负担和歧义.
KCB 池标注的价值由概念维度提供（同花顺概念命名与 stock_names.csv 的 concept 字段同源，
名称匹配命中可靠）.

**后续可考虑**（已记 deferred）：
- 维护 `storage/industry_aliases.csv` 显式 N:M 映射表
- 或：替换 stock_names.csv 的 industry 字段为东财细分行业（需追加 Tushare API 调用）

### 3.2 概念资金流单位

`moneyflow_cnt_ths.net_amount` Tushare 文档未明确标单位；
v1 通过观察板块聚合幅度推断为「百万元」（formatter 用 `unit="baiwan"` 除以 100 → 亿元）.
若发现数值显著偏离（例如某概念 +100 亿和实际新闻幅度不一致），需重新核实单位.

### 3.3 历史数据滚动

`compute_flow_persistence` 走窗口式 N+1 个工作日，每天调一次 `moneyflow_ind_dc`.
默认 `lookback_days=3` → 单次复盘 4 次 industry API + 各 1 次 concept/sw/hsgt = 7 次 Tushare 调用.
Tushare pro 单日 5000 次额度，远超需求.

## 4. cron 部署

已在 `crontab.txt` 同步（项目根备份）：

```cron
# 板块复盘 (KSS) - 每个交易日 17:30 收盘后（Tushare pro 数据延迟 buffer）
30 17 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_sector_review_daily.sh >> /tmp/kss_sector_review.log 2>&1
```

**为什么选 17:30**：A 股 15:00 收盘，Tushare pro 盘后聚合数据通常 16:30+ 才完整准备好；
17:30 给充分 buffer，避免触发时 API 返回空响应导致整份报告空.
也错开了周五 17:00 周报（`run_paper_trade_weekly.sh`），两条消息互不干扰.

## 5. 故障排查

| 现象 | 排查路径 |
|------|---------|
| 整份报告全 `_数据暂缺_` | 看 `/tmp/kss_sector_review.log`：Tushare token 是否有效？17:30 数据是否到位（可推迟到 18:00 重试） |
| Telegram 没收到 | `_send_notification` 返回 `{telegram: False}` → 看 telegram bot 容器状态：`docker logs telegram-bot-api` |
| 仅个别板块缺数据 | 报告底部「⚠️ 缺失数据源」列出具体字段；单 API 失败不影响其他段 |
| 轮动信号空 | 排名跃升阈值默认 50；A 股板块全天波动有限时本就少见，是正常信号 |
| KCB 池列全 `—` | 见 3.1 已知限制；预期行为，概念维度命中可靠 |

## 6. 验证步骤

新部署后第一天观察：

1. `crontab -l | grep sector_review` 确认条目存在
2. 等 17:30 后看 `/tmp/kss_sector_review.log`：是否有 timestamp + 推送结果行
3. Telegram 是否收到 5 段格式报告
4. 跑一次手动回放：`bash scripts/run_sector_review_daily.sh --date 上一交易日 --dry-run`
5. 测试套件：`pytest kss/tests/test_sector_*.py kss/tests/test_data.py -v`

## 7. 相关文档

- `docs/plans/2026-05-13-001-feat-sector-rotation-review-plan.md` —— 实施计划
- `docs/solutions/paper_trade_deployment.md` —— 早盘选股部署（同模式）
- `docs/solutions/telegram_deployment.md` —— Telegram bot 自建 server
