from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from kss.agent import AgentMessage, AgentState, SessionStore, ToolCall
from kss.agent.context import CompactionRecord
from kss.agent.session_store import QueuedInputLimitError


def test_session_store_crud_open_does_not_interrupt_normal_session(tmp_path):
    store = SessionStore(tmp_path)
    state = store.create_session(session_id="s1", metadata={"topic": "agent"})

    assert state.status == "running"
    opened = store.get_session("s1")
    assert opened is not None
    assert opened.status == "running"

    renamed = store.rename_session("s1", "Agent Core")
    assert renamed.metadata["title"] == "Agent Core"
    assert "updated_at" in renamed.metadata

    archived = store.archive_session("s1")
    assert archived.status == "archived"

    completed = store.complete_session("s1")
    assert completed.status == "completed"
    listed = store.list_sessions()
    assert [item.session_id for item in listed] == ["s1"]

    deleted = store.delete_session("s1")
    assert deleted.status == "deleted"
    assert store.list_sessions() == []
    assert store.list_sessions(include_deleted=True)[0].status == "deleted"


def test_session_store_marks_only_unfinished_run_interrupted(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="run-session")
    run_id = store.start_run("run-session", run_id="r1", owner_pid=-1)

    recovered = store.get_session("run-session")

    assert recovered is not None
    assert recovered.status == "interrupted"
    assert recovered.metadata["reason"] == "recovered_incomplete_run"
    assert recovered.metadata["run_id"] == run_id


def test_current_state_reads_live_run_without_triggering_crash_recovery(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(
        session_id="live-route",
        metadata={"provider_route": {"provider_id": "deepseek", "model_id": "chat"}},
    )
    store.start_run("live-route", run_id="active", client_turn_id="turn-1", owner_pid=os.getpid())

    state = store.current_state("live-route")

    assert state is not None
    assert state.metadata["provider_route"]["model_id"] == "chat"
    # current_state is a runtime-only metadata read, not the public restart
    # recovery path; the active run remains durable and live.
    assert store.find_run_by_client_turn_id("live-route", "turn-1")["status"] == "running"


def test_session_provider_route_is_append_only_and_replaces_effective_metadata(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="route", metadata={"title": "Route"})

    updated = store.set_provider_route("route", {
        "provider_id": "openai",
        "model_id": "gpt-test",
        "thinking_level": "medium",
    })

    assert updated.metadata["provider_route"]["model_id"] == "gpt-test"
    path = tmp_path / "storage" / "agent" / "sessions" / "route.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["type"] == "state_updated"
    assert entries[-1]["payload"]["metadata"]["provider_route"]["provider_id"] == "openai"


def test_session_store_recovery_appends_run_terminal_only_once(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="recover-once")
    store.start_run(
        "recover-once",
        run_id="run-once",
        client_turn_id="client-once",
        owner_pid=-1,
    )
    queued = store.add_queued_input(
        "recover-once",
        "run-once",
        "follow_up",
        "queued-after-crash",
        "恢复后继续",
    )

    first = store.get_session("recover-once")
    second = store.get_session("recover-once")

    assert first is not None and first.status == "interrupted"
    assert second is not None and second.status == "interrupted"
    path = tmp_path / "storage" / "agent" / "sessions" / "recover-once.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    terminal = [
        entry
        for entry in entries
        if entry["type"] == "run_finished"
        and entry["payload"]["run_id"] == "run-once"
    ]
    recovery_status = [
        entry
        for entry in entries
        if entry["type"] == "status_changed"
        and entry["payload"]["metadata"].get("reason") == "recovered_incomplete_run"
    ]
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "interrupted"
    assert len(recovery_status) == 1
    restored = [
        entry
        for entry in entries
        if entry["type"] == "queue_restored"
        and entry["payload"]["id"] == queued.id
    ]
    assert len(restored) == 1
    assert store.queued_inputs(session_id="recover-once")[0].status == "restored"


