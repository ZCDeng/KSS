"""U2 单测：薄前向 raw-capture 存储的 PIT/不可变/凭据/fail-closed 不变式.

约束（plan U2 Execution note，学习 #2）：这些不变式正是测试要钉死的——PIT 用代码
而非散文保证。所有 db 落 ``tmp_path``，不污染真实 storage/。
"""

from __future__ import annotations

import sqlite3

import pytest

from kss.data.intraday_store import (
    IntradayStore,
    MappingStatus,
    ObservationInput,
)
from kss.security.redaction import (
    REDACTED,
    contains_credential,
    redact_request,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path) -> IntradayStore:
    return IntradayStore(tmp_path / "intraday_quotes.db")


@pytest.fixture()
def instrument_id(store: IntradayStore) -> int:
    return store.register_instrument(
        symbol="688008.SH",
        asset_kind="stock",
        provider="eastmoney_akshare",
        provider_symbol="688008",
        active_from="2026-01-01",
    )


def _obs(instrument_id: int, rows, *, error=None, secrets=()) -> ObservationInput:
    return ObservationInput(
        instrument_id=instrument_id,
        provider="eastmoney_akshare",
        interval_minutes=1,
        request_meta={
            "method": "GET",
            "url": "https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=1.688008",
            "params": {"secid": "1.688008"},
        },
        payload_rows=rows,
        source_asof_ts="2026-06-19T15:00:00+08:00",
        availability_class="forward_observed",
        eligibility="forward_observed",
        status_code=200 if error is None else None,
        error=error,
    )


_PAYLOAD_A = [
    {"时间": "2026-06-19 14:59:00", "收盘": 10.1, "成交量": 1000},
    {"时间": "2026-06-19 15:00:00", "收盘": 10.2, "成交量": 1500},
]
_PAYLOAD_B = [
    {"时间": "2026-06-19 14:59:00", "收盘": 10.1, "成交量": 1000},
    {"时间": "2026-06-19 15:00:00", "收盘": 10.3, "成交量": 1800},  # 改了收盘/量
]


# --------------------------------------------------------------------------- #
# Covers test-spec U2：内容寻址 blob 去重 + observation lineage
# --------------------------------------------------------------------------- #


def test_same_payload_two_runs_one_blob_two_observations(store, instrument_id):
    r1 = store.ingest_run(
        provider="eastmoney_akshare",
        mode="close",
        trade_date="2026-06-19",
        observations=[_obs(instrument_id, _PAYLOAD_A)],
    )
    r2 = store.ingest_run(
        provider="eastmoney_akshare",
        mode="close",
        trade_date="2026-06-19",
        observations=[_obs(instrument_id, _PAYLOAD_A)],
    )
    assert r1.status == "completed" and r2.status == "completed"
    # 相同内容 → 一个 blob；两次 run 各一 observation。
    assert store.count_blobs() == 1
    assert store.count_observations() == 2
    # 第二个 run 没新写 blob（去重命中）。
    assert r1.blobs_written == 1 and r2.blobs_written == 0


def test_changed_payload_creates_second_blob(store, instrument_id):
    store.ingest_run(
        provider="eastmoney_akshare", mode="close", trade_date="2026-06-19",
        observations=[_obs(instrument_id, _PAYLOAD_A)],
    )
    store.ingest_run(
        provider="eastmoney_akshare", mode="close", trade_date="2026-06-19",
        observations=[_obs(instrument_id, _PAYLOAD_B)],
    )
    assert store.count_blobs() == 2
    assert store.count_observations() == 2


# --------------------------------------------------------------------------- #
# Covers test-spec U3：FK provenance（foreign_keys=ON 使孤儿写失败）
# --------------------------------------------------------------------------- #


def test_foreign_keys_on_rejects_orphan_observation(store, instrument_id):
    with pytest.raises(sqlite3.IntegrityError):
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO payload_observations "
                "(run_id, instrument_id, provider, interval_minutes, "
                " availability_class, eligibility, redacted_request_json, observed_at) "
                "VALUES ('NO_SUCH_RUN', ?, 'p', 1, 'forward_observed', "
                "'forward_observed', '{}', '2026-06-19T15:00:00+08:00')",
                (instrument_id,),
            )


def test_every_observation_joins_to_blob_and_run(store, instrument_id):
    r = store.ingest_run(
        provider="eastmoney_akshare", mode="close", trade_date="2026-06-19",
        observations=[_obs(instrument_id, _PAYLOAD_A)],
    )
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT o.observation_id FROM payload_observations o "
            "JOIN ingest_runs r ON o.run_id = r.run_id "
            "JOIN payload_blobs b ON o.payload_sha256 = b.payload_sha256 "
            "WHERE o.run_id = ?",
            (r.run_id,),
        ).fetchall()
    assert len(rows) == 1  # 每 observation 连得上 run 与 blob


# --------------------------------------------------------------------------- #
# Covers test-spec U10（部分）：fail-closed registry 解析
# --------------------------------------------------------------------------- #


def test_zero_mapping_resolves_unknown(store):
    res = store.resolve_instrument("999999.SH", "eastmoney_akshare", "2026-06-19")
    assert res.status is MappingStatus.UNKNOWN
    assert res.instrument_id is None


def test_expired_mapping_resolves_unknown(store):
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008",
        active_from="2026-01-01", active_to="2026-03-31",  # 已过期
    )
    res = store.resolve_instrument("688008.SH", "eastmoney_akshare", "2026-06-19")
    assert res.status is MappingStatus.UNKNOWN


