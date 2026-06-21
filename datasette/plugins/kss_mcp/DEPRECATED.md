# DEPRECATED — 此 datasette MCP pilot 已被取代

**取代者**：`scripts/kss_mcp.py`（U6a/U6b，PR #22 合并入 main）。

新 server 直接复用 `kss_app_bridge.dispatch`，与 SwiftUI app 同一逻辑面、同样代码渲染数字，
跑在 state-root bootstrap venv（含 `fastmcp==3.3.1`，进 U0 lock）。Claude Code 的 `kss-mcp`
注册（`~/.claude.json`）已于 2026-06-22 重指到新 server。

**`kss-mcp` console-script 撞名**：本 pilot 曾以 `.venv/bin/kss-mcp` 占用该名；重指后不再被 Claude Code
加载。本目录现仅剩构建产物（`.venv` / `.pytest_cache` / `.egg-info` / `__pycache__`），源码 `server.py`
已不在；保留仅为不擅删用户 venv。可手动 `rm -rf datasette/plugins/kss_mcp/` 彻底清理。
