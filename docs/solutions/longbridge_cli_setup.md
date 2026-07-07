# Longbridge CLI Setup & Runbook

> Track B（U8）：仓库 Claude Code agent 的 Longbridge CLI + KSS 只读代理能力面。
> OAuth 授权 host 已在 Clash 下验证可达（2026-07-08 smoke）。

## 安装 CLI

```bash
# macOS（Homebrew）
brew install --cask longbridge/tap/longbridge-terminal

# 或 curl 脚本
curl -sSL https://open.longbridge.com/longbridge/longbridge-terminal/install | sh

# 验证安装
longbridge --help
```

## OAuth 授权

```bash
longbridge auth login
```

浏览器自动打开授权页面。完成后 Token 自动保存到 `~/.longbridge/openapi/tokens/`。
**须用 paper trading 账户**（quote-only token 无交易 scope，KTD7 entitlement 门会核）。

验证授权成功：

```bash
longbridge quote 688008.SH
```

预期返回澜起科技的实时快照（last_done / prev_close / open / high / low / volume 等字段）。

## KSS 只读代理（唯一入口）

**绝对不调裸 `longbridge` 二进制**——它含 130+ 命令，包括 buy/sell/cancel/replace。
KSS 只读代理 `scripts/longbridge_ro.py` 是唯一入口，三支护栏：

1. 子命令白名单：仅 `{quote, kline, static-info}`
2. symbol 校验：`NNNNNN.(SH|SZ|BJ)` + 拒 shell 元字符
3. 凭据 entitlement 门：带 trade scope 即拒启

```bash
# 可用的（只读）
python scripts/longbridge_ro.py quote 688008.SH
python scripts/longbridge_ro.py kline 688008.SH
python scripts/longbridge_ro.py static-info 600519.SH

# 被拒的（交易）
python scripts/longbridge_ro.py buy 688008.SH        # → REJECT: denylist
python scripts/longbridge_ro.py positions             # → REJECT: not in allowlist
```

## 凭据 entitlement 门（KTD7）

代理启动时解码 access_token 的 JWT payload，检查 `ac` 字段：

- `lb_papertrading` → **通过**（paper 账户，无交易能力）
- `lb_live` / 任何含 `trade` 的 scope → **拒启**（`longbridge-ro/REJECT`）

只用 paper/quote-only token。若误用了 live token，代理直接拒绝启动。

## 撤销

```bash
# 删除 OAuth token
rm -rf ~/.longbridge/openapi/tokens/

# 卸载 CLI
brew uninstall --cask longbridge-terminal
```

## 注意事项

- Token exp = 2026-10-05（90 天窗）。SDK 无自动刷新（deferred），需手动续。
- CLI OAuth 授权 host 与行情网关为不同域（openapi.longbridge.com vs openapi.longportapp.com）。
  Clash 下两者均已验证可达。
- 北交所（.BJ）无实时行情，由代理返回空或 error。
- 裸 CLI 含交易能力——KSS agent 绝不可直接调 `longbridge`，只调代理。
- SDK 包已从 `longport` 更名为 `longbridge`（4.x），`longport` 已废弃。