def test_two_active_mappings_resolve_ambiguous(store):
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008", active_from="2026-01-01"
    )
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008X", active_from="2026-02-01"
    )
    res = store.resolve_instrument("688008.SH", "eastmoney_akshare", "2026-06-19")
    assert res.status is MappingStatus.AMBIGUOUS
    assert res.instrument_id is None  # 不解析 → 调用方据此零 provider 调用


def test_single_active_mapping_resolves(store, instrument_id):
    res = store.resolve_instrument("688008.SH", "eastmoney_akshare", "2026-06-19")
    assert res.status is MappingStatus.RESOLVED
    assert res.instrument_id == instrument_id
    assert res.provider_symbol == "688008"


# --------------------------------------------------------------------------- #
# 凭据安全（D5/D6）：脱敏唯一写入点 + 响应体回显 → credential_in_payload
# --------------------------------------------------------------------------- #


_FAKE_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445"  # 40-hex Tushare 风格


def test_redacted_request_json_strips_token(store, instrument_id):
    obs = ObservationInput(
        instrument_id=instrument_id,
        provider="tushare",
        interval_minutes=1,
        request_meta={
            "method": "POST",
            "url": f"https://api.tushare.pro?token={_FAKE_TOKEN}",
            "headers": {"Authorization": f"Bearer {_FAKE_TOKEN}"},
            "body": {"token": _FAKE_TOKEN, "api_name": "stk_mins"},
        },
        payload_rows=_PAYLOAD_A,
        source_asof_ts="2026-06-19T15:00:00+08:00",
        availability_class="forward_observed",
        eligibility="research_only",
    )
    store.ingest_run(
        provider="tushare", mode="close", trade_date="2026-06-19",
        observations=[obs], known_secrets=(_FAKE_TOKEN,),
    )
    obs_rows = store.list_observations()
    assert len(obs_rows) == 1
    assert _FAKE_TOKEN not in obs_rows[0]["redacted_request_json"]


def test_response_body_echoing_token_terminates_credential_in_payload(store, instrument_id):
    # 响应体回显 token（模拟 401 错误体把 token 打回来）。
    poisoned = [{"时间": "2026-06-19 15:00:00", "echoed": _FAKE_TOKEN}]
    result = store.ingest_run(
        provider="tushare", mode="close", trade_date="2026-06-19",
        observations=[_obs(instrument_id, poisoned)],
        known_secrets=(_FAKE_TOKEN,),
    )
    assert result.status == "failed"
    assert result.failure_reason == "credential_in_payload"
    # 无可查部分行：observation/blob 全无；只留终止 failed run。
    assert store.count_observations() == 0
    assert store.count_blobs() == 0
    with store._conn() as conn:
        failed = conn.execute(
            "SELECT failure_reason, status FROM ingest_runs WHERE status='failed'"
        ).fetchall()
    assert len(failed) == 1 and failed[0][0] == "credential_in_payload"


def test_terminal_failure_persists_run_with_no_data_rows(store):
    run_id = store.record_terminal_failure(
        provider="eastmoney_akshare", mode="close", trade_date="2026-06-19",
        failure_reason="calendar_unknown", error_summary="trade_cal unreachable",
    )
    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert run["failure_reason"] == "calendar_unknown"
    assert run["exit_code"] == 1
    assert store.count_observations() == 0


def test_unknown_terminal_reason_rejected(store):
    with pytest.raises(ValueError):
        store.record_terminal_failure(
            provider="p", mode="close", trade_date=None,
            failure_reason="not_a_real_reason",
        )


# --------------------------------------------------------------------------- #
# S3 双路径脱敏：U2 扫描 + 渲染器守卫共用同一 canonical 常量（同 fixture 都拒绝）
# --------------------------------------------------------------------------- #


def test_redaction_canonical_constant_dual_path_rejects_same_token():
    # 路径一（U2 响应体扫描）：contains_credential 命中 40-hex token。
    assert contains_credential(f'{{"echoed":"{_FAKE_TOKEN}"}}') is True
    # 路径二（请求序列化脱敏）：redact_request 把 token 全脱掉。
    red = redact_request(
        "POST", f"https://api.tushare.pro?token={_FAKE_TOKEN}",
        headers={"Authorization": f"Bearer {_FAKE_TOKEN}"},
        body={"token": _FAKE_TOKEN},
        known_secrets=(_FAKE_TOKEN,),
    )
    import json as _json

    serialized = _json.dumps(red)
    assert _FAKE_TOKEN not in serialized
    assert REDACTED in serialized


def test_contains_credential_no_false_positive_on_price_json():
    """普通价格 JSON（短数字）不应误判为凭据。"""
    clean = '{"时间":"2026-06-19 15:00:00","收盘":10.2,"成交量":1500}'
    assert contains_credential(clean) is False


# --------------------------------------------------------------------------- #
# state-root≠code-root：DB 落 state root（学习 #4 bundle 双根）
# --------------------------------------------------------------------------- #


def test_intraday_db_resolves_under_state_root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("KSS_STATE_ROOT", str(state))
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(tmp_path / "code"))
    import importlib

    import kss.config.paths as paths

    importlib.reload(paths)
    try:
        assert str(paths.INTRADAY_DB).startswith(str(state.resolve()))
        assert "code" not in str(paths.INTRADAY_DB)  # 不落 code root
    finally:
        # 还原 paths 模块全局，避免污染同进程其它测试。
        monkeypatch.undo()
        importlib.reload(paths)
