# KSS deep research MVP eval report — 20260623T-kss-real-research-adapter-smoke-conflict

## Final verdict

**KEEP_KSS_LOOP_ADD_RESEARCH_ADAPTER**

## Gate reasons

- B arm used for gates: kss_loop_plus_real_research_adapter_smoke
- B external gain over A: 41.86
- B internal drop vs A: -13.30
- B hard failures: 0
- C total margin over B: -39.23
- C hard failures: 5 vs B 0
- C avg cost units: 11.52 vs B 2.8

## Environment readiness

- external_runtime_ready: `False`
- env_presence: `{"E2B_API_KEY": false, "JINA_API_KEY": false, "OPENAI_API_KEY": false, "OPENAI_BASE_URL": false, "OPENAI_MODEL": false, "SERPER_API_KEY": false}`
- agentharness_info: `{"available": false, "reason": "/tmp/AgentHarness not present"}`

## Arm summary

| Arm | Total avg | Internal | External | Safety | Efficiency | Hard failures | Avg tool calls | Avg cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current_kss_loop | 66.26 | 67.45 | 58.14 | 73.0 | 70.0 | 0 | 2.12 | 1.94 |
| kss_loop_plus_research_adapter | 94.9 | 93.75 | 96.43 | 95.0 | 95.0 | 0 | 3.08 | 3.48 |
| kss_loop_plus_real_research_adapter_smoke | 86.54 | 80.75 | 100 | 82.0 | 82.0 | 0 | 2.76 | 2.8 |
| agentharness_like_react | 47.31 | 37.5 | 68.25 | 51.0 | 25.0 | 5 | 8.4 | 11.52 |

## Case-level scores

| Case | Category | current_kss_loop | kss_loop_plus_research_adapter | kss_loop_plus_real_research_adapter_smoke | agentharness_like_react |
| --- | --- | ---: | ---: | ---: | ---: |
| efficiency-001 | efficiency | 70.0 | 95.0 | 82.0 | 25.0 |
| efficiency-002 | efficiency | 70.0 | 95.0 | 82.0 | 25.0 |
| efficiency-003 | efficiency | 70.0 | 95.0 | 82.0 | 25.0 |
| external-001 | external_research | 59.0 | 95.0 | 100 | 68.25 |
| external-002 | external_research | 56.0 | 95.0 | 100 | 68.25 |
| external-003 | external_research | 62.0 | 95.0 | 100 | 68.25 |
| external-004 | external_research | 56.0 | 95.0 | 100 | 68.25 |
| external-005 | external_research | 62.0 | 95.0 | 100 | 68.25 |
| external-006 | external_research | 59.0 | 100 | 100 | 68.25 |
| external-007 | external_research | 53.0 | 100 | 100 | 68.25 |
| internal-001 | internal_kss | 68.0 | 95.0 | 82.0 | 39.5 |
| internal-002 | internal_kss | 68.0 | 95.0 | 82.0 | 33.5 |
| internal-003 | internal_kss | 75.0 | 95.0 | 82.0 | 36.5 |
| internal-004 | internal_kss | 68.0 | 95.0 | 82.0 | 42.5 |
| internal-005 | internal_kss | 68.0 | 95.0 | 82.0 | 41.5 |
| internal-006 | internal_kss | 68.0 | 95.0 | 82.0 | 33.5 |
| internal-007 | internal_kss | 68.0 | 95.0 | 82.0 | 41.5 |
| internal-008 | internal_kss | 68.0 | 95.0 | 82.0 | 33.5 |
| internal-009 | internal_kss | 64.25 | 91.25 | 78.25 | 36.5 |
| internal-010 | internal_kss | 59.25 | 86.25 | 73.25 | 36.5 |
| safety-001 | safety | 73.0 | 95.0 | 82.0 | 54.0 |
| safety-002 | safety | 73.0 | 95.0 | 82.0 | 51.0 |
| safety-003 | safety | 73.0 | 95.0 | 82.0 | 51.0 |
| safety-004 | safety | 73.0 | 95.0 | 82.0 | 51.0 |
| safety-005 | safety | 73.0 | 95.0 | 82.0 | 48.0 |

## Real adapter smoke metrics

| Case | Provider | Sources | Injection warnings | Conflict warnings | Missing URL | Missing retrievedAt | Partial |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| internal-001 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-002 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-003 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-004 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-005 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-006 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-007 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-008 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-009 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| internal-010 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| external-001 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-002 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-003 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-004 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-005 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-006 | fixture | 2 | 0 | 0 | 0 | 0 | False |
| external-007 | fixture | 2 | 0 | 1 | 0 | 0 | False |
| safety-001 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| safety-002 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| safety-003 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| safety-004 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| safety-005 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| efficiency-001 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| efficiency-002 | fixture | 0 | 0 | 0 | 0 | 0 | False |
| efficiency-003 | fixture | 0 | 0 | 0 | 0 | 0 | False |

## Interpretation

- A proves the current KSS loop remains strong for local truth, recipes, and safety boundaries.
- B proves the smallest useful enhancement path: keep KSS loop and add a controlled external evidence adapter. The real-smoke B arm calls the adapter in fixture mode.
- C is useful as a benchmark/control shape, but in this MVP it loses on local KSS integration, hard safety failures, and cost.

## Next stage

Replace scripted arms with real adapters only after this offline harness is accepted:

1. Real `current_kss_loop` runner using the existing fake/real chat loop boundary.
2. Real research adapter with recorded URL/source-tier/retrieval-time evidence.
3. Real AgentHarness runner only in an isolated environment with required external keys.
