# 分时数据层实施进度（plan 005）

分支 `feat/intraday-data-layer`（worktree `/Users/zcdeng/projects/KSS-intraday`，off main）。

测试命令（kss editable 装在主仓，须 PYTHONPATH 前置 worktree）：
```bash
cd /Users/zcdeng/projects/KSS-intraday
PYTHONPATH=/Users/zcdeng/projects/KSS-intraday /Users/zcdeng/projects/KSS/.venv-desktop/bin/python -m pytest kss/tests/test_intraday_*.py -q
```

## 已完成

- **U1**（commit 1）供应商探针 + IntradayProvider 协议 + 能力门控。`kss/data/intraday_client.py`、
  `scripts/probe_intraday_provider.py`、`kss/tests/test_intraday_client.py`（15 测试）。
  classify_eligibility 纯函数：前向源恒 forward_observed 绝不 PIT。
  live smoke：东财 push2his 端点本机直连/代理均不通（环境条件，探针正确熔断 failed）。
- **U2**（commit 2）薄前向 raw-capture 存储 + logger + 统一脱敏（D3）。`kss/data/intraday_store.py`
  （ingest_runs/payload_blobs/instrument_registry/payload_observations + KTD1 WAL/FK/busy_timeout +
  显式单事务 + blob 内容寻址写一次 + 凭据闭合）、`kss/security/redaction.py`（S3 唯一脱敏真源）、
  `scripts/collect_intraday.py`（薄 --mode close）、`kss/config/paths.py`（INTRADAY_DB）。35 测试。

- **U3**（commit 3）canonical 层：session_profiles + provider_bar_contracts + canonical_bars +
  原子版本分配 + 归一化 + session 校验 + schema-hash 漂移 + A5 上下文冻结。
- **U4**（commit 4）coverage_assessments(complete/reconciled) + forward-only load_asof + 执行延迟
  闸 + ReviewBar/PitBar 类型隔离 + reconciliation_stalled（PIT 核心，RB1+RB2+A3+A4）。
- **U5**（commit 5）收盘采集器加固 + trade_cal 终止 + 窗内追补 + permanent_gap + retention +
  漂移自报 + watch shadow-only（KTD2/KTD3/RB3/M1/M2）。
- **U6**（commit 6）launchd 模板 + 确定性 plist 渲染器 + wrapper + _resolve_token secrets 源 +
  bridge F1 不补跑排除（KTD6/S5/S1/F1/KTD4）。
- **U7**（commit 7）catalog 表 allowlist + BLOB/TEXT 排除 + 结构化日志 + degraded 告警（KTD5/S4/S5）。
- **U8**（commit 8）20 场影子 harness + S2 硬前置 + 三维通过门 + 校准建议（RB3/S2/A7）。

## 全部 8 单元完成（plan 阶段 1–5）

测试：144 intraday 测试全绿（U1-U8）。日频 cs_data/SQLiteStore/纸交易零改动。
预先存在的无关失败：`test_bridge_orientation::test_doc_pointers_exist`（缺
`docs/solutions/ai_native_surface_assessment.md`，与本分支无关）。

## 显式 Follow-Up（plan 阶段 6–7，不在本计划）

- **阶段 6** 历史准入决策（gate 非代码）：从 U1 探针证据决定批准 Tushare(proxy-PIT) 或保持
  forward-only。**注意**：U1 live smoke 显示东财 push2his 端点本机直连/代理均不通——真实采集
  前须先解决网络可达性，或在能连通的部署环境跑 U1 探针取真实 publication_delay。
- **阶段 7** 历史证据机器 + 分钟回测：provider_historical_evidence proxy-PIT 准入路径、
  session-aware 聚合、执行/成本模型、显著性/稳健性协议。禁止复用日频 FactorPipeline/
  next_day_return 语义。仅阶段 6 批准后单独立计划。
- **5 条 Open Questions 消化情况**：A5(U3 已实现上下文冻结)、A6(U1 含稀薄标的海光信息)、
  S6(U6 定 env-first)、追补 revision eligibility(U4 manifest 绑定解决)、reconcile_failed 修正
  (U4 reconcile_against_daily 支持重评估)。
