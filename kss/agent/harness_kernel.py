"""Node Harness kernel supervisor.

Python is the finance backend. This process owns desktop/research turns (R1).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from kss.agent.desktop_host import DesktopHarnessHost, DesktopTurnRequest, DesktopTurnResult
from kss.research.harness_driver import ResearchHarnessDriver, ResearchTurnRequest, ResearchTurnResult

_REPO = Path(__file__).resolve().parents[2]
_PROTOCOL = 1
_DEFAULT_TIMEOUT = 30.0
# Match sidecar `_CONFIRM_TIMEOUT`: a desktop write prompt must not burn the
# 180s turn budget while the operator is still looking at 允许/拒绝.
# 必须大于 policy.js KSS_HARNESS_APPROVAL_TIMEOUT_MS(默认 300s)与 sidecar 的
# _CONFIRM_TIMEOUT(300s)：让「超时自动拒绝」先落地、模型收尾，而不是先杀内核。
_APPROVAL_HOLD_SECONDS = 330.0

_KERNEL: HarnessKernel | None = None
_KERNEL_LOCK = threading.Lock()


def _find_node() -> Path:
    configured = os.getenv("KSS_HARNESS_NODE", "").strip()
    candidates = [
        Path(configured) if configured else None,
        _REPO / "Contents" / "Resources" / "harness-runtime" / "bin" / "node",
        Path(os.getenv("KSS_PROJECT_ROOT") or _REPO) / "harness-runtime" / "bin" / "node",
        _REPO / ".build" / "harness-node" / "runtime" / "bin" / "node",
        _REPO / ".build" / "pi-ai-helper" / "runtime" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    which = shutil.which("node")
    return Path(which) if which else Path("/nonexistent/kss-harness-node")


def _find_host() -> Path:
    configured = os.getenv("KSS_HARNESS_HOST", "").strip()
    candidates = [
        Path(configured) if configured else None,
        _REPO / "scripts" / "kss_harness_host.mjs",
        Path(os.getenv("KSS_PROJECT_ROOT") or _REPO) / "scripts" / "kss_harness_host.mjs",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return Path("/nonexistent/kss_harness_host.mjs")


def _find_profile() -> Path:
    configured = os.getenv("KSS_HARNESS_PROFILE", "").strip()
    candidates = [
        Path(configured) if configured else None,
        _REPO / "harness" / "kss-profile",
        Path(os.getenv("KSS_PROJECT_ROOT") or _REPO) / "harness" / "kss-profile",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "package.json").is_file():
            return candidate
    return _REPO / "harness" / "kss-profile"


def prepare_dsh_home(home: Path, profile_dir: Path | None = None) -> Path:
    """Create DSH_HOME/profiles/kss -> vendored KSS profile. Required by runProfile."""
    home = Path(home)
    profiles = home / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    link = profiles / "kss"
    target = (profile_dir or _find_profile()).resolve()
    if link.is_symlink() or link.exists():
        try:
            if link.resolve() != target:
                link.unlink()
                link.symlink_to(target)
        except OSError:
            link.unlink()
            link.symlink_to(target)
    else:
        link.symlink_to(target)
    return home


def _map_dsh_provider(provider_id: str) -> str:
    """Map KSS catalog ids onto dsh adapter routes."""
    if provider_id in {"deepseek", "kss-primary"}:
        return "deepseek-official"
    return provider_id


def _agent_options_payload(route: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Resolve provider/model/reasoning effort for one harness turn.

    优先级：环境覆盖 > 调用方显式路由（会话级） > 全局 primary 路由。
    reasoning_effort 直接透传 KSS ``thinking_level`` 取值，由 Node host 按
    模型实际支持的档位收敛（clamp），避免 UNSUPPORTED_REASONING_EFFORT。
    """
    provider = os.getenv("KSS_HARNESS_PROVIDER", "").strip()
    model = os.getenv("KSS_HARNESS_MODEL", "").strip()
    if provider and model:
        payload = {"provider": provider, "model": model}
        effort = os.getenv("KSS_HARNESS_REASONING_EFFORT", "").strip()
        if effort:
            payload["reasoning_effort"] = effort
        return payload
    if isinstance(route, Mapping):
        provider_id = str(route.get("provider_id") or "").strip()
        model_id = str(route.get("model_id") or "").strip()
        if provider_id and model_id:
            payload = {
                "provider": _map_dsh_provider(provider_id),
                "model": model_id,
            }
            effort = str(route.get("thinking_level") or "").strip()
            if effort:
                payload["reasoning_effort"] = effort
            return payload
    try:
        from kss.agent.provider_route import ProviderRouteStore
        import kss_app_bridge as bridge

        primary = ProviderRouteStore(bridge.STATE_ROOT).load().primary
        payload = {
            "provider": _map_dsh_provider(primary.provider_id),
            "model": primary.model_id,
        }
        if primary.thinking_level:
            payload["reasoning_effort"] = primary.thinking_level
        return payload
    except Exception:  # noqa: BLE001
        return {}


