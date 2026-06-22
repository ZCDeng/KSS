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


# =========================================================================== #
# U3：canonical bar 契约 + session 校验 + 版本化归一化（阶段 2）
# =========================================================================== #

from kss.data.intraday_store import (  # noqa: E402
    QualityFlag,
    build_legal_bar_ends,
    normalize_bar,
    validate_bar,
)


# --------------------------------------------------------------------------- #
# 纯函数：合法端点生成（不硬编码 240/241）
# --------------------------------------------------------------------------- #


def test_build_legal_bar_ends_sse_1m_yields_241():
    """SSE 1m end-stamped + 含开盘集合竞价 bar(09:30) → 241 个合法 bar-end."""
    ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1,
        include_segment_open=True,
    )
    assert len(ends) == 241
    assert ends[0] == "09:30"
    assert "09:31" in ends
    assert "11:30" in ends
    assert "13:01" in ends
    assert ends[-1] == "15:00"
    # 午休内的时间戳不在合法端点。
    assert "12:00" not in ends
    assert "11:31" not in ends


def test_build_legal_bar_ends_excludes_lunch_and_open_optional():
    """不含开盘竞价 bar → 240（区分零成交开盘 bar vs 缺记录）。"""
    ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1,
        include_segment_open=False,
    )
    assert len(ends) == 240
    assert "09:30" not in ends
    assert ends[0] == "09:31"


# --------------------------------------------------------------------------- #
# session profile / contract 注册（版本化）
# --------------------------------------------------------------------------- #


def _register_sse_1m_profile(store: IntradayStore, version: str, **kw) -> str:
    return store.register_session_profile(
        version=version,
        interval_minutes=1,
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        include_segment_open=True,
        effective_from=kw.get("effective_from", "2026-01-01"),
        effective_to=kw.get("effective_to"),
    )


def _register_contract(
    store: IntradayStore, version: str, basis: str, *, delay: int = 90
) -> str:
    return store.register_provider_bar_contract(
        version=version,
        provider="eastmoney_akshare",
        interval_minutes=1,
        timestamp_basis=basis,
        publication_delay_seconds=delay,
    )


def test_register_profile_and_contract_roundtrip(store):
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    prof = store.get_session_profile(pv)
    assert prof["interval_minutes"] == 1
    assert len(prof["legal_bar_ends"]) == 241
    contract = store.get_provider_bar_contract(cv)
    assert contract["timestamp_basis"] == "end"
    assert contract["publication_delay_seconds"] == 90


# --------------------------------------------------------------------------- #
# Covers test-spec U1：归一化字段/单位/bar_end_ts 精确匹配 fixture
# --------------------------------------------------------------------------- #


def test_normalize_bar_exact_match_end_stamped():
    """已知合法 1m payload（end 戳）→ 归一字段/单位/bar_end_ts 精确匹配。"""
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1,
        include_segment_open=True,
    )
    raw = {
        "时间": "2026-06-19 14:59:00",
        "开盘": 10.0,
        "收盘": 10.2,
        "最高": 10.3,
        "最低": 9.9,
        "成交量": 1500,
        "成交额": 15150.0,
    }
    norm = normalize_bar(
        raw,
        timestamp_basis="end",
        interval_minutes=1,
        trade_date="2026-06-19",
        legal_bar_ends=legal_ends,
    )
    assert norm.bar_end_ts == "2026-06-19T14:59:00+08:00"
    assert norm.trade_date == "2026-06-19"
    assert norm.open == 10.0
    assert norm.close == 10.2
    assert norm.high == 10.3
    assert norm.low == 9.9
    assert norm.volume == 1500
    assert norm.amount == 15150.0


