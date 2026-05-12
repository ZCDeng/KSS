---
title: log_mv 反向纸交易部署指南
tags: [deployment, paper-trade, cron, monitoring]
problem_type: operations
module: scripts/paper_trade_log_mv
created: 2026-05-12
---

# log_mv 反向纸交易部署指南

> 把 KSS 唯一通过 `is_deployable` 门槛的策略（log_mv 反向 + ExecutionModel，
> Sharpe 1.74 / DSR=1.00 prior 族）跑成 30 天纸交易并日推到手机.

## TL;DR

```bash
# 1. 部署 Telegram bot 自建 server（一次性，详见 docs/solutions/telegram_deployment.md）
docker compose -f deploy/telegram/docker-compose.yml up -d

# 2. KSS 项目根 .env 填好 Telegram 凭据（cron wrapper 会从这里 grep）
#    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_API_URL=http://127.0.0.1:8081

# 3. 部署 cron（每个交易日 9:00 跑 + 每周五 17:00 周报）
crontab -e
# 在 crontab 里加：
# 0 9 * * 1-5 cd /Users/zcdeng/projects/KSS && python3 scripts/paper_trade_log_mv.py --channel all >> /tmp/kss_daily.log 2>&1
# 0 17 * * 5 cd /Users/zcdeng/projects/KSS && python3 scripts/weekly_summary.py --channel all --alert-on-degradation >> /tmp/kss_weekly.log 2>&1
```

## 1. 环境变量

写在 KSS 项目根 `.env`（cron wrapper 会 grep 加载，不依赖 zshrc）：

| 变量 | 用途 | 是否必需 |
|------|------|----------|
| `TELEGRAM_BOT_TOKEN` | BotFather 拿到的 bot token | 推送到 Telegram 时必需 |
| `TELEGRAM_CHAT_ID` | 目标 chat id（@userinfobot 查自己 id） | 同上 |
| `TELEGRAM_API_URL` | 自建 server URL，默认 `http://127.0.0.1:8081` | 否（缺省走本机） |
| `TUSHARE_TOKEN` | Tushare Pro API | 若需要拉新数据 |

**获取方式**：见 `docs/solutions/telegram_deployment.md` 6 步走.

## 2. Cron 配置

```bash
# 编辑 crontab
crontab -e
```

加入两行（替换为你的实际项目路径）：

```cron
# log_mv 反向纸交易 - 每个交易日 9:00 跑选股 + 推送
0 9 * * 1-5 cd /Users/zcdeng/projects/KSS && /usr/bin/env python3 scripts/paper_trade_log_mv.py --channel all >> /tmp/kss_daily.log 2>&1

# 周报 - 每周五 17:00 收盘后跑
0 17 * * 5 cd /Users/zcdeng/projects/KSS && /usr/bin/env python3 scripts/weekly_summary.py --channel all --alert-on-degradation >> /tmp/kss_weekly.log 2>&1
```

**关键**：
- `1-5` 限定周一至周五（A 股交易日；节假日仍会触发但数据陈旧 → 脚本自动跳过空池）
- 用 `>> /tmp/kss_daily.log 2>&1` 截留日志便于排查
- 用绝对路径调 python（避免 PATH 不一致），`which python3` 看你的实际路径

**验证 cron 安装**：
```bash
crontab -l  # 确认行已加
```

## 3. 部署后第一周做什么

### Day 1（部署当天）
1. 手动跑一次确认环境：
   ```bash
   python3 scripts/paper_trade_log_mv.py --channel all
   ```
   预期：终端打印 Top 10 选股 + 微信收到推送
2. 看 `storage/paper_trade/<date>.json` 文件已生成

### Day 2~5
- 每天 9:01 看微信收到推送
- 不要交易！只是采集数据
- 收盘后看 `/tmp/kss_daily.log` 确认 cron 跑了且无 error

