---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: deep-research-and-pressure-review
execution: code
title: KSS × DojoAgents 分析内核与 Agent Loop 补强计划
type: feat
date: 2026-07-28
---

# KSS × DojoAgents 分析内核与 Agent Loop 补强计划

## 审核结论

原始方案方向正确，但不能按原样实施。深度审核结论为“重大修订后通过”：

- 当前 `after_step` hook 的返回值会被忽略，无法真正驱动恢复步骤。
- 最终文字在 Harness 审核前已经流向 Swift，事后标记 `incomplete` 无法撤回已展示的金融结论。
- Dojo 的 `HarnessDecision.complete` 不能映射为 KSS 的 Research `completed`。
- 当前 UI 会展示 provider thinking，分析模式必须显式关闭这条路径。
- `market_overview_v1`、`event_impact_v1` 不能作为 KSS Research `ProfileSpec`；它们应是轻量的交互式 `AnalysisContract`。
- 分开的 harness、policy、budget 三套模块会重复 `RuntimeRunOptions`、`ContextAssembler` 和 Research runner 的职责。
- 跨市场事件分析必须增加“来源市场—受影响市场—交易时点”的逐项证据契约。
- 推广指标必须覆盖假完成、无引用数字、推理泄漏和不支持的跨市场结论，不能只看延迟、token 和调用次数。

本计划是通过上述审核后的完整替代方案。

## Goal Capsule

- **Objective:** 在不替换 KSS Python sidecar、SwiftUI、`KSSAgentService`、`AgentRuntime` 和 Research DAG 的前提下，原生吸收 DojoAgents 的任务约束、循环 guard、逐步上下文预算和事件可观察性，交付两个可验证、只读、不会提前泄露不完整结论的金融分析 contract。
- **Upstream baseline:** `Alpha-Dojo/DojoAgents@0d3389e6f3739c0b0abc24869fa55a2e7acd19ef`，package version `0.1.9`。
- **Execution profile:** Python Agent Core 与 sidecar 协议优先；Swift 只消费新增 additive 事件和提供显式分析入口；Research 层只复用通用 loop controls，不新增 Research Profile。
- **Stop conditions:** 所有安全/兼容/晋级门槛通过；或真实 Provider 证明现有数据无法可靠满足首期 contract 的证据条件时，停在 observe，不切换 enforce。
- **Out of scope:** 真实或模拟组合管理、持仓诊断、调仓/交易、后台监控、Dojo runtime/Strands、Web dashboard、gateway、cron、插件平台、第二套 Agent scheduler。

## 关键架构决策

### KD1. 原生继承机制，不引入 Dojo runtime

保留当前链路：

```text
SwiftUI
  -> BridgeClient
  -> kss_sidecar.py
  -> KSSAgentService
  -> AgentRuntime
  -> kss_chat_loop.py
  -> KSS tools / Skills / memory
```

Research 继续作为 `AgentRuntime` 之上的 overlay：

```text
Research Profile / DAG / Audit
  -> AgentResearchTaskRunner
  -> KSSAgentService
  -> AgentRuntime
```

不采用 Dojo 的 `Runtime`、PlanEngine、AgentPool、sandbox、PATH plugin loader 或 turn-intent classifier。Dojo 的 planning/delegation 是应用层编排，并不替代 KSS 已有的持久 evidence/audit DAG。

### KD2. `AnalysisContract` 与 Research `ProfileSpec` 分离

首期定义两个代码内置、不可动态注入的交互式 contract：

- `market_overview_v1`
- `event_impact_v1`

它们不是 Research `profile_id`，不进入 `list_profiles()`、`create_goal()`、scheduler 或 publish gate。

Contract 只控制一次交互式 run 的：

- 工具和 Skill 白名单
- 必须满足的证据条件
- 时效和市场覆盖规则
- 最多一次恢复
- 最终答案交付闸门
- thinking 隔离
- `allow_write_tools=False`

普通自由聊天不自动匹配 contract；由 Seesaw 显式入口选择，避免引入新的 LLM intent classifier。

### KD3. Harness 只判断答案充分性

Harness 不返回或持久化 `complete`。统一使用：

```text
AnswerDisposition:
  accept
  recover
  emit_incomplete
```

- `accept` 只表示“本次交互回答满足 contract”。
- `recover` 表示丢弃候选答案并进行一次受限恢复。
- `emit_incomplete` 表示由程序生成证据缺口说明。
- Research task、goal、artifact 的 `completed` 只能由既有 `ResearchAuditService` 判定。

## Product Contract

### `market_overview_v1`

必须覆盖：