def test_normalize_bar_start_stamped_shifts_to_legal_end():
    """start 戳 → 归一到合法 bar-end（+interval）。"""
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1,
        include_segment_open=True,
    )
    raw = {"时间": "2026-06-19 14:58:00", "开盘": 10.0, "收盘": 10.2,
           "最高": 10.3, "最低": 9.9, "成交量": 1500, "成交额": 15150.0}
    norm = normalize_bar(
        raw, timestamp_basis="start", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    # start 14:58 → end 14:59（合法端点）。
    assert norm.bar_end_ts == "2026-06-19T14:59:00+08:00"


def test_normalize_bar_rejects_timestamp_not_in_profile():
    """不在冻结 profile 的时间戳（午休）→ 归一标记 not_in_profile。"""
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1,
        include_segment_open=True,
    )
    raw = {"时间": "2026-06-19 12:00:00", "开盘": 10.0, "收盘": 10.2,
           "最高": 10.3, "最低": 9.9, "成交量": 0, "成交额": 0.0}
    norm = normalize_bar(
        raw, timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    assert QualityFlag.NOT_IN_PROFILE in norm.quality_flags


# --------------------------------------------------------------------------- #
# Covers test-spec U4：session 校验，每缺陷置具体 quality flag
# --------------------------------------------------------------------------- #


def test_validate_legal_bar_passes_clean():
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    norm = normalize_bar(
        {"时间": "2026-06-19 10:00:00", "开盘": 10.0, "收盘": 10.1,
         "最高": 10.2, "最低": 9.9, "成交量": 100, "成交额": 1010.0},
        timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    flags = validate_bar(norm)
    assert flags == ()  # 合法完整 bar 无 flag


def test_validate_invalid_ohlc_flagged():
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    # high < close → 非法 OHLC。
    norm = normalize_bar(
        {"时间": "2026-06-19 10:00:00", "开盘": 10.0, "收盘": 10.5,
         "最高": 10.2, "最低": 9.9, "成交量": 100, "成交额": 1010.0},
        timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    flags = validate_bar(norm)
    assert QualityFlag.INVALID_OHLC in flags


def test_validate_negative_volume_flagged():
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    norm = normalize_bar(
        {"时间": "2026-06-19 10:00:00", "开盘": 10.0, "收盘": 10.1,
         "最高": 10.2, "最低": 9.9, "成交量": -5, "成交额": 1010.0},
        timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    flags = validate_bar(norm)
    assert QualityFlag.NEGATIVE_VOLUME in flags


def test_validate_lunch_timestamp_flagged_not_in_profile():
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    norm = normalize_bar(
        {"时间": "2026-06-19 12:00:00", "开盘": 10.0, "收盘": 10.1,
         "最高": 10.2, "最低": 9.9, "成交量": 100, "成交额": 1010.0},
        timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    flags = validate_bar(norm)
    assert QualityFlag.NOT_IN_PROFILE in flags


def test_validate_zero_volume_bar_passes_not_dropped():
    """零成交 bar（如集合竞价 09:30）合法 → 不置 flag、不静默丢（U5）。"""
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    norm = normalize_bar(
        {"时间": "2026-06-19 09:30:00", "开盘": 10.0, "收盘": 10.0,
         "最高": 10.0, "最低": 10.0, "成交量": 0, "成交额": 0.0},
        timestamp_basis="end", interval_minutes=1,
        trade_date="2026-06-19", legal_bar_ends=legal_ends,
    )
    flags = validate_bar(norm)
    assert flags == ()  # 零成交合法，区别于负量/缺记录


# --------------------------------------------------------------------------- #
# Covers test-spec U5：241 形 + 零成交 bar vs 缺戳
# --------------------------------------------------------------------------- #


def test_profile_accepts_241_shape_with_zero_volume():
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    # 241 形：开盘 09:30 零成交 bar 在合法端点内。
    assert "09:30" in legal_ends
    assert len(legal_ends) == 241


# --------------------------------------------------------------------------- #
# 端到端追认（certify）：从已存 blob 建 canonical（A5 上下文冻结）
# --------------------------------------------------------------------------- #


def _ingest_with_context(store, instrument_id, rows, *, schema_hash="h1",
                         profile_version="sse-1m-v1", contract_version="em-1m-end-v1"):
    obs = ObservationInput(
        instrument_id=instrument_id,
        provider="eastmoney_akshare",
        interval_minutes=1,
        request_meta={"method": "GET", "url": "https://push2his.eastmoney.com/x"},
        payload_rows=rows,
        source_asof_ts="2026-06-19T15:00:00+08:00",
        availability_class="forward_observed",
        eligibility="forward_observed",
        status_code=200,
        frozen_schema_hash=schema_hash,
        frozen_session_profile_version=profile_version,
        frozen_contract_version=contract_version,
    )
    return store.ingest_run(
        provider="eastmoney_akshare", mode="close",
        trade_date="2026-06-19", observations=[obs],
    )


_CANON_ROWS = [
    {"时间": "2026-06-19 14:59:00", "开盘": 10.0, "收盘": 10.2,
     "最高": 10.3, "最低": 9.9, "成交量": 1500, "成交额": 15150.0},
    {"时间": "2026-06-19 15:00:00", "开盘": 10.2, "收盘": 10.25,
     "最高": 10.3, "最低": 10.2, "成交量": 800, "成交额": 8200.0},
]


def test_certify_builds_canonical_from_blob_frozen_context(store, instrument_id):
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    r = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                             profile_version=pv, contract_version=cv)
    result = store.certify_observations_to_canonical(run_id=r.run_id)
    assert result["canonical_written"] == 2
    bars = store.list_canonical_bars(instrument_id=instrument_id)
    assert len(bars) == 2
    ts = {b["bar_end_ts"] for b in bars}
    assert "2026-06-19T14:59:00+08:00" in ts
    assert "2026-06-19T15:00:00+08:00" in ts
    # 双版本标识 + revision 冻结。
    assert all(b["session_profile_version"] == pv for b in bars)
    assert all(b["contract_version"] == cv for b in bars)
    assert all(b["revision"] == 1 for b in bars)


# --------------------------------------------------------------------------- #
# Covers test-spec U2：变 payload → revision 2，revision 1 保持可查（写一次-PIT）
# --------------------------------------------------------------------------- #


def test_changed_canonical_makes_revision_2_rev1_queryable(store, instrument_id):
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    r1 = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                              profile_version=pv, contract_version=cv)
    store.certify_observations_to_canonical(run_id=r1.run_id)

    changed = [dict(_CANON_ROWS[0], 收盘=10.9, 最高=11.0), _CANON_ROWS[1]]
    r2 = _ingest_with_context(store, instrument_id, changed,
                              profile_version=pv, contract_version=cv)
    store.certify_observations_to_canonical(run_id=r2.run_id)

    bar_1459 = store.list_canonical_bars(
        instrument_id=instrument_id, bar_end_ts="2026-06-19T14:59:00+08:00"
    )
    revs = {b["revision"]: b for b in bar_1459}
    assert set(revs) == {1, 2}  # 两个 revision 并存
    assert revs[1]["close"] == 10.2  # rev1 PIT 冻结值不被覆盖
    assert revs[2]["close"] == 10.9  # rev2 新值


def test_identical_canonical_does_not_bump_revision(store, instrument_id):
    """相同内容再追认 → 不产生 revision 2（写一次幂等）。"""
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    r1 = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                              profile_version=pv, contract_version=cv)
    store.certify_observations_to_canonical(run_id=r1.run_id)
    r2 = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                              profile_version=pv, contract_version=cv)
    store.certify_observations_to_canonical(run_id=r2.run_id)
    bar_1459 = store.list_canonical_bars(
        instrument_id=instrument_id, bar_end_ts="2026-06-19T14:59:00+08:00"
    )
    assert len(bar_1459) == 1  # 内容未变 → 仍只有 revision 1


# --------------------------------------------------------------------------- #
# Covers test-spec U11：start→end 契约 + 两 profile；旧行保留其 profile version
# --------------------------------------------------------------------------- #


def test_two_profiles_prior_rows_retain_version(store, instrument_id):
    """一原始戳在两 profile 下追认 → 旧 canonical 行保留其 profile version。"""
    pv1 = _register_sse_1m_profile(store, "sse-1m-v1",
                                   effective_from="2026-01-01",
                                   effective_to="2026-06-30")
    cv1 = _register_contract(store, "em-1m-end-v1", "end")
    r1 = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                              profile_version=pv1, contract_version=cv1)
    store.certify_observations_to_canonical(run_id=r1.run_id)

    # 新 profile 上线（不同 version），但旧行仍按 pv1 冻结。
    pv2 = _register_sse_1m_profile(store, "sse-1m-v2",
                                   effective_from="2026-07-01")
    bars = store.list_canonical_bars(instrument_id=instrument_id)
    assert all(b["session_profile_version"] == pv1 for b in bars)
    assert pv2 != pv1  # 新 profile 存在但不回溯套用


