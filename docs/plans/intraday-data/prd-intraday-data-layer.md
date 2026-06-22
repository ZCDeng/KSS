# PRD: KSS 分时数据层（PIT-safe）

## 状态与边界

`ralplan` 审核草案；只规划，不在本工件中授权实现。目标是让 KSS 获得可审计的 A 股分钟数据能力，而不把非 PIT 的实时网页数据混入现有日频回测骨架。

### 问题与目标

现有 `SQLiteStore` 的 `stock_quotes`/`index_quotes` 以 `(ts_code, trade_date)` 为主键，并以 `INSERT OR REPLACE` 更新（`kss/data/sqlite_store.py:75-104,192-224`）；它适合日频缓存，不保存分钟数据的来源版本或“何时可得”。KSS 的架构明确区分 PIT 回测与不可回流的实时解读源（`docs/kss_architecture_interactive.html:159-192`；`progress.md:391-411`）。

本项目新增一个隔离的、分钟级 PIT 研究通道，交付顺序为：

1. 先沉淀连续的前向 1 分钟事实并支持分时复盘；
2. 仅在供应商验收通过后，回补历史分钟数据；
3. 仅在数据质量、可得时点和执行模型都满足门槛后，允许独立的分钟级 walk-forward 回测；
4. 不改变现有日频 `cs_data_*.csv`、日频策略或纸交易的语义。

## RALPLAN-DR（审慎模式）

### Principles

1. **事件时间与观察时间分离**：市场 bar 的结束时刻不等于 KSS 获得它的时刻。
2. **来源可重演**：每次采集必须可定位到 provider、请求窗口、响应 hash 与抓取 run。
3. **失败闭合**：不完整、未知可得时点或未验收来源的数据不能成为策略输入。
4. **两条回测通道隔离**：日频契约保持不变；分钟数据只经专用查询/执行层进入分钟研究。
5. **小而可逆**：复用 SQLite、AKShare、Tushare、launchd 与现有 state root，不引入大型量化框架或新依赖。

### Decision Drivers

1. PIT 与可审计性高于免费数据覆盖量。
2. 免费 1 分钟数据必须立即开始前向沉淀，但不能虚构历史完整性。
3. 方案必须适配现有双根存储、launchd 运行记录和数据目录（`kss/config/paths.py:21-27`；`scripts/build_data_catalog.py:44-59`；`scripts/kss_app_bridge.py:2802-2858`）。

### Viable options

| 选项 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. AKShare/东财前向采集 + Tushare 验收后历史回补 + 独立 SQLite | 复用已声明依赖；立即开始积累；不改变日频路径 | 1m 免费窗口短；Tushare 权限和覆盖待验收 | **选用** |
| B. 只依赖免费 AKShare/东财 | 零额外成本、实现快 | AKShare 1m 源码固定近 5 日，无法提供长期研究样本 | 否决为长期方案，仅作 A 的近端采集器 |
| C. 引入 QUANTAXIS/vn.py/RQAlpha | 有成熟对象模型或回测语义 | 增加 Mongo/框架耦合，且不自带可审计 A 股分钟历史 | 否决；仅借鉴 schema/Bar 聚合思想 |
| D. 从日线合成分钟线 | 看似有完整历史 | 会伪造盘中路径，无法验证执行、冲击或时点 | 明确禁止 |