1. 明确的市场范围。
2. 每个市场的数据 `as_of` 和交易时段。
3. 基准指数或可用的市场宽度替代指标。
4. 板块轮动。
5. 异常标的或主题。
6. 催化剂。
7. 风险、反证和数据覆盖缺口。

行为要求：

- 只允许分析 KSS 实际具有新鲜数据的市场。
- 不得因为 Dojo README 展示了 A/HK/US 能力而假设 KSS 已有同等覆盖。
- 一个市场的数据缺失时，必须从 coverage 中排除或返回 `analysis_incomplete`，不得生成替代数字。

### `event_impact_v1`

必须覆盖：

1. 原始事件、`published_at`、来源及来源等级。
2. 影响传导机制。
3. 受影响行业和标的。
4. 行情或基本面确认。
5. 替代解释。
6. 反证、风险和失效条件。

跨市场影响链必须为每一条 claim 绑定：

- 来源市场。
- 事件时间。
- 受影响市场。
- 事件后同一有效交易时段或下一有效交易时段的确认数据。
- 对应 evidence ID。

任一段缺失时返回 `unsupported_cross_market`，不得输出确定性跨市场因果结论。

### 证据与数字纪律

- `official_or_primary` 只能由受控域名或明确 provenance 判定，不能仅凭标题中的“公告”等关键词升级。
- `published_at`、`data_as_of`、`retrieved_at` 分开保存；禁止用抓取时间冒充事件发生或数据时点。
- 所有用户可见金融数字必须绑定工具结果、metric ref 或 evidence ID。
- 未绑定数字必须在最终交付前删除，而不是仅显示警告。
- 事实、确定性计算和模型推断分别标记；模型推断不得升级为已验证事实。
- 外部搜索内容仍按不可信输入处理，注入告警不得被 Harness 忽略。

## Implementation Changes

### 1. Analysis Contract Registry

在 `kss/agent/` 增加代码定义的 `AnalysisContractRegistry`。

每个 contract 固定：

```text
contract_id
version
allowed_tools
allowed_skills
required_evidence
market_coverage_rules
freshness_rules
max_recovery_steps = 1
delivery_mode = gated
reveal_thinking = false
allow_write_tools = false
```

Contract 解析出的约束必须与现有 `RuntimeRunOptions` 取交集：

- 不能扩大 `allowed_tools`
- 不能扩大 `allowed_skills`
- 不能开启写权限
- 不能绕过 `trusted_internal_input`
- 不能提高调用方预算

### 2. 单一 Loop Controls 层

新增一个薄的 `kss/agent/loop_controls.py`，不分别建立 harness/policy/budget 三个并行控制器。

它负责：

- `AnalysisDeliveryValidator`
- `AnswerDisposition`
- per-run tool guard 状态
- provider context projection
- per-step usage 累计

`AgentRuntime` 继续只负责 run admission、abort、steering/follow-up、queue 和 persistence barrier；loop controls 不成为第二个 runtime。

### 3. 最终答案交付闸门

为分析 contract 扩展 chat loop：

1. 模型生成期间缓冲 assistant 文字，不立即发送给 Swift。
2. 工具调用、工具结果、阶段和证据进度仍实时发送。
3. 无工具调用的候选答案生成后，调用 `AnalysisDeliveryValidator`。
4. `accept`：
   - 释放缓冲答案。
   - 形成可见 assistant message。
   - 进入 session 持久化。
5. `recover`：
   - 丢弃候选答案。
   - 保存候选哈希和未满足条件，不保存候选正文。
   - 注入仅对当前 run 可见的恢复约束。
   - 最多继续一次 provider step。
6. `emit_incomplete`：
   - 丢弃候选答案。
   - 由程序按未满足条件生成确定性的缺口说明。
   - 不使用未经验证的模型数字或因果结论。

普通聊天保持现有逐 chunk 流式行为，不经过答案闸门。

### 4. Thinking 隔离

对两个分析 contract：

- 不向 UI 发送 `thinking_start`、`thinking_delta`、`thinking_end`。
- 不进入 memory、compaction summary 或 evidence。
- Provider 若需要 reasoning block 连续性，只在当前 run 的内存 conversation 中保留。
- 被标记为 internal 的 thinking block 不进入 Swift hydration/replay。
- 不改变普通聊天现有 thinking 策略。

### 5. Loop Guard

工具调用签名：

```text
sha256(tool_name + canonical_json(arguments))
```

工具内部增加：

```text
repeat_policy:
  stable_read
  volatile_read
  write
```

首期 observe 使用 Dojo 原始阈值语义：