def test_start_then_end_contract_same_bar_end(store, instrument_id):
    """同一原始戳在 start 契约 vs end 契约下，归一到声明的 bar end."""
    legal_ends = build_legal_bar_ends(
        segments=(("09:30", "11:30"), ("13:00", "15:00")),
        interval_minutes=1, include_segment_open=True,
    )
    raw = {"时间": "2026-06-19 14:58:00", "开盘": 10.0, "收盘": 10.2,
           "最高": 10.3, "最低": 9.9, "成交量": 1500, "成交额": 15150.0}
    end_norm = normalize_bar(raw, timestamp_basis="end", interval_minutes=1,
                             trade_date="2026-06-19", legal_bar_ends=legal_ends)
    start_norm = normalize_bar(raw, timestamp_basis="start", interval_minutes=1,
                               trade_date="2026-06-19", legal_bar_ends=legal_ends)
    # end 契约：14:58 即 bar end；start 契约：14:58 是 start → end 14:59。
    assert end_norm.bar_end_ts == "2026-06-19T14:58:00+08:00"
    assert start_norm.bar_end_ts == "2026-06-19T14:59:00+08:00"


# --------------------------------------------------------------------------- #
# S4：schema-hash 漂移 → schema_drift 终止，无 canonical 行
# --------------------------------------------------------------------------- #


