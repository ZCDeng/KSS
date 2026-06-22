# Test Specification: KSS 分时数据层

Companion to `prd-intraday-data-layer.md`. These tests are acceptance gates, not an authorization to implement minute strategy logic.

## Unit tests

| ID | Subject | Fixture / action | Expected result |
|---|---|---|---|
| U1 | canonical normalization | Known valid 1m payload, Asia/Shanghai session | normalized fields, units and `bar_end_ts` exactly match fixture |
| U2 | blob/observation immutability | store same payload in two runs, then changed payload for same bar | one blob, two observations, one canonical revision for same content; changed payload makes revision 2; revision 1 remains queryable |
| U3 | provenance / FK | insert canonical bars and deliberately orphan references | every bar joins to observation, blob and run; `foreign_keys=ON` makes orphan write fail |
| U4 | session validator | legal timestamp, lunch timestamp, duplicate, negative volume, invalid OHLC | only legal complete bar passes; every defect sets a specific quality flag |
| U5 | expected session profile | provider profile with 241 timestamps and zero-volume bar | profile accepts valid 241 shape; missing timestamp is incomplete, not silently filled |
| U6 | as-of availability_class and manifest binding | forward, provider-evidence and research-backfill observations plus timed assessments; rev1 has a passing manifest, rev2 arrives before its own assessment | earlier as-of returns only qualifying revision; research backfill/late observation/unassessed row never enters PIT query; rev2 is excluded rather than inheriting rev1's passed assessment |
| U7 | availability/execution guard | signal on N close / same-close requested fill | rejected or moved to N+1 according to configured execution delay |
| U8 | provider capability | permissions/coverage/error stubs | failure produces `research_only` / failed run, never `pit_backtest_eligible` |
| U9 | catalog metadata | fake DB + meta overlay | catalog exposes DB/table fields; overlay drift appears as warning, following `scripts/build_data_catalog.py:80-91` |
| U10 | mapping / retention | expired mapping, two overlapping active mappings, and oversized payload/run | zero mapping skips with `mapping_unknown`; overlap is rejected at registry write or resolves `mapping_ambiguous` with **no provider call**; retention limit creates terminal failed run and no queryable rows |
| U11 | session/profile contract | one raw timestamp under `start` then `end` contracts and two distinct session profiles | normalizes to the declared bar end; prior rows retain their profile version; no global 241-row assumption |

All test databases use `tmp_path`, following `kss/tests/test_sqlite_store.py:1-22`; no test writes project `storage/`.

## Integration tests

1. **Close collector happy path**: local mocked provider returns a complete current session for three symbols. Verify one completed run, raw payload hashes, canonical rows, coverage summaries and a zero exit code.
2. **Partial provider failure**: one symbol times out after bounded retries. Verify persisted failed/partial run, that successful symbols are marked per-symbol only, aggregate run is non-zero, and no day becomes `complete` globally.
3. **Next-cycle daily reconciliation and revision admission**: fake daily source differs in high/amount beyond tolerance after an initial complete assessment. Verify an `as_of_ts` between the two assessments returns the then-qualified revision, while `as_of_ts >= reconciliation_failed.assessed_at` rejects it. Separately ingest canonical rev2 after rev1's passing assessment but before rev2 has a certified manifest: `load_asof` must exclude rev2 until its own matching complete assessment exists. This proves failure applies from its assessment time and a green rev1 cannot authorize rev2.
4. **Restart/replay**: invoke collector twice with identical fixture output. Verify two observations and one canonical revision; mutate payload and verify a second revision, not an overwrite.
5. **State-root relocation/rendering**: invoke `scripts/render_intraday_launchd_plist.py` with absolute `--project-root`, `--state-root=tmp_path/state` and explicit output. Verify parsed plist has no template marker or relative path, its `EnvironmentVariables.KSS_STATE_ROOT`, DB/raw storage and stdout/stderr paths resolve under the supplied root, and `ProgramArguments` names the absolute wrapper.
6. **Launchd/bridge inspection**: place that rendered plist at the bridge-globbed deployment destination, parse it with the existing `_scheduled_job` code path, and confirm title/category/log path/schedule become visible without calling `launchctl` in CI. A command-level deployment smoke check runs `plutil -lint`; `launchctl bootstrap` remains manual/local only.
7. **Calendar failure**: make `trade_cal` unavailable. Verify a persisted `calendar_unknown` terminal run and non-zero exit, never weekday/archive fallback.

## End-to-end / shadow acceptance

Run a controlled 20-session shadow cycle on the approved active strategy universe (not all stock names).

- Daily close collector runs on trade-cal-confirmed days, serially and produces run summaries.
- For every successful full day, report coverage percentage, missing timestamps, reconciliation status, response hash, latency and disk increment.
- Success = at least 19 of 20 expected close runs succeed, zero `complete` day has a reconciliation mismatch, and no run consumes a later-ingested bar in an as-of replay.
- A failed source day must stay a visible gap; operators must not backfill it with a rolling 1m response unless its field quality is revalidated.

## Provider admission test

`probe_intraday_provider.py` is run with a valid local token but must not print the token. Its machine-readable report contains provider identity, 10-symbol outcomes, source ranges, minutes, status codes, quotas/errors, schema hashes and eligibility.

Admission assertions:

- `AKShare/Eastmoney` is accepted only as `forward_observed`/`research_only`; its 1m five-day limitation is expected from upstream source.
- Tushare is `pit_backtest_eligible` only if entitlement, requested historical coverage, completeness, correction policy, frozen manifest evidence, a documented conservative (late-biased) `available_from_ts` proxy, and evidence reference/hash all pass. The proxy formula and its worst-case delay must be frozen; the absence of per-bar vendor availability proof does not by itself fail admission.
- Unknown entitlement, coverage, or correction policy causes a hard failure for historical minute backtest; shadow collection may continue only if its own source is available.

## Observability checks

- Every run emits structured `run_id`, provider, requested/succeeded symbols, rows, coverage, latency, DB bytes and status.
- Alert condition test: no completed close run by 16:00 or coverage below configured threshold emits a machine-readable `degraded` state.
- Privacy/security test: logs/catalog and the persisted redacted request never include `TUSHARE_TOKEN` or credential headers; raw payload blobs contain response body only.

## Minute-backtest gate (deferred)

Before adding any minute strategy, write tests proving:

1. feature inputs have `bar_end_ts + publication_delay <= signal_time`;
2. fills occur only after the signal bar;
3. no incomplete/reconciliation-failed session enters training or evaluation;
4. costs and price limits are applied on the minute execution path; and
5. the minute strategy is separately evaluated by the existing significance/robustness protocol rather than inheriting daily strategy approval.

## Commands at implementation time

```bash
pytest -q kss/tests/test_intraday_store.py kss/tests/test_intraday_client.py kss/tests/test_intraday_collector.py
pytest -q kss/tests/test_build_data_catalog.py kss/tests/test_sqlite_store.py
python3 scripts/probe_intraday_provider.py --report storage/reports/intraday_provider_probe.json
python3 scripts/collect_intraday.py --mode close --dry-run
```

The exact Python executable must follow the project’s verified desktop/runtime environment; the test suite must not make a live provider call by default.