- 相同失败：2 次告警，5 次后阻止。
- 同一工具连续失败：3 次告警，8 次后终止。
- 稳定只读无进展：2 次告警，5 次后阻止。

状态规则：

- 实时行情等 `volatile_read` 不比较相同结果。
- 成功调用清除对应失败计数。
- Steering/follow-up 开始时重置计数。
- 普通 tool continuation 不重置。
- `write` 不允许自动重试、参数修复或绕过确认。

Batch 规则：

- `block` 只阻止目标调用并返回合成工具错误，不取消同批其他合法只读调用。
- `halt` 必须在 batch 真实执行前形成；所有尚未执行的调用返回合成终止结果。
- 保留现有调用顺序、完成顺序事件和 transcript 回填顺序。

### 6. 单次 Run 上下文预算

现有 `ContextAssembler` 继续负责跨 turn 的持久压缩。新增逻辑只处理每次 provider 调用前的临时 projection：

```text
input_limit = context_window - max_output_tokens
soft_limit = input_limit * 0.85
```

达到 soft limit：

- 折叠最旧的完整工具结果正文。
- 保留工具名、call ID、错误状态、结果哈希、evidence/artifact 引用。
- 保持 tool-call/tool-result 配对。
- 只修改 provider-facing 副本，不修改 canonical transcript 或 JSONL。

Usage 规则：

- 每次 provider step 分别记录 usage。
- 累计 input/output/total，不能只保存最后一个 step。
- Provider 不返回 usage 时必须标记 `estimated=true`。
- 下一次预计调用会突破 `max_provider_tokens` 时，在调用前以 `provider_token_budget_exceeded` 结束。

## Public Interfaces

### Sidecar request

`agent-turn` 增加可选字段：

```json
{
  "analysis_contract_id": "market_overview_v1 | event_impact_v1 | null"
}
```

- 未知 contract ID 直接拒绝。
- 客户端不能传工具白名单、control mode、恢复次数或 thinking 策略。
- 原有不带该字段的请求行为不变。

### Swift bridge

`BridgeClient.agentTurn` 增加：

```swift
analysisContractId: String? = nil
```

Seesaw：

- 市场总览入口发送 `market_overview_v1`。
- 新闻/事件影响入口发送 `event_impact_v1`。
- 普通聊天传 `nil`。

### RuntimeRunOptions

只新增：

```text
analysis_contract_id: str | None
control_mode: off | observe | enforce
```

`control_mode` 由受信调用方或本地配置决定，不从 sidecar 用户请求读取。

### Additive Agent v1 events

- `analysis_phase`
- `context_projection`
- `step_usage`
- `loop_guard`
- `answer_evaluation`
- `analysis_incomplete`

旧 lifecycle、sequence、turn/message/tool 事件保持不变。未知 additive 事件必须可被旧 Swift decoder 忽略。

UI 只显示：

- 阶段
- step
- 数据时点
- 证据数量
- 未满足条件
- 最终 incomplete 原因

不显示：

- provider thinking
- recovery prompt
- 被拒绝的候选答案
- 模型内部评价文本

## Research Integration Boundary

- 首期不新增 `market_overview_v1` 或 `event_impact_v1` Research Profile。
- 不修改 `ResearchAgentSpec`。
- 不建立第二套 Harness 完成状态。
- 现有 Research runner 可启用通用 loop guard、step usage 和 context projection，初始只使用 `observe`。
- Research task、goal、artifact 的完成和发布继续完全由 `ProfileSpec.criteria`、evidence ledger、compiler audit 和 `ResearchAuditService` 决定。
- Research lifecycle 的 `goal_status`、`research_start/end`、`task_ready/start/end`、monotonic sequence 和 snapshot/replay 语义保持不变。

若后续需要在 Research Workbench 交付市场总览或事件影响，应另行设计代码定义的 Research DAG，并同时处理：

- Profile picker
- 输入 schema
- snapshot
- scheduler
- protected task
- Skill allowlist
- evidence criteria
- compiler/publish gate

该扩展不属于本计划首期范围。

## Test Plan

### Baseline

当前规划期验证基线：

```text
104 passed
```

覆盖 Agent Runtime、chat loop、service、Research service 和 pilot evaluation 的定向测试。

实施开始前补跑完整 Python/Swift baseline，并把结果固化到实施记录。

### 回归测试

- 普通聊天仍逐 chunk 流式展示。
- 分析 contract 在答案通过前不会出现正文 chunk。
- 被拒绝的候选答案不进入 session replay、memory 或 evidence。
- Abort、steering、follow-up、queue、写确认、并行工具顺序和 persistence barrier 行为不变。
- 旧版 Swift decoder 能忽略新增事件。
- 历史 session 可以正常 replay。
- event sequence 无重复、无倒退、无因答案缓冲产生的 lifecycle 缺口。