def test_schema_drift_terminates_no_canonical(store, instrument_id):
    """frozen schema_hash 与探针冻结 hash 不符 → schema_drift 终止、无 canonical。"""
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    r = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                             schema_hash="drifted-hash",
                             profile_version=pv, contract_version=cv)
    result = store.certify_observations_to_canonical(
        run_id=r.run_id, expected_schema_hash="frozen-probe-hash"
    )
    assert result["status"] == "failed"
    assert result["failure_reason"] == "schema_drift"
    assert result["canonical_written"] == 0
    assert store.list_canonical_bars(instrument_id=instrument_id) == []
    # 终止 failed run 持久化。
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT failure_reason FROM ingest_runs WHERE failure_reason='schema_drift'"
        ).fetchall()
    assert len(rows) == 1


def test_schema_drift_distinct_from_empty_response(store, instrument_id):
    """空响应 ≠ schema_drift：空 payload 的 observation 无行可追认但不终止。"""
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    obs = ObservationInput(
        instrument_id=instrument_id, provider="eastmoney_akshare",
        interval_minutes=1,
        request_meta={"method": "GET", "url": "https://push2his.eastmoney.com/x"},
        payload_rows=None,  # 空/错误响应
        source_asof_ts=None, availability_class="forward_observed",
        eligibility="forward_observed", status_code=200, error="empty response",
        frozen_schema_hash="h1", frozen_session_profile_version=pv,
        frozen_contract_version=cv,
    )
    r = store.ingest_run(provider="eastmoney_akshare", mode="close",
                         trade_date="2026-06-19", observations=[obs])
    result = store.certify_observations_to_canonical(run_id=r.run_id)
    assert result["status"] != "failed"  # 空响应不是 schema_drift
    assert result["canonical_written"] == 0


# --------------------------------------------------------------------------- #
# A5：observation 冻结上下文随 blob 一起落地（U2 retroactive 认证依据）
# --------------------------------------------------------------------------- #


def test_observation_freezes_context(store, instrument_id):
    pv = _register_sse_1m_profile(store, "sse-1m-v1")
    cv = _register_contract(store, "em-1m-end-v1", "end")
    r = _ingest_with_context(store, instrument_id, _CANON_ROWS,
                             schema_hash="probe-h1",
                             profile_version=pv, contract_version=cv)
    obs = store.list_observations(r.run_id)
    assert obs[0]["frozen_schema_hash"] == "probe-h1"
    assert obs[0]["frozen_session_profile_version"] == pv
    assert obs[0]["frozen_contract_version"] == cv


# --------------------------------------------------------------------------- #
# U3 FK provenance：canonical 行连得上 observation（孤儿写失败）
# --------------------------------------------------------------------------- #


def test_canonical_orphan_observation_fk_rejected(store, instrument_id):
    _register_sse_1m_profile(store, "sse-1m-v1")
    _register_contract(store, "em-1m-end-v1", "end")
    with pytest.raises(sqlite3.IntegrityError):
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO canonical_bars "
                "(instrument_id, symbol, bar_end_ts, trade_date, interval_minutes, "
                " source, revision, open, high, low, close, volume, amount, "
                " session_profile_version, contract_version, observation_id, "
                " quality_flags, content_sha256, certified_at) "
                "VALUES (?, 's', '2026-06-19T15:00:00+08:00', '2026-06-19', 1, "
                " 'eastmoney_akshare', 1, 1, 1, 1, 1, 1, 1, 'sse-1m-v1', "
                " 'em-1m-end-v1', 999999, '', 'x', '2026-06-19T15:05:00+08:00')",
                (instrument_id,),
            )
