"""研究节点的 Harness 驱动缝：工作区、白名单、不重放写、子 agent 继承。

生产路径不再走 AgentRuntime + ``reject_write``。真实 Node Harness 会话可经
``session`` 注入；测试使用 Fake 仍覆盖 cwd / allowlist / 串行契约。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .allowlist import (
    DEFAULT_RESEARCH_WRITE_TOOLS,
    allowlist_fingerprint,
    bound_write_allowlist,
)

AGENT_PRESET = "research"
_APPLIED_NAME = "applied_writes.json"
_META_NAME = "attempt_meta.json"
_FS_WRITE_TOOLS = frozenset(DEFAULT_RESEARCH_WRITE_TOOLS)


@dataclass(frozen=True)
class ResearchAllowlist:
    tools: tuple[str, ...]
    cwd: str

    def as_dict(self) -> dict[str, Any]:
        return {"tools": list(self.tools), "cwd": self.cwd}


@dataclass
class ResearchTurnRequest:
    goal_id: str
    task: dict[str, Any]
    attempt_id: str
    prompt: str
    origin: str
    workspace: Path
    allowlist: ResearchAllowlist
    agent_preset: str = AGENT_PRESET
    attach_desktop_answerer: bool = False
    applied_write_ids: tuple[str, ...] = ()
    child: bool = False


@dataclass
class ResearchTurnResult:
    harness_status: str
    assistant_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    applied_write_ids: tuple[str, ...] = ()
    error: str | None = None
    tool_evidence: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class ResearchTurnSession(Protocol):
    def run(self, request: ResearchTurnRequest, driver: ResearchHarnessDriver) -> ResearchTurnResult:
        ...


def _default_research_session() -> ResearchTurnSession:
    try:
        from kss.agent.harness_kernel import get_harness_kernel
        kernel = get_harness_kernel()
        if kernel is not None and kernel.alive:
            return kernel.research_session()
    except Exception:  # noqa: BLE001
        pass
    return UnavailableResearchTurnSession()


class UnavailableResearchTurnSession:
    """未注入真实 Harness 时失败关闭，避免回落到 Python loop 主人。"""

    def run(self, request: ResearchTurnRequest, driver: ResearchHarnessDriver) -> ResearchTurnResult:
        return ResearchTurnResult(
            harness_status="interrupted",
            error="harness_session_unavailable",
            applied_write_ids=request.applied_write_ids,
        )


class ResearchHarnessDriver:
    """为一次 attempt 准备独立工作区并执行白名单内的写。"""

    def __init__(
        self,
        *,
        state_root: Path,
        project_root: Path,
        session: ResearchTurnSession | None = None,
    ) -> None:
        self._state_root = Path(state_root).resolve()
        self._project_root = Path(project_root).resolve()
        self._session = session if session is not None else _default_research_session()
        self._lock = threading.Lock()
        self.last_requests: list[ResearchTurnRequest] = []

    def abort(self, *_: Any, **__: Any) -> bool:
        return False

    def prepare_attempt(
        self,
        *,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
        origin: str = "manual",
    ) -> ResearchTurnRequest:
        tools = tuple(bound_write_allowlist(task))
        task_id = str(task.get("task_id") or "task")
        base = (
            self._state_root
            / "storage"
            / "agent"
            / "research"
            / "workspaces"
            / str(goal_id)
            / task_id
        )
        base.mkdir(parents=True, exist_ok=True)
        requested_cwd = str((task.get("payload") or {}).get("workspace_cwd") or "")
        fingerprint = allowlist_fingerprint(list(tools), requested_cwd)
        meta_path = base / _META_NAME
        previous = {}
        if meta_path.exists():
            try:
                previous = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
        reuse = (
            str(previous.get("fingerprint") or "") == fingerprint
            and str(previous.get("agent_preset") or AGENT_PRESET) == AGENT_PRESET
        )
        if reuse:
            workspace = Path(str(previous.get("workspace") or (base / "ws")))
        else:
            workspace = base / "ws"
            if workspace.exists() and not reuse:
                workspace = base / f"ws-{attempt_id}"
        if self._forbidden_cwd(workspace if not requested_cwd else Path(requested_cwd)):
            raise PermissionError("research cwd may not be the repository root or database")
        workspace.mkdir(parents=True, exist_ok=True)
        cwd = str(workspace)
        allowlist = ResearchAllowlist(tools=tools, cwd=cwd)
        applied = tuple(str(item) for item in (previous.get("applied_write_ids") or []) if reuse)
        meta_path.write_text(
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    "workspace": cwd,
                    "agent_preset": AGENT_PRESET,
                    "applied_write_ids": list(applied),
                    "origin": origin,
                    "attach_desktop_answerer": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ResearchTurnRequest(
            goal_id=str(goal_id),
            task=task,
            attempt_id=str(attempt_id),
            prompt="",
            origin=str(origin or "manual"),
            workspace=workspace,
            allowlist=allowlist,
            agent_preset=AGENT_PRESET,
            attach_desktop_answerer=False,
            applied_write_ids=applied,
        )

    def inherit_child_allowlist(
        self,
        parent: ResearchAllowlist,
        *,
        requested_tools: list[str] | None = None,
        requested_cwd: str | None = None,
    ) -> ResearchAllowlist:
        """KTD8：子 agent 继承父白名单与 cwd，提权请求被忽略。"""
        del requested_tools, requested_cwd
        return ResearchAllowlist(tools=parent.tools, cwd=parent.cwd)

    def execute_tool(
        self,
        request: ResearchTurnRequest,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
        call_id: str,
        child: bool = False,
        escalate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        allowlist = request.allowlist
        if child and escalate:
            allowlist = self.inherit_child_allowlist(
                request.allowlist,
                requested_tools=list(escalate.get("tools") or []),
                requested_cwd=str(escalate.get("cwd") or "") or None,
            )
        if name not in _FS_WRITE_TOOLS:
            if name not in allowlist.tools:
                return {
                    "error": "research_write_denied",
                    "is_error": True,
                    "hint": f'research write "{name}" is not on the allowlist',
                }
            return {
                "error": "live_write_not_cwd_local",
                "is_error": True,
                "hint": "KSS live writes go through Python dispatch after grant",
            }
        if name not in allowlist.tools:
            return {
                "error": "research_write_denied",
                "is_error": True,
                "hint": f'research write "{name}" is not on the allowlist',
            }
        if self._forbidden_cwd(Path(allowlist.cwd)):
            return {
                "error": "research_cwd_forbidden",
                "is_error": True,
                "hint": "research cwd may not be the repository root or database",
            }
        target = args.get("path") or args.get("file_path") or args.get("cwd")
        if isinstance(target, str) and target:
            resolved = self._resolve_in_workspace(allowlist.cwd, target)
            if resolved is None:
                return {
                    "error": "path_escapes_workspace",
                    "is_error": True,
                }
            if self._forbidden_cwd(resolved if resolved.is_dir() else resolved.parent):
                return {
                    "error": "research_cwd_forbidden",
                    "is_error": True,
                    "hint": "research cwd may not be the repository root or database",
                }
        if call_id in request.applied_write_ids:
            return {"ok": True, "skipped": "already_applied", "call_id": call_id}
        applied = self._apply_fs_write(allowlist.cwd, name, args)
        if applied.get("is_error"):
            return applied
        ids = list(request.applied_write_ids) + [call_id]
        request.applied_write_ids = tuple(ids)
        self._persist_applied(request, ids)
        return {"ok": True, "call_id": call_id, **applied}

    def run(self, request: ResearchTurnRequest) -> ResearchTurnResult:
        filled = ResearchTurnRequest(
            goal_id=request.goal_id,
            task=request.task,
            attempt_id=request.attempt_id,
            prompt=request.prompt,
            origin=request.origin,
            workspace=request.workspace,
            allowlist=request.allowlist,
            agent_preset=AGENT_PRESET,
            attach_desktop_answerer=False,
            applied_write_ids=request.applied_write_ids,
            child=request.child,
        )
        with self._lock:
            self.last_requests.append(filled)
        result = self._session.run(filled, self)
        self._persist_applied(filled, list(result.applied_write_ids))
        return result

    def _apply_fs_write(
        self, cwd: str, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        rel = str(args.get("path") or args.get("file_path") or "notes.md")
        resolved = self._resolve_in_workspace(cwd, rel)
        if resolved is None:
            return {"error": "path_escapes_workspace", "is_error": True}
        if name == "bash":
            # 默认白名单含 bash，但单元测试不执行任意 shell；仅记录策略放行。
            return {"tool": "bash", "cwd": cwd}
        resolved.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content") or args.get("new_string") or "")
        if name == "edit" and resolved.exists():
            old = str(args.get("old_string") or "")
            text = resolved.read_text(encoding="utf-8")
            if old:
                text = text.replace(old, content, 1)
            else:
                text = content
            resolved.write_text(text, encoding="utf-8")
        else:
            resolved.write_text(content, encoding="utf-8")
        return {"path": str(resolved), "tool": name}

    def _persist_applied(self, request: ResearchTurnRequest, ids: list[str]) -> None:
        meta_path = request.workspace.parent / _META_NAME
        payload = {}
        if meta_path.exists():
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload["applied_write_ids"] = ids
        payload["workspace"] = str(request.workspace)
        payload["agent_preset"] = AGENT_PRESET
        payload["attach_desktop_answerer"] = False
        meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        journal = request.workspace / ".kss" / _APPLIED_NAME
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            json.dumps({"applied_write_ids": ids}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _forbidden_cwd(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return True
        repo = self._project_root
        db = (self._state_root / "storage" / "kss.db").resolve()
        if resolved == repo:
            return True
        if resolved == db:
            return True
        return False

    def _resolve_in_workspace(self, cwd: str, candidate: str) -> Path | None:
        base = Path(cwd).resolve()
        target = Path(candidate)
        resolved = target.resolve() if target.is_absolute() else (base / target).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            return None
        return resolved
