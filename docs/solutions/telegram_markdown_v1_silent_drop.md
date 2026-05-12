---
title: Telegram Markdown V1 推送静默丢失：诊断与防御
tags: [telegram, markdown-v1, escape, silent-failure, paper-trade, push-template, regression-test]
problem_type: silent-failure
module: kss/prediction
created: 2026-05-12
---

# Telegram Markdown V1 推送静默丢失：诊断与防御

## 问题

`format_pool_markdown` + `paper_trade_log_mv.py` 组装的每日推送在 cell 含**未配对** Markdown V1 保留字 (`_ * `` ` `` [`) 时被 Telegram parser 拒收：API 返回 `400 Bad Request: can't parse entities`，`TelegramBot.send()` 返回 `False`（不抛异常），cron 退出码 `0`，**整条推送丢失但没有任何告警**。

本 doc 记录 PR #2 review 中发现这个 latent bug 的诊断路径、修复方案 (PR #3 commit `be89574`) 以及防御纪律。

## 症状

- Telegram API: `400 Bad Request: can't parse entities — Can't find end of the entity starting at byte offset N`
- `TelegramBot.send()` 返回 `False`、**不抛异常**（[`kss/notifications/telegram_bot.py:51-100`](../../kss/notifications/telegram_bot.py)）
- Cron exit code 0，无 Python traceback，无 email
- 默认 stdout 在 cron 下被丢弃，除非 `>> /tmp/kss_*.log 2>&1` 显式重定向（KSS crontab 已配）
- 内容相关 (content-dependent)：仅当 Top-10 cell 含未配对 V1 保留字时触发
  - 真实触发样本：`*ST航图`（`storage/stock_names.csv` 在册 4 只 \*ST：688066/033/622/287）
  - 潜在样本：含 `_` 的 concept 名（如 `5G_概念`，目前 Tushare 返回的 7 种 concept 都没有 `_`，但下次扩池随时可能引入）
- **production 之前一直通过靠巧合**：H1 `(long_low)` 的 1 个 `_` 和表头 `log_mv` 的 1 个 `_` 在消息内意外配对成 italic，让 parser 接受。一旦出现第 3 个**奇数**位的保留字（任何 \*ST 的 `*`）立刻塌

## 失败的尝试

- **切 `parse_mode="HTML"`**：理论上免疫 Markdown 转义，但要把整条推送的 `` ` ``code`` ` ``、`**bold**`、表格 pipes 全改成 HTML 标签（`<code>` / `<b>` / 等），并且仍需 escape `< > &`。改造面太大，性价比低
- **每个 cell backtick-wrap 成 code span**：code span 内 V1 不解析任何保留字，技术上可行；但会把列表头和股票名都变成等宽字体，UX 倒退，且对中文渲染不友好
- **最终选择反斜杠 escape**：`\_` 在 V1 渲染为字面 `_`，视觉零变化、改动最小（PR #3 仅 +5 escape 调用点）

## 解决方案

**1. 新增 `_md_v1_escape()` helper**（[`kss/prediction/cross_sectional_forecast.py:34-48`](../../kss/prediction/cross_sectional_forecast.py)）

```python
import re

_MD_V1_ESCAPE_RE = re.compile(r"([_*`\[])")

def _md_v1_escape(s: str) -> str:
    """Escape Telegram Markdown V1 reserved chars (_ * ` [) for a single cell."""
    return _MD_V1_ESCAPE_RE.sub(r"\\\1", s)
```

**2. 在 `format_pool_markdown` 所有用户数据注入点调用**

```python
# Before — factor 名、dir_label、cell 值都裸出
cols = ["排名", "代码", factor, "rank%", "计划权重"]
lines = [f"# {date_str} 横截面选股 (`{factor}` / {dir_label})", ...]
if has_name:
    cells.append(str(r.get("stock_name", "")))

# After — 5 处都 escape
cols = ["排名", "代码", _md_v1_escape(factor), "rank%", "计划权重"]
lines = [f"# {date_str} 横截面选股 (`{factor}` / {_md_v1_escape(dir_label)})", ...]
if has_name:
    cells.append(_md_v1_escape(str(r.get("stock_name", ""))))
# industry / concept 同理
```

**3. 用 `chat_id=0` 探针验证 parser 接受**

Telegram API **先解析消息体、再校验 chat_id**，所以用 `chat_id=0` 就能在不发送到真实频道的情况下分离两类 400：

```python
import os, requests

tok = os.environ["TELEGRAM_BOT_TOKEN"]
url = f"https://api.telegram.org/bot{tok}/sendMessage"
cases = {
    "raw_log_mv":     "| 排名 | 代码 | log_mv | rank% |",        # 表头未 escape
    "escaped_log_mv": "| 排名 | 代码 | log\\_mv | rank% |",       # 反斜杠
    "wrapped_log_mv": "| 排名 | 代码 | `log_mv` | rank% |",       # code span
    "st_cell":        "| 1 | `688066.SH` | 14.6 | *ST航图 |",      # 触发现实 bug
    "concept_5g":     "| 1 | `688066.SH` | 14.6 | 5G_概念 |",      # latent 触发
    "paren_cell":     "| 1 | `688066.SH` | 14.6 | 锂电池(动力) |",  # MarkdownV2 保留、V1 安全
}
for label, body in cases.items():
    r = requests.post(url, json={"chat_id": 0, "text": body,
                                 "parse_mode": "Markdown"}, timeout=15)
    d = r.json()
    print(f"{label:18s} desc={d.get('description')!r}")
# raw_log_mv         "Can't find end of the entity starting at byte offset 23"
# escaped_log_mv     "chat not found"      ← parse OK
# wrapped_log_mv     "chat not found"      ← parse OK
# st_cell            "Can't find end of the entity starting at byte offset 35"
# concept_5g         "Can't find end of the entity starting at byte offset 37"
# paren_cell         "chat not found"      ← parse OK
```

`"chat not found"` = parse 通过、仅 chat 错；`"can't parse entities"` = parse 失败。这把"哪一条消息会撞 parser"的二分查找压到 O(行数) 次 API call、零侧效。

**4. 截断行列数派生**

`f"| … | 还有 N 只省略 | | | |"` 早就写死 5 cells；name + industry + concept 上线后表格 8 列、截断行 5 列对不齐。改为：

```python
pad = " | ".join([""] * (len(cols) - 2))
lines.append(f"| … | 还有 {len(in_top) - max_rows} 只省略 | {pad} |")
```

**5. `merge_cols` 对称守卫**

`scripts/paper_trade_log_mv.py:407` 之前把 `industry` 写死、`concept` 才条件，未来 CSV 丢 industry 会撞 KeyError 被 except 吞掉、名称+行业+概念注入全失败。改成 for-loop：

```python
merge_cols = ["ts_code", "name"]
for opt in ("industry", "concept"):
    if opt in nm_df.columns:
        merge_cols.append(opt)
```

## 这样为什么对

**Markdown V1 保留字集只有 4 个**：`_` `*` `` ` `` `[`。MarkdownV2 才把 `~ > # + - = | { } . ! ( )` 也算保留 —— 这就是为什么 `锂电池(动力)` 在 V1 探针下 parse OK 但凭"括号是保留字"的直觉会误判。`docs/solutions/telegram_deployment.md:60` 之前列错（贴的是 V2 集合），PR #3 已经修正。

**`\_` 在 V1 渲染为字面 `_`、零视觉差**，所以 escape 是 3 个备选方案里改动面最小的：

| 方案 | 改动面 | 视觉差 | 长期可维护 |
|------|--------|--------|------------|
| 切 HTML | 整条推送重写 | 渲染风格变 | 中 |
| Backtick-wrap | 每个 cell 包 | 中文等宽难看 | 中 |
| 反斜杠 escape | 5 个调用点 | **零** | **好** |

**production 之前不挂的"巧合"**：每日推送里恰好有 2 个未配对 `_`（H1 dir_label 里 `(long_low)`、表头 `log_mv`）— **偶数个、刚好配对成 italic**。任何 \*ST 入选会在 cell 加 1 个 `*` 让总未配对保留字变成奇数 → entity 不闭合 → 整条 reject。这是典型的"靠不变量碰巧成立"的脆弱依赖，escape 把它从碰巧通过升级为**契约通过**。

## 防御

1. **`format_pool_markdown` 或任何兄弟函数里，凡是 `f"... {user_data} ..."` 形式注入到 `parse_mode="Markdown"` body 的字符串，必须走 `_md_v1_escape()`**。helper 是唯一允许的入口
2. **`chat_id=0` 探针是"推送静默失败"类问题的首选诊断手段** —— 一条 `curl` 区分 parser 错和投递错，对生产频道零侧效。下次再遇 Telegram 类静默问题先跑探针，不要急着摸 token / env
3. **回归测试断言字面 escape 出现在渲染输出**（[`kss/tests/test_cross_sectional_forecast.py:309-329`](../../kss/tests/test_cross_sectional_forecast.py)）：
   ```python
   assert r"\*ST航图" in md, "* must be escaped"
   assert r"5G\_概念" in md, "_ must be escaped"
   assert r"log\_mv" in md, "factor name with _ must be escaped"
   ```
   远比"函数返回字符串"强 —— refactor 后任何丢 escape 调用点的改动会立刻挂
4. **cron 必须有 stdout/stderr 重定向**（KSS crontab 已经配 `>> /tmp/kss_*.log 2>&1`）。否则即使 escape 100% 正确，未来 Telegram 改 V1 规则 / 加保留字时，新 bug 会重复进入"静默失败 + 无可观测信号"的循环。Fail-loud 纪律对静默路径尤其关键

## 相关文档

- [`docs/solutions/telegram_deployment.md`](./telegram_deployment.md) — Telegram bot 通道部署 + 已修正的 V1 保留字列表 + `_md_v1_escape()` 引用
- [`docs/solutions/paper_trade_deployment.md`](./paper_trade_deployment.md) — 每日纸交易部署 + cron + 推送排查（line 140 Q&A 可追加"parser 失败 → 用 `chat_id=0` 探针"一条）
- [`docs/solutions/project_retrospective.md`](./project_retrospective.md) §7.1 — `行业映射粗糙` 已在 PR #1/#2 标 RESOLVED
- PR #2 (`f37c3d9`)：concept 板块上线（暴露 \*ST 风险）
- PR #3 (`be89574`)：escape + 截断行 + merge_cols 对称 + 4 个回归测试
