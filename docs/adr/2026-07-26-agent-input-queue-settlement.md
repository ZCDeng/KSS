# ADR：Agent 输入队列与 Run Settlement

- 状态：Accepted
- 日期：2026-07-26
- 范围：KSS Agent Core protocol v1

## 背景

KSS 已有状态化 Agent Runtime、持久会话、工具执行、上下文压缩与中止语义，但生成期间不能补充当前任务，也不能把后续问题排入同一条长连接。若把补充输入当成新的 `agent-turn`，会破坏单会话串行和幂等边界；若队列只保存在 Swift 内存，App 或 sidecar 异常退出又会造成用户输入静默丢失。

## 决策

1. 一个正常工作的 Agent run 只发送一次 `agent_start` 和一次 `agent_end`，内部每次完整模型响应及其工具结果形成独立的 `turn_start` / `turn_end`。
2. `steering` 在当前 assistant 的完整工具批次持久化后批量应用，并在下一次 provider 调用前作为独立 user messages 注入。
3. `follow_up` 仅在当前工作自然结束、没有待应用 steering 时逐条应用；每条 follow-up 形成新的 user/assistant 消息组，但仍属于同一个 run 和同一条长连接。
4. 队列使用 append-only JSONL 记录 `queue_added`、`queue_applied`、`queue_restored`、`queue_discarded`。应用项目时，正式 user message 与 `queue_applied` 必须在同一文件锁事务内落盘。
5. `client_message_id` 是队列幂等键。相同 ID 只返回既有项目和状态，不重复排队或注入。
6. 每条输入继续经过 user sanitizer，最多 500 字；每个 run 最多接受 8 条 queued input，超限明确拒绝。
7. abort、provider failure、stop hook、tool terminate、预算耗尽或崩溃恢复时，所有未应用项目转换为 `restored`。它们只在 UI 的“待发送”区域恢复，绝不自动重放。
8. `agent_end` 是 settlement barrier：消息、工具结果、队列终态、run terminal state 与 JSONL 均完成持久化后才能发送。

## 安全边界

- 等待写确认时可以继续排队，但 queued input 不改变已经展示、批准或执行中的写操作。
- abort 对待确认写仍等价于拒绝；已经进入同步执行的写工具只允许安全收尾，其晚到结果不得被伪装成已取消。
- restored 项目重新发送时通过 `source_queue_id` 原子消除原项目；发送失败或被拒绝时原项目保留。
- Skill resource 只允许读取已启用 Skill 自身目录内的普通 UTF-8 文本，不执行脚本，不允许路径或符号链接逃逸。
- 结构化记忆召回必须保留真实 memory ID 和来源；`thesis` 始终标记为“待复核的历史判断”，不得作为实时事实。

## 后果

- Swift 必须在生成期间保持输入框可编辑，并根据 `queue_update` 的 accepted/rejected 结果决定是否清空编辑器。
- Runtime、SessionStore、sidecar 与 Swift hydration 都必须理解 queued input 状态，但旧 protocol v1 客户端仍可忽略新增字段。
- 会话分支、并行工具、Skill 市场、embedding 和无输入 continue/retry 不属于本 ADR 的实现范围。