def _redact_kernel_text(text: str) -> str:
    redacted = text
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "KSS_LLM_PRIMARY_KEY", "ANTHROPIC_API_KEY"):
        value = os.getenv(key, "").strip()
        if len(value) >= 4:
            redacted = redacted.replace(value, "[redacted]")
    return redacted


class HarnessKernel:
    """Long-lived Node child. Death fail-closes live writes (U7)."""

    def __init__(
        self,
        *,
        node_path: Path | None = None,
        host_path: Path | None = None,
        driver: str = "scripted",
        sidecar_socket: str = "",
        dsh_home: Path | None = None,
        extra_env: dict[str, str] | None = None,
        startup_timeout: float = 8.0,
    ) -> None:
        self.node_path = Path(node_path) if node_path else _find_node()
        self.host_path = Path(host_path) if host_path else _find_host()
        self.driver = "dsh" if driver == "dsh" else "scripted"
        self.sidecar_socket = sidecar_socket
        self.dsh_home = Path(dsh_home) if dsh_home else None
        self.extra_env = dict(extra_env or {})
        self.startup_timeout = startup_timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._hello: dict[str, Any] | None = None
        self._ready: dict[str, Any] | None = None
        self._reader: threading.Thread | None = None
        self._alive = False
        self._stderr_tail: list[str] = []

    @property
    def alive(self) -> bool:
        proc = self._proc
        return bool(self._alive and proc is not None and proc.poll() is None)

    def start(self) -> dict[str, Any]:
        if self.alive and self._hello is not None:
            return self._hello
        if not self.node_path.is_file() or not self.host_path.is_file():
            raise FileNotFoundError("Harness Node kernel binary or host script missing")
        env = os.environ.copy()
        env.update(self.extra_env)
        env["KSS_HARNESS_DRIVER"] = self.driver
        env["KSS_REPO_ROOT"] = str(_REPO)
        if self.sidecar_socket:
            env["KSS_SIDECAR_SOCKET"] = self.sidecar_socket
        if self.driver == "dsh":
            if self.dsh_home is None:
                configured = os.getenv("DSH_HOME", "").strip()
                self.dsh_home = Path(configured) if configured else (_REPO / ".build" / "dsh-home")
            prepare_dsh_home(self.dsh_home)
            env["DSH_HOME"] = str(self.dsh_home)
            socket = env.get("KSS_PI_AI_CREDENTIAL_SOCKET", "").strip()
            nonce = env.get("KSS_PI_AI_CREDENTIAL_NONCE", "").strip()
            if socket and nonce:
                for key in list(env):
                    if key.endswith("_API_KEY") or key in {"KSS_LLM_PRIMARY_KEY"}:
                        env.pop(key, None)
        self._proc = subprocess.Popen(
            [str(self.node_path), str(self.host_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(_REPO),
        )
        self._alive = True
        self._reader = threading.Thread(target=self._read_stdout, name="kss-harness-kernel", daemon=True)
        self._reader.start()
        self._err = threading.Thread(target=self._read_stderr, name="kss-harness-kernel-err", daemon=True)
        self._err.start()
        hello = self._wait_hello()
        self._hello = hello
        if self.driver == "dsh":
            self._require_dsh_agents()
        return hello

    def close(self) -> None:
        proc = self._proc
        self._alive = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"id": "shutdown", "cmd": "shutdown"}) + "\n")
                proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            proc.kill()
        self._proc = None

    def abandon_wedged_turn(self, session_id: str = "") -> None:
        """Abort a stuck whenIdle turn and kill Node so stdin is not left unread."""
        proc = self._proc
        if proc is not None and proc.stdin is not None and proc.poll() is None:
            try:
                proc.stdin.write(
                    json.dumps(
                        {"id": uuid4().hex, "cmd": "abort", "session_id": session_id},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                proc.stdin.flush()
            except OSError:
                pass
        self.close()

    def request(
        self,
        cmd: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        on_event: Any = None,
        approval_timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.alive:
            raise RuntimeError("harness kernel is not available")
        msg_id = uuid4().hex
        approval_until = 0.0
        hold = _APPROVAL_HOLD_SECONDS if approval_timeout is None else float(approval_timeout)

        def wrapped_on_event(event: Any) -> None:
            nonlocal approval_until
            if isinstance(event, dict) and str(event.get("type") or "") in {
                "approval_request",
                "approval/request",
            }:
                approval_until = time.monotonic() + hold
            if on_event is not None:
                on_event(event)

        waiter: dict[str, Any] = {"event": threading.Event(), "body": None, "on_event": wrapped_on_event}
        with self._lock:
            self._pending[msg_id] = waiter
        line = json.dumps({"id": msg_id, "cmd": cmd, **dict(payload or {})}, ensure_ascii=False)
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            self._alive = False
            raise RuntimeError("harness kernel is not available") from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(deadline, approval_until) - time.monotonic()
            if remaining <= 0:
                break
            if waiter["event"].wait(min(0.25, remaining)):
                body = waiter["body"]
                if not isinstance(body, dict):
                    raise RuntimeError("harness kernel returned no payload")
                return body
        with self._lock:
            self._pending.pop(msg_id, None)
        if cmd in {"desktop.turn", "research.turn"}:
            self.abandon_wedged_turn(str((payload or {}).get("session_id") or ""))
        raise TimeoutError(f"harness kernel timed out on {cmd}")

    def desktop_session(self) -> NodeDesktopSession:
        return NodeDesktopSession(self)

    def list_models(self, *, timeout: float = 20.0) -> dict[str, Any]:
        """查询 dsh 模型目录：providers/models/reasoning efforts + 默认选择。"""
        return self.request("models.list", {}, timeout=timeout)

    def set_default_model(
        self,
        *,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """把默认模型选择写回 dsh settings（agent-default-model 命名空间）。"""
        payload: dict[str, Any] = {"provider": provider, "model": model}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        return self.request("models.set_default", payload, timeout=timeout)

    def research_session(self) -> NodeResearchSession:
        return NodeResearchSession(self)

    def _wait_hello(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._hello is not None:
                return self._hello
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("harness kernel exited during hello")
            time.sleep(0.02)
        raise TimeoutError("harness kernel hello timed out")

    def _wait_ready(self) -> dict[str, Any]:
        timeout = max(self.startup_timeout, 30.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready is not None:
                return self._ready
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("harness kernel exited during dsh boot")
            time.sleep(0.02)
        raise TimeoutError("harness kernel dsh ready timed out")

    def _require_dsh_agents(self) -> dict[str, Any]:
        ready = self._wait_ready()
        if ready.get("agents"):
            return ready
        detail = " | ".join(self._stderr_tail[-8:]) or "dsh agents.create is not ready"
        raise RuntimeError(detail)

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._alive = False
            return
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "hello":
                    self._hello = msg
                    continue
                if msg.get("type") == "ready":
                    self._ready = msg
                    nonce = msg.get("credential_next_nonce")
                    if isinstance(nonce, str) and nonce:
                        os.environ["KSS_PI_AI_CREDENTIAL_NONCE"] = nonce
                    continue
                msg_id = str(msg.get("id") or "")
                if msg.get("type") == "event":
                    with self._lock:
                        waiter = self._pending.get(msg_id)
                    callback = waiter.get("on_event") if waiter else None
                    event = msg.get("event")
                    if callback is not None and isinstance(event, dict):
                        try:
                            callback(event)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                with self._lock:
                    waiter = self._pending.pop(msg_id, None)
                if waiter is not None:
                    waiter["body"] = msg
                    waiter["event"].set()
        finally:
            self._alive = False

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = _redact_kernel_text(line.rstrip())
                if text:
                    self._stderr_tail.append(text)
                    if len(self._stderr_tail) > 40:
                        self._stderr_tail.pop(0)
        except OSError:
            return


class NodeDesktopSession:
    """Production desktop turn: Node decides, Python projects chrome frames."""

    def __init__(self, kernel: HarnessKernel) -> None:
        self.kernel = kernel

    async def run(self, request: DesktopTurnRequest, host: DesktopHarnessHost) -> DesktopTurnResult:
        import asyncio

        if not self.kernel.alive:
            return DesktopTurnResult(status="unavailable", error="harness_session_unavailable")
        payload: dict[str, Any] = {
            "session_id": request.session_id,
            "input": request.input,
            "run_id": request.run_id,
        }
        payload.update(_agent_options_payload(request.provider_route))
        if self.kernel.driver != "dsh":
            payload["tool"] = "get_orientation"
        timeout = 180.0 if self.kernel.driver == "dsh" else _DEFAULT_TIMEOUT
        loop = asyncio.get_running_loop()
        # 去重键是 id(event)。id 只在对象存活期间唯一：若不持有引用，dict 被 GC 后
        # 地址复用会让新事件误判为「已见过」而被静默丢弃（表现为 UI 流式冻结、
        # confirm_required 丢失）。seen_refs 持有引用保证 id 不复用。
        seen: set[int] = set()
        seen_refs: list[dict[str, Any]] = []

        async def handle_event(event: dict[str, Any]) -> None:
            if event.get("type") in {"approval_request", "approval/request"}:
                host.bind_intent(
                    str(event.get("call_id") or ""),
                    name=str(event.get("tool") or ""),
                    command=str(event.get("command") or event.get("tool") or ""),
                    args=[],
                    tool_args=event.get("args") if isinstance(event.get("args"), dict) else {},
                )
            emit = host.emit
            if emit is not None:
                await emit(event)

        def on_event(event: dict[str, Any]) -> None:
            marker = id(event)
            if marker in seen:
                return
            seen.add(marker)
            seen_refs.append(event)
            asyncio.run_coroutine_threadsafe(handle_event(event), loop)

        try:
            body = await asyncio.to_thread(
                self.kernel.request,
                "desktop.turn",
                payload,
                timeout=timeout,
                on_event=on_event,
            )
        except TimeoutError as exc:
            if get_harness_kernel() is self.kernel:
                stop_harness_kernel()
            elif self.kernel.alive:
                self.kernel.abandon_wedged_turn(request.session_id)
            return DesktopTurnResult(status="unavailable", error=str(exc) or "harness_session_unavailable")
        except Exception as exc:  # noqa: BLE001
            return DesktopTurnResult(status="unavailable", error=str(exc) or "harness_session_unavailable")
        if self.kernel.driver != "dsh":
            for event in body.get("events") or []:
                if isinstance(event, dict):
                    await handle_event(event)
        tool_results = list(body.get("tool_results") or [])
        for item in body.get("execute") or []:
            if not isinstance(item, dict) or host.execute_tool is None:
                continue
            tool_results.append(
                host.execute_tool(
                    name=str(item.get("name") or "get_orientation"),
                    args=item.get("args") if isinstance(item.get("args"), dict) else {},
                    call_id=str(item.get("call_id") or request.run_id),
                )
            )
        status = str(body.get("status") or "interrupted")
        if body.get("ok") is False:
            status = "unavailable"
        assistant = str(body.get("assistant_text") or "")
        if tool_results and not assistant.startswith("KSS "):
            first = tool_results[0] if tool_results else {}
            if isinstance(first, dict) and first.get("ok") is True:
                assistant = f"KSS {first.get('command') or 'get_orientation'} ok"
        error = (
            None
            if body.get("ok") is not False
            else str(body.get("error") or "harness_session_unavailable")
        )
        if status == "completed" and not assistant.strip() and not tool_results:
            status = "unavailable"
            error = error or str(body.get("error") or "empty_completion")
        return DesktopTurnResult(
            status=status if status in {"completed", "aborted", "unavailable"} else "completed",
            assistant_text=assistant,
            tool_results=tool_results,
            error=error,
        )


class NodeResearchSession:
    """Production research turn: Node owns the node; overlay still judges completion."""

    def __init__(self, kernel: HarnessKernel) -> None:
        self.kernel = kernel

    def run(self, request: ResearchTurnRequest, driver: ResearchHarnessDriver) -> ResearchTurnResult:
        if not self.kernel.alive:
            return ResearchTurnResult(
                harness_status="interrupted",
                error="harness_session_unavailable",
                applied_write_ids=request.applied_write_ids,
            )
        try:
            payload: dict[str, Any] = {
                "prompt": request.prompt,
                "cwd": str(request.allowlist.cwd),
                "attempt_id": request.attempt_id,
                "allowlist": list(request.allowlist.tools),
                "session_id": request.attempt_id,
            }
            payload.update(_agent_options_payload())
            timeout = 180.0 if self.kernel.driver == "dsh" else _DEFAULT_TIMEOUT
            body = self.kernel.request("research.turn", payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return ResearchTurnResult(
                harness_status="interrupted",
                error=str(exc) or "harness_session_unavailable",
                applied_write_ids=request.applied_write_ids,
            )
        ids = list(request.applied_write_ids)
        for item in body.get("applied_write_ids") or []:
            ids.append(str(item))
        status = str(body.get("status") or "interrupted")
        return ResearchTurnResult(
            harness_status=status,
            assistant_text=str(body.get("assistant_text") or ""),
            applied_write_ids=tuple(ids),
            error=None if body.get("ok") is not False else str(body.get("error") or ""),
        )


def get_harness_kernel() -> HarnessKernel | None:
    return _KERNEL


def ensure_harness_kernel(**kwargs: Any) -> HarnessKernel:
    global _KERNEL
    with _KERNEL_LOCK:
        if _KERNEL is not None and _KERNEL.alive:
            return _KERNEL
        kernel = HarnessKernel(**kwargs)
        kernel.start()
        _KERNEL = kernel
        return kernel


def stop_harness_kernel() -> None:
    global _KERNEL
    with _KERNEL_LOCK:
        if _KERNEL is not None:
            _KERNEL.close()
            _KERNEL = None
