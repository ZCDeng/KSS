# MVP: KSS deep research eval — 验证 AgentHarness 是替代还是增强

Date: 2026-06-22
Status: MVP validation artifact
Scope: 离线评测，不改生产 agent loop，不接 KSSDeck UI，不开放写路径。

## 目标

验证一个决策，而不是验证一个框架能不能安装：

> KSS 应该替换现有 agent loop，还是保留薄 loop 并增强 deep research 能力？

本 MVP 以 24 个固定案例跑三组候选：

- **A: current_kss_loop** — 当前 KSS 薄 loop + orientation + recipes。
- **B: kss_loop_plus_research_adapter** — 保留当前 loop，增加受控外部证据层。
- **C: agentharness_like_react** — AgentHarness/ReAct-style 离线对照组。

## 决策原则

1. KSS 本地真值优先：金融数字必须来自工具或冻结 fixture。
2. Deep research 只能补外部背景，不能覆盖 KSS 本地数据。
3. 写操作必须人在环；eval 中任何自动写都一票否决。
4. assistant 是 operator/explainer，不是投资 decider。
5. 候选 runtime 只有在总分、安全、成本、可维护性都明显胜出时才进入替代 spike。

## 七步执行映射

1. 固化 MVP 计划：本文件。
2. 建立 24 个固定案例：`evals/deep_research/cases.yaml`。
3. 跑 Arm A：`current_kss_loop`。
4. 跑 Arm B：`kss_loop_plus_research_adapter`。
5. 跑 Arm C：`agentharness_like_react`。
6. 生成矩阵报告：`evals/deep_research/reports/<run_id>.md`。
7. 给出结论判定：报告中的 `Final verdict`。

## 接受标准

- 三组候选全部产出 trace。
- 报告包含 category 均分、总分、硬性失败数、成本估算与最终判定。
- B 在 external_research 类案例上较 A 提升至少 15 分。
- B 在 internal_kss 类案例上较 A 下降不超过 5 分。
- B 的 hard_failures 必须为 0。
- C 只有在总分、external_research、安全和成本综合优于 B 时，才允许进入生产替代 spike。

## 当前 MVP 的边界

这个 MVP 是 scripted offline eval：它验证评测框架、决策规则和候选策略形状，不声称已经完成真实 LLM/Serper/Jina/E2B 的线上 benchmark。若环境中缺少 `OPENAI_BASE_URL`、`SERPER_API_KEY`、`JINA_API_KEY`、`E2B_API_KEY` 等外部依赖，报告会显式标记为 `external_runtime_ready: false`。

下一阶段若要做真实模型评测，应复用相同 case/rules/scorer，并把 arm adapter 从 scripted policy 替换为真实 KSS loop / research adapter / AgentHarness runner。

