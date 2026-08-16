# `@kss/harness-plugins`

KSS 业务能力的唯一 Cordis 登记面：目录从 chat `TOOL_SPECS` 派生。

- 只读工具：desktop / research / MCP 投影（`mcpVisible`）。
- live 写：仅 desktop + research；execute 只发 sidecar RPC 意图，Python 仅在 Harness 已允许该 `callId` 后 `_execute_write`。
- R12 投资可标写命令永不登记。

写策略（U3）：桌面 `ask` + 仅回答自己拥有的 agent；研究会话保持 `ask`，`tools/pre-execute` 白名单 allow/deny（未命中不问人）。`never` 对所有写（含白名单 bash）deny。桌面允许后 sidecar `grant_harness_write(callId, command)`。

`kss_mcp.py` `_LIVE` 不是本包目录。包变更在下次 `agents.create` / MCP 重连后可见。