def test_session_store_persists_client_turn_id_and_queries_terminal_statuses(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="idempotent")

    store.start_run(
        "idempotent", run_id="running", client_turn_id="client-running"
    )
    assert store.find_run_by_client_turn_id(
        "idempotent", "client-running"
    )["status"] == "running"
    store.finish_run("idempotent", "running", status="completed")
    completed = store.find_run_by_client_turn_id(
        "idempotent", "client-running"
    )
    assert completed is not None
    assert completed["run_id"] == "running"
    assert completed["status"] == "completed"

    store.start_run("idempotent", run_id="failed", client_turn_id="client-failed")
    store.finish_run("idempotent", "failed", status="failed", reason="provider")
    failed = store.find_run_by_client_turn_id("idempotent", "client-failed")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["reason"] == "provider"

    store.start_run(
        "idempotent", run_id="interrupted", client_turn_id="client-interrupted"
    )
    store.interrupt_session("idempotent", run_id="interrupted", reason="abort")
    interrupted = store.find_run_by_client_turn_id(
        "idempotent", "client-interrupted"
    )
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
    assert interrupted["reason"] == "abort"


def test_queue_add_is_idempotent_and_limit_is_cumulative(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-limit")
    store.start_run("queue-limit", run_id="run-1", client_turn_id="turn-1")

    first = store.add_queued_input(
        "queue-limit", "run-1", "steering", "message-1", "先查风险"
    )
    duplicate = store.add_queued_input(
        "queue-limit", "run-1", "steering", "message-1", "不同正文也不重复"
    )
    assert duplicate == first

    store.discard_queued_input("queue-limit", first.id)
    for index in range(2, 9):
        store.add_queued_input(
            "queue-limit",
            "run-1",
            "follow_up",
            f"message-{index}",
            f"问题 {index}",
        )
    with pytest.raises(QueuedInputLimitError):
        store.add_queued_input(
            "queue-limit", "run-1", "follow_up", "message-9", "超限"
        )

    path = tmp_path / "storage" / "agent" / "sessions" / "queue-limit.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(entry["type"] == "queue_added" for entry in entries) == 8


def test_queue_reader_normalizes_legacy_steer_mode(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="legacy-steer")
    store.append_entry(
        "legacy-steer",
        "queue_added",
        {
            "id": "legacy-queue",
            "client_message_id": "legacy-message",
            "session_id": "legacy-steer",
            "run_id": "legacy-run",
            "mode": "steer",
            "content": "旧 steering",
            "status": "queued",
            "created_at": 1.0,
            "applied_at": None,
        },
    )

    item = store.queued_inputs(session_id="legacy-steer")[0]
    assert item.mode == "steering"


def test_apply_queued_input_atomically_writes_message_then_applied_once(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-apply")
    queued = store.add_queued_input(
        "queue-apply", "run-1", "steering", "message-1", "改变方向"
    )

    applied = store.apply_queued_input(
        "queue-apply", queued.id, target_run_id="run-2"
    )
    again = store.apply_queued_input(
        "queue-apply", queued.id, target_run_id="run-2"
    )

    assert applied == again
    assert applied.status == "applied"
    messages = store.read_messages("queue-apply")
    assert [message.content for message in messages] == ["改变方向"]
    assert messages[0].metadata["run_id"] == "run-2"
    path = tmp_path / "storage" / "agent" / "sessions" / "queue-apply.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    lifecycle = [
        entry["type"]
        for entry in entries
        if entry["type"] in {"message_appended", "queue_applied"}
    ]
    assert lifecycle == ["message_appended", "queue_applied"]


def test_pending_queue_restores_once_for_any_run_settlement(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-restore")
    queued = store.add_queued_input(
        "queue-restore", "run-1", "follow_up", "message-1", "继续"
    )

    first = store.restore_pending_inputs("queue-restore", "run-1")
    second = store.restore_pending_inputs("queue-restore", "run-1")

    assert [item.id for item in first] == [queued.id]
    assert first[0].status == "restored"
    assert second == []
    path = tmp_path / "storage" / "agent" / "sessions" / "queue-restore.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(entry["type"] == "queue_restored" for entry in entries) == 1


def test_source_queue_is_discarded_in_same_apply_transaction(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-source")
    source = store.add_queued_input(
        "queue-source", "old-run", "follow_up", "old-message", "恢复输入"
    )
    store.restore_pending_inputs("queue-source", "old-run")
    replacement = store.add_queued_input(
        "queue-source",
        "new-run",
        "follow_up",
        "new-message",
        "恢复输入",
        source_queue_id=source.id,
    )

    store.apply_queued_input(
        "queue-source", replacement.id, target_run_id="new-run"
    )

    all_items = store.queued_inputs(
        session_id="queue-source", include_terminal=True
    )
    by_id = {item.id: item for item in all_items}
    assert by_id[replacement.id].status == "applied"
    assert by_id[source.id].status == "discarded"
    path = tmp_path / "storage" / "agent" / "sessions" / "queue-source.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["type"] for entry in entries[-3:]] == [
        "message_appended",
        "queue_applied",
        "queue_discarded",
    ]


def test_append_message_source_queue_fails_closed_when_not_restored(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-consume")
    source = store.add_queued_input(
        "queue-consume", "old-run", "follow_up", "old-message", "旧输入"
    )
    message = AgentMessage(
        id="new-message",
        role="user",
        content="新输入",
        timestamp=1.0,
    )

    with pytest.raises(ValueError, match="restored"):
        store.append_message(
            "queue-consume", message, source_queue_id=source.id
        )

    assert store.read_messages("queue-consume") == []
    assert store.queued_inputs(session_id="queue-consume") == [source]


def test_queue_replacement_rejects_non_restored_source_before_acceptance(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="queue-source-admission")
    source = store.add_queued_input(
        "queue-source-admission",
        "old-run",
        "follow_up",
        "old-message",
        "旧输入",
    )

    with pytest.raises(ValueError, match="restored"):
        store.add_queued_input(
            "queue-source-admission",
            "new-run",
            "follow_up",
            "new-message",
            "重发",
            source_queue_id=source.id,
        )

    all_items = store.queued_inputs(
        session_id="queue-source-admission",
        include_terminal=True,
    )
    assert all_items == [source]


def test_atomic_run_admission_rejects_live_owner_and_duplicate(tmp_path):
    store = SessionStore(tmp_path)
    first = store.try_start_run(
        "atomic",
        run_id="run-1",
        client_turn_id="client-1",
    )
    busy = store.try_start_run(
        "atomic",
        run_id="run-2",
        client_turn_id="client-2",
    )
    duplicate = store.try_start_run(
        "atomic",
        run_id="run-3",
        client_turn_id="client-1",
    )

    assert first.admitted is True
    assert busy.admitted is False
    assert busy.status == "running"
    assert busy.run_id == "run-1"
    assert duplicate.admitted is False
    assert duplicate.status == "running"
    assert duplicate.run_id == "run-1"


def test_atomic_run_admission_recovers_dead_owner_before_duplicate_check(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="restart")
    store.start_run(
        "restart",
        run_id="dead-run",
        client_turn_id="dead-client",
        owner_pid=-1,
    )

    duplicate = store.find_run_by_client_turn_id("restart", "dead-client")
    rejected = store.try_start_run(
        "restart",
        run_id="replacement-with-same-key",
        client_turn_id="dead-client",
    )
    accepted = store.try_start_run(
        "restart",
        run_id="replacement",
        client_turn_id="new-client",
    )

    assert duplicate is not None and duplicate["status"] == "interrupted"
    assert rejected.admitted is False
    assert rejected.status == "interrupted"
    assert accepted.admitted is True


def test_atomic_run_admission_serializes_independent_store_instances(tmp_path):
    stores = (SessionStore(tmp_path), SessionStore(tmp_path))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: item[0].try_start_run(
                    "double-sidecar",
                    run_id=f"run-{item[1]}",
                    client_turn_id=f"client-{item[1]}",
                ),
                ((stores[0], 1), (stores[1], 2)),
            )
        )

    assert sum(result.admitted for result in results) == 1
    assert {result.status for result in results} == {"running"}
    winner = next(result for result in results if result.admitted)
    loser = next(result for result in results if not result.admitted)
    assert loser.run_id == winner.run_id


def test_session_store_compaction_round_trip(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="compaction")
    record = CompactionRecord(
        summary={
            "目标": "目标",
            "偏好": "偏好",
            "已完成": "完成",
            "关键决策": "决策",
            "未完成": "待办",
            "关键证据": "证据",
        },
        first_kept_entry_id="message-7",
        tokens_before=25_000,
        tokens_after=7_500,
        model="model-a",
        usage={"input_tokens": 100, "output_tokens": 50},
        fallback_used=False,
    )

    entry = store.append_compaction("compaction", record, run_id="run-1")
    loaded = store.latest_compaction("compaction")

    assert entry["type"] == "compaction"
    assert loaded is not None
    assert loaded["entry_id"] == entry["id"]
    assert loaded["first_kept_entry_id"] == "message-7"
    assert loaded["summary"]["关键决策"] == "决策"
    assert loaded["tokens_before"] == 25_000
    assert loaded["tokens_after"] == 7_500
    assert loaded["run_id"] == "run-1"


def test_concurrent_session_appends_keep_single_parent_chain(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="concurrent")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda value: store.append_entry(
                    "concurrent", "concurrent_event", {"value": value}
                ),
                range(40),
            )
        )

    path = tmp_path / "storage" / "agent" / "sessions" / "concurrent.jsonl"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 41
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert entries[0]["parent_id"] is None
    assert all(
        entry["parent_id"] == entries[index - 1]["id"]
        for index, entry in enumerate(entries[1:], start=1)
    )


