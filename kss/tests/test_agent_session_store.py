from __future__ import annotations

import json

from kss.agent import AgentMessage, AgentState, SessionStore, ToolCall


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
    run_id = store.start_run("run-session", run_id="r1")

    recovered = store.get_session("run-session")

    assert recovered is not None
    assert recovered.status == "interrupted"
    assert recovered.metadata["reason"] == "recovered_incomplete_run"
    assert recovered.metadata["run_id"] == run_id


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
    run_id = store.start_run("s2")
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