### Day 5（第一周末）
```bash
# 周报
python3 scripts/weekly_summary.py --lookback 5
```
- 预期：`n_days_logged: 5, n_days_with_returns: 3`（T+2 需 2 天延迟）
- 看 Sharpe 是否在合理范围

### Day 30
- 累计 ~20-22 个交易日
- `python3 scripts/paper_trade_log_mv.py --summary` 看完整对比
- 如果 Sharpe > 1.0 且与历史基线 1.74 偏离 < 50% → 可考虑接实盘小仓位

## 4. 监控

### 退出码语义

| 命令 | 退出码 | 含义 |
|------|--------|------|
| `paper_trade_log_mv.py` | 0 | 正常 |
| `paper_trade_log_mv.py` | 1 | 数据加载失败 / 无可选股票 |
| `paper_trade_log_mv.py --summary --alert` | 2 | **衰减告警**（Sharpe < baseline × 50%）|
| `weekly_summary.py --alert-on-degradation` | 2 | 同上 |

### 配合系统监控

cron 退出码非零 → 系统通常会邮件通知。如需更主动：

```bash
# 在 cron 行末加
... || /usr/local/bin/curl -X POST "https://your-alert-url"
```

### 关键指标 alert 阈值

| 指标 | 健康范围 | 告警阈值 |
|------|---------|---------|
| 周 Sharpe | > 1.0 | < 0.87（baseline × 50%）|
| 周回撤 | > -10% | < -25% |
| 数据完整率 | > 80% | < 50% |
| 周换手率 | < 30% | > 50%（异常频繁换仓）|

## 5. 数据更新

`cs_data_688*.csv` 默认不会自动更新。要让纸交易用上最新数据：

```bash
# 加到 daily cron 里，时间放在 8:30（开盘前）
30 8 * * 1-5 cd /Users/zcdeng/projects/KSS && /usr/bin/env python3 -m kss.cli.main update --pool kcb50 >> /tmp/kss_update.log 2>&1
```

`kss update` 用 Tushare 增量拉数据并更新 CSV.

## 6. 常见问题排查

### Q: Telegram 收不到推送
1. 检查 env：`echo $TELEGRAM_BOT_TOKEN | head -c 10`
2. cron 跑的是 `scripts/run_paper_trade_daily.sh` 包装脚本，它会从 `.env` grep 加载；
   直接绑到 `python3 scripts/paper_trade_log_mv.py` 当 cron 命令则 env 不继承.
3. 自建 server 起着吗：`curl http://127.0.0.1:8081/bot$TELEGRAM_BOT_TOKEN/getMe`
4. 手动跑一次确认：`python3 scripts/paper_trade_log_mv.py --channel telegram`

### Q: 提示"剔除 49 只数据陈旧的股票"
- 数据没更新到目标日期；先跑 `kss update --pool kcb50`
- 或临时放宽：`--date 2025-03-01`（用历史日期）

### Q: 累计 summary 始终为 0
- T+2 数据延迟问题；第一个有真实收益数据的日子是部署后第 3 个交易日
- 等就行

### Q: 选股数 < 10 → 跳过
- 当日科创板池子太多停牌 / 涨停？
- 看 cs_data freshness：`python3 scripts/paper_trade_log_mv.py --date 2025-03-01` 验证
- 必要时降低 `MIN_STOCKS`（默认 10）

## 7. 退出 / 暂停纸交易

```bash
# 暂时停掉 cron（注释相关两行）
crontab -e

# 累计日志保留在 storage/paper_trade/ 用于事后复盘
# 或全部删除：rm storage/paper_trade/*.json
```

## 8. 参考

- 策略推导：`docs/solutions/lookahead_bias_lessons.md`
- 框架文档：`README.md`、`kss/README.md`
- 唯一上线策略证明：`storage/reports/kcb50_ultimate_report.md`
- 已知缺陷：`docs/solutions/known_bias_gaps.md`