### Contract 与金融安全测试

- 完整证据。
- 过期证据。
- 缺失数据。
- 错误/伪造事件。
- 来源冲突。
- 盘前、盘中、盘后、周末和非交易日事件。
- 跨市场影响链缺少来源市场证据。
- 跨市场影响链缺少受影响市场确认。
- 跨市场时区或交易日错位。
- 未绑定金融数字。
- 定性判断冒充事实。
- 外部 evidence prompt injection。
- Holdings、rebalance、watchlist、调仓等超范围请求。
- 两个 contract 的 thinking leak 数必须为 0。
- `answer_evaluation=accept` 不能令 Research goal 进入 completed。

### Loop Guard 测试

- 三类 `repeat_policy`。
- Dojo 默认告警/阻止阈值。
- Success reset。
- Steering/follow-up reset。
- Tool continuation 不 reset。
- Block 单调用。
- Halt 所有尚未执行调用。
- Write 不自动重试。
- Batch 内合法调用不因单个 block 被取消。

### Context 与 usage 测试

- 每个 provider step 分别产生 usage。
- Usage 正确累计。
- Estimate 与 actual 明确区分。
- 85% projection 触发。
- Projection 不修改 canonical transcript。
- 保持 tool-call/tool-result 配对。
- 保留 evidence/artifact ref。
- 超预算发生在下一次 provider 调用前。

### Sidecar 与 Swift 测试

- `analysis_contract_id` 新旧请求兼容。
- 未知 ID 被严格拒绝。
- 客户端不能传 control mode 或扩权字段。
- Seesaw 两个显式入口传递正确 ID。
- 普通聊天不传 contract。
- 分析阶段、数据时点、证据和 incomplete 原因正确渲染。
- Provider thinking 不渲染、不回放。

## Observe → Enforce Promotion

### 固定 fixture

每个 contract 至少 12 个固定场景，覆盖：

- 正常完成
- 证据不足
- 数据过期
- 工具失败
- 重复工具循环
- 超预算
- 事件来源冲突
- 跨市场数据完整
- 跨市场数据缺失
- 数字未绑定
- Portfolio scope leakage
- Thinking suppression

### 真实 Provider

使用相同模型、route 和冻结输入：

- 每个 contract
- 每种模式
- 至少 5 次配对运行

晋级报告必须离线、可复现，只提供决策证据，不能自动切换生产模式。

### 强制晋级条件

```text
false_complete = 0
numbers_without_refs = 0
unsupported_cross_market_claims = 0
thinking_leak_events = 0
write_attempts = 0
audit_completion_bypass = 0
context_hard_limit_breaches = 0
```

同时要求：

- Required criteria 覆盖率不低于 observe 基线。
- 中位 provider token 增幅不超过 30%。
- 重复无进展调用率下降或持平。
- 不允许通过增加 `analysis_incomplete` 比例来人为改善 token/延迟。

延迟首轮只记录，不作为硬门槛。累计至少 30 次同 route 样本后再建立 p90/p95 门槛。

## Release Verification

- 完整 Python 测试。
- 完整 Swift 测试。
- Lint、typecheck 和静态分析。
- Sidecar 新旧请求兼容。
- Session replay。
- 真实 `/Applications/KSSDesktop.app` 运行进程路径。
- Application Support runtime root。
- Sidecar 实际日志。
- 市场总览 smoke。
- 事件影响 smoke。
- 证据不足 smoke。
- 跨市场拒绝 smoke。
- UI 无 provider thinking。
- UI 无未绑定金融数字。

开发迭代不重复 notarization。最终发行包再执行：

- codesign
- notarization
- stapler
- Gatekeeper

## 明确拒绝项

- 不做 Dojo runtime 整体迁移。
- 不引入 `strands-agents`、DojoSDK 或新第三方依赖。
- 不采用 Dojo sandbox 作为安全边界。
- 不采用会修改 `PATH` 的 plugin loader。
- 不采用 Dojo PlanEngine 或 AgentPool。
- 不复制 portfolio-specific Harness。
- 不建立第二套 Research scheduler。
- 不允许模型自我宣告 Research 完成。
- 不在首期支持持仓截图、组合诊断、调仓或模拟组合。
- 不声称 KSS 已具备 Dojo 的全市场数据覆盖。

如复制任何 Apache-2.0 源码片段，先核对固定 commit 的 LICENSE/NOTICE，并履行适用于复制材料的再分发与修改声明义务；优先重新实现机制而非复制代码。