Upstream evidence: [AKShare minute source](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_em.py#L1042-L1154) supports 1/5/15/30/60 minutes but fixes 1m to five days; [Tushare `pro_bar`](https://github.com/waditu/tushare/blob/master/tushare/pro/data_pro.py#L34-L51) exposes minute frequencies but not entitlement guarantees; [QUANTAXIS](https://github.com/QUANTAXIS/QUANTAXIS/blob/master/doc/user-guide/data-fetching.md#L100-L121) is a schema reference, not a dependency choice.

## Functional requirements

### F1. Provider contract and admission gate

Define an `IntradayProvider` protocol with `fetch_bars(...)`, provider name/version, supported intervals/assets, `source_asof_ts`, and an explicit capability result. Implement no strategy dependency before the command `scripts/probe_intraday_provider.py` produces an immutable report for 10 representative instruments (KCB, ChiNext, ETF, index where relevant) covering:

- minute frequency and earliest/latest timestamps;
- expected field mapping and units;
- 20-session continuity / duplicate rate / null rate;
- 95th percentile request latency, 429/5xx count and retry behavior;
- Tushare entitlement, quota and correction-policy result;
- whether a historical response provides availability/version evidence.

Acceptance: missing entitlement, a short coverage window, or unknown correction policy must classify the provider `research_only`, never `pit_backtest_eligible`. Historical point-in-time availability is supplied by a documented conservative (late-biased) `available_from_ts` proxy (see F5), not by per-bar vendor proof — so the absence of per-bar availability proof alone does not force `research_only`, but the proxy formula and its worst-case delay must be documented and frozen.

### F2. Immutable storage and canonical bar contract

Add `INTRADAY_DB = STORAGE_ROOT / "intraday_quotes.db"` beside existing mutable state roots; do not extend the daily `SQLiteStore` tables. The database contains:

```sql
ingest_runs(run_id TEXT PRIMARY KEY, provider TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, requested_symbols INTEGER, succeeded_symbols INTEGER,
  status TEXT NOT NULL, request_manifest_sha256 TEXT NOT NULL, error_summary TEXT);

payload_blobs(payload_sha256 TEXT PRIMARY KEY, response_zlib BLOB NOT NULL,
  response_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);

payload_observations(observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ingest_runs(run_id),
  provider TEXT NOT NULL, retrieved_at TEXT NOT NULL, redacted_request_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL REFERENCES payload_blobs(payload_sha256), parser_version TEXT NOT NULL,
  availability_class TEXT NOT NULL CHECK(availability_class IN
    ('forward_observed','provider_historical_evidence','research_backfill')),
  available_from_ts TEXT, evidence_ref TEXT, evidence_hash TEXT, provider_revision_id TEXT,
  eligibility TEXT NOT NULL CHECK(eligibility IN ('pit_backtest_eligible','research_only')));

instrument_registry(instrument_id TEXT NOT NULL, registry_version TEXT NOT NULL,
  asset_class TEXT NOT NULL, canonical_symbol TEXT NOT NULL, exchange TEXT NOT NULL,
  provider TEXT NOT NULL, provider_symbol TEXT NOT NULL, effective_from TEXT NOT NULL,
  effective_to TEXT, session_profile_id TEXT NOT NULL,
  PRIMARY KEY(instrument_id, registry_version, provider, effective_from));

session_profiles(session_profile_id TEXT NOT NULL, profile_version TEXT NOT NULL,
  effective_from TEXT NOT NULL, effective_to TEXT, timezone TEXT NOT NULL,
  legal_bar_endpoints_json TEXT NOT NULL, auction_policy TEXT NOT NULL,
  PRIMARY KEY(session_profile_id, profile_version, effective_from));

provider_bar_contracts(provider TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
  contract_version TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
  timestamp_semantics TEXT NOT NULL CHECK(timestamp_semantics IN ('start','end')),
  publication_delay_seconds INTEGER NOT NULL, session_profile_id TEXT NOT NULL,
  profile_version TEXT NOT NULL, PRIMARY KEY(provider, interval_minutes, contract_version, effective_from));

canonical_bars(instrument_id TEXT NOT NULL, observation_id TEXT NOT NULL REFERENCES payload_observations(observation_id),
  bar_end_ts TEXT NOT NULL,
  interval_minutes INTEGER NOT NULL, trade_date TEXT NOT NULL, open REAL NOT NULL,
  high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL,
  amount REAL, vwap REAL, source TEXT NOT NULL, source_asof_ts TEXT,
  observed_at TEXT NOT NULL, session_profile_id TEXT NOT NULL, profile_version TEXT NOT NULL,
  provider_contract_version TEXT NOT NULL, revision INTEGER NOT NULL, quality_flags TEXT NOT NULL,
  PRIMARY KEY(instrument_id, bar_end_ts, interval_minutes, source, revision));

coverage_assessments(assessment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ingest_runs(run_id),
  instrument_id TEXT NOT NULL, trade_date TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
  assessed_at TEXT NOT NULL, assessment_kind TEXT NOT NULL, status TEXT NOT NULL,
  expected_count INTEGER NOT NULL, received_count INTEGER NOT NULL, missing_json TEXT NOT NULL,
  provider TEXT NOT NULL, provider_contract_version TEXT NOT NULL,
  canonical_manifest_sha256 TEXT NOT NULL, reconciliation_provider TEXT,
  tolerance_version TEXT, details_json TEXT NOT NULL);
```

`payload_blobs` de-duplicates bytes while `payload_observations` records **every** HTTP observation/run. `eligibility` is the provider-level admission class copied from the F1 gate at ingest time (`pit_backtest_eligible` or `research_only`); it is distinct from per-observation `availability_class` — `eligibility` records what the provider was admitted as, `availability_class` records how this specific observation was obtained. `session_profiles` freezes the Asia/Shanghai legal bar-end endpoints, lunch/auction policy and effective range; `provider_bar_contracts` freezes whether each provider timestamp is bar-start or bar-end plus the matching `publication_delay_seconds`. Canonical rows persist both version identifiers, so a later profile cannot reinterpret prior timestamps. Canonical rows are append/versioned: a conflicting observation creates a higher revision; the first accepted observation remains queryable.

An assessment certifies an immutable **dataset snapshot**, not a loose date: `canonical_manifest_sha256` is the SHA-256 of the canonical JSON list sorted by `(instrument_id, bar_end_ts, interval_minutes, source)` and containing each expected endpoint's selected revision, `observation_id`, `provider_contract_version`, session/profile version, plus the explicit missing-endpoint list. It is recomputed whenever a canonical revision changes or reconciliation runs. `load_asof` first selects the candidate rows as of `as_of_ts`, derives that exact manifest, then admits them only if a complete/reconciled assessment with the same instrument/day/interval/provider/contract/manifest has `assessed_at <= as_of_ts`. A later revision therefore cannot inherit a prior revision's green assessment.

The store enables `foreign_keys=ON`, `journal_mode=WAL` and a bounded `busy_timeout` on every connection; each run writes its run state, observations, canonical rows and initial assessment in one transaction. A terminal failed run persists without queryable partial rows. Revision allocation has a unique index on `(instrument_id, bar_end_ts, interval_minutes, source, revision)` and happens inside that transaction. The existing daily cache deliberately overwrites identical keys, so it cannot provide this invariant (`kss/data/sqlite_store.py:192-224`).

Credential safety is a storage invariant, not only a logging rule. A single serializer is the sole writer of `redacted_request_json` and must strip, from URL query, headers and body, any field whose key matches `(token|key|secret|auth|credential)` case-insensitively before persistence. Before a response is compressed into `payload_blobs`, the ingest path scans the decoded body for the configured token/account patterns and refuses persistence (terminal `credential_in_payload` run) on a match, so no credential or account identifier is stored at rest. `error_summary` is written through the same redactor.

### F3. Collection and recovery

- Before any provider call, collector resolves a versioned `instrument_registry` manifest. The write path validates, in the same transaction, that effective intervals for one `(instrument_id, provider)` never overlap; the runtime resolver additionally requires **exactly one** active mapping at collection time. Zero matches produce `mapping_unknown`; more than one produces terminal `mapping_ambiguous`; both make zero provider calls for that instrument. No code infers an Eastmoney `secid` from a prefix. Registry validation is an application-level range query (SQLite has no portable exclusion constraint), covered by a transaction/concurrency test.
- `scripts/collect_intraday.py --mode close` runs at 15:05 on actual SSE trading days, uses the provider gate/capabilities, serial rate limiting and bounded retry policy inherited from the Tushare pattern (`kss/data/tushare_client.py:16-68`). `trade_cal` failure creates a `calendar_unknown` failed run and exits non-zero; it must not fall back to weekday/archive guessing.
- The close collector ingests the complete **current** session only. It must not update prior 1m days from a rolling response that has zero/altered opens; it records a coverage gap instead.
- Optional `--mode watch` polls only a configured small watchlist at five-minute cadence during market sessions; it is shadow/observability only until separately enabled.
- The tracked artifact is `deploy/launchd/com.zcdeng.kss.collect_intraday.plist.template`; the owned standard-library renderer is `scripts/render_intraday_launchd_plist.py`. Deployment must run `python3 scripts/render_intraday_launchd_plist.py --project-root <absolute-project-root> --state-root <absolute-state-root> --output deploy/launchd/com.zcdeng.kss.collect_intraday.plist`, then `plutil -lint` and `launchctl bootstrap gui/<uid> <rendered-plist>`. The renderer writes concrete absolute `ProgramArguments`, `EnvironmentVariables.KSS_STATE_ROOT`, and `StandardOutPath`/`StandardErrorPath` below `<state-root>/storage/logs/launchd`; it rejects unresolved placeholders and relative paths. The rendered `.plist` lives at `PROJECT_ROOT/deploy/launchd/` (the code root the bridge globs — not the state root), so existing plist-log introspection (`scripts/kss_app_bridge.py:2802-2858`) keeps working; "state-root isolation" here means only the plist's **runtime** paths (`StandardOutPath`/`StandardErrorPath`, `EnvironmentVariables.KSS_STATE_ROOT`) resolve under the writable state root, not the plist file location. The renderer is warranted (vs the static plists used elsewhere) precisely because that state-root path varies between dev and bundled installs (the documented code/state double-root split), so absolute paths cannot be hard-coded once. Register the new label/title/category in the bridge's explicit maps. `TUSHARE_TOKEN` must never appear in the rendered `.plist` `EnvironmentVariables` block (only `KSS_STATE_ROOT` is injected there); the collector reads the token from `KSS_STATE_ROOT/secrets/` (mode 0600, git-ignored) or the macOS keychain, and the renderer rejects output containing any token-pattern string.
- Collection appends a `coverage_assessments` record per instrument/day/interval: expected timestamps, received count, duplicate count, missing timestamps, terminal status, reconciliation source/tolerance, provider/contract version, certified canonical manifest and assessment time.
- Configure hard limits before shadow collection: default `INTRADAY_MAX_DB_BYTES=4294967296` and `INTRADAY_MAX_RAW_BYTES_PER_RUN=67108864`. Exceeding either creates a terminal `retention_limit` run and exits non-zero; an operator must adjust the documented configuration after observing 20 sessions.

### F4. Quality and reconciliation

Use a provider/session calendar rather than a hard-coded 240-row count. The current Eastmoney probe returned 241 timestamps for a full session; the validator must distinguish expected zero-volume bars from missing records. It first normalizes source timestamps according to the matching versioned provider contract (`start` timestamps become their legal end endpoint; `end` timestamps remain endpoints) and rejects any timestamp absent from the frozen session profile. Quality is append-only: each check writes a time-stamped `coverage_assessments` record, not a mutable status field. Each accepted day must satisfy:

- timezone is Asia/Shanghai; timestamp is a legal session endpoint;
- `low <= min(open, close) <= max(open, close) <= high`, non-negative volume/amount;
- no duplicate `(symbol, bar_end_ts, interval, source, revision)`;
- daily OHLCV reconciliation to an independently fetched day bar on the next available data cycle, with tolerances documented in config;
- the response structure matches the schema hash frozen at provider admission (F1); a structural mismatch produces a terminal `schema_drift` run with no canonical rows plus an operator alert, distinct from an empty/malformed response;
- non-complete coverage has `quality_flags` and cannot flow into backtest queries.

### F5. PIT query and execution contract

`IntradayStore.load_asof(instruments, start, end, interval, as_of_ts, eligibility)` returns only the newest revision with a matching, valid **data-set eligibility** and a complete/reconciled `coverage_assessments.assessed_at <= as_of_ts` for that exact certified provider/contract/canonical manifest. Its deterministic tie-breaker is `available_from_ts`, then `observed_at`, revision, and `observation_id`.

The `eligibility` argument selects which provider admission class to admit (backtest callers pass `pit_backtest_eligible` only); within that, observations are further gated by their per-observation `availability_class`. Provider observations are classified as `forward_observed`, `provider_historical_evidence`, or `research_backfill`. `pit_backtest` can read forward observations, or provider historical evidence whose `available_from_ts` is a **documented conservative (late-biased) proxy** — `bar_end` plus the worst-case publication delay, or the close of `trade_date + 1` — carried with a frozen manifest and evidence reference/hash and flagged as proxy-PIT rather than proven-PIT. A late-biased proxy is safe against look-ahead (it can only under-credit a strategy). `research_backfill` is permanently excluded from PIT backtests regardless of provider. The forward-only `load_asof`/eligibility/execution-delay guard ships in Phase 2; the `provider_historical_evidence` proxy path is built in Phase 7 after Tushare admission. Signal computation can use only completed bars where `bar_end_ts + publication_delay <= signal_time`; an order generated from bar N cannot execute at bar N close and must fill no earlier than the configured next eligible bar. Raw prices and corporate-action adjustment factors remain separate.

The first application is read-only intraday review. Minute backtesting is an explicit later phase and must use this query API plus a dedicated cost/execution model; it must not call existing day-based `FactorPipeline` or reuse daily `next_day_return` semantics.

### F6. Data catalog, observability and operational surface

Register `intraday_quotes.db` in the explicit catalog whitelist (`scripts/build_data_catalog.py:44-59`) and add its field meanings to `kss/config/data_catalog_meta.yaml`. The whitelist entry enumerates a table allowlist (`ingest_runs`, `coverage_assessments`, `instrument_registry`, `session_profiles`, `provider_bar_contracts`, `canonical_bars`) and excludes every BLOB-typed column from reflection; `payload_blobs` and `payload_observations.redacted_request_json` never appear in catalog output. Surface only state/coverage/latest timestamp through the bridge; never expose raw payload bytes. Each run logs provider, run id, symbols, rows, coverage ratio, backlog and failure reason. Alert when an active session's final coverage is below configured threshold or no complete close run exists by 16:00 local time; the coverage threshold and the F3 retention limits are conservative starting values to be calibrated after the Phase 5 shadow run.

## Non-goals

- No intraday order routing, brokerage integration, tick/order-book storage, or automatic production trading.
- No replacement of Tushare daily data or existing daily backtests.
- No claim that scraped responses alone *prove* historical point-in-time availability; historical PIT uses a documented conservative (late-biased) `available_from_ts` proxy, explicitly flagged as proxy-PIT, never asserted as proven.
- No import of QUANTAXIS, vn.py, RQAlpha, MongoDB, Kafka, or a new database dependency.

## Delivery phases

1. **Contract and registry probe + thin forward logger** — freeze a versioned instrument/provider mapping manifest, run a controlled ten-instrument provider probe, **and ship a minimal append-only raw-capture logger (blob + retrieved_at + run id, no canonical processing) so forward collection begins immediately**; stop historical scope if Tushare fails the gate. Canonical normalization is layered later over the retained blobs (non-lossy: the immutable blobs plus retrieved_at allow versioned profiles/assessments to certify these bars retroactively).
2. **Forward-PIT store + domain tests** — implement isolated SQLite store, blob/observation lineage, non-overlapping mapping enforcement, atomic revisioning, snapshot-bound time-versioned assessments, canonical normalization, session validator and fixtures, plus the forward-only `load_asof`/eligibility/execution-delay guard. The historical-evidence admission path (`provider_historical_evidence`, `evidence_ref`/`evidence_hash`, conservative-proxy `available_from_ts`) is deferred to Phase 7.
3. **Close collector** — implement serial, idempotent current-session collection with atomic run state, retention limits and coverage assessments; add template, deterministic state-root renderer, wrapper and rendered-plist deployment check.
4. **Catalog and Runbook visibility** — register catalog metadata, bridge schedule/status, and operational documentation.
5. **20-session shadow run** — monitor completeness, reconciliation, retry and disk growth; no strategy consumption. Calibrate `INTRADAY_MAX_DB_BYTES`, `INTRADAY_MAX_RAW_BYTES_PER_RUN` and the coverage-alert threshold from observed values.
6. **Historical admission decision** — approve Tushare (or explicitly retain forward-only mode) from probe evidence and frozen manifests.
7. **Historical admission machinery + minute research/backtest slice** — only after gates pass, add the `provider_historical_evidence` proxy-PIT admission path (conservative `available_from_ts`, evidence reference/hash), session-aware aggregation, execution/cost tests, then run existing robustness/DSR standards on a separately labelled strategy.

## Acceptance criteria and stop rules

1. The intraday database is under `KSS_STATE_ROOT/storage`, and no existing daily DB/table/schema is altered.
2. Every canonical row links to a specific observation, which links to a raw blob and ingest run; replayed identical payloads preserve two observations but one canonical revision, while conflicting payloads preserve both revisions.
3. A complete session has valid session timestamps and reconciles to day OHLCV within configured tolerances; incomplete or subsequently failed assessments are visible and rejected by PIT queries as of their assessment time. A later canonical revision is excluded until an assessment certifies its exact manifest.
4. Provider outage/permission failure ends the run non-zero with a persisted run record and no partial data marked complete.
5. Re-running the same payload is idempotent; re-reading a changed payload does not overwrite the earlier revision.
6. `load_asof` demonstrably excludes a later observation, `research_backfill` observations, a bar with no qualifying assessment, and the execution model rejects same-close fills.
7. The 20-session shadow report records at least 95% successful close runs and zero falsely-complete sessions; otherwise Phase 6 is blocked.
8. Minute backtest code cannot be merged until provider admission is `pit_backtest_eligible`, history/retention are documented, and unit/integration tests in the test spec pass.

## Pre-mortem

| Failure scenario | Early warning | Mitigation / stop condition |
|---|---|---|
| Eastmoney changes/limits endpoint | repeated 429/JSON schema errors, missing close runs | circuit-break provider, retain raw failure metadata, no automatic alternate-source merge; investigate and re-probe |
| Tushare account has insufficient minute coverage | probe returns permission error or narrow/unexplained range | remain forward-only; do not create backtest result from short free window |
| Backtest leaks post-close information | same-close fills, later observation or unassessed rows appear | `load_asof` and execution invariant tests block merge; labelled violation fails CI |

## ADR-001: Isolated versioned SQLite fact store with two-source admission

### Decision

Use a new state-root SQLite database with content-addressed blobs, per-run observations, versioned canonical bars, versioned quality assessments and frozen instrument mappings. AKShare/东财 supplies forward near-term collection; Tushare is eligible for historical minutes only after explicit provider-contract acceptance, admitted as proxy-PIT via a documented conservative (late-biased) `available_from_ts`, not per-bar proof. The forward-PIT core ships first (Phase 1 thin logger + Phase 2 store); the historical-evidence proxy machinery is deferred to Phase 7.

### Drivers

PIT evidence, existing SQLite/launchd patterns, and the factual five-day limitation of free 1m upstream data.

### Alternatives considered

Free source only; framework/Mongo adoption; daily-to-minute synthesis. These are rejected respectively for insufficient historical coverage, unnecessary coupling/no data guarantee, and fabricated intraday path.

### Why chosen

It starts collecting immediately while making historical and strategy eligibility explicit rather than implicit.

### Consequences

Adds a database, deployment-rendered scheduled job and bounded raw retention, and defers strategy use until shadow/probe gates pass. It leaves daily behaviour unchanged.

Minimum viable payoff even if Tushare entitlement never passes: the owner still accumulates self-observed forward 1m bars (proven-PIT by construction), usable immediately for read-only intraday review and — once enough sessions accrue — a forward-only walk-forward. The Phase 1 thin logger therefore delivers value independent of the historical-admission outcome, which is why it leads the sequence.

### Follow-ups

Decide Tushare entitlement after Phase 1; size raw-payload retention after Phase 5; separately plan minute execution modelling only after the shadow report passes.

## Likely file map

- New: `kss/data/intraday_store.py`, `kss/data/intraday_client.py`, `scripts/probe_intraday_provider.py`, `scripts/collect_intraday.py`, `scripts/run_collect_intraday.sh`, `scripts/render_intraday_launchd_plist.py`, `deploy/launchd/com.zcdeng.kss.collect_intraday.plist.template`, generated `deploy/launchd/com.zcdeng.kss.collect_intraday.plist`, `kss/tests/test_intraday_*.py`.
- Extend: `kss/config/paths.py`, `kss/data/tushare_client.py`, `scripts/build_data_catalog.py`, `kss/config/data_catalog_meta.yaml`, `scripts/kss_app_bridge.py`.
- Do not change: `cs_data_*.csv`, `kss/data/sqlite_store.py` daily table semantics, existing daily strategy/backtest behavior.

## Execution handoff after consensus

This section is a handoff guide only; it does not authorize execution in the current `ralplan` turn.

### Available agent roster and recommended staffing

Available roles: `planner`, `architect`, `critic`, `explore`, `researcher`, `dependency-expert`, `executor`, `test-engineer`, and `verifier`.

For the next explicit implementation request, prefer **`$ultragoal` + `$team`** because the provider investigation, storage contract, scheduled deployment and verification evidence have separable ownership:

| Lane | Role | Ownership | Required proof |
|---|---|---|---|
| Provider gate | `researcher` + `dependency-expert` | ten-instrument probe, Tushare entitlement/correction-policy evidence | immutable probe report and admission class |
| Store/collector | `executor` | schema, manifest-bound `load_asof`, mapping resolver, collector | focused unit and mocked collector integration tests |
| Tests and deployment | `test-engineer` | boundary fixtures, renderer, bridge/catalog integration | `pytest`, `plutil -lint`, no-live-call CI proof |
| Acceptance | `verifier` | replay, 20-session report, PIT/retention audit | durable shadow report and stop-rule checklist |

Suggested team launch hint (only after the user authorizes implementation): start from this PRD and its companion test specification, assign exclusive ownership by the four lanes above, then have `verifier` review the combined diff before merge. Do not run collection, bootstrap launchd or use a live token as part of planning.

### Team verification and goal modes

The team is not complete until it has evidence for: provider probe classification; unit/integration suite; dry-run collector; rendered plist parse plus `plutil -lint`; catalog/bridge visibility; and the 20-session shadow report meeting the 19/20 and zero-false-complete gates. `$ultragoal` is the best default to preserve these checkpoints across the long shadow phase; `$team` is appropriate for the initial multi-file implementation. `$autoresearch-goal` is useful only if provider capability remains the sole unresolved question; `$performance-goal` is not applicable. `$ralph` is a fallback only for a single-owner sequential repair loop after a failed gate, not the preferred initial execution mode.

## Consensus changelog

- Initial draft grounded in repository inspection and upstream GitHub source evidence.
- Architect round 1: requested observation/blob separation, historical availability classes, time-versioned assessment, SQLite transaction/FK discipline, mapping and deployment details; incorporated.
- Architect round 2: requested versioned session/time semantics and non-retroactive reconciliation assertions; incorporated and approved.
- Critic round 1: requested assessment-to-manifest binding, fail-closed overlapping mapping handling, and concrete launchd renderer/template/deployment contract; incorporated.
- Architect round 3: approved the three Critic-requested closures.
- Critic round 2: approved. See durable consensus record in `.omx/state/intraday-data-layer-ralplan-consensus.json`.
