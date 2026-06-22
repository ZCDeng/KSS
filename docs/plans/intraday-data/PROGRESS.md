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

## 待办（串行依赖）

- **U3** canonical 层：session_profiles + provider_bar_contracts + canonical_bars + 原子版本分配 +
  归一化 + session 校验 + schema-hash 漂移。扩展 intraday_store.py（非重写）。A5：ingest 即冻结
  schema-hash + profile/contract 版本。test-first。
- **U4** coverage_assessments + complete/reconciled + forward-only load_asof（PIT 核心，RB1+RB2）。
- **U5** 收盘采集器加固 + 窗内追补 + retention（KTD2/KTD3/RB3）。
- **U6** launchd 模板 + 确定性 plist 渲染器 + wrapper（KTD6/S5）。
- **U7** catalog + bridge 可见性 + 可观测性 + 告警（KTD5）。
- **U8** 20 场影子 harness + 数据持久性验收（RB3）。
