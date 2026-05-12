---
title: Telegram Bot 推送通道部署
tags: [deployment, telegram, notifications]
problem_type: operations
module: notifications
created: 2026-05-12
---

# Telegram Bot 推送通道部署

KSS 通知通道从 iLink 微信换到 Telegram. **默认走云 Bot API**（`api.telegram.org`），
零运维、零 docker、零 api_id 申请. 若后续真需要自建（文件 > 50MB 等），见末尾 *Appendix*.

## 5 步走

### 1. BotFather 拿 token
私聊 [@BotFather](https://t.me/BotFather) → `/newbot` → 起名 → 拿 `TELEGRAM_BOT_TOKEN`.
形如 `123456:ABC-DEF...`. 已有就跳.

### 2. @userinfobot 拿 chat_id
私聊 [@userinfobot](https://t.me/userinfobot) 发任意消息 → 它回你的 ID（整数）.
个人对话场景 `TELEGRAM_CHAT_ID` 填这个数. 群组场景去发 `getUpdates` 取群 chat_id.

### 3. 填 KSS 根 `.env`
```bash
cat >> /Users/zcdeng/projects/KSS/.env <<'EOF'
TELEGRAM_BOT_TOKEN=<step 1 拿到的全 token>
TELEGRAM_CHAT_ID=<step 2 拿到的 id>
TELEGRAM_API_URL=https://api.telegram.org
EOF
```

`TELEGRAM_API_URL` 留 `https://api.telegram.org` 即走云；若自建 server 改 `http://127.0.0.1:8081`.
Python 端代码不动.

### 4. Health check
```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
# 期望: {"ok":true,"result":{"id":..., "is_bot":true, "username":"...bot", ...}}
```

### 5. 端到端发一条
```bash
python3 -c "
import os
from kss.notifications import TelegramBot
# 临时塞 env（实际 cron 跑时由 run_paper_trade_daily.sh 从 .env 读）
os.environ.update({k: open('/Users/zcdeng/projects/KSS/.env').read().split(f'{k}=')[1].split(chr(10))[0]
                   for k in ['TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_API_URL']})
print(TelegramBot().send('hello from KSS'))
"
# 期望: True，手机收到消息
```

## Troubleshooting

- **`ok=false, description="Unauthorized"`** → token 错或 bot 被 BotFather revoke 了，重发 `/token` 给 BotFather.
- **`ok=false, description="chat not found"`** → 你**还没主动给 bot 发过任何消息**. Telegram 规则：bot 不能主动发消息给从未交互过的用户. 解法：手机 TG 搜 `@<你的 bot username>` → 点 Start → 重新发一遍.
- **国内访问 `api.telegram.org` 偶发超时** → 网络抖动，cron 失败下次重试即可；KSS `TelegramBot.send` 失败 return False、不抛异常，不会让 paper_trade 整体崩.
- **Markdown 报 `Bad Request: can't parse entities`** → 消息里含 Telegram Markdown V1 保留字符（实际是 `_ * ` `` ` `` ` [`——`()[]` 是 MarkdownV2 才保留的），用反斜杠 escape (`\_` / `\*` 等) 或改 `parse_mode="HTML"`. KSS 的 `cross_sectional_forecast._md_v1_escape()` 已对推送表格的用户数据列（名称/行业/概念/factor）做反斜杠 escape；如新增类似输入源记得复用同一 helper.

## Cron 集成

`scripts/run_paper_trade_daily.sh` 已封装好：

1. 从 KSS `.env` grep 三个 TELEGRAM_* 变量，export 后 exec python
2. 不 source 整个 .env（防 cookie / jwt 等含特殊字符的行炸 bash）

部署到 crontab：
```cron
0 9 * * 1-5 /Users/zcdeng/projects/KSS/scripts/run_paper_trade_daily.sh >> /tmp/kss_daily.log 2>&1
```

---

## Appendix — 何时考虑自建 telegram-bot-api server

只在以下任一情况：

1. 推送文件 > 50MB（云上限），如 PDF 报告 / 视频回测帧
2. 完全离线本地化（数据不出境）
3. 频繁发图 / 文件，云 API 限速明显

否则**坚决用云**——自建要 `api_id`/`api_hash` 必须人在 my.telegram.org 注册 app，国内手机号经常被拒；
docker 还得维护 update 文件持久化、TDLib 启动时间长（首次启动数十秒）、版本升级要重启等运维成本.

自建步骤已 deprecated，需要时去 git 历史里翻 `deploy/telegram/` 目录复活.