def test_session_store_repairs_corrupt_tail_and_keeps_valid_entries(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="s2")
    store.append_message(
        "s2",
        AgentMessage(
            id="m1",
            role="assistant",
            content="你好",
            timestamp=1.0,
            tool_calls=(
                ToolCall(
                    id="t1",
                    name="search",
                    arguments={"q": "KSS"},
                    result={"ok": True},
                    error=None,
                ),
            ),
        ),
    )
    run_id = store.start_run("s2", owner_pid=-1)
    path = tmp_path / "storage" / "agent" / "sessions" / "s2.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"broken":')

    state = store.get_session("s2")
    assert state is not None
    assert state.status == "interrupted"
    assert state.metadata["run_id"] == run_id
    assert path.read_bytes().endswith(b"\n")
    assert b'{"broken":' not in path.read_bytes()

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(line["version"] == 1 for line in lines)
    assert all({"id", "parent_id", "timestamp"}.issubset(line) for line in lines)
    message = store.read_messages("s2")[0]
    assert message.content == "你好"
    assert message.tool_calls[0].arguments == {"q": "KSS"}
    assert message.tool_calls[0].result == {"ok": True}


def test_session_entries_link_parent_id_to_previous_entry(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create_session(session_id="chain")
    assert first.session_id == "chain"
    second = store.append_entry("chain", "custom", {"value": 1})
    third = store.append_entry("chain", "custom", {"value": 2})

    assert second["parent_id"] is not None
    assert third["parent_id"] == second["id"]


def test_session_update_state_round_trips(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="s3")
    updated = store.update_state(
        AgentState(session_id="s3", cursor=7, active_skill_ids=("a",), pinned_skill_ids=("a",))
    )

    loaded = store.get_session("s3")
    assert updated.cursor == 7
    assert loaded is not None
    assert loaded.cursor == 7
    assert loaded.active_skill_ids == ("a",)
